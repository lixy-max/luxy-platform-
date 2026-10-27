# -*- coding: utf-8 -*-
"""S28 治理层 + 受控自主 单元测试"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import capability_registry as cr
import capability_importer as imp
import hive_autonomy as au
import hive_gov as gov


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s28_"))
        self.patches = [
            mock.patch.object(gov, "GOV_DIR", self.tmp / "gov"),
            mock.patch.object(gov, "POLICY_FILE", self.tmp / "gov" / "policy.json"),
            mock.patch.object(gov, "APPROVALS_FILE", self.tmp / "gov" / "approvals.json"),
            mock.patch.object(gov, "BUDGET_FILE", self.tmp / "gov" / "budget.json"),
            mock.patch.object(gov, "AUDIT_FILE", self.tmp / "gov" / "audit.jsonl"),
            mock.patch.object(cr, "DB_PATH", self.tmp / "cap.db"),
            mock.patch.object(imp, "SANDBOX_DIR", self.tmp / "sandbox"),
            mock.patch.object(imp, "IMPORT_DIR", self.tmp / "imports"),
            mock.patch.object(imp, "MANIFEST_DIR", self.tmp / "manifests"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        cr.seed_from_platform()


class TestGov(Base):
    def test_kill_switch(self):
        self.assertFalse(gov.is_killed())
        gov.kill_switch(True)
        self.assertTrue(gov.is_killed())
        r = gov.request("capability_import", {})
        self.assertFalse(r["allowed"])
        self.assertEqual(r["status"], "blocked_kill_switch")
        gov.kill_switch(False)
        self.assertTrue(gov.request("capability_import",
                                    {"manifest": {"type": "tool", "data": {"sensitivity": "low"}}})["allowed"])

    def test_low_auto_high_pending(self):
        low = gov.request("capability_import",
                          {"manifest": {"type": "tool", "data": {"sensitivity": "low"}}})
        self.assertTrue(low["allowed"])
        self.assertEqual(low["mode"], "auto_approve")
        high = gov.request("capability_import",
                           {"manifest": {"type": "tool", "data": {"sensitivity": "high"}}})
        self.assertFalse(high["allowed"])
        self.assertEqual(high["status"], "pending_approval")
        self.assertTrue(high.get("approval_id"))

    def test_approve_reuse_and_reject(self):
        req = {"manifest": {"type": "tool", "data": {"sensitivity": "high"}}}
        gate = gov.request("capability_import", req)
        ap = gov.approve(gate["approval_id"], approver="tester")
        self.assertTrue(ap["ok"])
        again = gov.request("capability_import", req)
        self.assertTrue(again["allowed"])
        self.assertEqual(again["mode"], "approved_reuse")
        gate2 = gov.request("capability_import",
                            {"manifest": {"type": "tool", "data": {"sensitivity": "high",
                                                                   "needs": ["其他"]}}})
        self.assertEqual(gate2["status"], "pending_approval")
        rj = gov.reject(gate2["approval_id"], approver="tester")
        self.assertTrue(rj["ok"])
        self.assertEqual(gov.get_approval(gate2["approval_id"])["status"], "rejected")

    def test_budget(self):
        policy = gov.load_policy()
        policy["daily_budget"]["cloud_calls"] = 2
        gov.save_policy(policy)
        gov.record_spend("cloud_calls", 2)
        self.assertFalse(gov.check_budget("cloud_calls", 1)["allowed"])
        self.assertTrue(gov.check_budget("cloud_calls", 0)["allowed"])

    def test_stats_and_audit(self):
        gov.kill_switch(True)
        gov.kill_switch(False)
        st = gov.stats()
        self.assertGreaterEqual(st["audit_entries"], 2)
        self.assertIn("policy", st)


class TestAutonomy(Base):
    def test_run_loop(self):
        r = au.run_loop(3)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["passed"], 3)
        for loop in r["loops"]:
            self.assertTrue(loop["gap_detected"])
            self.assertTrue(loop["verify"]["match"])
            self.assertTrue(loop["deployed"])

    def test_high_risk_approve_then_import(self):
        out = au.demo_high_risk()
        self.assertEqual(out["gate"]["status"], "pending_approval")
        self.assertEqual(out["approval"]["approval"]["status"], "approved")
        self.assertTrue(out["import_after_approval"]["ok"])
        self.assertIn(out["import_after_approval"]["status"], ("active", "shadow"))

    def test_kill_switch_demo(self):
        out = au.demo_kill_switch()
        self.assertFalse(out["killed_blocked"]["ok"])
        self.assertTrue(out["killed_blocked"]["blocked"])
        self.assertTrue(out["restored"]["ok"])

    def test_budget_demo(self):
        out = au.demo_budget()
        self.assertFalse(out["check_after_spend"]["allowed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
