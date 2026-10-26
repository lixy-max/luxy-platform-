# -*- coding: utf-8 -*-
"""S1：蒸馏评分 / 表格KB / 兼容性 / 任务表归一化 测试"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))

import distill_score as ds
import chimera_compat as cc
import route_table as rt
import table_kb as tk

XLS = '/home/luxy/paper_lora_project/data/uploads/pilot_qiaojiao_toys.xls'


class TestDistillScore(unittest.TestCase):
    def test_high_value(self):
        r = ds.score({"success": True, "occurrence": 6, "covered": False, "steps": 3,
                      "tool_quality": "A", "has_tool_calls": True})
        self.assertEqual(r["level"], "high")
        self.assertGreaterEqual(r["score"], 70)
        self.assertEqual(len(r["parts"]), 5)

    def test_skip_value(self):
        r = ds.score({"success": False, "occurrence": 0, "covered": True, "steps": 1,
                      "tool_quality": "D"})
        self.assertEqual(r["level"], "skip")
        self.assertLess(r["score"], 50)

    def test_watch_value(self):
        r = ds.score({"success": True, "occurrence": 1, "covered": False, "steps": 2,
                      "tool_quality": "C"})
        self.assertIn(r["level"], ("watch", "high"))

    def test_business_bonus(self):
        base = ds.score({"success": True, "occurrence": 1, "covered": False, "steps": 3,
                         "tool_quality": "B"})
        bonus = ds.score({"success": True, "occurrence": 1, "covered": False, "steps": 3,
                          "tool_quality": "B", "user_value": 1.0})
        self.assertGreater(bonus["score"], base["score"])

    def test_weights_sum(self):
        self.assertAlmostEqual(sum(ds.WEIGHTS.values()), 1.0, places=6)

    def test_explainable_parts(self):
        r = ds.score({"success": True})
        self.assertTrue(all("why" in p and "points" in p for p in r["parts"]))

    def test_batch(self):
        out = ds.score_batch([{"success": True}, {"success": False}])
        self.assertEqual(len(out), 2)


class TestTableKb(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tk_ut_"))
        self._orig = tk.DOCS_DIR
        tk.DOCS_DIR = self.tmp / "docs"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, tk, "DOCS_DIR", self._orig)

    def test_stats(self):
        st = tk.table_stats(XLS)
        self.assertEqual(st["total_rows"], 88)
        self.assertEqual(len(st["sheets"]), 1)
        self.assertIn("产品编号", st["sheets"][0]["headers"])

    def test_parse_and_docs(self):
        parsed = tk.parse_table(XLS)
        self.assertEqual(parsed["total_rows"], 88)
        docs = tk.to_markdown_docs(parsed, batch_size=10)
        names = [d["name"] for d in docs]
        self.assertTrue(any("概览" in n for n in names))
        self.assertTrue(any("安装培训清单" in n for n in names))
        self.assertTrue(any("价格排序" in n for n in names))
        self.assertTrue(any("产品索引" in n for n in names))
        # 价格排序文档应包含最高价
        price_doc = [d for d in docs if "价格排序" in d["name"]][0]["text"]
        self.assertIn("75000", price_doc)

    def test_install_training_doc(self):
        parsed = tk.parse_table(XLS)
        docs = tk.to_markdown_docs(parsed, batch_size=10)
        doc = [d for d in docs if "安装培训清单" in d["name"]][0]["text"]
        self.assertIn("安装培训", doc)
        self.assertGreater(len(doc), 100)

    def test_ingest_with_mock_kb(self):
        with mock.patch("kb.create_kb"), \
             mock.patch("kb.ingest_folder", return_value={"added_docs": 13, "added_chunks": 36}) as m:
            r = tk.ingest_table(XLS, "test_kb", batch_size=10)
        self.assertEqual(r["total_rows"], 88)
        self.assertEqual(r["ingest"]["added_chunks"], 36)
        self.assertTrue(m.called)


class TestCompat(unittest.TestCase):
    def test_direct_transplant(self):
        c = {"name": "x", "repo": "a/x", "install": "pip", "deps_weight": 1, "gpu": "none",
             "docker": False, "system_deps": [], "license": "mit", "stars": 10000}
        r = cc.compat(c)
        self.assertEqual(r["tier"], "direct_transplant")
        self.assertGreaterEqual(r["score"], 80)

    def test_docker_borrow(self):
        c = {"name": "y", "install": "docker", "deps_weight": 3, "gpu": "optional",
             "docker": True, "system_deps": ["es"], "license": "apache-2.0", "stars": 60000}
        r = cc.compat(c)
        self.assertEqual(r["tier"], "borrow_rebuild")

    def test_unknown_license_needs_review(self):
        c = {"name": "z", "install": "pip", "deps_weight": 0, "gpu": "none", "docker": False,
             "license": "", "stars": 100}
        r = cc.compat(c)
        self.assertEqual(r["tier"], "needs_review")

    def test_copyleft_borrow(self):
        c = {"name": "w", "install": "pip", "deps_weight": 1, "gpu": "optional", "docker": False,
             "license": "agpl-3.0", "stars": 45000}
        r = cc.compat(c)
        self.assertIn(r["tier"], ("borrow_rebuild", "needs_review"))
        self.assertEqual(r["license_level"], "caution")

    def test_port_plan_sorted(self):
        plan = cc.port_plan(use_cloud=False)
        tiers = [c["tier"] for c in plan["candidates"]]
        order = {"direct_transplant": 0, "borrow_rebuild": 1, "cloud": 2, "needs_review": 3, "skip": 4}
        vals = [order[t] for t in tiers]
        self.assertEqual(vals, sorted(vals))

    def test_enrich_keeps_known_license(self):
        # GitHub 返回 other 时，不应覆盖已知 agpl 许可
        cands = [{"name": "m", "repo": "opendatalab/MinerU", "license": "agpl-3.0"}]
        with mock.patch("search_existing.search_github",
                        return_value=[{"id": "opendatalab/MinerU", "stars": 45000, "license": "other"}]):
            out = cc.enrich_from_github(cands)
        self.assertEqual(out[0]["license"], "agpl-3.0")


class TestRouteTableNorm(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rt_norm_"))
        self._orig = rt.DB_PATH
        rt.DB_PATH = self.tmp / "rt.db"
        self._patch = mock.patch.object(rt, "_embed", return_value=None)
        self._patch.start()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, rt, "DB_PATH", self._orig)
        self.addCleanup(self._patch.stop)

    def test_target_semantics_override(self):
        t = rt.create_table("需求", items=[
            {"content": "上云", "category": "local", "target": "cloud"},
            {"content": "移植", "category": "local", "target": "chimera"},
            {"content": "能力", "category": "local", "target": "lora:agri_expert_v1"},
        ])
        self.assertEqual(t["items"][0]["category"], "cloud")
        self.assertEqual(t["items"][1]["category"], "transplant")
        self.assertEqual(t["items"][2]["target"], "capability:lora:agri_expert_v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
