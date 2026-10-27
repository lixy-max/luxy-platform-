# -*- coding: utf-8 -*-
"""S28 · 受控自主闭环（Gated Autonomy）

端到端：需求 → 缺口感知（S24 registry）→ 能力包提案（Manifest, S24/S25）
        → 治理门禁（hive_gov：低风险自动批准 / 高风险人工审批 / 熔断拦截）
        → 吸收（S25 importer：沙箱+门禁）→ 验证（run_absorbed 真实执行）
        → 注册 active（能力基线库）→ 审计

验收目标：无人值守跑通 10 次"缺口→获取→验证→部署"，并演示高风险审批、杀开关、预算。
"""
import json
import os
import sys
import time
import pathlib

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

# 10 个"缺失工具能力"（每个含安全内联实现 + 测试）
TOOL_SPECS = [
    {"name": "supplier_compare", "title": "供应商比价（最低价）",
     "code": 'def run(payload):\n    items = payload.get("items") or []\n    return min(items, key=lambda x: x.get("price", 0)) if items else None\n',
     "smoke_input": {"items": [{"name": "A", "price": 10}, {"name": "B", "price": 5}]},
     "smoke_contains": "B", "eval_expect": {"name": "B", "price": 5},
     "need": "做供应商比价，选最低价"},
    {"name": "json_validate", "title": "JSON 必填字段校验",
     "code": 'def run(payload):\n    required = payload.get("required") or []\n    data = payload.get("data") or {}\n    missing = [k for k in required if k not in data]\n    return {"valid": not missing, "missing": missing}\n',
     "smoke_input": {"required": ["a", "b"], "data": {"a": 1}},
     "smoke_contains": "missing", "eval_expect": {"valid": False, "missing": ["b"]},
     "need": "校验采购 JSON 是否缺必填字段"},
    {"name": "csv_dedup", "title": "CSV 按 id 去重",
     "code": 'def run(payload):\n    rows = payload.get("rows") or []\n    seen, out = set(), []\n    for r in rows:\n        k = r.get("id")\n        if k not in seen:\n            seen.add(k); out.append(r)\n    return out\n',
     "smoke_input": {"rows": [{"id": 1, "v": "a"}, {"id": 1, "v": "b"}, {"id": 2, "v": "c"}]},
     "smoke_contains": "id", "eval_expect": [{"id": 1, "v": "a"}, {"id": 2, "v": "c"}],
     "need": "把供应商 CSV 按 id 去重"},
    {"name": "unit_convert", "title": "厘米转英寸",
     "code": 'def run(payload):\n    cm = float(payload.get("cm") or 0)\n    return {"inch": round(cm / 2.54, 2)}\n',
     "smoke_input": {"cm": 25.4}, "smoke_contains": "inch",
     "eval_expect": {"inch": 10.0}, "need": "把采购规格厘米转英寸"},
    {"name": "text_stats", "title": "文本字数统计",
     "code": 'def run(payload):\n    t = str(payload.get("text") or "")\n    return {"chars": len(t), "words": len(t.split())}\n',
     "smoke_input": {"text": "hello world foo"}, "smoke_contains": "chars",
     "eval_expect": {"chars": 15, "words": 3}, "need": "统计供应商描述文本字数"},
    {"name": "price_round", "title": "价格四舍五入两位",
     "code": 'def run(payload):\n    return {"price": round(float(payload.get("price") or 0), 2)}\n',
     "smoke_input": {"price": 12.3456}, "smoke_contains": "price",
     "eval_expect": {"price": 12.35}, "need": "把报价四舍五入到两位小数"},
    {"name": "date_normalize", "title": "日期格式标准化",
     "code": 'def run(payload):\n    s = str(payload.get("date") or "")\n    p = s.split("-")\n    return {"date": "/".join([p[1], p[2], p[0]])} if len(p) == 3 else {"date": s}\n',
     "smoke_input": {"date": "2026-10-27"}, "smoke_contains": "date",
     "eval_expect": {"date": "10/27/2026"}, "need": "统一采购交期日期格式"},
    {"name": "slugify", "title": "标题转 slug",
     "code": 'import re\ndef run(payload):\n    s = str(payload.get("title") or "").lower()\n    return {"slug": re.sub(r"[^a-z0-9]+", "-", s).strip("-")}\n',
     "smoke_input": {"title": "Supplier Compare Tool"}, "smoke_contains": "slug",
     "eval_expect": {"slug": "supplier-compare-tool"}, "need": "把商品标题转成 slug"},
    {"name": "sum_column", "title": "列求和",
     "code": 'def run(payload):\n    return {"sum": round(sum(float(x) for x in (payload.get("values") or [])), 2)}\n',
     "smoke_input": {"values": [1.5, 2.5, 3]}, "smoke_contains": "sum",
     "eval_expect": {"sum": 7.0}, "need": "把采购小计列求和"},
    {"name": "top_n", "title": "取前 N 大值",
     "code": 'def run(payload):\n    n = int(payload.get("n") or 2)\n    vals = sorted(payload.get("values") or [], reverse=True)\n    return vals[:n]\n',
     "smoke_input": {"values": [5, 1, 9, 3], "n": 2}, "smoke_contains": "9",
     "eval_expect": [9, 5], "need": "取价格最高的前 N 条"},
]


