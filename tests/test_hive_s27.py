# -*- coding: utf-8 -*-
"""S27 训练队列 + 灰度自动回滚 单元测试"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hive_canary as cn
import hive_train_queue as tq


class TestTrainQueue(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s27q_"))
        self.p = mock.patch.object(tq, "QUEUE_FILE", self.tmp / "queue.json")
        self.p.start()
        self.addCleanup(self.p.stop)
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def test_enqueue_list_get(self):
        r = tq.enqueue({"dataset": "/tmp/ds.jsonl", "lora_name": "t1"})
        self.assertTrue(r["ok"])
        tid = r["task"]["id"]
        self.assertIsNotNone(tq.get(tid))
        self.assertEqual(len(tq.list_tasks()), 1)

    def test_priority_and_next_ready(self):
        a = tq.enqueue({"dataset": "/tmp/a.jsonl", "priority": 9})["task"]
        b = tq.enqueue({"dataset": "/tmp/b.jsonl", "priority": 1})["task"]
        self.assertEqual(tq.next_ready()["id"], b["id"])

    def test_retry_defer_flow(self):
        a = tq.enqueue({"dataset": "/tmp/a.jsonl"})["task"]
        tq.mark_running(a["id"])
        rec = tq.mark_failed(a["id"], {"ok": False}, retry=True, delay_s=999)
        self.assertEqual(rec["status"], "deferred")
        self.assertGreater(rec["next_retry_at"], time.time())
        tq.retry_deferred(0)
        self.assertEqual(tq.get(a["id"])["status"], "deferred")
        # 重试 3 次后失败
        tq._update(a["id"], attempts=tq.MAX_ATTEMPTS)
        rec2 = tq.mark_failed(a["id"], {"ok": False}, retry=True)
        self.assertEqual(rec2["status"], "failed")

    def test_process_next_success(self):
        a = tq.enqueue({"dataset": "/tmp/a.jsonl"})["task"]
        out = tq.process_next(executor=lambda t: {"ok": True, "adapter": "/x"})
        self.assertTrue(out["ok"])
        self.assertEqual(tq.get(a["id"])["status"], "done")

    def test_process_next_failure_retry(self):
        a = tq.enqueue({"dataset": "/tmp/a.jsonl"})["task"]
        out = tq.process_next(executor=lambda t: {"ok": False, "error": "boom"})
        self.assertFalse(out["ok"])
        self.assertEqual(tq.get(a["id"])["status"], "deferred")

    def test_process_empty(self):
        self.assertFalse(tq.process_next(dry_run=True)["ok"])


class TestCanary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s27c_"))
        self.p = mock.patch.object(cn, "STATE_FILE", self.tmp / "canary.json")
        self.p.start()
        self.addCleanup(self.p.stop)
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.hl = mock.MagicMock()
        self.hl.promote.return_value = {"ok": True, "status": "canary"}
        self.hl.promote_active.return_value = {"ok": True, "status": "active"}
        self.hl.rollback.return_value = {"ok": True, "status": "rolled_back"}
        self.patches = [mock.patch.dict(sys.modules, {"hive_hotload": self.hl})]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_start_and_observe(self):
        r = cn.start_canary("lora_x", ratio=0.05)
        self.assertTrue(r["ok"])
        self.hl.promote.assert_called_once()
        ev = cn.evaluate("lora_x")
        self.assertEqual(ev["status"], "observing")

    def test_healthy_auto_promote(self):
        cn.start_canary("lora_x")
        for _ in range(6):
            cn.record("lora_x", True, quality=0.9, latency_ms=500)
        ev = cn.evaluate("lora_x")
        self.assertEqual(ev["status"], "healthy")
        out = cn.auto("lora_x")
        self.assertEqual(out["action"], "promote_active")
        self.hl.promote_active.assert_called_once()

    def test_breach_auto_rollback(self):
        cn.start_canary("lora_bad")
        for _ in range(6):
            cn.record("lora_bad", False, quality=0.1, latency_ms=9000)
        ev = cn.evaluate("lora_bad")
        self.assertEqual(ev["status"], "breaching")
        out = cn.auto("lora_bad")
        self.assertEqual(out["action"], "rollback")
        self.hl.rollback.assert_called_once()

    def test_record_without_start(self):
        self.assertFalse(cn.record("nope", True)["ok"])

    def test_status_and_clear(self):
        cn.start_canary("lora_x")
        st = cn.status("lora_x")
        self.assertTrue(st["ok"])
        self.assertEqual(cn.status()["stats"]["count"], 1)
        cn.clear()
        self.assertFalse(cn.status("lora_x")["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
