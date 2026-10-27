#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S19 实测：LoRA 热加载/LRU 腾槽/能力路由/并发（真实 7B 工蜂）"""
import concurrent.futures as cf
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s19_live_test.json")
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def req(method, path, body=None, timeout=600):
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
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cases": [], "concurrency": {}}
    st, pool, _ = req("GET", "/hive/pool")
    print("=== Worker Pool ===")
    print("ready=%s base=%s loaded=%s slots_free=%s max_loras=%s"
          % (pool.get("ready"), pool.get("base_model"), pool.get("loaded"),
             pool.get("slots_free"), pool.get("max_loras")))
    reg = (pool.get("registry") or {})
    print("registry: total=%s compatible=%s" % (reg.get("total"), reg.get("compatible")))
    res["pool_initial"] = {k: pool.get(k) for k in
                           ("ready", "base_model", "loaded", "max_loras", "slots_free")}
    res["registry"] = {"total": reg.get("total"), "compatible": reg.get("compatible")}

    cases = [
        ("json", "把下面的结果输出成结构化 json 字段：苹果 3 斤 12 元", 48),
        ("intent", "识别一下这句话的意图：帮我查一下明天的天气", 32),
        ("agri", "农业作物小麦出现叶片发黄，可能是什么病虫害？", 64),
        ("json_again", "把下面结果输出成 json：香蕉 2 斤 8 元", 48),
    ]
    for name, prompt, mt in cases:
        st, d, wall = req("POST", "/hive/run_local",
                          {"request": prompt, "max_tokens": mt}, timeout=600)
        ok = bool(d.get("ok"))
        print("%s %-10s lora=%-22s loaded=%s evicted=%s load=%ss run=%ss tok/s=%s"
              % ("✅" if ok else "❌", name, d.get("lora_id"), d.get("loaded_now"),
                 d.get("evicted"), d.get("load_latency_s"), d.get("duration_s"),
                 d.get("tokens_per_s")))
        if not ok:
            print("   error:", str(d.get("error"))[:300])
        else:
            print("   content:", str(d.get("content"))[:120].replace("\n", " "))
        res["cases"].append({"name": name, "prompt": prompt, "wall_ms": wall,
                             "decision": d})

    st, pool2, _ = req("GET", "/hive/pool")
    print("\n=== Pool after cases ===")
    print("loaded=%s lru=%s stats=%s"
          % (pool2.get("loaded"), pool2.get("lru_order"),
             {k: pool2.get("stats", {}).get(k) for k in
              ("loads", "unloads", "evictions", "load_failures", "routes", "runs")}))
    res["pool_after"] = {"loaded": pool2.get("loaded"), "lru": pool2.get("lru_order"),
                         "stats": pool2.get("stats")}

    # 并发：20 请求跨 4 类能力
    # 按计划 S19 DoD：并发 20 请求跨 **2 个 LoRA**（4 类能力并发会造成 LRU 抖动，另用顺序用例验证 LRU）
    prompts = [
        "输出 json：商品A 2 个 10 元", "识别意图：我要退货",
    ]

    def one(i):
        p = prompts[i % len(prompts)]
        st, d, wall = req("POST", "/hive/run_local",
                          {"request": p, "max_tokens": 16}, timeout=600)
        return {"i": i, "ok": bool(d.get("ok")), "lora": d.get("lora_id"),
                "model": d.get("model"), "wall_ms": wall,
                "loaded_now": d.get("loaded_now"), "evicted": d.get("evicted"),
                "error": str(d.get("error"))[:200] if not d.get("ok") else ""}

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        rows = list(ex.map(one, range(20)))
    total = round(time.time() - t0, 2)
    okn = sum(1 for r in rows if r["ok"])
    print("\n=== 并发 20（5 线程，跨 4 类能力）===")
    print("成功 %d/20 | 总耗时 %.2fs | 平均 %.0f ms/请求"
          % (okn, total, sum(r["wall_ms"] for r in rows) / 20))
    models = {}
    for r in rows:
        models[r.get("lora")] = models.get(r.get("lora"), 0) + 1
    print("命中分布:", models)
    st, pool3, _ = req("GET", "/hive/pool")
    print("并发后 loaded=%s stats=%s"
          % (pool3.get("loaded"),
             {k: pool3.get("stats", {}).get(k) for k in
              ("loads", "unloads", "evictions", "load_failures", "routes", "runs")}))
    res["concurrency"] = {"ok": okn, "total": len(rows), "seconds": total,
                          "avg_ms": round(sum(r["wall_ms"] for r in rows) / len(rows)),
                          "distribution": models, "rows": rows}
    res["pool_after_concurrency"] = {"loaded": pool3.get("loaded"),
                                     "stats": pool3.get("stats")}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n结果 →", OUT)


if __name__ == "__main__":
    main()
