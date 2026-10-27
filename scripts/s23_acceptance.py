#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S23 最终集成验收：端到端 + 压测 + 回归 + 台账"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s23_acceptance.json")
sys.path.insert(0, "/home/luxy/paper_lora_project")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RESULTS = []


def call(method, path, body=None, timeout=600):
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


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "pass": bool(ok), "detail": str(detail)[:300]})
    print("%s %-34s %s" % ("✅" if ok else "❌", name, str(detail)[:110]))


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # ① 基础服务
    st, h, _ = call("GET", "/health", timeout=10)
    check("backend /health", st == 200)
    st, a, _ = call("GET", "/architecture/status", timeout=120)
    o = a.get("overall") or {}
    check("架构自检 18/18", o.get("score") == 1.0 and o.get("pass") == o.get("total"),
          "%.0f%% pass=%s/%s" % ((o.get("score") or 0) * 100, o.get("pass"), o.get("total")))

    # ② 三路由
    routes = {}
    for name, req in (("local", "把这份表格按单价排序并导出 Excel"),
                      ("cloud", "查一下 2026 年最新的大模型论文并分析趋势"),
                      ("sim", "用 500 并发压测路由系统并注入脏数据")):
        st, d, wall = call("POST", "/hive/decide", {"request": req, "use_llm": True})
        dec = d.get("decision") or {}
        routes[name] = {"http": st, "target": dec.get("target"), "wall_ms": wall,
                        "memory_used": (d.get("meta") or {}).get("memory_used")}
        check("三路由 %s" % name, dec.get("target") == name,
              "→ %s %.1fs" % (dec.get("target"), wall / 1000))
    res["routes"] = routes

    # ③ 本地执行（S19 LoRA）
    st, d, wall = call("POST", "/hive/run_failover",
                       {"request": "把结果输出成 json：苹果 3 斤 12 元", "max_tokens": 64,
                        "force_local": True}, timeout=600)
    check("本地执行(LoRA)", d.get("route") == "local" and d.get("ok"),
          "%s lora=%s %.1fs" % (d.get("route"), (d.get("local") or {}).get("lora_id"),
                                wall / 1000))
    res["local_exec"] = d

    # ④ Failover 云端 + 轨迹
    st, d, wall = call("POST", "/hive/run_failover",
                       {"request": "解释一下 vLLM LoRA 动态加载原理", "lora_id": "swk_persona_v1",
                        "max_tokens": 200, "task_type": "rag"}, timeout=900)
    check("Failover→云端+轨迹", d.get("route") == "cloud" and d.get("trajectory_recorded"),
          "%.1fs traj=%s" % (wall / 1000, d.get("trajectory_id")))
    res["failover"] = d

    # ⑤ 轨迹 → 质检 → 阈值
    st, q, _ = call("POST", "/hive/quality/run", {"limit": 200}, timeout=300)
    check("轨迹质检", q.get("passed", 0) >= 1,
          "passed=%s/%s" % (q.get("passed"), q.get("total")))
    st, c, _ = call("POST", "/hive/autotune/check",
                    {"dry_run": True, "allow_stop_worker": True}, timeout=120)
    check("阈值检查+训练计划", (c.get("plan") or {}).get("action") in ("direct", "stop_worker",
                                                                     "stop_main_brain", "defer"),
          "plan=%s" % (c.get("plan") or {}).get("action"))
    res["quality"] = q
    res["autotune"] = c

    # ⑥ 热加载流水线 + 回滚
    cand = "agri_expert_v1"
    call("POST", "/hive/hotload/register", {"lora_id": cand})
    st, ev, _ = call("POST", "/hive/hotload/evaluate", {"lora_id": cand, "max_tokens": 64},
                     timeout=600)
    st, pr, _ = call("POST", "/hive/hotload/promote", {"lora_id": cand, "ratio": 0.05})
    st, rb, _ = call("POST", "/hive/hotload/rollback", {"lora_id": cand})
    check("热加载 shadow→灰度→回滚",
          ev.get("passed") and (pr.get("candidate") or {}).get("status") == "canary"
          and (rb.get("candidate") or {}).get("status") == "rolled_back",
          "eval=%s canary=%s rollback=%s" % (ev.get("passed"),
                                             (pr.get("candidate") or {}).get("status"),
                                             (rb.get("candidate") or {}).get("status")))
    res["hotload"] = {"evaluate": ev, "promote": pr, "rollback": rb}

    # ⑦ 仿真节点
    st, simr, wall = call("POST", "/hive/sim/run",
                          {"scenario": "mixed", "users": 5, "duration_s": 3, "n": 20},
                          timeout=300)
    tr = (simr.get("traffic") or {})
    dr = (simr.get("dirty") or {})
    check("仿真压测+脏数据隔离",
          (tr.get("requests") or 0) > 10 and dr.get("kb_written") is False,
          "req=%s qps=%s dirty=%s/%s" % (tr.get("requests"), tr.get("qps"),
                                         dr.get("quarantined"), dr.get("injected")))
    res["sim"] = simr

    # ⑧ 临时知识库记忆
    st, mems, _ = call("GET", "/hive/memory/stats")
    st, sres, _ = call("GET", "/hive/memory/search?q=%E5%8E%8B%E6%B5%8B&limit=2",
                       timeout=180)
    rows = sres.get("results") or []
    if not rows:
        st, sres, _ = call("GET", "/hive/memory/search?q=%E5%8E%8B%E6%B5%8B&limit=2&semantic=true",
                           timeout=180)
        rows = sres.get("results") or []
    check("临时知识库记忆", (mems.get("total") or 0) >= 3 and bool(rows),
          "total=%s semantic=%s" % (mems.get("total"), rows[0].get("source") if rows else "-"))
    res["memory"] = {"stats": mems, "search": sres}

    # ⑨ 治理与指标
    st, fs, _ = call("GET", "/hive/failover/stats")
    st, tjs, _ = call("GET", "/hive/trajectories/stats")
    st, pj, _ = call("GET", "/hive/pool")
    check("工蜂池/轨迹/Failover 状态", pj.get("ready") and tjs.get("total") is not None,
          "worker=%s trajectories=%s" % (pj.get("ready"), tjs.get("total")))
    try:
        import bug_ledger as bl
        bl_st = bl.stats()
        check("Bug 台账", bl_st.get("total", 0) > 0,
              "total=%s fixed=%s parked=%s" % (bl_st.get("total"),
                                               bl_st.get("by_status", {}).get("fixed"),
                                               bl_st.get("by_status", {}).get("parked")))
        res["ledger"] = bl_st
    except Exception as e:
        check("Bug 台账", False, str(e)[:100])
    res.update({"failover_stats": fs, "trajectory_stats": tjs, "pool": {
        k: pj.get(k) for k in ("ready", "base_model", "loaded", "max_loras")}})

    # 汇总
    passed = sum(1 for r in RESULTS if r["pass"])
    totaln = len(RESULTS)
    print("\n=== 验收汇总 ===\n通过 %d/%d" % (passed, totaln))
    res["summary"] = {"passed": passed, "total": totaln,
                      "verdict": "PASS" if passed == totaln else "FAIL",
                      "checks": RESULTS}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("结果 →", OUT)


if __name__ == "__main__":
    main()
