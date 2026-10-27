# -*- coding: utf-8 -*-
"""S22 · LoRA 热加载流水线（shadow → 评测 → 灰度 → 晋级/回滚）

状态机：
  candidate → shadow（注册，不参与路由）
  shadow --评测通过--> canary（按比例灰度；route_with_canary 决定是否走它）
  canary --灰度观察通过--> active（正式参与 S19 能力路由）
  shadow/canary --评测失败或人工回滚--> failed / rolled_back（卸载并停止灰度）

状态持久化：data/hive/lora_state.json（不改平台 registry，只做 overlay）
"""
import hashlib
import json
import os
import time
from pathlib import Path

import hive_lora_registry as reg_mod
import hive_worker_pool as pool

PROJECT_DIR = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("HIVE_LORA_STATE", str(PROJECT_DIR / "data" / "hive" / "lora_state.json")))
EVAL_PROMPTS = [
    "用一句话说明你能做什么。",
    "把结果输出成 json：苹果 3 斤 12 元。",
]
MIN_QUALITY = float(os.environ.get("HIVE_HOTLOAD_MIN_QUALITY", "0.6"))
DEFAULT_RATIO = float(os.environ.get("HIVE_CANARY_RATIO", "0.05"))
ACTIVE_STATUSES = ("active",)


def _load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_state(lora_id):
    return (_load_state().get(lora_id) or {}).get("status")


