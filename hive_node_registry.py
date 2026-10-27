# -*- coding: utf-8 -*-
"""S26 · 节点注册表（Node Registry）

多节点工蜂的"事实源"：每个节点登记 类型/URL/基座/GPU/槽位/成本/能力/健康。
- 真实节点：w1 = 7B AWQ（:8002，GPU1）
- 验证节点：w2 = mock 轻量服务（:8003，CPU）——用于验证多节点调度/故障转移，
  **不冒充真实 1.5B 模型**；真实 1.5B 工蜂需要独立显存，后续替换即可（同 schema）。
- cloud = 云端教师（成本高，兜底）

能力��容量：max_loras / loaded_loras；cost_score 越小越便宜；priority 越小越优先。
健康检查：GET /v1/models（vllm）或 /health（mock），10s 缓存。
"""
import json
import os
import pathlib
import threading
import time
import urllib.error
import urllib.request

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
NODES_FILE = pathlib.Path(os.environ.get("HIVE_NODES_FILE",
                                         str(PROJECT_DIR / "data" / "hive" / "nodes.json")))
_lock = threading.RLock()
_health_cache = {}   # node_id -> (ts, result)
HEALTH_TTL_S = float(os.environ.get("HIVE_NODE_HEALTH_TTL", "10"))

DEFAULT_NODES = [
    {"id": "w1", "type": "vllm", "url": "http://127.0.0.1:8002",
     "base_model": "Qwen2.5-7B-Instruct-AWQ", "family": "qwen2.5",
     "gpu": "RTX 3060 12GB", "vram_gb": 12, "max_loras": 2, "loaded_loras": [],
     "cost_score": 1.0, "priority": 1, "labels": ["local", "7b", "lora"],
     "capabilities": ["text", "json", "sheet", "intent", "analyze", "persona"],
     "status": "unknown", "served_model": ""},
    {"id": "w2", "type": "mock", "url": "http://127.0.0.1:8003",
     "base_model": "qwen2.5-1.5b-mock", "family": "qwen2.5",
     "gpu": "CPU (mock)", "vram_gb": 0, "max_loras": 1, "loaded_loras": [],
     "cost_score": 2.0, "priority": 2, "labels": ["local", "1.5b", "mock"],
     "capabilities": ["text", "json", "intent", "1.5b"],
     "status": "unknown", "served_model": "mock-1.5b"},
    {"id": "cloud", "type": "cloud", "url": "",
     "base_model": "deepseek-flash", "family": "*",
     "gpu": "cloud", "vram_gb": 0, "max_loras": 999, "loaded_loras": [],
     "cost_score": 8.0, "priority": 9, "labels": ["cloud"],
     "capabilities": ["text", "json", "vision", "web", "reasoning"],
     "status": "healthy", "served_model": "deepseek-flash"},
]