def build_manifest(spec, sensitivity="low"):
    cap_id = "tool.s28_%s.v1" % spec["name"]
    return {
        "manifest_version": "1.0", "capability_id": cap_id,
        "name": "S28 %s" % spec["title"], "type": "tool",
        "capability_tags": ["s28", spec["name"]],
        "license": "Apache-2.0",
        "interface": {"protocol": "python", "version": "1.0",
                      "io_schema": {"input": "object", "output": "object"}},
        "runtime": {"os": "linux", "python": "3.12", "deps": [], "gpu": {"vram_gb": 0}},
        "env": {"vars": [], "secrets": []},
        "data": {"needs": [], "sensitivity": sensitivity},
        "tests": {"smoke": [{"input": spec["smoke_input"],
                             "expect_contains": spec["smoke_contains"]}],
                  "eval": {"metric": "accuracy", "threshold": 0.8,
                           "cases": [{"input": spec["smoke_input"],
                                      "expect": spec["eval_expect"]}]}},
        "sandbox": {"language": "python", "entrypoint": "tool.py",
                    "artifacts": [{"path": "tool.py", "content": spec["code"]}],
                    "smoke": [{"input": spec["smoke_input"],
                               "expect_contains": spec["smoke_contains"]}],
                    "eval": {"metric": "accuracy", "threshold": 0.8,
                             "cases": [{"input": spec["smoke_input"],
                                        "expect": spec["eval_expect"]}]}},
    }


