#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S28 验收：治理（审批/预算/熔断/审计）+ 受控自主 10 次闭环"""
import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/home/luxy/paper_lora_project")

BASE = "http://127.0.0.1:8001"
OUT = "/home/luxy/paper_lora_project/data/hive/s28_acceptance.json"
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
    print("%s %-40s %s" % ("✅" if ok else "❌", name, str(detail)[:120]))


HIGH = {
    "manifest_version": "1.0", "capability_id": "tool.s28_high_risk_demo.v1",
    "name": "敏感数据处理能力（需审批）", "type": "tool",
    "capability_tags": ["s28", "high_risk"],
    "license": "Apache-2.0",
    "interface": {"protocol": "python", "version": "1.0",
                  "io_schema": {"input": "object", "output": "object"}},
    "runtime": {"os": "linux", "python": "3.12", "deps": [], "gpu": {"vram_gb": 0}},
    "env": {"vars": [], "secrets": []},
    "data": {"needs": [], "sensitivity": "high"},
    "tests": {"smoke": [{"input": {}, "expect_contains": "ok"}],
              "eval": {"metric": "accuracy", "threshold": 0.8,
                       "cases": [{"input": {}, "expect": {"ok": True}}]}},
    "sandbox": {"language": "python", "entrypoint": "tool.py",
                "artifacts": [{"path": "tool.py",
                               "content": "def run(p):\n    return {\"ok\": True}\n"}],
                "smoke": [{"input": {}, "expect_contains": "ok"}],
                "eval": {"metric": "accuracy", "threshold": 0.8,
                         "cases": [{"input": {}, "expect": {"ok": True}}]}},
}

