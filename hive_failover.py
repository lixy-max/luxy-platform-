# -*- coding: utf-8 -*-
"""S20 · Failover + 云端教师（Cloud Teacher）

职责：
- 失败阈值：本地 worker 连续失败 ≥ K（或不可用/超时）→ 触发升级
- 云端教师：整包 context（请求 + 补充背景 + 本地失败原因）→ 云端 provider（deepseek/kimi/glm）
- 轨迹回收：云端成功 → 统一 trajectory schema → data/hive/trajectories/*.jsonl
- 成功后重置连续失败计数（阈值解锁）；统计数据供 S21 自动训练使用
"""
import json
import os
import time
from collections import deque
from pathlib import Path

import requests

import hive_trajectory as traj_mod
import hive_worker_pool as pool

PROJECT_DIR = Path(__file__).resolve().parent
FAIL_MAX = int(os.environ.get("HIVE_FAIL_MAX", "2"))          # 连续失败阈值
FAIL_WINDOW_S = float(os.environ.get("HIVE_FAIL_WINDOW_S", "300"))
LOCAL_TIMEOUT_S = float(os.environ.get("HIVE_LOCAL_TIMEOUT_S", "60"))
CLOUD_ORDER = [x.strip() for x in os.environ.get(
    "HIVE_CLOUD_ORDER", "deepseek,kimi,glm").split(",") if x.strip()]
CLOUD_TIMEOUT = float(os.environ.get("HIVE_CLOUD_TIMEOUT_S", "240"))
CLOUD_MAX_TOKENS = int(os.environ.get("HIVE_CLOUD_MAX_TOKENS", "2000"))

CLOUD_SYSTEM = ("你是蜂巢的「云端教师」。本地工蜂无法完成/失败后，你收到完整上下文。"
                "请给出高质量、可执行的完整答案；如涉及工具调用，说明步骤与参数。"
                "用中文回答，结构清晰。")

_fail_ts = deque()          # 最近失败时间戳
_consecutive = 0
_stats = {"local_ok": 0, "local_fail": 0, "escalations": 0,
          "cloud_ok": 0, "cloud_fail": 0, "trajectories": 0, "last_error": ""}


# ══════════════════════════════════════════════════════════════
# 失败状态
# ══════════════════════════════════════════════════════════════
def _prune():
    now = time.time()
    while _fail_ts and now - _fail_ts[0] > FAIL_WINDOW_S:
        _fail_ts.popleft()


def record_failure(reason=""):
    global _consecutive
    _prune()
    _fail_ts.append(time.time())
    _consecutive += 1
    _stats["local_fail"] += 1
    _stats["last_error"] = str(reason)[:300]


def record_success():
    global _consecutive
    _consecutive = 0
    _fail_ts.clear()


def should_escalate():
    return _consecutive >= FAIL_MAX


def stats():
    return {"consecutive_failures": _consecutive, "fail_max": FAIL_MAX,
            "recent_failures": len(_fail_ts), "fail_window_s": FAIL_WINDOW_S,
            "local_timeout_s": LOCAL_TIMEOUT_S, "cloud_order": CLOUD_ORDER,
            **_stats}


def reset():
    global _consecutive
    _consecutive = 0
    _fail_ts.clear()
    for k in ("local_ok", "local_fail", "escalations", "cloud_ok",
              "cloud_fail", "trajectories"):
        _stats[k] = 0
    _stats["last_error"] = ""


