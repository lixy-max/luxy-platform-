# -*- coding: utf-8 -*-
"""奇美拉 v1 测试：候选发现（打桩）/ 政策闸门 / 计划结构 / 落库 / 批准"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chimera  # noqa: E402

CAND_ALLOW = {"source": "github", "id": "jsvine/pdfplumber", "url": "https://github.com/jsvine/pdfplumber",
              "stars": 10850, "desc": "plumb a PDF", "license": "mit", "updated": "2026-10-01"}
CAND_COPYLEFT = {"source": "github", "id": "x/gpl-tool", "url": "", "stars": 5000,
                 "desc": "gpl tool", "license": "gpl-3.0", "updated": "2026-01-01"}
CAND_UNKNOWN = {"source": "github", "id": "y/no-license", "url": "", "stars": 2000,
                "desc": "no license", "license": "none", "updated": "2026-01-01"}
CAND_LOWSTARS = {"source": "github", "id": "z/small", "url": "", "stars": 10,
                 "desc": "small", "license": "mit", "updated": "2026-01-01"}


class TestGate(unittest.TestCase):
    def test_allow(self):
        st, why = chimera.gate_candidate(CAND_ALLOW)
        self.assertEqual(st, "auto_approved")

    def test_copyleft(self):
        st, why = chimera.gate_candidate(CAND_COPYLEFT)
        self.assertEqual(st, "needs_review")
        self.assertIn("copyleft", why)

    def test_unknown_license(self):
        st, why = chimera.gate_candidate(CAND_UNKNOWN)
        self.assertEqual(st, "needs_review")

    def test_low_stars(self):
        st, why = chimera.gate_candidate(CAND_LOWSTARS)
        self.assertEqual(st, "needs_review")

    def test_license_level(self):
        self.assertEqual(chimera._license_level("MIT"), "allow")
        self.assertEqual(chimera._license_level("Apache-2.0"), "allow")
        self.assertEqual(chimera._license_level("GPL-3.0"), "caution")
        self.assertEqual(chimera._license_level(""), "unknown")


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="chimera_ut_"))
        self._orig = (chimera.CHIMERA_DIR, chimera.CANDIDATES_FILE)
        chimera.CHIMERA_DIR = self.tmp
        chimera.CANDIDATES_FILE = self.tmp / "candidates.jsonl"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, chimera, "CHIMERA_DIR", self._orig[0])
        self.addCleanup(setattr, chimera, "CANDIDATES_FILE", self._orig[1])
        self._cap_patch = mock.patch(
            "capabilities.register_proposed_capability",
            side_effect=lambda c, gap="", plan_id="": {"cap_id": chimera.candidate_capability_id(c)})
        self._cap_patch.start()
        self.addCleanup(self._cap_patch.stop)

    def test_plan_structure_and_gate(self):
        fake = [CAND_ALLOW, CAND_COPYLEFT, CAND_UNKNOWN, CAND_LOWSTARS]
        with mock.patch.object(chimera, "discover", return_value=fake), \
             mock.patch.object(chimera, "cloud_review", return_value={"status": "skipped", "reason": "no key"}):
            p = chimera.plan("pdf table extraction", top_k=4)
        self.assertEqual(p["chimera_version"], "v1")
        self.assertEqual(p["gap"], "pdf table extraction")
        self.assertEqual(len(p["candidates"]), 4)
        self.assertIn(p["candidates"][0]["id"], ("jsvine/pdfplumber",))
        self.assertEqual(len(p["auto_approved"]), 1)
        self.assertEqual(len(p["needs_review"]), 3)
        self.assertEqual(len(p.get("capabilities_registered") or []), 4)
        self.assertEqual(p["plan_ir"]["steps"][0]["step"], "discover")
        self.assertIn("不自动安装", p["plan_ir"]["approval"])
        # 落库
        lines = chimera.CANDIDATES_FILE.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 4)
        rec = json.loads(lines[0])
        self.assertEqual(rec["gap"], "pdf table extraction")

    def test_render_text(self):
        with mock.patch.object(chimera, "discover", return_value=[CAND_ALLOW]), \
             mock.patch.object(chimera, "cloud_review", return_value={"status": "skipped"}):
            p = chimera.plan("pdf", top_k=1)
        txt = chimera.render_plan_text(p)
        self.assertIn("奇美拉缺口计划", txt)
        self.assertIn("pdfplumber", txt)
        self.assertIn("discover", txt)

    def test_list_and_approve(self):
        with mock.patch.object(chimera, "discover", return_value=[CAND_ALLOW]), \
             mock.patch.object(chimera, "cloud_review", return_value={"status": "skipped"}):
            p = chimera.plan("pdf", top_k=1)
        cid = p["candidates"][0]["candidate_id"]
        cands = chimera.list_candidates()
        self.assertTrue(any(c.get("candidate_id") == cid for c in cands))
        rec = chimera.approve_candidate(cid, approved_by="policy")
        self.assertEqual(rec["action"], "approve")
        with self.assertRaises(ValueError):
            chimera.approve_candidate("no-such-id")

    def test_cloud_review_skipped_without_key(self):
        with mock.patch("cloud_bridge.available", return_value=False):
            r = chimera.cloud_review("gap", [CAND_ALLOW])
        self.assertEqual(r["status"], "skipped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
