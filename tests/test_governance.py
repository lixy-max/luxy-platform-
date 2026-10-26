# -*- coding: utf-8 -*-
"""P5 治理层测试：KILL 开关 / 预算熔断 / 审计 / 影子期晋级"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import governance as gov


class TestGovernance(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gov_ut_"))
        self._orig = (gov.GOV_DIR, gov.STATE_FILE, gov.AUDIT_FILE)
        gov.GOV_DIR = self.tmp
        gov.STATE_FILE = self.tmp / "state.json"
        gov.AUDIT_FILE = self.tmp / "audit.jsonl"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, gov, "GOV_DIR", self._orig[0])
        self.addCleanup(setattr, gov, "STATE_FILE", self._orig[1])
        self.addCleanup(setattr, gov, "AUDIT_FILE", self._orig[2])

    def test_freeze_unfreeze(self):
        self.assertFalse(gov.is_frozen())
        gov.freeze(reason="test")
        self.assertTrue(gov.is_frozen())
        ok, why = gov.check_exec_allowed()
        self.assertFalse(ok)
        self.assertIn("冻结", why)
        gov.unfreeze()
        self.assertFalse(gov.is_frozen())
        ok, _ = gov.check_exec_allowed()
        self.assertTrue(ok)

    def test_budget_daily(self):
        with mock.patch.object(gov, "EXEC_PER_DAY", 2):
            for _ in range(2):
                gov.record_exec("cap:x", ok=True)
            ok, why = gov.check_exec_allowed()
        self.assertFalse(ok)
        self.assertIn("预算", why)

    def test_budget_minute(self):
        with mock.patch.object(gov, "EXEC_PER_MINUTE", 2):
            for _ in range(2):
                gov.record_exec("cap:x", ok=True)
            ok, why = gov.check_exec_allowed()
        self.assertFalse(ok)
        self.assertIn("频率", why)

    def test_status_fields(self):
        gov.record_exec("cap:x", ok=True)
        st = gov.status()
        self.assertEqual(st["exec_today"], 1)
        self.assertFalse(st["frozen"])
        self.assertIn("exec_per_day", st)

    def test_audit(self):
        gov.audit("test_action", foo="bar")
        rows = gov.read_audit(limit=10)
        self.assertTrue(any(r.get("action") == "test_action" for r in rows))
        self.assertEqual(rows[-1].get("foo"), "bar")

    def test_promote_ready(self):
        caps = [
            {"cap_id": "ext:a", "status": "shadow", "uses": 12, "success_rate": 0.95},
            {"cap_id": "ext:b", "status": "shadow", "uses": 3, "success_rate": 1.0},
            {"cap_id": "ext:c", "status": "shadow", "uses": 12, "success_rate": 0.5},
        ]
        with mock.patch("capabilities.list_capabilities", return_value=caps), \
             mock.patch("capabilities.set_status") as m:
            promoted = gov.promote_ready(min_uses=10, min_rate=0.9)
        self.assertEqual(promoted, ["ext:a"])
        m.assert_called_once_with("ext:a", "active")

    def test_maybe_promote(self):
        cap = {"cap_id": "ext:a", "status": "shadow", "uses": 10, "success_rate": 0.92}
        with mock.patch("capabilities.get_capability", return_value=cap), \
             mock.patch("capabilities.set_status") as m:
            self.assertTrue(gov.maybe_promote("ext:a"))
        m.assert_called_once_with("ext:a", "active")

    def test_maybe_promote_not_ready(self):
        cap = {"cap_id": "ext:a", "status": "shadow", "uses": 1, "success_rate": 1.0}
        with mock.patch("capabilities.get_capability", return_value=cap):
            self.assertFalse(gov.maybe_promote("ext:a"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
