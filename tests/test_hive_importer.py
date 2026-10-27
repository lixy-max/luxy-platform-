# -*- coding: utf-8 -*-
"""S25 能力包吸收/沙箱/门禁 单元测试"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import capability_importer as imp
import capability_registry as cr

TOOL_CODE = '''"""供应商比价（demo）"""
def run(payload):
    items = payload.get("items") or []
    if not items:
        return None
    return sorted(items, key=lambda x: x.get("price", 0))[0]
'''

GOOD = {
    "manifest_version": "1.0", "capability_id": "tool.supplier_compare.v1",
    "name": "供应商比价工具", "type": "tool", "capability_tags": ["supplier", "compare"],
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
                "artifacts": [{"path": "tool.py", "content": TOOL_CODE}],
                "smoke": [{"input": {"items": [{"name": "A", "price": 10},
                                              {"name": "B", "price": 5}]},
                           "expect_contains": "B"}],
                "eval": {"metric": "accuracy", "threshold": 0.8,
                         "cases": [{"input": {"items": [{"name": "A", "price": 10},
                                                        {"name": "B", "price": 5}]},
                                     "expect": {"name": "B", "price": 5}}]}},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s25_"))
        self.patches = [
            mock.patch.object(cr, "DB_PATH", self.tmp / "cap.db"),
            mock.patch.object(imp, "SANDBOX_DIR", self.tmp / "sandbox"),
            mock.patch.object(imp, "IMPORT_DIR", self.tmp / "imports"),
            mock.patch.object(imp, "MANIFEST_DIR", self.tmp / "manifests"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def _m(self, **over):
        m = json.loads(json.dumps(GOOD))
        m.update(over)
        return m


class TestDirect(Base):
    def test_direct_pass(self):
        r = imp.import_manifest(self._m(), mode="auto")
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "active")
        self.assertEqual(r["decision"], "direct")
        self.assertTrue(r["gates"]["smoke"]["pass"])
        self.assertTrue(r["gates"]["eval"]["pass"])
        self.assertTrue(r["gates"]["security"]["pass"])
        self.assertTrue(Path(r["sandbox"]).is_dir())
        e = cr.get("absorbed.tool.supplier_compare.v1")
        self.assertEqual(e["status"], "active")

    def test_run_absorbed(self):
        r = imp.import_manifest(self._m(), mode="auto")
        self.assertTrue(r["ok"])
        out = imp.run_absorbed("tool.supplier_compare.v1",
                               {"items": [{"name": "A", "price": 10},
                                          {"name": "B", "price": 5}]})
        self.assertTrue(out["ok"])
        self.assertEqual(out["output"]["name"], "B")
        self.assertGreaterEqual(out["latency_ms"], 0)

    def test_smoke_fail(self):
        m = self._m()
        m["sandbox"]["smoke"] = [{"input": {}, "expect_contains": "ZZZ_NOT_EXIST"}]
        r = imp.import_manifest(m, mode="auto")
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "rejected")
        self.assertFalse(r["gates"]["smoke"]["pass"])
        self.assertEqual(cr.get("absorbed.tool.supplier_compare.v1")["status"], "broken")

    def test_security_reject(self):
        m = self._m()
        m["sandbox"]["artifacts"][0]["content"] = "import subprocess\ndef run(p):\n    return p\n"
        r = imp.import_manifest(m, mode="auto")
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(any("subprocess" in str(i.get("pattern")) or
                            "subprocess" in str(i.get("why"))
                            for i in r["gates"]["security"]["issues"]))

    def test_path_traversal_reject(self):
        m = self._m()
        m["sandbox"]["artifacts"][0]["path"] = "../evil.py"
        r = imp.import_manifest(m, mode="auto")
        self.assertFalse(r["ok"])
        self.assertIn("路径非法", r.get("reason", ""))

    def test_dry_run(self):
        r = imp.import_manifest(self._m(), mode="auto", dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "planned")
        self.assertFalse(Path(r["sandbox"]).exists())
        self.assertIsNone(cr.get("absorbed.tool.supplier_compare.v1"))


class TestRebuildTrain(Base):
    def _rebuild_manifest(self):
        m = json.loads(json.dumps(GOOD))
        m["capability_id"] = "tool.legacy_compare.v1"
        m["name"] = "遗留比价服务"
        m["interface"]["protocol"] = "grpc"
        m["license"] = "GPL-3.0"
        m["runtime"] = {"os": "linux", "python": "3.9",
                        "deps": ["d%d" % i for i in range(25)],
                        "gpu": {"vram_gb": 16}}
        m["tests"] = {"smoke": ["connect ok"]}   # 无 eval → 性能分低
        m.pop("sandbox", None)
        return m

    def test_rebuild_shadow(self):
        r = imp.import_manifest(self._rebuild_manifest(), mode="auto")
        self.assertTrue(r["ok"])
        self.assertEqual(r["decision"], "rebuild")
        self.assertEqual(r["status"], "shadow")
        self.assertTrue(r["gates"]["lint"]["pass"])
        self.assertTrue((Path(r["sandbox"]) / "workflow.json").exists())
        self.assertTrue((Path(r["sandbox"]) / "adapter.py").exists())

    def test_train_plan(self):
        m = json.loads(json.dumps(GOOD))
        m["type"] = "lora"
        m["capability_id"] = "lora.supplier_styler.v1"
        m["name"] = "供应商风格 LoRA"
        m.pop("sandbox", None)
        fake_plan = {"action": "stop_worker", "gpu": 1, "reason": "3060 训练",
                     "fallback_used": False}
        with mock.patch("hive_trainer.plan_training", return_value=fake_plan):
            r = imp.import_manifest(m, mode="train")
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "pending_training")
        self.assertEqual(r["train_plan"]["action"], "stop_worker")

    def test_deny_low_score(self):
        m = self._rebuild_manifest()
        m["interface"]["protocol"] = "grpc"
        m["license"] = "unknown-proprietary"
        m["runtime"] = {"os": "windows", "python": "2.7", "deps": ["x"] * 30,
                        "gpu": {"vram_gb": 80}}
        m["tests"] = {}
        r = imp.import_manifest(m, mode="auto")
        self.assertFalse(r["ok"])
        self.assertEqual(r["decision"], "deny")

    def test_bad_mode(self):
        r = imp.import_manifest(GOOD, mode="magic")
        self.assertFalse(r["ok"])
        self.assertIn("mode 非法", r.get("reason", ""))

    def test_imports_list(self):
        imp.import_manifest(self._m(), mode="auto")
        rows = imp.list_imports()
        self.assertTrue(rows)
        self.assertEqual(rows[0]["capability_id"], "tool.supplier_compare.v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
