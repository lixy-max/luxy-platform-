#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S20 实测：本地失败→云端教师→轨迹回收 / 阈值 / 复位（真实 DeepSeek）"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s20_live_test.json")
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
        return e.code, {"error": e.read().decode()[:500]}, round((time.time() - t0) * 1000)
    except Exception as e:
        return None, {"error": "%s: %s" % (type(e).__name__, str(e)[:300])}, round((time.time() - t0) * 1000)


def show(tag, d, wall):
    ok = bool(d.get("ok"))
    print("%s %-16s route=%-5s wall=%5.1fs traj=%s trigger=%s"
          % ("✅" if ok else "❌", tag, d.get("route"), wall / 1000,
             d.get("trajectory_recorded"), (d.get("failover") or {}).get("trigger") or
             (d.get("failover") or {}).get("triggered")))
    if ok:
        ans = str(d.get("answer") or d.get("local", {}).get("lora_id") or "")[:110]
        print("   answer:", ans.replace("\n", " "))
    else:
        print("   error:", str(d.get("error"))[:250])


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cases": []}
    call("POST", "/hive/failover/reset")
    st, stats0, _ = call("GET", "/hive/failover/stats")
    print("=== 初始 ==="); print(json.dumps({k: stats0.get(k) for k in
                                            ("consecutive_failures", "fail_max", "cloud_order")},
                                           ensure_ascii=False))

    cases = []

    # A/B：本地失败（坏 LoRA 路径）→ 云端教师 → 轨迹
    for i in range(2):
        prompt = "请解释 LoRA 动态加载的原理，并给出 vLLM 下的部署要点（第%d次）" % (i + 1)
        st, d, wall = call("POST", "/hive/run_failover",
                           {"request": prompt, "context": "S20 failover 实测；本地用坏 LoRA 触发失败",
                            "lora_id": "swk_persona_v1", "max_tokens": 200,
                            "task_type": "rag"}, timeout=900)
        show("local_fail_%d" % (i + 1), d, wall)
        cases.append({"name": "local_fail_%d" % (i + 1), "prompt": prompt,
                      "wall_ms": wall, "result": d})

    st, stats1, _ = call("GET", "/hive/failover/stats")
    print("\n=== 两次失败后 ===")
    print("consecutive=%s escalations=%s cloud_ok=%s trajectories=%s"
          % (stats1.get("consecutive_failures"), stats1.get("escalations"),
             stats1.get("cloud_ok"), stats1.get("trajectories")))

    # C：阈值触发 → 下一个普通请求直接云端（不走本地）
    st, d, wall = call("POST", "/hive/run_failover",
                       {"request": "用一句话说明蜂巢三路由的目标", "max_tokens": 120,
                        "task_type": "other"}, timeout=900)
    show("threshold_direct", d, wall)
    print("   failover:", json.dumps(d.get("failover"), ensure_ascii=False)[:220])
    cases.append({"name": "threshold_direct", "wall_ms": wall, "result": d})

    # D：force_local 本地成功 → 阈值复位
    st, d, wall = call("POST", "/hive/run_failover",
                       {"request": "把结果输出成 json：苹果 3 斤 12 元", "max_tokens": 64,
                        "force_local": True, "task_type": "data"}, timeout=900)
    show("force_local_ok", d, wall)
    cases.append({"name": "force_local_ok", "wall_ms": wall, "result": d})
    st, stats2, _ = call("GET", "/hive/failover/stats")
    print("\n=== 本地成功复位后 ===")
    print("consecutive=%s local_ok=%s" % (stats2.get("consecutive_failures"),
                                          stats2.get("local_ok")))

    # E：复杂任务 force_cloud → 云端教师质量回答 + 轨迹
    st, d, wall = call("POST", "/hive/run_failover",
                       {"request": "把合同条款与最新法规逐条比对，给出 3 条风险与修改建议",
                        "context": "合同类型：软件采购；预算 50 万；交付周期 6 个月",
                        "force_cloud": True, "max_tokens": 800, "task_type": "rag"}, timeout=900)
    show("force_cloud_complex", d, wall)
    cases.append({"name": "force_cloud_complex", "wall_ms": wall, "result": d})

    # 轨迹
    st, trajs, _ = call("GET", "/hive/trajectories?limit=5")
    st2, tstats, _ = call("GET", "/hive/trajectories/stats")
    print("\n=== 轨迹 ===")
    print("stats:", json.dumps(tstats, ensure_ascii=False)[:400])
    for t in (trajs.get("trajectories") or [])[:3]:
        print(" ", t.get("trajectory_id"), "|", t.get("provider"), "|",
              (t.get("tags") or {}).get("quality"), "| failover:", (t.get("tags") or {}).get("failover"),
              "|", str(t.get("task"))[:50])
    st3, stats3, _ = call("GET", "/hive/failover/stats")
    print("final failover stats:", json.dumps({k: stats3.get(k) for k in
                                               ("consecutive_failures", "local_ok", "local_fail",
                                                "escalations", "cloud_ok", "cloud_fail",
                                                "trajectories")}, ensure_ascii=False))
    res.update({"cases": cases, "stats_after_pairs": stats1,
                "stats_after_reset": stats2, "stats_final": stats3,
                "trajectories_stats": tstats,
                "trajectories_sample": trajs.get("trajectories", [])[:3]})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n结果 →", OUT)


if __name__ == "__main__":
    main()
