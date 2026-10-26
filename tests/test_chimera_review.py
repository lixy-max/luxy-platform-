# -*- coding: utf-8 -*-
"""P6 奇美拉自动云审测试（全打桩：不调 Kimi / 不装包）"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chimera_review as cr

CAND_AUTO = {"candidate_id": "c1", "id": "vercel-labs/agent-browser", "source": "github",
             "stars": 44607, "license": "apache-2.0", "gate": "auto_approved"}
CAND_REVIEW = {"candidate_id": "c2", "id": "x/no-license", "source": "github",
               "stars": 900, "license": "none", "gate": "needs_review"}


def _plan(cands, verdict="approve", recommend=None, status="ok"):
    return {"gap": "browser", "plan_id": "p1", "candidates": [dict(c) for c in cands],
            "cloud_review": {"status": status,
                             "review": {"verdict": verdict,
                                        "recommend": recommend if recommend is not None else [c["id"] for c in cands],
                                        "concerns": ["c1"], "license_notes": "note",
                                        "sandbox_requirements": ["no net"]}}}


class TestCloudGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="review_ut_"))
        self._orig = cr.REVIEWS_FILE
        cr.REVIEWS_FILE = self.tmp / "reviews.jsonl"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, cr, "REVIEWS_FILE", self._orig)

    def test_approve_and_promote(self):
        plan = _plan([CAND_AUTO, CAND_REVIEW])
        fake_caps = {"ext:github:vercel-labs-agent-browser":
                     {"cap_id": "ext:github:vercel-labs-agent-browser", "status": "proposed"}}

        class FakeCaps:
            @staticmethod
            def get_capability(cid): return fake_caps.get(cid)
            @staticmethod
            def set_status(cid, st): fake_caps[cid]["status"] = st

        with mock.patch.dict(sys.modules, {"capabilities": FakeCaps}):
            decision = cr.apply_cloud_review(plan)
        self.assertEqual(decision["verdict"], "approve")
        self.assertEqual(plan["final_gates"]["auto_approved"],
                         ["ext:github:vercel-labs-agent-browser"][0:0] + [CAND_AUTO["candidate_id"]])
        self.assertIn(CAND_REVIEW["candidate_id"], plan["final_gates"]["needs_review"])
        self.assertEqual(decision["promoted_capabilities"],
                         ["ext:github:vercel-labs-agent-browser"])
        self.assertTrue(cr.REVIEWS_FILE.exists())

    def test_reject(self):
        plan = _plan([CAND_AUTO], verdict="reject", recommend=[])
        decision = cr.apply_cloud_review(plan)
        self.assertIn(CAND_AUTO["candidate_id"], plan["final_gates"]["rejected"])
        self.assertEqual(decision["counts"]["rejected"], 1)

    def test_need_more(self):
        plan = _plan([CAND_AUTO, CAND_REVIEW], verdict="need_more", recommend=[])
        cr.apply_cloud_review(plan)
        self.assertEqual(len(plan["final_gates"]["auto_approved"]), 0)
        self.assertEqual(len(plan["final_gates"]["needs_review"]), 2)

    def test_recommend_by_repo_shortname(self):
        plan = _plan([CAND_AUTO], verdict="need_more", recommend=["agent-browser"])
        cr.apply_cloud_review(plan)
        self.assertIn(CAND_AUTO["candidate_id"], plan["final_gates"]["auto_approved"])

    def test_cloud_unavailable_falls_back_to_policy(self):
        plan = _plan([CAND_AUTO, CAND_REVIEW], status="skipped")
        decision = cr.apply_cloud_review(plan)
        self.assertEqual(decision["verdict"], "unavailable")
        self.assertIn(CAND_AUTO["candidate_id"], plan["final_gates"]["auto_approved"])
        self.assertIn(CAND_REVIEW["candidate_id"], plan["final_gates"]["needs_review"])

    def test_auto_dry_run_only_auto_approved(self):
        plan = _plan([CAND_AUTO, CAND_REVIEW])
        with mock.patch("capabilities.get_capability", return_value=None), \
             mock.patch("capabilities.set_status"):
            cr.apply_cloud_review(plan)
        with mock.patch("sandbox_installer.dry_run",
                        return_value={"can_install": True, "risks": [],
                                      "checks": {"pypi": {"package": "selenium"}}}) as m:
            out = cr.auto_dry_run(plan)
        self.assertEqual(len(out), 1)
        self.assertTrue(m.called)
        self.assertTrue(out[0]["can_install"])
        self.assertEqual(out[0]["pypi"], "selenium")

    def test_auto_review_flow(self):
        plan = _plan([CAND_AUTO])
        with mock.patch("chimera.plan", return_value=plan), \
             mock.patch("capabilities.get_capability", return_value={"status": "proposed"}), \
             mock.patch("capabilities.set_status"), \
             mock.patch("sandbox_installer.dry_run", return_value={"can_install": True, "risks": []}):
            out = cr.auto_review("browser", top_k=2, do_dry_run=True)
        self.assertTrue(out.get("auto_reviewed"))
        self.assertEqual(out["cloud_decision"]["verdict"], "approve")
        self.assertEqual(len(out.get("dry_runs") or []), 1)

    def test_review_one(self):
        with mock.patch("chimera.cloud_review",
                        return_value={"status": "ok",
                                      "review": {"verdict": "approve", "recommend": ["agent-browser"]}}):
            rec = cr.review_one(CAND_AUTO)
        self.assertEqual(rec["final_gate"], "auto_approved")

    def test_review_one_reject(self):
        with mock.patch("chimera.cloud_review",
                        return_value={"status": "ok", "review": {"verdict": "reject", "recommend": []}}):
            rec = cr.review_one(CAND_AUTO)
        self.assertEqual(rec["final_gate"], "rejected")


if __name__ == "__main__":
    unittest.main(verbosity=2)
