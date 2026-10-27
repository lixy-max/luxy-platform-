# -*- coding: utf-8 -*-
"""S19 · Worker Pool + LoRA Router（vLLM 动态加载 / LRU 腾槽 / 能力路由）

★ 关键稳定性设计（实测 vLLM 0.11 bug）：
  vLLM 在"有推理请求 in-flight 时"执行 load/unload LoRA 会触发
  punica adapter mapping 竞态（ValueError: np.int32(x) is not in list → EngineDeadError）。
  因此本模块用 **读写锁**：推理请求持"读锁"；load/unload/LRU 腾槽持"写锁"
  （写锁等待所有 in-flight 请求结束），从根上避免该竞态。
"""
import json
import os
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

import requests

import hive_lora_registry as reg_mod

WORKER_ENDPOINT = os.environ.get("HIVE_WORKER_ENDPOINT", "http://127.0.0.1:8002")
PLATFORM = os.environ.get("HIVE_PLATFORM", "http://127.0.0.1:8001")
MAX_LORAS = int(os.environ.get("HIVE_W1_MAX_LORAS", "2"))
REQUEST_TIMEOUT = float(os.environ.get("HIVE_WORKER_TIMEOUT_S", "180"))
LOAD_TIMEOUT = float(os.environ.get("HIVE_LORA_LOAD_TIMEOUT_S", "120"))
WRITE_LOCK_TIMEOUT = float(os.environ.get("HIVE_LORA_WRITE_LOCK_S", "90"))

_lock = threading.RLock()
_rw = threading.Condition(_lock)
_readers = 0
_writing = False
_lru = OrderedDict()      # lora_id -> last_used_ts
_loaded = set()
_inflight = {}
_stats = {"loads": 0, "unloads": 0, "evictions": 0, "load_failures": 0,
          "routes": 0, "runs": 0, "race_retries": 0, "write_waits": 0,
          "last_error": ""}

CAP_HINTS = [
    (["json", "结构化", "schema", "抽取", "字段", "格式化输出"], ["json", "extract", "format"]),
    (["意图", "分类", "语义", "识别意图", "路由分类"], ["intent", "semantic", "classify"]),
    (["农业", "作物", "种植", "病虫害"], ["agri", "domain", "expert"]),
    (["人格", "风格", "语气", "角色"], ["persona", "style"]),
    (["刷题", "题库", "考试", "qa", "问答"], ["drill", "qa"]),
    (["论文", "文献", "摘要"], ["paper", "analyze"]),
    (["工作流", "workflow", "搭建", "蒸馏"], ["workflow", "authoring"]),
]


@contextmanager
def _reader():
    """推理请求：读锁（可并发；写锁需等我们全部结束）"""
    global _readers
    with _lock:
        while _writing:
            _rw.wait(0.5)
        _readers += 1
    try:
        yield
    finally:
        with _lock:
            _readers = max(0, _readers - 1)
            _rw.notify_all()


@contextmanager
def _writer(timeout=None):
    """加载/卸载：写锁（等待所有 in-flight 推理结束）"""
    global _writing
    timeout = WRITE_LOCK_TIMEOUT if timeout is None else timeout
    end = time.time() + timeout
    with _lock:
        waited = False
        while _writing or _readers > 0:
            remain = end - time.time()
            if remain <= 0:
                raise TimeoutError("写锁等待超时（in-flight 请求未结束）")
            waited = True
            _rw.wait(min(0.3, remain))
        if waited:
            _stats["write_waits"] += 1
        _writing = True
    try:
        yield
    finally:
        with _lock:
            _writing = False
            _rw.notify_all()


def infer_capabilities(request):
    low = str(request or "").lower()
    caps = []
    for kws, cs in CAP_HINTS:
        if any(k in low for k in kws):
            caps.extend(c for c in cs if c not in caps)
    return caps


# ══════════════════════════════════════════════════════════════
# Worker 探测
# ══════════════════════════════════════════════════════════════
def _served_ids(timeout=5):
    try:
        r = requests.get(WORKER_ENDPOINT + "/v1/models", timeout=timeout)
        if r.status_code != 200:
            return [], "http_%d" % r.status_code
        ids = [m.get("id") for m in (r.json() or {}).get("data") or [] if m.get("id")]
        return ids, ""
    except Exception as e:
        return [], "%s: %s" % (type(e).__name__, str(e)[:120])


def _base_of(ids):
    if not ids:
        return ""
    cands = [i for i in ids if "/" in i or "models" in i]
    if cands:
        return max(cands, key=len)
    return ids[0]


def worker_status():
    ids, err = _served_ids()
    base = _base_of(ids)
    loaded = [i for i in ids if i != base]
    with _lock:
        _loaded.clear()
        _loaded.update(loaded)
        for l in loaded:
            _lru.setdefault(l, time.time())
        for l in list(_lru):
            if l not in _loaded:
                _lru.pop(l, None)
        lru_order = list(_lru.keys())
    return {"ready": bool(base), "endpoint": WORKER_ENDPOINT, "base_model": base,
            "loaded": sorted(loaded), "lru_order": lru_order,
            "max_loras": MAX_LORAS, "slots_free": max(0, MAX_LORAS - len(loaded)),
            "family": reg_mod._family(base), "error": err,
            "inflight": dict(_inflight)}


