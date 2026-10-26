# -*- coding: utf-8 -*-
"""S9 进化指标测试：指标计算/打点/快照/趋势"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import evolution_metrics as em
import trajectory_distill as td


class TestEvolutionMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="evo_ut_"))
        self._orig = (em.METRICS_DIR, em.SNAPSHOT_FILE)
        em.METRICS_DIR = self.tmp / "metrics"
        em.SNAPSHOT_FILE = em.METRICS_DIR / "evolution.jsonl"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, em, "METRICS_DIR", self._orig[0])
        self.addCleanup(setattr, em, "SNAPSHOT_FILE", self._orig[1])

    def test_current_structure(self):
        m = em.current()
        self.assertIn("kpis", m)
        self.assertIn("task_tables", m)
        self.assertIn("workflows", m)
        self.assertIn("cloud", m)
        self.assertIn("capabilities", m)
        self.assertIn("bugs", m)
        self.assertIn("at", m)

    def test_snapshot_and_trend(self):
        em.snapshot(save=True)
        em.snapshot(save=True)
        rows = em.trend(limit=5)
        self.assertEqual(len(rows), 2)
        self.assertIn("kpis", rows[0])

    def test_summary_text(self):
        txt = em.summary_text()
        self.assertIn("进化指标", txt)
        self.assertIn("任务表", txt)
        self.assertIn("Bug", txt)

    def test_cloud_metrics_from_file(self):
        cloud_dir = self.tmp / "data" / "cloud"
        cloud_dir.mkdir(parents=True)
        rows = [{"at": "2026-10-26T00:00:00", "model": "kimi-k2.6", "ok": True, "duration": 2.0},
                {"at": "2026-10-26T00:00:01", "model": "kimi-k2.6", "ok": False, "duration": 4.0}]
        (cloud_dir / "usage.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        with mock.patch.object(em, "PROJECT_DIR", self.tmp):
            c = em._cloud_metrics()
        self.assertEqual(c["calls_total"], 2)
        self.assertEqual(c["calls_ok"], 1)
        self.assertEqual(c["success_rate"], 0.5)
        self.assertEqual(c["avg_duration"], 3.0)

    def test_reuse_log(self):
        distill = self.tmp / "data" / "distilled"
        distill.mkdir(parents=True)
        with mock.patch.object(td, "DISTILL_DIR", distill), \
             mock.patch.object(em, "PROJECT_DIR", self.tmp):
            td.record_reuse("wf1", task="任务", score=0.9, channel="task_table")
            td.record_reuse("wf2", task="任务2", score=0.8, channel="chat_fastpath")
            w = em._workflow_metrics()
        self.assertEqual(w["reuse_total"], 2)
        self.assertEqual(w["reuse_by_channel"].get("task_table"), 1)


class TestKpis(unittest.TestCase):
    def test_kpis_compose(self):
        k = em._kpis({"completion_rate": 0.5, "tables": 2}, {"reuse_total": 3, "workflows": 4},
                     {"calls_total": 10, "success_rate": 0.9},
                     {"active": 5}, {"fixed_rate": 0.4})
        self.assertEqual(k["local_closure_rate"], 0.5)
        self.assertEqual(k["workflow_reuse_count"], 3)
        self.assertEqual(k["cloud_calls"], 10)
        self.assertEqual(k["capability_active"], 5)
        self.assertEqual(k["bug_fixed_rate"], 0.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
