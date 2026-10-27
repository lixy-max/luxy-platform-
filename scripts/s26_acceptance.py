#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S26 验收：多节点工蜂 + 容量/成本感知调度 + 故障转移"""
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
PROJECT = Path("/home/luxy/paper_lora_project")
OUT = PROJECT / "data" / "hive" / "s26_acceptance.json"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RESULTS = []


def call(method, path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), round((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:600]}, round((time.time() - t0) * 1000)
    except Exception as e:
        return None, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, round((time.time() - t0) * 1000)


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "pass": bool(ok), "detail": str(detail)[:400]})
    print("%s %-38s %s" % ("✅" if ok else "❌", name, str(detail)[:120]))


def http_ok(url, timeout=2):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start_mock():
    if http_ok("http://127.0.0.1:8003/health"):
        return None
    env = dict(__import__("os").environ)
    env["MOCK_PORT"] = "8003"
    p = subprocess.Popen([sys.executable, str(PROJECT / "scripts" / "mock_worker.py")],
                         cwd=str(PROJECT), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if http_ok("http://127.0.0.1:8003/health"):
            return p
        time.sleep(0.2)
    return p


def stop_mock(p):
    try:
        if p:
            p.terminate()
            p.wait(timeout=5)
        else:
            subprocess.run(["pkill", "-f", "mock_worker.py"], timeout=5)
    except Exception:
        pass


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    mock_proc = start_mock()
    time.sleep(0.5)

    # ① 节点注册表 + 健康（确保 w2 具备 1.5b 能力标签）
    call("POST", "/hive/nodes/register",
         {"node": {"id": "w2", "capabilities": ["text", "json", "intent", "1.5b"]}})
    st, nodes, _ = call("GET", "/hive/nodes")
    ids = [n.get("id") for n in (nodes.get("nodes") or [])]
    check("节点注册表（w1/w2/cloud）", {"w1", "w2", "cloud"} <= set(ids), ids)
    st, h, _ = call("POST", "/hive/nodes/health?refresh=true")
    health = h.get("health") or {}
    check("健康检查：真实 w1 + mock w2",
          (health.get("w1") or {}).get("healthy") and (health.get("w2") or {}).get("healthy"),
          {k: v.get("status") for k, v in health.items()})
    res["health"] = health

    # ② 能力/成本路由
    st, p15, _ = call("GET", "/hive/nodes/pick?capability=1.5b")
    check("能力路由：1.5b → w2", (p15.get("node") or {}).get("id") == "w2",
          (p15.get("node") or {}).get("id"))
    st, pjson, _ = call("GET", "/hive/nodes/pick?capability=json&policy=cost")
    check("成本策略：json → w1（cost 1.0 < 2.0）", (pjson.get("node") or {}).get("id") == "w1",
          (pjson.get("node") or {}).get("id"))
    st, pvis, _ = call("GET", "/hive/nodes/pick?capability=vision")
    check("能力缺失 → cloud", (pvis.get("node") or {}).get("id") == "cloud",
          (pvis.get("node") or {}).get("id"))
    res["pick"] = {"1.5b": p15, "json_cost": pjson, "vision": pvis}

    # ③ 并发 20 跨节点
    call("POST", "/hive/nodes/stats/reset", {})
    outs = []
    lock = threading.Lock()

    def one(i):
        st, r, wall = call("POST", "/hive/nodes/run",
                           {"request": "并发任务 %d：输出一句确认" % i,
                            "capability": "text", "policy": "balanced",
                            "max_tokens": 16, "max_attempts": 3}, timeout=300)
        with lock:
            outs.append({"ok": r.get("ok"), "node": r.get("node"),
                         "latency_ms": r.get("latency_ms"), "wall_ms": wall})

    threads = [threading.Thread(target=one, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    okn = sum(1 for o in outs if o["ok"])
    nodes_hit = sorted({o["node"] for o in outs if o.get("node")})
    check("并发 20 跨节点成功", okn == 20, "ok=%s/20 nodes=%s" % (okn, nodes_hit))
    check("真实节点 + mock 节点均参与", {"w1", "w2"} <= set(nodes_hit), nodes_hit)
    res["concurrent"] = {"ok": okn, "nodes": nodes_hit, "samples": outs[:20]}

    # ④ 故障转移：停掉 mock（保留过期健康缓存）→ 强制先选 w2 → 失败 → 自动转 w1
    st, before, _ = call("GET", "/hive/nodes/stats")
    call("POST", "/hive/nodes/register",
         {"node": {"id": "w2", "priority": 0, "status": "healthy"}})
    stop_mock(mock_proc)
    time.sleep(1.0)
    st, r1, _ = call("POST", "/hive/nodes/run",
                     {"request": "缓存过期场景：w2 已挂", "capability": "text",
                      "policy": "latency", "max_tokens": 16, "max_attempts": 3},
                     timeout=300)
    st, stats2, _ = call("GET", "/hive/nodes/stats")
    find = (stats2.get("route_stats") or {}).get("failovers", 0)
    att_nodes = [a.get("node") for a in (r1.get("attempts") or [])]
    check("故障转移：w2 挂→自动切 w1",
          r1.get("ok") and r1.get("node") == "w1" and find >= 1
          and "w2" in att_nodes and "w1" in att_nodes,
          "node=%s failovers=%s attempts=%s" % (r1.get("node"), find, att_nodes))
    call("POST", "/hive/nodes/register",
         {"node": {"id": "w2", "priority": 2, "status": "healthy"}})
    st, h2, _ = call("POST", "/hive/nodes/health?refresh=true")
    health2 = h2.get("health") or {}
    check("故障检测：w2 不可用", not (health2.get("w2") or {}).get("healthy"),
          (health2.get("w2") or {}).get("status"))
    fo_ok = 0
    fo_nodes = []
    for i in range(5):
        st, r, _ = call("POST", "/hive/nodes/run",
                        {"request": "故障转移测试 %d" % i, "capability": "text",
                         "policy": "balanced", "max_tokens": 16, "max_attempts": 3},
                        timeout=300)
        if r.get("ok"):
            fo_ok += 1
            fo_nodes.append(r.get("node"))
    check("故障转移后 5/5 全走 w1",
          fo_ok == 5 and set(fo_nodes) == {"w1"}, "ok=%s nodes=%s" % (fo_ok, set(fo_nodes)))
    res["failover"] = {"health": health2, "ok": fo_ok, "nodes": fo_nodes,
                       "stats": stats2, "first": r1}

    # ⑤ 恢复 mock + 容量感知：w1 满载 → 选 w2
    mock_proc = start_mock()
    time.sleep(0.5)
    st, h3, _ = call("POST", "/hive/nodes/health?refresh=true")
    check("mock 节点恢复", ((h3.get("health") or {}).get("w2") or {}).get("healthy"),
          ((h3.get("health") or {}).get("w2") or {}).get("status"))
    call("POST", "/hive/nodes/register", {"node": {"id": "w1", "loaded_loras": ["a", "b"],
                                                   "status": "healthy"}})
    st, pcap, _ = call("GET", "/hive/nodes/pick?capability=json&policy=cost")
    check("容量感知：w1 满载 → w2", (pcap.get("node") or {}).get("id") == "w2",
          (pcap.get("node") or {}).get("id"))
    call("POST", "/hive/nodes/register", {"node": {"id": "w1", "loaded_loras": [],
                                                   "status": "healthy"}})
    st, stats3, _ = call("GET", "/hive/nodes/stats")
    route = stats3.get("route_stats") or {}
    check("调度统计", route.get("total", 0) >= 25 and set((route.get("per_node") or {}).keys())
          >= {"w1", "w2"}, "total=%s per_node=%s" % (route.get("total"),
                                                     list((route.get("per_node") or {}).keys())))
    res["stats"] = stats3

    passed = sum(1 for x in RESULTS if x["pass"])
    totaln = len(RESULTS)
    print("\n=== S26 验收汇总 ===\n通过 %d/%d" % (passed, totaln))
    res["summary"] = {"passed": passed, "total": totaln,
                      "verdict": "PASS" if passed == totaln else "FAIL",
                      "checks": RESULTS}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("结果 →", OUT)


if __name__ == "__main__":
    main()
