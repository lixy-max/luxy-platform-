# -*- coding: utf-8 -*-
"""S27 · 灰度自动监控与回滚（Canary Monitor）

补齐 S22 的"灰度无自动回滚"：
  start_canary → 记录流量结果（ok/quality/latency）→ evaluate（阈值判定）
  → auto：breaching → 自动 rollback；healthy 且样本足够 → （可选）自动 promote_active
状态落盘：data/hive/canary_state.json；阈值可用环境变量覆盖。
"""
import json
import os
import pathlib
import statistics
import threading
import time

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
STATE_FILE = pathlib.Path(os.environ.get(
    "HIVE_CANARY_STATE", str(PROJECT_DIR / "data" / "hive" / "canary_state.json")))
MIN_SAMPLES = int(os.environ.get("HIVE_CANARY_MIN_SAMPLES", "5"))
MAX_ERROR_RATE = float(os.environ.get("HIVE_CANARY_MAX_ERROR_RATE", "0.2"))
MIN_QUALITY = float(os.environ.get("HIVE_CANARY_MIN_QUALITY", "0.5"))
MAX_P95_MS = float(os.environ.get("HIVE_CANARY_MAX_P95_MS", "5000"))
AUTO_PROMOTE = os.environ.get("HIVE_CANARY_AUTO_PROMOTE", "1") in ("1", "true", "True")
MAX_SAMPLES_KEEP = 200
_lock = threading.RLock()


def _load():
    try:
        if STATE_FILE.exists():
            d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
    except Exception:
        pass
    return {}


def _save(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _thresholds(custom=None):
    t = {"min_samples": MIN_SAMPLES, "max_error_rate": MAX_ERROR_RATE,
         "min_quality": MIN_QUALITY, "max_p95_ms": MAX_P95_MS,
         "auto_promote": AUTO_PROMOTE}
    if custom:
        t.update({k: v for k, v in custom.items() if v is not None})
    return t


def start_canary(lora_id, ratio=0.05, thresholds=None):
    """开始灰度监控（内部调用 S22 hotload.promote 进入 canary）"""
    try:
        import hive_hotload as hl
        pr = hl.promote(lora_id, ratio=ratio)
    except Exception as e:
        return {"ok": False, "error": "hotload.promote 失败: %s" % str(e)[:200]}
    with _lock:
        state = _load()
        state[str(lora_id)] = {
            "lora_id": str(lora_id), "ratio": float(ratio),
            "status": "observing", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "thresholds": _thresholds(thresholds),
            "samples": [], "counts": {"total": 0, "ok": 0, "fail": 0},
            "last_metrics": {}, "last_action": None, "promote_result": pr,
        }
        _save(state)
    return {"ok": True, "lora_id": str(lora_id), "status": "observing",
            "thresholds": _thresholds(thresholds), "promote": pr}


def record(lora_id, ok, quality=None, latency_ms=None):
    lid = str(lora_id)
    with _lock:
        state = _load()
        st = state.get(lid)
        if not st:
            return {"ok": False, "error": "未开始灰度: %s" % lid}
        st["samples"].append({"ok": bool(ok),
                              "quality": (float(quality) if quality is not None else None),
                              "latency_ms": (float(latency_ms) if latency_ms is not None else None),
                              "at": time.time()})
        st["samples"] = st["samples"][-MAX_SAMPLES_KEEP:]
        st["counts"]["total"] = len(st["samples"])
        st["counts"]["ok"] = sum(1 for s in st["samples"] if s["ok"])
        st["counts"]["fail"] = st["counts"]["total"] - st["counts"]["ok"]
        _save(state)
    return {"ok": True, "lora_id": lid, "counts": st["counts"]}


def _metrics(st):
    samples = st.get("samples") or []
    total = len(samples)
    fails = sum(1 for s in samples if not s["ok"])
    quals = [s["quality"] for s in samples if s.get("quality") is not None]
    lats = sorted([s["latency_ms"] for s in samples if s.get("latency_ms") is not None])
    p95 = None
    if lats:
        idx = min(len(lats) - 1, int(round(0.95 * (len(lats) - 1))))
        p95 = lats[idx]
    return {"total": total, "fail": fails,
            "error_rate": round(fails / total, 4) if total else 0.0,
            "avg_quality": round(statistics.mean(quals), 4) if quals else None,
            "p95_latency_ms": p95}


def evaluate(lora_id):
    lid = str(lora_id)
    with _lock:
        st = (_load().get(lid))
        if not st:
            return {"ok": False, "error": "未开始灰度: %s" % lid}
        th = st.get("thresholds") or _thresholds()
        m = _metrics(st)
        breach = []
        if m["total"] >= th["min_samples"]:
            if m["error_rate"] > th["max_error_rate"]:
                breach.append("error_rate %.2f > %.2f" % (m["error_rate"], th["max_error_rate"]))
            if m["avg_quality"] is not None and m["avg_quality"] < th["min_quality"]:
                breach.append("avg_quality %.2f < %.2f" % (m["avg_quality"], th["min_quality"]))
            if m["p95_latency_ms"] is not None and m["p95_latency_ms"] > th["max_p95_ms"]:
                breach.append("p95 %.0fms > %.0fms" % (m["p95_latency_ms"], th["max_p95_ms"]))
        if m["total"] < th["min_samples"]:
            status = "observing"
        elif breach:
            status = "breaching"
        else:
            status = "healthy"
        st["status"] = status
        st["last_metrics"] = m
        st["last_breach"] = breach
        _save(_load() | {lid: st})
    return {"ok": True, "lora_id": lid, "status": status, "metrics": m,
            "breach": breach, "thresholds": th}


def auto(lora_id):
    """自动判定：breaching→rollback；healthy+样本足→promote_active（可关）"""
    ev = evaluate(lora_id)
    if not ev.get("ok"):
        return ev
    lid = str(lora_id)
    st = _load().get(lid) or {}
    th = st.get("thresholds") or _thresholds()
    action = "none"
    result = {}
    if ev["status"] == "breaching":
        try:
            import hive_hotload as hl
            result = hl.rollback(lid, reason="canary 自动回滚: " + "; ".join(ev["breach"][:3]))
        except Exception as e:
            result = {"ok": False, "error": str(e)[:200]}
        action = "rollback"
    elif ev["status"] == "healthy" and th.get("auto_promote"):
        try:
            import hive_hotload as hl
            result = hl.promote_active(lid)
        except Exception as e:
            result = {"ok": False, "error": str(e)[:200]}
        action = "promote_active"
    with _lock:
        state = _load()
        st = state.get(lid) or {}
        st["last_action"] = action
        st["last_action_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        st["last_action_result"] = result
        state[lid] = st
        _save(state)
    return {"ok": True, "lora_id": lid, "status": ev["status"], "action": action,
            "metrics": ev["metrics"], "result": result}


def status(lora_id=None):
    state = _load()
    if lora_id:
        return {"ok": bool(state.get(str(lora_id))), "canary": state.get(str(lora_id))}
    return {"ok": True, "canaries": state,
            "thresholds": _thresholds(),
            "stats": {"count": len(state),
                      "promoted": sum(1 for v in state.values() if v.get("last_action") == "promote_active"),
                      "rolled_back": sum(1 for v in state.values() if v.get("last_action") == "rollback")}}


def clear(lora_id=None):
    with _lock:
        state = _load()
        if lora_id:
            state.pop(str(lora_id), None)
        else:
            state = {}
        _save(state)
    return {"ok": True}
