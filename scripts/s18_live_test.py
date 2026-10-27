#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S18 三路由实测（真实 14B 主脑）：8 个典型请求"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s18_live_test.json")
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CASES = [
    ("sim", "用 500 并发压测一下我们的路由系统，看看会不会把脏数据写进知识库"),
    ("sim", "生成一批合成脏数据，测试知识库的隔离机制"),
    ("local", "列出当前 LoRA 和工蜂状态"),
    ("local", "把这份表格按单价排序并导出 Excel"),
    ("local", "读取本地 Excel 采购清单并汇总"),
    ("cloud", "查一下 2026 年最新的大模型论文并分析趋势"),
    ("cloud", "把这份合同和最新法规逐条比对，给出风险与修改建议"),
    ("cloud", "从京东和淘宝抓取价格，做一份采购清单"),
    ("local", "帮我处理一下这个"),
]


def post(path, body, timeout=240):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode()), round((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}, round((time.time() - t0) * 1000)
    except Exception as e:
        return None, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, round((time.time() - t0) * 1000)


def main():
    results = []
    print("=== S18 三路由实测（真实 14B）===")
    for expect, req in CASES:
        st, d, wall = post("/hive/decide", {"request": req, "use_llm": True})
        dec = (d or {}).get("decision") or {}
        meta = (d or {}).get("meta") or {}
        target = dec.get("target")
        ok = st == 200 and (d or {}).get("ok")
        mark = "✅" if (ok and target == expect) else ("🟡" if ok else "❌")
        print("%s [期望 %-5s → 实际 %-5s] %5.1fs | conf=%.2f | fallback=%s attempts=%s mode=%s"
              % (mark, expect, target, wall / 1000, dec.get("confidence") or 0,
                 meta.get("fallback"), meta.get("attempts"), (meta.get("llm") or {}).get("mode")))
        print("    请求:", req[:60])
        if target == "local":
            print("    routing:", dec.get("routing"))
        elif target == "cloud":
            print("    cloud:", dec.get("cloud"))
        elif target == "sim":
            print("    sim:", dec.get("sim"))
        results.append({"expect": expect, "request": req, "http": st, "wall_ms": wall,
                        "decision": dec, "meta": meta})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 汇总
    n = len(results)
    correct = sum(1 for r in results if r["decision"].get("target") == r["expect"])
    fallbacks = sum(1 for r in results if (r["meta"] or {}).get("fallback"))
    avg = sum(r["wall_ms"] for r in results) / max(1, n)
    print("\n=== 汇总 ===")
    print("正确率: %d/%d | 兜底: %d | 平均耗时: %.0f ms"
          % (correct, n, fallbacks, avg))
    print("结果 →", OUT)

    # 状态与审计
    st, d, _ = post("/hive/decide", {"request": "查一下最新论文", "use_llm": False}, timeout=60)
    print("use_llm=False →", (d or {}).get("decision", {}).get("target"),
          "| fallback:", (d or {}).get("meta", {}).get("fallback"))

    def get(path):
        try:
            with urllib.request.urlopen(BASE + path, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            return {"error": str(e)[:200]}

    print("status:", json.dumps(get("/hive/status"), ensure_ascii=False)[:400])
    decs = get("/hive/decisions?limit=3")
    print("decisions 条数:", len((decs or {}).get("decisions") or []))


if __name__ == "__main__":
    main()
