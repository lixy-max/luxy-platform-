#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S23 记忆验证：主脑路由→临时知识库记录→检索→上下文注入（含语义 KB）"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s23_memory_test.json")
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def call(method, path, body=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), round((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}, round((time.time() - t0) * 1000)
    except Exception as e:
        return None, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, round((time.time() - t0) * 1000)


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # 清空旧记忆
    call("POST", "/hive/memory/clear", {}, timeout=120)

    print("=== 1) 产生 3 条路由决策（自动入临时知识库）===")
    tasks = [
        "把表格按单价排序并导出 Excel",
        "查一下 2026 年最新的大模型论文并分析趋势",
        "用 500 并发压测路由系统并注入脏数据",
    ]
    decisions = []
    for t in tasks:
        st, d, wall = call("POST", "/hive/decide", {"request": t, "use_llm": True})
        dec = d.get("decision") or {}
        print("  %-3s %-6s %.1fs memory_used=%s" % (
            "✅" if d.get("ok") else "❌", dec.get("target"), wall / 1000,
            (d.get("meta") or {}).get("memory_used")))
        decisions.append(d)
    time.sleep(3)  # 等异步 KB 写入

    print("\n=== 2) 记忆统计 ===")
    st, s, _ = call("GET", "/hive/memory/stats")
    print("total=%s alive=%s targets=%s kb_chunks=%s" % (
        s.get("total"), s.get("alive"), s.get("targets"),
        (s.get("kb") or {}).get("chunks")))
    res["stats"] = s

    print("\n=== 3) 关键词检索 ===")
    st, r, _ = call("GET", "/hive/memory/search?q=%E5%8E%8B%E6%B5%8B&limit=3&semantic=false")
    rows = r.get("results") or []
    print("命中 %d 条:" % len(rows))
    for x in rows[:3]:
        print("  [%s] %s → target=%s" % (x.get("source"), str(x.get("request"))[:70], x.get("target")))
    res["search_keyword"] = r

    print("\n=== 4) 语义检索（动态知识库 bge-m3）===")
    for q in ("并发压测怎么做", "论文分析任务"):
        st, r2, wall = call("GET", "/hive/memory/search?q=%s&limit=2&semantic=true"
                            % urllib.request.quote(q), timeout=180)
        rows2 = r2.get("results") or []
        print("  q=%r -> %d 条 (%s) %s" % (
            q, len(rows2), rows2[0].get("source") if rows2 else "-", "%.1fs" % (wall / 1000)))
        for x in rows2[:2]:
            print("     [%s] %s" % (x.get("source"), str(x.get("request"))[:90].replace("\n", " ")))
        res.setdefault("search_semantic", []).append({"query": q, "rows": rows2, "wall_ms": wall})

    print("\n=== 5) 上下文注入（再问同类任务，看 meta.memory_used）===")
    st, d, wall = call("POST", "/hive/decide",
                       {"request": "再压测一下路由，顺便看脏数据隔离", "use_llm": True})
    meta = d.get("meta") or {}
    print("target=%s memory_used=%s memory_chars=%s %.1fs"
          % ((d.get("decision") or {}).get("target"), meta.get("memory_used"),
             meta.get("memory_chars"), wall / 1000))
    res["decide_with_memory"] = d

    print("\n=== 6) TTL 清理 ===")
    st, cl, _ = call("POST", "/hive/memory/cleanup")
    print("cleanup:", cl)
    res["cleanup"] = cl

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n结果 →", OUT)


if __name__ == "__main__":
    main()