def _load(lora_id):
    entry = reg_mod.get(lora_id)
    if not entry:
        return False, "unknown lora: %s" % lora_id, 0.0
    if not entry.get("path_exists"):
        return False, "路径不存在: %s" % entry.get("path"), 0.0
    t0 = time.time()
    errors = []
    for attempt in (1, 2):
        try:
            r = requests.post(WORKER_ENDPOINT + "/v1/load_lora_adapter",
                              json={"lora_name": lora_id, "lora_path": entry["path"]},
                              timeout=LOAD_TIMEOUT)
            if r.status_code == 200:
                return True, ("retry_ok" if attempt > 1 else ""), round(time.time() - t0, 2)
            txt = r.text[:300]
            if "already" in txt.lower() or "exists" in txt.lower():
                return True, "already_loaded", round(time.time() - t0, 2)
            errors.append("vllm%s %d: %s" % ("" if attempt == 1 else "-retry",
                                             r.status_code, txt))
        except Exception as e:
            errors.append("vllm%s: %s" % ("" if attempt == 1 else "-retry", str(e)[:120]))
        if attempt == 1:
            time.sleep(0.8)
    try:
        r2 = requests.post(PLATFORM + "/loras/load",
                           json={"lora_name": lora_id, "lora_path": entry["path"],
                                 "force": True}, timeout=LOAD_TIMEOUT)
        if r2.status_code == 200:
            return True, "", round(time.time() - t0, 2)
        errors.append("platform %d: %s" % (r2.status_code, r2.text[:200]))
    except Exception as e:
        errors.append("platform: %s" % str(e)[:150])
    _stats["load_failures"] += 1
    _stats["last_error"] = "; ".join(errors)[:400]
    return False, _stats["last_error"], round(time.time() - t0, 2)


def _unload(lora_id):
    t0 = time.time()
    try:
        r = requests.post(WORKER_ENDPOINT + "/v1/unload_lora_adapter",
                          json={"lora_name": lora_id}, timeout=LOAD_TIMEOUT)
        if r.status_code == 200:
            return True, "", round(time.time() - t0, 2)
    except Exception:
        pass
    try:
        r2 = requests.post(PLATFORM + "/loras/unload",
                           json={"lora_name": lora_id}, timeout=LOAD_TIMEOUT)
        if r2.status_code == 200:
            return True, "", round(time.time() - t0, 2)
    except Exception:
        pass
    return False, "unload failed", round(time.time() - t0, 2)


def ensure_loaded(lora_id):
    """确保 LoRA 在 worker 上（写锁内执行 load/unload，避免 vLLM 竞态）"""
    out = {"ok": False, "lora_id": lora_id, "loaded_now": False, "evicted": [],
           "latency_s": 0.0, "error": ""}
    t0 = time.time()
    with _lock:
        _stats["routes"] += 1
        if lora_id in _loaded:
            _lru[lora_id] = time.time()
            _lru.move_to_end(lora_id)
            out.update({"ok": True, "loaded_now": False})
            out["latency_s"] = round(time.time() - t0, 2)
            return out
    st = worker_status()
    if not st["ready"]:
        out["error"] = "worker 不可用: %s" % (st.get("error") or "")
        return out
    try:
        with _writer():
            st2 = worker_status()
            if not st2["ready"]:
                out["error"] = "worker 不可用: %s" % (st2.get("error") or "")
                return out
            if lora_id in st2["loaded"]:
                with _lock:
                    _loaded.add(lora_id)
                    _lru[lora_id] = time.time()
                    _lru.move_to_end(lora_id)
                out.update({"ok": True, "loaded_now": False})
                out["latency_s"] = round(time.time() - t0, 2)
                return out
            while len(st2["loaded"]) >= MAX_LORAS:
                candidates = [l for l in st2["loaded"] if _inflight.get(l, 0) <= 0]
                if not candidates:
                    out["error"] = "槽位被 in-flight 请求占满，稍后重试（已加载 %s）" % st2["loaded"]
                    _stats["load_failures"] += 1
                    return out
                victim = candidates[0]
                ok, err, _ = _unload(victim)
                if not ok:
                    out["error"] = "LRU 卸载失败(%s): %s" % (victim, err)
                    return out
                out["evicted"].append(victim)
                _stats["evictions"] += 1
                _stats["unloads"] += 1
                st2 = worker_status()
            ok, werr, lat = _load(lora_id)
            out["load_latency_s"] = lat
            if not ok:
                out["error"] = werr
                return out
            _stats["loads"] += 1
            with _lock:
                _loaded.add(lora_id)
                _lru[lora_id] = time.time()
                _lru.move_to_end(lora_id)
            out.update({"ok": True, "loaded_now": True})
            out["latency_s"] = round(time.time() - t0, 2)
            return out
    except TimeoutError as e:
        out["error"] = "等待推理请求结束超时: %s" % e
        _stats["load_failures"] += 1
        return out