def _set_state(lora_id, status, **extra):
    state = _load_state()
    item = state.get(lora_id) or {}
    item.update({"lora_id": lora_id, "status": status,
                 "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    item.update(extra)
    state[lora_id] = item
    _save_state(state)
    return item


def _ensure_registered(lora_id):
    """确保 adapter 在平台 registry（新训练产物需要补登记）"""
    entry = reg_mod.get(lora_id)
    if entry:
        return entry
    path = Path("/home/luxy/loras") / lora_id
    cfg = path / "adapter_config.json"
    if not (path.is_dir() and cfg.exists()):
        return None
    try:
        ac = json.loads(cfg.read_text(encoding="utf-8"))
        base = ac.get("base_model_name_or_path") or ""
        rank = ac.get("r") or 16
    except Exception:
        base, rank = "", 16
    try:
        reg_path = reg_mod.REG_PATH
        reg = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else {}
        reg[lora_id] = {"name": lora_id, "path": str(path), "base_model": base,
                        "rank": rank, "description": "S22 热加载流水线登记",
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        reg_path.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return reg_mod.get(lora_id)


# ══════════════════════════════════════════════════════════════
# 流水线步骤
# ══════════════════════════════════════════════════════════════
def register_candidate(lora_id, base_model=None, allow_mismatch=False):
    """注册候选（shadow，不参与路由）；坏 adapter / base 家族不匹配 → 拒绝"""
    entry = _ensure_registered(lora_id)
    if not entry:
        return {"ok": False, "error": "adapter 不存在或无法登记: %s" % lora_id}
    # ① 路径/配置校验（坏 adapter 不允许进入流水线）
    if not entry.get("path_exists") or not entry.get("has_adapter_config"):
        return {"ok": False,
                "error": "adapter 不可用（路径缺失或无 adapter_config）: %s" % lora_id,
                "adapter": {"path": entry.get("path"),
                            "path_exists": entry.get("path_exists"),
                            "has_adapter_config": entry.get("has_adapter_config")}}
    st = pool.worker_status()
    worker_family = reg_mod._family(st.get("base_model"))
    family_ok = entry.get("family") == worker_family
    # ② base 家族校验（1.5B adapter 不能挂到 7B 工蜂）
    if not family_ok and not allow_mismatch:
        return {"ok": False,
                "error": "base 家族不匹配：候选 %s vs worker %s（不能热加载到当前工蜂）"
                         % (entry.get("family"), worker_family),
                "candidate_family": entry.get("family"),
                "worker_family": worker_family}
    item = _set_state(lora_id, "shadow", base_model=base_model or entry.get("base_model"),
                      family=entry.get("family"),
                      worker_family=worker_family,
                      family_ok=bool(family_ok), canary_ratio=0.0,
                      version=1, history=[{"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                          "event": "registered_shadow"}])
    return {"ok": True, "candidate": item,
            "warning": "" if family_ok else "候选与当前 worker base 不同家族（allow_mismatch=True）"}


def _run_prompts(lora_id=None, prompts=None, max_tokens=64):
    out = []
    for p in (prompts or EVAL_PROMPTS):
        r = pool.run(request=p, lora_id=lora_id or None, max_tokens=max_tokens,
                     capabilities=None) if lora_id else pool.run(request=p, max_tokens=max_tokens)
        out.append({"prompt": p, "ok": bool(r.get("ok")),
                    "content": str(r.get("content") or "")[:300],
                    "duration_s": r.get("duration_s"),
                    "tokens_per_s": r.get("tokens_per_s"),
                    "error": r.get("error")})
    return out


def evaluate(lora_id, prompts=None, max_tokens=64, min_quality=None):
    """评测候选：加载 adapter → 跑评测 prompt → 质量/延迟对比基线"""
    prompt_list = prompts or EVAL_PROMPTS
    base_runs = _run_prompts(None, prompt_list, max_tokens)
    cand_loaded = pool.ensure_loaded(lora_id)
    if not cand_loaded.get("ok"):
        item = _set_state(lora_id, "failed", eval={"error": cand_loaded.get("error")})
        return {"ok": False, "status": "failed", "error": cand_loaded.get("error"),
                "candidate": item}
    cand_runs = _run_prompts(lora_id, prompt_list, max_tokens)
    ok_cnt = sum(1 for r in cand_runs if r["ok"] and len(r["content"]) >= 10)
    quality = round(ok_cnt / max(1, len(cand_runs)), 2)
    base_q = round(sum(1 for r in base_runs if r["ok"] and len(r["content"]) >= 10) /
                   max(1, len(base_runs)), 2)
    lat = [r["duration_s"] for r in cand_runs if r.get("duration_s")]
    base_lat = [r["duration_s"] for r in base_runs if r.get("duration_s")]
    ev = {"quality": quality, "base_quality": base_q,
          "avg_latency_s": round(sum(lat) / len(lat), 2) if lat else None,
          "base_avg_latency_s": round(sum(base_lat) / len(base_lat), 2) if base_lat else None,
          "prompts": len(prompt_list),
          "candidate_runs": cand_runs, "base_runs": base_runs,
          "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    gate = MIN_QUALITY if min_quality is None else float(min_quality)
    passed = quality >= gate and quality >= base_q * 0.8
    ev["gate"] = {"min_quality": gate, "passed": bool(passed)}
    item = _set_state(lora_id, "shadow" if passed else "failed", eval=ev)
    return {"ok": True, "status": item["status"], "evaluation": ev,
            "passed": passed, "candidate": item}


def promote(lora_id, ratio=DEFAULT_RATIO):
    """shadow → canary（灰度）"""
    state = _load_state().get(lora_id) or {}
    if state.get("status") != "shadow":
        return {"ok": False, "error": "只有 shadow 可晋级 canary（当前 %r）" % state.get("status")}
    ev = state.get("eval") or {}
    if not (ev.get("gate") or {}).get("passed"):
        return {"ok": False, "error": "评测未通过，不能灰度"}
    item = _set_state(lora_id, "canary", canary_ratio=float(ratio),
                      canary_started_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    st = _load_state().get(lora_id) or {}
    hist = list(st.get("history") or [])
    hist.append({"at": item["updated_at"], "event": "promoted_canary", "ratio": float(ratio)})
    _set_state(lora_id, "canary", canary_ratio=float(ratio), history=hist)
    return {"ok": True, "candidate": item}


def promote_active(lora_id, ratio=None):
    """canary → active（正式）"""
    state = _load_state().get(lora_id) or {}
    if state.get("status") not in ("canary", "shadow"):
        return {"ok": False, "error": "只有 canary/shadow 可晋级 active（当前 %r）"
                % state.get("status")}
    item = _set_state(lora_id, "active", canary_ratio=1.0,
                      promoted_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    st = _load_state().get(lora_id) or {}
    hist = list(st.get("history") or [])
    hist.append({"at": item["updated_at"], "event": "promoted_active"})
    _set_state(lora_id, "active", canary_ratio=1.0, history=hist)
    return {"ok": True, "candidate": item}


def rollback(lora_id, reason="manual"):
    """回滚：卸载 + 停止灰度/降级"""
    st = pool.worker_status()
    unloaded = None
    if lora_id in (st.get("loaded") or []):
        ok, err, lat = pool._unload(lora_id)
        unloaded = {"ok": ok, "error": err, "latency_s": lat}
    item = _set_state(lora_id, "rolled_back", canary_ratio=0.0, rollback_reason=reason,
                      rollback_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    st2 = _load_state().get(lora_id) or {}
    hist = list(st2.get("history") or [])
    hist.append({"at": item["updated_at"], "event": "rolled_back", "reason": reason})
    _set_state(lora_id, "rolled_back", canary_ratio=0.0, rollback_reason=reason,
               history=hist, unload=unloaded)
    return {"ok": True, "candidate": _load_state().get(lora_id), "unload": unloaded}


def canary_lora():
    """当前灰度中的候选（取第一个）"""
    for lid, item in (_load_state().items()):
        if item.get("status") == "canary" and float(item.get("canary_ratio") or 0) > 0:
            return lid, item
    return None, None


def route_with_canary(active_lora, request):
    """加权路由：按请求哈希决定走灰度候选还是 active；返回 {lora_id, is_canary}"""
    cand, item = canary_lora()
    if not cand:
        return {"lora_id": active_lora, "is_canary": False, "ratio": 0.0}
    ratio = float(item.get("canary_ratio") or 0)
    h = int(hashlib.md5(str(request or "").encode("utf-8")).hexdigest()[:8], 16)
    hit = (h % 10000) < int(ratio * 10000)
    return {"lora_id": cand if hit else active_lora,
            "is_canary": bool(hit), "ratio": ratio, "active": active_lora}


def status():
    state = _load_state()
    st = pool.worker_status()
    return {"ok": True, "worker": {k: st.get(k) for k in
                                   ("ready", "base_model", "loaded", "slots_free")},
            "states": state, "canary": canary_lora()[0],
            "config": {"min_quality": MIN_QUALITY, "default_ratio": DEFAULT_RATIO}}


def pipeline(lora_id, prompts=None, ratio=DEFAULT_RATIO, max_tokens=64):
    """一键：注册 shadow → 评测 → 灰度（晋级 active 由人工/观测后调用）"""
    steps = {}
    steps["register"] = register_candidate(lora_id)
    if not steps["register"].get("ok"):
        return {"ok": False, "steps": steps}
    steps["evaluate"] = evaluate(lora_id, prompts, max_tokens)
    if not steps["evaluate"].get("passed"):
        return {"ok": False, "steps": steps, "final": "failed",
                "reason": "评测未通过"}
    steps["promote_canary"] = promote(lora_id, ratio)
    return {"ok": True, "steps": steps, "final": "canary", "lora_id": lora_id,
            "ratio": ratio}
