#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S22 实测：热加载流水线（shadow/评测/灰度/晋级/回滚）+ 仿真节点（压测/脏数据）"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s22_live_test.json")
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def call(method, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), round((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:800]}, round((time.time() - t0) * 1000)
    except Exception as e:
        return None, {"error": "%s: %s" % (type(e).__name__, str(e)[:400])}, round((time.time() - t0) * 1000)


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": []}
    cand = "agri_expert_v1"
    print("=== 1) 热加载流水线：(%s) ===" % cand)
    st, r, wall = call("POST", "/hive/hotload/register", {"lora_id": cand})
    print("register: ok=%s status=%s family_ok=%s" % (
        r.get("ok"), (r.get("candidate") or {}).get("status"),
        (r.get("candidate") or {}).get("family_ok")))
    res["register"] = r

    st, r, wall = call("POST", "/hive/hotload/evaluate", {"lora_id": cand, "max_tokens": 64})
    ev = r.get("evaluation") or {}
    print("evaluate: passed=%s status=%s quality=%s base_q=%s lat=%ss base_lat=%ss"
          % (r.get("passed"), r.get("status"), ev.get("quality"), ev.get("base_quality"),
             ev.get("avg_latency_s"), ev.get("base_avg_latency_s")))
    print("   content:", str((ev.get("candidate_runs") or [{}])[0].get("content"))[:80].replace("\n", " "))
    res["evaluate"] = r

    st, r, wall = call("POST", "/hive/hotload/promote", {"lora_id": cand, "ratio": 0.05})
    print("promote(canary 5%%): ok=%s status=%s ratio=%s"
          % (r.get("ok"), (r.get("candidate") or {}).get("status"),
             (r.get("candidate") or {}).get("canary_ratio")))
    res["promote"] = r

    # 灰度路由比例
    hits, total = 0, 300
    for i in range(total):
        st, r, _ = call("POST", "/hive/hotload/route",
                        {"active_lora": "base", "request": "灰度测试请求 %d" % i}, timeout=60)
        if r.get("is_canary"):
            hits += 1
    ratio = round(hits / total, 3)
    print("canary route: %d/%d = %.1f%%（目标 5%%）" % (hits, total, ratio * 100))
    res["canary_route"] = {"hits": hits, "total": total, "ratio": ratio}

    st, r, wall = call("POST", "/hive/hotload/promote_active", {"lora_id": cand})
    print("promote_active: ok=%s status=%s" % (r.get("ok"),
                                               (r.get("candidate") or {}).get("status")))
    res["promote_active"] = r

    st, r, wall = call("POST", "/hive/hotload/rollback", {"lora_id": cand})
    print("rollback: ok=%s status=%s unload=%s" % (
        r.get("ok"), (r.get("candidate") or {}).get("status"),
        (r.get("candidate") or {}).get("unload")))
    res["rollback"] = r

    # 负例：不存在的 adapter
    st, r, _ = call("POST", "/hive/hotload/register", {"lora_id": "swk_persona_v1"})
    print("register(broken): ok=%s error=%s" % (r.get("ok"), str(r.get("error"))[:80]))
    res["register_broken"] = r

    st, r, _ = call("GET", "/hive/hotload/status")
    print("hotload status: canary=%s worker_loaded=%s" % (
        r.get("canary"), (r.get("worker") or {}).get("loaded")))
    res["hotload_status"] = r

    print("\n=== 2) 仿真节点 ===")
    st, t, wall = call("POST", "/hive/sim/run",
                       {"scenario": "http_concurrent", "users": 10, "duration_s": 5,
                        "target": "/hive/decide", "use_llm": False}, timeout=300)
    tr = t.get("traffic") or {}
    print("压测: requests=%s ok=%s errors=%s qps=%s p50=%sms p95=%sms p99=%sms"
          % (tr.get("requests"), tr.get("ok"), tr.get("errors"), tr.get("qps"),
             (tr.get("latency_ms") or {}).get("p50"),
             (tr.get("latency_ms") or {}).get("p95"),
             (tr.get("latency_ms") or {}).get("p99")))
    res["traffic"] = t

    st, d, wall = call("POST", "/hive/sim/run",
                       {"scenario": "dirty_data", "n": 30}, timeout=300)
    dr = d.get("dirty") or {}
    print("脏数据: generated=%s injected=%s passed=%s quarantined=%s KB写入=%s"
          % (dr.get("generated"), dr.get("injected"), dr.get("passed"),
             dr.get("quarantined"), dr.get("kb_written")))
    res["dirty"] = d

    st, reports, _ = call("GET", "/hive/sim/reports?limit=5")
    st2, sstats, _ = call("GET", "/hive/sim/stats")
    print("sim 报告数:", len(reports.get("reports") or []), "| quarantine:",
          sstats.get("quarantine"))
    res["sim_reports"] = reports
    res["sim_stats"] = sstats

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n结果 →", OUT)


if __name__ == "__main__":
    main()
