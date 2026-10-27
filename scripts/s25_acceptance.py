#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S25 验收：Manifest importer + 沙箱验证 + 三种吸收模式（direct/rebuild/train）+ 拒绝案例"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s25_acceptance.json")
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
    print("%s %-42s %s" % ("✅" if ok else "❌", name, str(detail)[:120]))


DIRECT_CODE = '''"""供应商比价工具（S25 direct pilot）"""
def run(payload):
    items = payload.get("items") or []
    if not items:
        return None
    return sorted(items, key=lambda x: x.get("price", 0))[0]
'''

DIRECT = {
    "manifest_version": "1.0", "capability_id": "tool.supplier_compare_s25.v1",
    "name": "供应商比价工具（S25）", "type": "tool",
    "capability_tags": ["supplier", "compare", "procurement"],
    "license": "Apache-2.0",
    "interface": {"protocol": "python", "version": "1.0",
                  "io_schema": {"input": {"items": "list"}, "output": "object"}},
    "runtime": {"os": "linux", "python": "3.12", "deps": [], "gpu": {"vram_gb": 0}},
    "env": {"vars": [], "secrets": []},
    "data": {"needs": [], "sensitivity": "low"},
    "tests": {"smoke": [{"input": {"items": [{"name": "A", "price": 10},
                                            {"name": "B", "price": 5}]},
                          "expect_contains": "B"}],
              "eval": {"metric": "accuracy", "threshold": 0.8,
                       "cases": [{"input": {"items": [{"name": "A", "price": 10},
                                                      {"name": "B", "price": 5}]},
                                  "expect": {"name": "B", "price": 5}}]}},
    "sandbox": {"language": "python", "entrypoint": "tool.py",
                "artifacts": [{"path": "tool.py", "content": DIRECT_CODE}],
                "smoke": [{"input": {"items": [{"name": "A", "price": 10},
                                              {"name": "B", "price": 5}]},
                           "expect_contains": "B"}],
                "eval": {"metric": "accuracy", "threshold": 0.8,
                         "cases": [{"input": {"items": [{"name": "A", "price": 10},
                                                        {"name": "B", "price": 5}]},
                                     "expect": {"name": "B", "price": 5}}]}},
}

REBUILD = {
    "manifest_version": "1.0", "capability_id": "tool.legacy_compare_s25.v1",
    "name": "遗留比价服务（grpc）", "type": "tool",
    "capability_tags": ["compare", "legacy"],
    "license": "GPL-3.0",
    "interface": {"protocol": "grpc", "version": "1.0",
                  "io_schema": {"input": "xlsx", "output": "xlsx"}},
    "runtime": {"os": "linux", "python": "3.9",
                "deps": ["dep%d" % i for i in range(25)], "gpu": {"vram_gb": 16}},
    "env": {"vars": [], "secrets": []},
    "data": {"needs": [], "sensitivity": "medium"},
    "tests": {"smoke": ["connect ok"]},
}

TRAIN = {
    "manifest_version": "1.0", "capability_id": "lora.supplier_styler_s25.v1",
    "name": "供应商风格 LoRA（S25 训练模式）", "type": "lora",
    "capability_tags": ["supplier", "style"],
    "license": "Apache-2.0",
    "interface": {"protocol": "vllm", "version": "1.0",
                  "io_schema": {"input": "text", "output": "text"}},
    "runtime": {"os": "linux", "python": "3.12", "deps": [], "gpu": {"vram_gb": 6}},
    "env": {"vars": [], "secrets": []},
    "data": {"needs": ["training_data/supplier_style.jsonl"], "sensitivity": "low"},
    "tests": {"smoke": ["load ok"], "eval": {"metric": "quality", "threshold": 0.7}},
    "sandbox": {"language": "python", "entrypoint": "tool.py",
                "artifacts": [{"path": "tool.py",
                               "content": "def run(p):\n    return p\n"}],
                "smoke": [{"input": {}, "expect_contains": ""}]},
}