def _load_raw():
    try:
        if NODES_FILE.exists():
            data = json.loads(NODES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
    except Exception:
        pass
    try:
        NODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        NODES_FILE.write_text(json.dumps(DEFAULT_NODES, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:
        pass
    return [dict(n) for n in DEFAULT_NODES]


def _save(nodes):
    try:
        NODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        NODES_FILE.write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def list_nodes():
    with _lock:
        return [dict(n) for n in _load_raw()]


def get_node(node_id):
    for n in _load_raw():
        if n.get("id") == node_id:
            return dict(n)
    return None


def register_node(node):
    if not node or not node.get("id"):
        return {"ok": False, "error": "id 必填"}
    nid = str(node["id"])
    with _lock:
        nodes = _load_raw()
        old = next((n for n in nodes if n.get("id") == nid), {})
        merged = dict(old)
        merged.update(node)
        merged.setdefault("type", "vllm")
        merged.setdefault("status", "unknown")
        merged.setdefault("loaded_loras", [])
        merged.setdefault("max_loras", 1)
        merged.setdefault("cost_score", 1.0)
        merged.setdefault("priority", 5)
        merged.setdefault("capabilities", ["text"])
        merged.setdefault("family", "*")
        nodes = [n for n in nodes if n.get("id") != nid] + [merged]
        _save(nodes)
    return {"ok": True, "node": merged}


def update_node(node_id, **fields):
    with _lock:
        nodes = _load_raw()
        for n in nodes:
            if n.get("id") == node_id:
                n.update(fields)
                _save(nodes)
                return {"ok": True, "node": n}
    return {"ok": False, "error": "节点不存在: %s" % node_id}


def set_loaded_loras(node_id, loras):
    return update_node(node_id, loaded_loras=list(loras or []))


def capacity(node):
    maxl = int(node.get("max_loras") or 0)
    loaded = len(node.get("loaded_loras") or [])
    return max(0, maxl - loaded)


def health_check(node, timeout=3):
    nid = node.get("id")
    now = time.time()
    cached = _health_cache.get(nid)
    if cached and now - cached[0] < HEALTH_TTL_S:
        return cached[1]
    if node.get("type") == "cloud":
        res = {"healthy": True, "status": "healthy", "latency_ms": 0, "detail": "cloud"}
        _health_cache[nid] = (now, res)
        return res
    url = str(node.get("url") or "").rstrip("/")
    if not url:
        res = {"healthy": False, "status": "down", "detail": "无 URL"}
        _health_cache[nid] = (now, res)
        return res
    t0 = time.time()
    res = {"healthy": False, "status": "down", "latency_ms": None, "detail": ""}
    paths = ("/v1/models", "/health") if node.get("type") == "vllm" else ("/health", "/v1/models")
    for path in paths:
        try:
            with urllib.request.urlopen(url + path, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "ignore")
                res.update({"healthy": resp.status == 200, "status": "healthy",
                            "latency_ms": round((time.time() - t0) * 1000, 1),
                            "detail": body[:200]})
                if path == "/v1/models":
                    try:
                        data = json.loads(body)
                        ids = [m.get("id") for m in (data.get("data") or [])]
                        if ids:
                            res["served_model"] = ids[0]
                    except Exception:
                        pass
                break
        except urllib.error.HTTPError as e:
            res.update({"healthy": False, "status": "degraded",
                        "detail": "HTTP %s" % e.code})
        except Exception as e:
            res["detail"] = str(e)[:120]
    _health_cache[nid] = (time.time(), res)
    return res


def check_all(update=True, force=False):
    if force:
        _health_cache.clear()
    out = {}
    nodes = _load_raw()
    for n in nodes:
        nid = n.get("id")
        h = health_check(n)
        out[nid] = h
        if update:
            upd = {"status": h.get("status"), "last_health": time.strftime("%Y-%m-%dT%H:%M:%S")}
            if h.get("served_model"):
                upd["served_model"] = h["served_model"]
            update_node(nid, **upd)
    return out


def available_nodes(capability=None, family=None, include_cloud=False):
    """健康 + 能力匹配 + 有容量的节点（按策略排序交给调用方）"""
    nodes = _load_raw()
    out = []
    for n in nodes:
        if n.get("type") == "cloud" and not include_cloud:
            continue
        if n.get("status") in ("degraded", "down"):
            continue
        h = _health_cache.get(n.get("id"))
        healthy = bool(h and h[0] and h[1].get("healthy"))
        if n.get("status") == "healthy":
            healthy = True
        if not healthy:
            continue
        if capability:
            caps = n.get("capabilities") or []
            if capability not in caps:
                continue
        if family and family != "*" and n.get("family") not in ("*", family):
            continue
        if capacity(n) <= 0 and n.get("type") != "cloud":
            continue
        out.append(dict(n))
    return out


def stats():
    nodes = _load_raw()
    return {"nodes": len(nodes),
            "by_type": {t: sum(1 for n in nodes if n.get("type") == t)
                        for t in {n.get("type") for n in nodes}},
            "by_status": {s: sum(1 for n in nodes if n.get("status") == s)
                          for s in {n.get("status") for n in nodes}},
            "capacity": {n.get("id"): capacity(n) for n in nodes if n.get("type") != "cloud"},
            "file": str(NODES_FILE)}
