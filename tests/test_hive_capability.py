# -*- coding: utf-8 -*-
"""S24 能力基线库 / KB 分层 / 云端 Manifest 单元测试"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import capability_manifest as cm
import capability_registry as cr
import kb_layers as kbl

GOOD_MANIFEST = {
    "manifest_version": "1.0", "capability_id": "tool.supplier_compare.v1",
    "name": "供应商比价工具", "type": "tool",
    "source": {"provider": "github", "repo": "demo/supplier", "license": "Apache-2.0"},
    "interface": {"protocol": "openapi", "version": "1.0",
                  "io_schema": {"input": "xlsx", "output": "xlsx"}},
    "runtime": {"os": "linux", "python": "3.12", "deps": ["pandas"],
                "gpu": {"vram_gb": 0}, "disk_gb": 1},
    "env": {"vars": [], "secrets": []},
    "data": {"needs": ["supplier_list.xlsx"], "sensitivity": "medium"},
    "license": "Apache-2.0",
    "tests": {"smoke": ["import ok"], "eval": {"metric": "accuracy", "threshold": 0.8}},
    "fallback": {"strategy": "cloud"},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s24_")
        self.patches = [
            mock.patch.object(cr, "DB_PATH", Path(self.tmp) / "cap.db"),
            mock.patch.object(kbl, "DATA_SOURCES", Path(self.tmp) / "data_sources.json"),
            mock.patch.object(kbl, "MANIFEST_DIR", Path(self.tmp) / "manifests"),
            mock.patch.object(cm, "MANIFEST_DIR", Path(self.tmp) / "manifests"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cr.seed_from_platform()

    def tearDown(self):
        pass


class TestRegistry(Base):
    def test_seed(self):
        s = cr.stats()
        self.assertGreater(s["total"], 60)
        self.assertGreaterEqual(s["by_kind"].get("lora", 0), 10)
        self.assertGreaterEqual(s["by_kind"].get("tool", 0), 5)
        self.assertGreaterEqual(s["by_kind"].get("knowledge", 0), 5)
        self.assertIsNotNone(cr.get("lora.json_extract"))

    def test_required_capabilities(self):
        caps = [r["capability"] for r in cr.required_capabilities("把表格排序并导出 Excel")]
        self.assertIn("skill.sheet_ops", caps)
        caps2 = [r["capability"] for r in cr.required_capabilities("合同和法规逐条比对")]
        self.assertIn("knowledge.contract", caps2)
        caps3 = [r["capability"] for r in cr.required_capabilities("500并发压测并注入脏数据")]
        self.assertIn("sim.load", caps3)

    def test_plan_verdicts(self):
        self.assertEqual(cr.plan_for_task("把表格排序导出")["verdict"], "local")
        self.assertEqual(cr.plan_for_task("合同法规比对")["verdict"], "cloud")
        self.assertEqual(cr.plan_for_task("并发压测脏数据")["verdict"], "sim")
        self.assertEqual(cr.plan_for_task("写一份岩土工程勘察报告")["verdict"], "unknown")

    def test_crud(self):
        r = cr.register({"cap_id": "tool.demo_test", "kind": "tool", "name": "demo",
                         "status": "active", "tags": ["demo"]})
        self.assertTrue(r["ok"])
        self.assertIsNotNone(cr.get("tool.demo_test"))
        self.assertTrue(any(c["cap_id"] == "tool.demo_test" for c in cr.list_caps(kind="tool")))
        self.assertTrue(cr.set_status("tool.demo_test", "shadow")["ok"])
        self.assertEqual(cr.get("tool.demo_test")["status"], "shadow")
        self.assertTrue(cr.delete("tool.demo_test")["ok"])

    def test_bad_register(self):
        self.assertFalse(cr.register({"cap_id": "x", "kind": "nope"})["ok"])
        self.assertFalse(cr.register({"kind": "tool"})["ok"])


class TestKBLayers(Base):
    def test_mandatory_context(self):
        ctx = kbl.mandatory_context()
        self.assertIn("能力基线", ctx)
        self.assertIn("策略", ctx)
        self.assertLessEqual(len(ctx), 2600)

    def test_context_bundle_procurement(self):
        plan = cr.plan_for_task("生成采购清单，来源本地供应商清单")
        ctx = kbl.context_bundle("生成采购清单，来源本地供应商清单", plan, worker="w1")
        self.assertEqual(ctx["layers"]["L1_domain"]["domain"], "procurement")
        self.assertIn("qiaojiao_toys_2025_v2", ctx["mount"]["l1"])
        self.assertGreaterEqual(len(ctx["mount"]["data"]), 2)
        self.assertIn("L0.5", ctx["mount"]["must_read"])

    def test_acl_isolation(self):
        plan = cr.plan_for_task("生成采购清单")
        ctx = kbl.context_bundle("生成采购清单", plan, worker="unknown_worker")
        self.assertTrue(ctx["mount"]["dropped_by_acl"])
        self.assertEqual(ctx["layers"]["L3_data"]["sources"], [])

    def test_data_sources(self):
        srcs = kbl.load_data_sources()
        self.assertGreaterEqual(len(srcs), 2)
        r = kbl.register_data_source({"id": "test_src", "name": "测试源", "type": "excel",
                                      "schema": {"columns": ["a"]}, "acl": ["w1"]})
        self.assertTrue(r["ok"])
        self.assertTrue(any(d["id"] == "test_src" for d in kbl.load_data_sources()))


class TestManifest(Base):
    def test_validate_good_bad(self):
        self.assertEqual(cm.validate_manifest(GOOD_MANIFEST), [])
        bad = dict(GOOD_MANIFEST)
        bad.pop("runtime")
        self.assertTrue(cm.validate_manifest(bad))

    def test_score_decisions(self):
        sc = cm.score_compat(GOOD_MANIFEST)
        self.assertGreaterEqual(sc["score"], 75)
        self.assertEqual(sc["decision"], "direct")
        weak = json.loads(json.dumps(GOOD_MANIFEST))
        weak["license"] = "GPL-3.0"
        weak["interface"]["protocol"] = "weird"
        weak["tests"]["eval"] = {}
        sc2 = cm.score_compat(weak)
        self.assertLess(sc2["score"], 75)
        self.assertIn(sc2["decision"], ("rebuild", "train", "deny"))

    def test_absorb_dry_run(self):
        r = cm.absorb(GOOD_MANIFEST, dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["decision"], "direct")
        self.assertEqual(cm.list_manifests(), [])

    def test_absorb_real_registers_shadow(self):
        r = cm.absorb(GOOD_MANIFEST, dry_run=False)
        self.assertTrue(r["ok"])
        self.assertIn("manifest_path", r)
        self.assertTrue(cr.get("absorbed.tool.supplier_compare.v1"))
        self.assertEqual(cr.get("absorbed.tool.supplier_compare.v1")["status"], "shadow")


class TestDecideIntegration(Base):
    def test_decide_capability_plan(self):
        import hive_orchestrator as hv
        good = {"target": "local", "confidence": 0.9, "task_type": "data",
                "intent_summary": "排序", "subtasks": [{"id": 1, "desc": "x", "executor": "local:base"}],
                "routing": {"worker_id": "w1", "lora_id": "base", "why": "x",
                            "timeout_s": 60, "max_retries": 1, "escalate": False},
                "cloud": {"provider": "", "model": "", "context_policy": "digest",
                          "why": "", "est_cost": "low"},
                "sim": {"scenario": "", "users": 0, "duration_s": 0,
                        "dirty_policy": "", "report_to": ""},
                "fallback_chain": ["local:base"], "needs_clarification": False,
                "reason_trace": ["x"]}
        captured = {}

        def fake_llm(messages, **kw):
            captured["messages"] = messages
            return json.dumps(good, ensure_ascii=False), {"mode": "guided"}
        inv = {"workers": [{"id": "w1", "status": "ready", "base": "7B"}],
               "loras": [{"id": "base", "score": 0.6, "capability": ["general"]}],
               "cloud": [{"provider": "deepseek", "model": "deepseek-flash", "cost": "low"}],
               "sim": [{"scenario": "http_concurrent"}],
               "tools": [], "kbs": [], "policy": {}}
        dtmp = tempfile.mkdtemp(prefix="s24d_")
        with (mock.patch.object(hv, "build_inventory", return_value=inv),
              mock.patch.object(hv, "_llm_json", side_effect=fake_llm),
              mock.patch.object(hv, "HIVE_DIR", Path(dtmp)),
              mock.patch.object(hv, "DECISIONS_FILE", Path(dtmp) / "d.jsonl"),
              mock.patch("hive_memory.context_for", return_value=""),
              mock.patch("hive_memory.record", return_value={"ok": True})):
            r = hv.decide("把表格排序导出", use_llm=True)
        self.assertTrue(r["ok"])
        self.assertIn("capability_plan", r)
        self.assertEqual(r["capability_plan"]["verdict"], "local")
        self.assertIn("capability_verdict", r["meta"])
        sys_text = captured["messages"][0]["content"]
        self.assertIn("能力基线", sys_text if "能力基线" in sys_text else
                      captured["messages"][-1]["content"])
        shutil.rmtree(dtmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