LOW = {
    "manifest_version": "1.0", "capability_id": "tool.s28_low_risk_demo.v1",
    "name": "低风险工具（自动批准）", "type": "tool",
    "capability_tags": ["s28", "low"],
    "license": "Apache-2.0",
    "interface": {"protocol": "python", "version": "1.0",
                  "io_schema": {"input": "object", "output": "object"}},
    "runtime": {"os": "linux", "python": "3.12", "deps": [], "gpu": {"vram_gb": 0}},
    "env": {"vars": [], "secrets": []},
    "data": {"needs": [], "sensitivity": "low"},
    "tests": {"smoke": [{"input": {}, "expect_contains": ""}],
              "eval": {"metric": "accuracy", "threshold": 0.8,
                       "cases": [{"input": {}, "expect": {}}]}},
    "sandbox": {"language": "python", "entrypoint": "tool.py",
                "artifacts": [{"path": "tool.py",
                               "content": "def run(p):\n    return {\"ok\": True}\n"}],
                "smoke": [{"input": {}, "expect_contains": "ok"}],
                "eval": {"metric": "accuracy", "threshold": 0.8,
                         "cases": [{"input": {}, "expect": {"ok": True}}]}},
}


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    suffix = time.strftime("%H%M%S")
    call("POST", "/hive/gov/kill", {"on": False})
    # 清理上一轮 S28 已吸收能力，保证"缺口"可重复检测
    try:
        import capability_registry as _cr
        with _cr._conn() as _c:
            _c.execute("DELETE FROM capabilities WHERE cap_id LIKE 'absorbed.tool.s28_%'")
            _c.commit()
    except Exception as _e:
        print("清理旧能力失败:", _e)

    # ① 治理初始状态
    st, gs, _ = call("GET", "/hive/gov/stats")
    check("治理策略加载（熔断关闭）", st == 200 and gs.get("kill_switch") is False,
          "budget=%s approvals=%s" % (gs.get("budget"), gs.get("approvals")))

    # ② 受控自主 10 次闭环
    st, run, wall = call("POST", "/hive/autonomy/run", {"n": 10}, timeout=900)
    loops = run.get("loops") or []
    okn = sum(1 for x in loops if x.get("ok"))
    check("无人值守 10 次闭环", st == 200 and run.get("passed") == 10,
          "passed=%s/10 %.1fs" % (run.get("passed"), wall / 1000))
    check("每次均识别缺口→验证→部署",
          all(x.get("gap_detected") and (x.get("verify") or {}).get("match")
              and x.get("deployed") for x in loops),
          "gaps=%d verified=%d deployed=%d" % (
              sum(1 for x in loops if x.get("gap_detected")),
              sum(1 for x in loops if (x.get("verify") or {}).get("match")),
              sum(1 for x in loops if x.get("deployed"))))
    gate_modes = sorted({(x.get("gate") or {}).get("status") for x in loops})
    check("低风险策略自动批准", gate_modes == ["allowed"], gate_modes)
    res["loop"] = {"passed": run.get("passed"), "total": run.get("total"),
                   "loops": loops, "gate_modes": gate_modes}

    # ③ 注册表新增 10 个 active
    st, caps, _ = call("GET", "/hive/capability/list?status=active&limit=1000")
    ids = {c.get("cap_id") for c in (caps.get("capabilities") or [])}
    new_ids = [x["capability_id"] for x in loops
               if ("absorbed." + x["capability_id"]) in ids]
    check("能力基线库新增 10 条 active", len(new_ids) == 10, "%d/10" % len(new_ids))

    # ④ 高风险 → 必须审批
    HIGH_RUN = json.loads(json.dumps(HIGH))
    HIGH_RUN["capability_id"] = "tool.s28_high_risk_demo_%s.v1" % suffix
    HIGH_RUN["name"] = "敏感数据处理能力（需审批）" + suffix
    st, imp1, _ = call("POST", "/hive/manifest/import",
                       {"manifest": HIGH_RUN, "mode": "auto"}, timeout=300)
    check("高风险能力被审批门禁拦截",
          imp1.get("status") == "pending_approval" and imp1.get("approval_id"),
          "status=%s id=%s" % (imp1.get("status"), str(imp1.get("approval_id"))))
    aid = imp1.get("approval_id")
    st, pend, _ = call("GET", "/hive/gov/approvals")
    check("审批列表可见", any(a.get("id") == aid for a in (pend.get("pending") or [])),
          "pending=%s" % len(pend.get("pending") or []))
    st, ap, _ = call("POST", "/hive/gov/approve",
                     {"approval_id": aid, "approver": "human_s28", "note": "验收审批"})
    check("人工批准成功", ap.get("ok") and (ap.get("approval") or {}).get("status") == "approved",
          (ap.get("approval") or {}).get("status"))
    st, imp2, _ = call("POST", "/hive/manifest/import",
                       {"manifest": HIGH_RUN, "mode": "auto"}, timeout=300)
    check("批准后吸收成功", imp2.get("ok") and imp2.get("status") in ("active", "shadow"),
          "status=%s governance=%s" % (imp2.get("status"), imp2.get("governance")))
    res["high_risk"] = {"first": imp1, "approve": ap, "second": imp2}

    # ⑤ 拒绝路径
    high2 = json.loads(json.dumps(HIGH))
    high2["capability_id"] = "tool.s28_reject_demo_%s.v1" % suffix
    high2["name"] = "另一个高风险能力（应被拒绝）"
    high2["data"]["needs"] = ["other"]
    st, imp3, _ = call("POST", "/hive/manifest/import",
                       {"manifest": high2, "mode": "auto"}, timeout=300)
    aid2 = imp3.get("approval_id")
    st, rj, _ = call("POST", "/hive/gov/reject",
                     {"approval_id": aid2, "approver": "human_s28", "note": "验收拒绝"})
    st, caps2, _ = call("GET", "/hive/capability/list?status=active&limit=1000")
    ids2 = {c.get("cap_id") for c in (caps2.get("capabilities") or [])}
    check("高风险拒绝后不吸收",
          rj.get("ok") and ("absorbed.tool.s28_reject_demo_%s.v1" % suffix) not in ids2,
          "reject=%s" % (rj.get("approval") or {}).get("status"))
    res["reject"] = {"import": imp3, "reject": rj}

    # ⑥ 杀开关
    call("POST", "/hive/gov/kill", {"on": True})
    st, blocked, _ = call("POST", "/hive/manifest/import",
                          {"manifest": LOW, "mode": "auto"}, timeout=300)
    check("杀开关拦截自修改动作", blocked.get("status") == "blocked_kill_switch",
          "status=%s" % blocked.get("status"))
    call("POST", "/hive/gov/kill", {"on": False})
    st, restored, _ = call("POST", "/hive/manifest/import",
                           {"manifest": LOW, "mode": "auto"}, timeout=300)
    check("解除熔断后恢复", restored.get("ok") and restored.get("status") == "active",
          "status=%s" % restored.get("status"))
    res["kill_switch"] = {"blocked": blocked, "restored": restored}

    # ⑦ 预算
    st, pol, _ = call("GET", "/hive/gov/policy")
    policy = pol.get("policy") or {}
    old_budget = json.dumps(policy.get("daily_budget") or {})
    policy["daily_budget"] = {"cloud_calls": 3, "train_minutes": 60}
    call("POST", "/hive/gov/policy", policy)
    call("POST", "/hive/gov/budget/record", {"kind": "cloud_calls", "amount": 3})
    st, b1, _ = call("POST", "/hive/gov/budget/check", {"kind": "cloud_calls", "amount": 1})
    check("预算超限拦截", b1.get("allowed") is False,
          "used=%s limit=%s" % (b1.get("used"), b1.get("limit")))
    res["budget"] = b1
    # 恢复预算
    policy["daily_budget"] = json.loads(old_budget)
    call("POST", "/hive/gov/policy", policy)

    # ⑧ 审计
    st, au, _ = call("GET", "/hive/gov/audit?limit=100")
    rows = au.get("audit") or []
    events = [r.get("event") for r in rows]
    check("治理审计留痕", len(rows) >= 15 and "autonomy_loop" in events,
          "entries=%s events=%s" % (len(rows), sorted(set(events))[:8]))
    res["audit"] = {"count": len(rows), "events": sorted(set(events))}

    # ⑨ 熔断/恢复演示（模块级）
    st, kd, _ = call("POST", "/hive/autonomy/kill_demo", timeout=300)
    check("熔断演示：拦截后恢复", kd.get("killed_blocked", {}).get("blocked")
          and kd.get("restored", {}).get("ok"),
          "blocked=%s restored=%s" % (kd.get("killed_blocked", {}).get("blocked"),
                                      kd.get("restored", {}).get("ok")))
    res["kill_demo"] = kd

    # ⑩ 最终统计
    st, gs2, _ = call("GET", "/hive/gov/stats")
    st, arch, _ = call("GET", "/architecture/status", timeout=120)
    overall = arch.get("overall") or {}
    check("治理统计", gs2.get("approvals", {}).get("approved", 0) >= 1, gs2.get("approvals"))
    res["gov_stats"] = gs2
    res["architecture"] = overall

    passed = sum(1 for x in RESULTS if x["pass"])
    totaln = len(RESULTS)
    print("\n=== S28 验收汇总 ===\n通过 %d/%d" % (passed, totaln))
    res["summary"] = {"passed": passed, "total": totaln,
                      "verdict": "PASS" if passed == totaln else "FAIL",
                      "checks": RESULTS}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("结果 →", OUT)


if __name__ == "__main__":
    main()