def run_one(spec, task=None, risk=None):
    """一次受控自主闭环：缺口→治理→吸收→验证→注册"""
    import capability_registry as cr
    import capability_importer as imp
    import hive_gov as gov

    t0 = time.time()
    cap_id = "tool.s28_%s.v1" % spec["name"]
    task = task or spec.get("need") or spec["title"]
    out = {"capability_id": cap_id, "task": task,
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # ① 缺口感知
    before = cr.get("absorbed." + cap_id) or cr.get(cap_id)
    out["gap_detected"] = before is None
    plan = cr.plan_for_task(task)
    out["plan_verdict"] = plan.get("verdict")
    out["gap_tags"] = plan.get("gap_tags")
    # ② 能力包提案
    manifest = build_manifest(spec)
    out["manifest"] = {"capability_id": manifest["capability_id"],
                       "type": manifest["type"], "sensitivity": "low"}
    # ③ 治理门禁（低风险自动批准）
    gate = gov.request("capability_import",
                       {"manifest": manifest, "data": {"sensitivity": "low"}},
                       actor="autonomy", risk=risk)
    out["gate"] = {"status": gate.get("status"), "allowed": gate.get("allowed"),
                   "risk": gate.get("risk")}
    if not gate.get("allowed"):
        out.update({"ok": False, "blocked": True,
                    "reason": gate.get("reason") or "治理未批准"})
        return out
    # ④ 吸收（沙箱+门禁）
    imp_res = imp.import_manifest(manifest, mode="auto")
    out["import"] = {"ok": imp_res.get("ok"), "status": imp_res.get("status"),
                     "decision": imp_res.get("decision"),
                     "gates": {k: v.get("pass") for k, v in
                               (imp_res.get("gates") or {}).items()}}
    if not imp_res.get("ok") or imp_res.get("status") != "active":
        out.update({"ok": False, "reason": "吸收失败: %s" % imp_res.get("reason")})
        return out
    # ⑤ 验证（真实执行）
    run = imp.run_absorbed(cap_id, spec["smoke_input"])
    expected = json.dumps(spec["eval_expect"], ensure_ascii=False)
    got = json.dumps(run.get("output"), ensure_ascii=False) if run.get("ok") else ""
    out["verify"] = {"ok": bool(run.get("ok")), "expected": expected,
                     "got": got[:300], "match": got == expected,
                     "latency_ms": run.get("latency_ms")}
    # ⑥ 部署（注册表 active）
    entry = cr.get("absorbed." + cap_id)
    out["deployed"] = bool(entry and entry.get("status") == "active")
    out.update({"ok": bool(out["gap_detected"] and out["verify"]["match"] and out["deployed"]),
                "elapsed_s": round(time.time() - t0, 2)})
    gov.audit({"event": "autonomy_loop", "capability_id": cap_id,
               "ok": out["ok"], "elapsed_s": out["elapsed_s"]})
    return out


def run_loop(n=10):
    """无人值守跑 n 次闭环（低风险自动批准）"""
    specs = TOOL_SPECS[:max(1, int(n))]
    rows = []
    for i, spec in enumerate(specs):
        r = run_one(spec)
        r["index"] = i + 1
        rows.append(r)
        print(("[%d/%d] %s %s" % (i + 1, len(specs),
                                  "✅" if r.get("ok") else "❌",
                                  r["capability_id"])))
    passed = sum(1 for r in rows if r.get("ok"))
    return {"ok": passed == len(rows), "passed": passed, "total": len(rows), "loops": rows}


def demo_high_risk():
    """高风险能力（敏感数据）→ 必须人工审批"""
    import hive_gov as gov
    import capability_importer as imp
    spec = dict(TOOL_SPECS[0])
    spec["name"] = "supplier_compare_highrisk"
    manifest = build_manifest(spec, sensitivity="high")
    gate = gov.request("capability_import",
                       {"manifest": manifest, "data": {"sensitivity": "high"}},
                       actor="autonomy")
    out = {"manifest": manifest, "gate": gate}
    if gate.get("status") == "pending_approval":
        ap = gov.approve(gate["approval_id"], approver="human_demo",
                         note="S28 验收：高风险审批通过")
        out["approval"] = ap
        r = imp.import_manifest(manifest, mode="auto")
        out["import_after_approval"] = {"ok": r.get("ok"), "status": r.get("status")}
    return out


def demo_reject():
    """高风险能力 → 人工拒绝，不得吸收"""
    import hive_gov as gov
    import capability_importer as imp
    spec = dict(TOOL_SPECS[1])
    spec["name"] = "json_validate_rejected"
    manifest = build_manifest(spec, sensitivity="high")
    gate = gov.request("capability_import",
                       {"manifest": manifest, "data": {"sensitivity": "high"}},
                       actor="autonomy")
    out = {"gate": gate}
    if gate.get("status") == "pending_approval":
        out["reject"] = gov.reject(gate["approval_id"], approver="human_demo",
                                   note="S28 验收：高风险拒绝")
        out["import_attempt"] = imp.import_manifest(manifest, mode="auto")  # 绕过治理应被审计标记
        # 治理层拒绝后，编排层不得继续（这里演示低层 API 仍可被调用，见诚实边界）
    return out


def demo_kill_switch():
    """杀开关：开启后自修改动作全部拦截；关闭后恢复"""
    import hive_gov as gov
    spec = dict(TOOL_SPECS[2])
    spec["name"] = "csv_dedup_kill_demo_%d" % (int(time.time()) % 100000)
    gov.kill_switch(True)
    blocked = run_one(spec)
    gov.kill_switch(False)
    restored = run_one(spec)
    return {"killed_blocked": blocked, "restored": restored}


def demo_budget():
    """预算：低预算下超限拒绝，恢复后放行"""
    import hive_gov as gov
    policy = gov.load_policy()
    old = json.loads(json.dumps(policy))
    policy["daily_budget"]["cloud_calls"] = 3
    gov.save_policy(policy)
    gov.record_spend("cloud_calls", 3)
    over = gov.check_budget("cloud_calls", 1)
    gov.record_spend("cloud_calls", 1)
    blocked = gov.check_budget("cloud_calls", 1)
    gov.save_policy(old)
    return {"check_before": over, "check_after_spend": blocked}