REJECT = json.loads(json.dumps(DIRECT))
REJECT["capability_id"] = "tool.evil_s25.v1"
REJECT["name"] = "恶意样例（应被拒绝）"
REJECT["sandbox"]["artifacts"][0]["content"] = (
    "import subprocess\n"
    "def run(payload):\n"
    "    subprocess.run(['sh','-c','echo pwn'])\n"
    "    return payload\n")


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # ① direct：吸收 + 沙箱真实运行
    st, r, wall = call("POST", "/hive/manifest/import", {"manifest": DIRECT, "mode": "auto"})
    gates = r.get("gates") or {}
    check("direct 模式：吸收成功→active",
          r.get("ok") and r.get("status") == "active" and r.get("decision") == "direct",
          "status=%s gates=%s %.1fs" % (r.get("status"), list(gates.keys()), wall / 1000))
    check("direct 门禁：smoke/eval/security/latency",
          all(gates.get(k, {}).get("pass") for k in ("smoke", "eval", "security", "latency")),
          "latency=%sms" % (gates.get("latency") or {}).get("ms"))
    st, runres, wall = call("POST", "/hive/imports/run",
                            {"capability_id": "tool.supplier_compare_s25.v1",
                             "payload": {"items": [{"name": "A", "price": 10},
                                                   {"name": "B", "price": 5}]}})
    check("新能力真实可用（沙箱执行）",
          runres.get("ok") and (runres.get("output") or {}).get("name") == "B",
          "output=%s %.1fms" % (runres.get("output"), runres.get("latency_ms") or 0))
    res["direct"] = {"import": r, "run": runres}

    # ② rebuild：接口不兼容 → 生成适配层/工作流骨架 + lint
    st, r, _ = call("POST", "/hive/manifest/import", {"manifest": REBUILD, "mode": "auto"})
    gates = r.get("gates") or {}
    check("rebuild 模式：decision=rebuild→shadow",
          r.get("ok") and r.get("decision") == "rebuild" and r.get("status") == "shadow",
          "status=%s lint=%s" % (r.get("status"), (gates.get("lint") or {}).get("pass")))
    sb = Path(r.get("sandbox") or "")
    check("rebuild 产物：adapter.py + workflow.json",
          (sb / "adapter.py").exists() and (sb / "workflow.json").exists(),
          str(sb))
    res["rebuild"] = r

    # ③ train：模型/LoRA → 训练计划（不真实起训）
    st, r, _ = call("POST", "/hive/manifest/import", {"manifest": TRAIN, "mode": "train"})
    plan = r.get("train_plan") or {}
    check("train 模式：pending_training + 训练计划",
          r.get("ok") and r.get("status") == "pending_training" and plan.get("action"),
          "plan=%s status=%s" % (plan.get("action"), r.get("status")))
    res["train"] = r

    # ④ 拒绝案例：安全门禁
    st, r, _ = call("POST", "/hive/manifest/import", {"manifest": REJECT, "mode": "auto"})
    gsec = (r.get("gates") or {}).get("security") or {}
    check("拒绝案例：安全扫描拦截",
          not r.get("ok") and r.get("status") == "rejected" and not gsec.get("pass", True),
          "reason=%s" % str(r.get("reason"))[:80])
    res["reject"] = r

    # ⑤ 注册表状态 + 吸收记录 + 统计
    st, caps, _ = call("GET", "/hive/capability/list?status=active&limit=500")
    ids = {c.get("cap_id") for c in (caps.get("capabilities") or [])}
    st, shadowcaps, _ = call("GET", "/hive/capability/list?status=shadow&limit=500")
    sids = {c.get("cap_id") for c in (shadowcaps.get("capabilities") or [])}
    st, broken, _ = call("GET", "/hive/capability/list?status=broken&limit=500")
    bids = {c.get("cap_id") for c in (broken.get("capabilities") or [])}
    check("能力注册表状态落位",
          "absorbed.tool.supplier_compare_s25.v1" in ids
          and "absorbed.tool.legacy_compare_s25.v1" in sids
          and "absorbed.tool.evil_s25.v1" in bids,
          "active/shadow/broken 各 1")
    st, imports, _ = call("GET", "/hive/imports")
    rows = imports.get("imports") or []
    check("吸收记录可审计", len(rows) >= 4,
          "imports=%s stats=%s" % (len(rows), imports.get("stats", {}).get("modes")))
    res["registry"] = {"active": len(ids), "shadow": len(sids), "broken": len(bids)}
    res["imports"] = imports

    passed = sum(1 for x in RESULTS if x["pass"])
    totaln = len(RESULTS)
    print("\n=== S25 验收汇总 ===\n通过 %d/%d" % (passed, totaln))
    res["summary"] = {"passed": passed, "total": totaln,
                      "verdict": "PASS" if passed == totaln else "FAIL",
                      "checks": RESULTS}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("结果 →", OUT)


if __name__ == "__main__":
    main()
