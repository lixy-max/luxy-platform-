# -*- coding: utf-8 -*-
"""S26 · 多节点工蜂调度（Multi-node Worker Pool）

- 节点选择：健康 + 能力匹配 + 容量 + 策略（balanced/cost/latency）
- 节点执行：mock 节点（验证用）/ vLLM 节点（真实 7B）
- 故障转移：节点失败 → 标记 degraded → 换下一个本地节点 → 全失败返回 need_cloud
- 统计：每节点 请求/成功/失败/延迟
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

import hive_node_registry as reg

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
POLICIES = ("balanced", "cost", "latency")
_lock = threading.RLock()
_rr = {"i": 0}
_stats = {"total": 0, "ok": 0, "fail": 0, "failovers": 0,
          "per_node": {}, "last_errors": []}


def _bump(node_id, ok, latency_ms=None):
    with _lock:
        _stats["total"] += 1
        _stats["ok" if ok else "fail"] += 1
        s = _stats["per_node"].setdefault(node_id, {"requests": 0, "ok": 0, "fail": 0,
                                                    "latency_ms": []})
        s["requests"] += 1
        s["ok" if ok else "fail"] += 1
        if latency_ms is not None:
            s["latency_ms"] = (s["latency_ms"] + [round(float(latency_ms), 1)])[-20:]


def reset_stats():
    with _lock:
        _stats.update({"total": 0, "ok": 0, "fail": 0, "failovers": 0,
                       "per_node": {}, "last_errors": []})
    return {"ok": True}


def _num(v, default=9.0):
    """安全数值（0 不能被 or 吞掉）"""
    try:
        return float(v) if v not in (None, "") else default
    except Exception:
        return default


def pick(capability=None, family=None, policy="balanced", include_cloud=True, exclude=None):
    policy = policy if policy in POLICIES else "balanced"
    exclude = set(exclude or [])
    cands = [n for n in reg.available_nodes(capability=capability, family=family)
             if n.get("id") not in exclude]
    if policy == "cost":
        cands.sort(key=lambda n: (_num(n.get("cost_score")), _num(n.get("priority"))))
    elif policy == "latency":
        cands.sort(key=lambda n: (_num(n.get("priority")), _num(n.get("cost_score"))))
    else:  # balanced
        cands.sort(key=lambda n: (_num(n.get("priority")), _num(n.get("cost_score"))))
        if cands:
            with _lock:
                _rr["i"] += 1
                idx = _rr["i"] % len(cands)
            cands = cands[idx:] + cands[:idx]
    if cands:
        return {"ok": True, "node": cands[0], "candidates": [c.get("id") for c in cands],
                "policy": policy}
    if include_cloud:
        cloud = reg.get_node("cloud")
        if cloud:
            return {"ok": True, "node": cloud, "candidates": ["cloud"], "policy": policy,
                    "reason": "本地无可用节点 → 云端兜底"}
    return {"ok": False, "error": "无可用节点", "policy": policy}


def _fetch_served_model(node):
    try:
        with urllib.request.urlopen(str(node.get("url")).rstrip("/") + "/v1/models",
                                    timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
            ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
            return ids[0] if ids else ""
    except Exception:
        return ""


def run_node(node, request, max_tokens=256, timeout=180):
    """在指定节点执行（mock / vllm）"""
    if node.get("type") == "cloud":
        return {"ok": False, "error": "cloud 节点需走 Failover 云端通道（S20）",
                "need_cloud": True}
    url = str(node.get("url") or "").rstrip("/")
    t0 = time.time()
    try:
        if node.get("type") == "mock":
            body = json.dumps({"request": request, "max_tokens": max_tokens}).encode()
            req = urllib.request.Request(url + "/mock/generate", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            return {"ok": True, "node": node.get("id"), "text": data.get("text"),
                    "latency_ms": round((time.time() - t0) * 1000, 1),
                    "served_model": data.get("node")}
        # vLLM
        model = node.get("served_model") or _fetch_served_model(node)
        payload = {"model": model, "messages": [{"role": "user", "content": request}],
                   "max_tokens": int(max_tokens), "temperature": 0.2}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(url + "/v1/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        text = ""
        try:
            text = data["choices"][0]["message"]["content"]
        except Exception:
            text = json.dumps(data)[:300]
        return {"ok": True, "node": node.get("id"), "text": text,
                "latency_ms": round((time.time() - t0) * 1000, 1),
                "served_model": model}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "ignore")[:300]
        return {"ok": False, "node": node.get("id"), "error": "HTTP %s: %s" % (e.code, err),
                "latency_ms": round((time.time() - t0) * 1000, 1)}
    except Exception as e:
        return {"ok": False, "node": node.get("id"),
                "error": "%s: %s" % (type(e).__name__, str(e)[:200]),
                "latency_ms": round((time.time() - t0) * 1000, 1)}


def route_run(request, capability=None, family=None, policy="balanced",
              max_tokens=256, max_attempts=3, exclude=None):
    """选择节点 + 执行 + 失败转移（全失败 → need_cloud）"""
    attempts = []
    exclude = list(exclude or [])
    for _ in range(max(1, int(max_attempts))):
        sel = pick(capability=capability, family=family, policy=policy, exclude=exclude)
        if not sel.get("ok"):
            break
        node = sel["node"]
        if node.get("type") == "cloud":
            attempts.append({"node": "cloud", "ok": False, "reason": "本地不可用，需云端"})
            return {"ok": False, "need_cloud": True, "attempts": attempts,
                    "reason": "本地无可用节点，需云端兜底"}
        r = run_node(node, request, max_tokens=max_tokens)
        r["capability"] = capability
        attempts.append(r)
        _bump(node.get("id"), r.get("ok"), r.get("latency_ms"))
        if r.get("ok"):
            if len(attempts) > 1:
                with _lock:
                    _stats["failovers"] += 1
            return {"ok": True, "node": node.get("id"), "result": r,
                    "attempts": attempts, "policy": policy,
                    "latency_ms": sum(a.get("latency_ms") or 0 for a in attempts)}
        exclude.append(node.get("id"))
        # 标记降级（真实故障验证）
        reg.update_node(node.get("id"), status="degraded")
        with _lock:
            _stats["last_errors"] = (_stats["last_errors"] +
                                     [{"node": node.get("id"),
                                       "error": str(r.get("error"))[:120]}])[-5:]
    with _lock:
        _stats["fail"] += 0
    return {"ok": False, "attempts": attempts, "need_cloud": True,
            "reason": "本地节点均失败，需云端兜底"}


def stats():
    with _lock:
        return dict(_stats, per_node={k: dict(v) for k, v in _stats["per_node"].items()})


def node_stats():
    return {"node_registry": reg.stats(), "route_stats": stats()}