# ══════════════════════════════════════════════════════════════
# 云端教师
# ══════════════════════════════════════════════════════════════
def _providers():
    try:
        st = json.loads((PROJECT_DIR / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return st.get("providers") or {}


def _provider_candidates():
    provs = _providers()
    out = []
    for name in CLOUD_ORDER + [k for k in provs if k not in CLOUD_ORDER]:
        cfg = provs.get(name) or {}
        if not isinstance(cfg, dict):
            continue
        key = str(cfg.get("api_key") or "").strip()
        base = str(cfg.get("api_base") or "").strip()
        model = str(cfg.get("model") or "").strip()
        if not (key and base and model):
            continue
        if "127.0.0.1" in base or "localhost" in base:
            continue
        out.append({"provider": name, "api_base": base.rstrip("/"),
                    "api_key": key, "model": model})
    return out


def cloud_ask(prompt, system=CLOUD_SYSTEM, max_tokens=CLOUD_MAX_TOKENS, temperature=0.3):
    """按偏好顺序调用云端；返回 (content, info)"""
    errors = []
    for cfg in _provider_candidates():
        t0 = time.time()
        body = {"model": cfg["model"],
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": str(prompt)}],
                "max_tokens": max_tokens}
        m = cfg["model"].lower()
        body["temperature"] = 1.0 if "kimi-k2" in m or "moonshot" in m else temperature
        try:
            r = requests.post(cfg["api_base"] + "/chat/completions",
                              headers={"Authorization": "Bearer " + cfg["api_key"],
                                       "Content-Type": "application/json"},
                              json=body, timeout=CLOUD_TIMEOUT)
            if r.status_code != 200:
                errors.append("%s %d: %s" % (cfg["provider"], r.status_code, r.text[:160]))
                continue
            d = r.json()
            msg = ((d.get("choices") or [{}])[0].get("message") or {})
            content = str(msg.get("content") or "").strip()
            if not content:
                content = str(msg.get("reasoning_content") or "").strip()[-1500:]
            if not content:
                errors.append("%s 返回空内容" % cfg["provider"])
                continue
            try:
                import cloud_bridge as _cb
                if hasattr(_cb, "_log_usage"):
                    _cb._log_usage(cfg, True, time.time() - t0)
            except Exception:
                pass
            return content, {"provider": cfg["provider"], "model": cfg["model"],
                             "duration_s": round(time.time() - t0, 2),
                             "attempts": len(errors) + 1, "via": "cloud_teacher"}
        except Exception as e:
            errors.append("%s: %s" % (cfg["provider"], str(e)[:160]))
    raise RuntimeError("云端教师全部不可用: " + "; ".join(errors)[:400])


# ══════════════════════════════════════════════════════════════
# 主入口：本地失败 → 云端教师 → 轨迹回收
# ══════════════════════════════════════════════════════════════
def _build_cloud_prompt(request, context="", local_error=""):
    parts = ["【任务】\n" + str(request or "")]
    if context:
        parts.append("【补充背景（整包 context）】\n" + str(context)[:4000])
    if local_error:
        parts.append("【本地工蜂失败原因】\n" + str(local_error)[:600])
    parts.append("请给出完整、可直接采用的高质量答案。")
    return "\n\n".join(parts)


def run_with_failover(request, context="", capabilities=None, messages=None,
                      lora_id=None, max_tokens=256, force_local=False,
                      force_cloud=False, task_type="other"):
    """执行：本地优先（S19 路由）→ 失败/超时/阈值 → 云端教师 → 轨迹回收"""
    t0 = time.time()
    st = pool.worker_status()
    local_ready = bool(st.get("ready"))
    escalate_flag = should_escalate()
    reason = ""
    if force_cloud:
        reason = "force_cloud"
    elif not local_ready:
        reason = "worker_unavailable"
    elif escalate_flag and not force_local:
        reason = "consecutive_failures>=%d" % FAIL_MAX

    # ── 云端路径（直接/兜底）──
    if reason:
        _stats["escalations"] += 1
        try:
            content, info = cloud_ask(_build_cloud_prompt(request, context, reason))
            _stats["cloud_ok"] += 1
            traj = traj_mod.build_trajectory(
                request, content, provider=info["provider"], model=info["model"],
                duration_s=info["duration_s"], context=context,
                failover={"trigger": reason, "local_ready": local_ready,
                          "consecutive_failures": _consecutive,
                          "local_error": reason},
                task_type=task_type,
                extra_steps=[{"turn": 0, "thought": "触发 failover：" + reason,
                              "tool": "", "input": {"request": str(request)[:300]},
                              "result": "", "status": "skipped"}])
            rec = traj_mod.record(traj)
            if rec.get("ok"):
                _stats["trajectories"] += 1
            return {"ok": True, "route": "cloud", "answer": content,
                    "cloud": info, "failover": {"triggered": True, "reason": reason,
                                                "threshold": FAIL_MAX},
                    "trajectory_id": traj["trajectory_id"],
                    "trajectory_recorded": bool(rec.get("ok")),
                    "duration_s": round(time.time() - t0, 2)}
        except Exception as e:
            _stats["cloud_fail"] += 1
            _stats["last_error"] = "cloud: %s" % str(e)[:300]
            return {"ok": False, "route": "cloud", "error": str(e)[:400],
                    "failover": {"triggered": True, "reason": reason},
                    "duration_s": round(time.time() - t0, 2)}

    # ── 本地路径（S19 route/run）──
    local = pool.run(request=request, messages=messages, lora_id=lora_id,
                     capabilities=capabilities, max_tokens=max_tokens)
    local_dur = local.get("duration_s") or round(time.time() - t0, 2)
    if local.get("ok") and float(local_dur) <= LOCAL_TIMEOUT_S:
        _stats["local_ok"] += 1
        record_success()
        return {"ok": True, "route": "local", "answer": local.get("content"),
                "local": {k: local.get(k) for k in
                          ("worker_id", "lora_id", "model", "loaded_now", "evicted",
                           "duration_s", "tokens_per_s", "usage")},
                "failover": {"triggered": False}, "duration_s": local_dur}

    # ── 本地失败/超时 → 云端教师 ──
    err = "local_timeout(>%.0fs)" % LOCAL_TIMEOUT_S if local.get("ok") else \
        (local.get("error") or "local_failed")
    record_failure(err)
    _stats["escalations"] += 1
    try:
        content, info = cloud_ask(_build_cloud_prompt(request, context, err))
        _stats["cloud_ok"] += 1
        traj = traj_mod.build_trajectory(
            request, content, provider=info["provider"], model=info["model"],
            duration_s=info["duration_s"], context=context,
            failover={"trigger": "local_failed", "local_error": str(err)[:300],
                      "local_duration_s": local_dur,
                      "local_route": {k: local.get(k) for k in ("lora_id", "model")}},
            task_type=task_type,
            extra_steps=[{"turn": 0, "thought": "本地失败 → 升级云端教师",
                          "tool": "", "input": {"request": str(request)[:300]},
                          "result": str(err)[:400], "status": "failed"}])
        rec = traj_mod.record(traj)
        if rec.get("ok"):
            _stats["trajectories"] += 1
        return {"ok": True, "route": "cloud", "answer": content,
                "cloud": info,
                "failover": {"triggered": True, "reason": "local_failed",
                             "local_error": str(err)[:300],
                             "consecutive_failures": _consecutive},
                "trajectory_id": traj["trajectory_id"],
                "trajectory_recorded": bool(rec.get("ok")),
                "duration_s": round(time.time() - t0, 2)}
    except Exception as e:
        _stats["cloud_fail"] += 1
        _stats["last_error"] = "cloud: %s" % str(e)[:300]
        return {"ok": False, "route": "cloud", "error": str(e)[:400],
                "failover": {"triggered": True, "reason": "local_failed",
                             "local_error": str(err)[:300],
                             "cloud_error": str(e)[:300]},
                "duration_s": round(time.time() - t0, 2)}
