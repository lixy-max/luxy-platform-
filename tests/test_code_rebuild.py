# -*- coding: utf-8 -*-
"""S8 代码借鉴重建测试：抓取/分析/计划/适配器/自测/登记"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import code_rebuild as cr


class TestCodeRebuild(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rebuild_ut_"))
        self._orig = (cr.REBUILDS_DIR, cr.PORTED_DIR, cr.REBUILD_LOG)
        cr.REBUILDS_DIR = self.tmp / "rebuilds"
        cr.PORTED_DIR = self.tmp / "ported"
        cr.REBUILD_LOG = self.tmp / "rebuilds.jsonl"
        self.repo = self.tmp / "rebuilds" / "a-b"
        (self.repo / "original").mkdir(parents=True)
        (self.repo / "original" / "chunker.py").write_text(
            "import tiktoken\nimport numpy as np\n\n"
            "def chunk(text, size=500):\n    return [text[i:i+size] for i in range(0, len(text), size)]\n\n"
            "def split_sentences(text):\n    return text.split('。')\n", encoding="utf-8")
        (self.repo / "LICENSE").write_text("MIT License\n\nPermission is hereby granted...", encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for k, v in zip(("REBUILDS_DIR", "PORTED_DIR", "REBUILD_LOG"), self._orig):
            self.addCleanup(setattr, cr, k, v)

    def test_analyze(self):
        a = cr.analyze(self.repo)
        self.assertEqual(a["py_files"], 1)
        self.assertIn("tiktoken", a["heavy_imports"])
        self.assertIn("chunk", [f["name"] for f in a["functions"]])
        self.assertEqual(a["license_kind"], "mit")

    def test_plan(self):
        a = cr.analyze(self.repo)
        meta = {"repo": "a/b", "slug": "a-b", "root": str(self.repo), "files": []}
        p = cr.plan_rebuild(meta, a, goal="分块")
        self.assertEqual(p["decision"], "borrow_rebuild")
        self.assertTrue(any("tiktoken" in r for r in p["risks"]))
        self.assertIn("chunk", [f["name"] for f in p["borrow_candidates"]])

    def test_make_adapter_template_and_smoke(self):
        a = cr.analyze(self.repo)
        meta = {"repo": "a/b", "slug": "a-b", "root": str(self.repo), "files": [],
                "license": {"file": "LICENSE", "head": "MIT License"}}
        ad = cr.make_adapter(meta, cr.plan_rebuild(meta, a), use_llm=False)
        self.assertTrue(Path(ad["wrapper"]).exists())
        self.assertTrue(Path(ad["attribution"]).exists())
        self.assertEqual(ad["code_source"], "template")
        self.assertTrue(ad["smoke"].get("ok"), ad["smoke"])

    def test_rebuild_flux(self):
        from unittest import mock as m
        meta = {"repo": "a/b", "slug": "a-b", "root": str(self.repo),
                "files": [{"path": "chunker.py", "ok": True}],
                "license": {"file": "LICENSE", "head": "MIT License"}}
        fake_cap = {"cap_id": "ext:rebuild:a-b", "installed": True, "executable": True}
        with m.patch.object(cr, "fetch_files", return_value=meta), \
             m.patch.object(cr, "register_rebuild_capability", return_value=fake_cap), \
             m.patch.object(cr, "make_adapter", wraps=cr.make_adapter):
            r = cr.rebuild("a/b", ["chunker.py"], goal="分块", cap_id="ext:rebuild:a-b",
                           approve=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["capability_id"], "ext:rebuild:a-b")
        self.assertEqual(len(cr.list_rebuilds()), 1)

    def test_requires_approve(self):
        with self.assertRaises(ValueError):
            cr.rebuild("a/b", ["x.py"], approve=False)

    def test_unknown_license_blocked(self):
        (self.repo / "LICENSE").write_text("All rights reserved.\n", encoding="utf-8")
        meta = {"repo": "a/b", "slug": "a-b", "root": str(self.repo), "files": [], "license": None}
        from unittest import mock as m
        with m.patch.object(cr, "fetch_files", return_value=meta):
            r = cr.rebuild("a/b", ["chunker.py"], approve=True)
        self.assertFalse(r["ok"])
        self.assertIn("需人工确认", r["reason"])

    def test_fetch_mocked(self):
        def fake_http(url, timeout=30, binary=False):
            if "LICENSE" in url:
                return "MIT License"
            return "def f():\n    return 1\n"
        with mock.patch.object(cr, "_http", side_effect=fake_http):
            meta = cr.fetch_files("a/b", ["mod.py"])
        self.assertTrue(meta["files"][0]["ok"])
        self.assertTrue((Path(meta["root"]) / "LICENSE").exists())
        self.assertEqual(meta["license"]["file"], "LICENSE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