# ══════════════════════════════════════════════════════════════
# 路由 / 执行
# ══════════════════════════════════════════════════════════════
def route(request, capabilities=None):
    caps = list(capabilities or infer_capabilities(request))
    st = worker_status()
    if not st["ready"]:
        return {"ok": False, "error": "worker 不可用: %s" % (st.get("error") or ""),
                "capabilities": caps}
    entry = reg_mod.pick(caps, worker_base=st["base_model"]) if caps else None
    if entry is None:
        return {"ok": True, "worker_id": "w1", "lora_id": "base",
                "base_model": st["base_model"], "endpoint": WORKER_ENDPOINT,
                "loaded_now": False, "evicted": [], "why": "无匹配能力 → base",
                "capabilities": caps}
    ensured = ensure_loaded(entry["id"])
    if not ensured["ok"]:
        return {"ok": False, "error": ensured.get("error"),
                "worker_id": "w1", "lora_id": entry["id"],
                "capabilities": caps, "evicted": ensured.get("evicted")}
    return {"ok": True, "worker_id": "w1", "lora_id": entry["id"],
            "base_model": st["base_model"], "endpoint": WORKER_ENDPOINT,
            "loaded_now": ensured["loaded_now"], "evicted": ensured["evicted"],
            "load_latency_s": ensured.get("load_latency_s", 0.0),
            "score": entry["score"], "capability": entry["capability"],
            "capabilities": caps,
            "why": "能力匹配 %s → %s(score %.2f)" % (caps, entry["id"], entry["score"])}


def run(request=None, messages=None, lora_id=None, capabilities=None,
        max_tokens=256, temperature=0.2):
    """路由 + 执行（推理持读锁 + pin 保护，避免加载/卸载打断）"""
    t0 = time.time()
    if messages is None:
        messages = [{"role": "user", "content": str(request or "")}]
    if lora_id and lora_id != "base":
        st = worker_status()
        if not st["ready"]:
            return {"ok": False, "error": "worker 不可用"}
        ensured = ensure_loaded(lora_id)
        if not ensured["ok"]:
            return {"ok": False, "error": ensured.get("error"), "lora_id": lora_id}
        r = {"ok": True, "worker_id": "w1", "lora_id": lora_id,
             "base_model": st["base_model"], "endpoint": WORKER_ENDPOINT,
             "loaded_now": ensured["loaded_now"], "evicted": ensured["evicted"],
             "load_latency_s": ensured.get("load_latency_s", 0.0)}
    else:
        r = route(request if request is not None else messages[-1]["content"], capabilities)
        if not r.get("ok"):
            return r
    st = worker_status()
    model = r["lora_id"] if r["lora_id"] != "base" else st["base_model"]
    pin = r["lora_id"] if r.get("lora_id") not in (None, "base") else None
    last_err = ""
    for attempt in range(2):
        resp = None
        if pin:
            with _lock:
                _inflight[pin] = _inflight.get(pin, 0) + 1
        try:
            with _reader():
                resp = requests.post(WORKER_ENDPOINT + "/v1/chat/completions",
                                     json={"model": model, "messages": messages,
                                           "max_tokens": max_tokens,
                                           "temperature": temperature},
                                     timeout=REQUEST_TIMEOUT)
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, str(e)[:200])
        finally:
            if pin:
                with _lock:
                    _inflight[pin] = max(0, _inflight.get(pin, 0) - 1)
                    if _inflight[pin] == 0:
                        _lru[pin] = time.time()
        if resp is not None and resp.status_code == 200:
            d = resp.json()
            content = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            usage = d.get("usage") or {}
            _stats["runs"] += 1
            out = {**r, "ok": True, "model": model, "content": content, "usage": usage,
                   "duration_s": round(time.time() - t0, 2),
                   "tokens_per_s": round((usage.get("completion_tokens") or 0) /
                                         max(0.001, time.time() - t0), 1)}
            if attempt > 0:
                out["retried"] = True
                _stats["race_retries"] += 1
            return out
        if resp is not None:
            last_err = "chat %d: %s" % (resp.status_code, resp.text[:300])
            if resp.status_code == 404 and attempt == 0 and pin:
                e2 = ensure_loaded(pin)
                if e2.get("ok"):
                    continue
    return {**r, "ok": False, "error": last_err or "unknown error"}


def pool_status():
    with _lock:
        st = worker_status()
        st["lru_order"] = list(_lru.keys())
        st["stats"] = dict(_stats)
        st["registry"] = reg_mod.summary(worker_base=st.get("base_model"))
        return st


def reset_state():
    global _readers, _writing
    with _lock:
        _lru.clear()
        _loaded.clear()
        _inflight.clear()
        _readers = 0
        _writing = False
        for k in _stats:
            _stats[k] = 0 if isinstance(_stats[k], int) else ""
