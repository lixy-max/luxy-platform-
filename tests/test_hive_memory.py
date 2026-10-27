# -*- coding: utf-8 -*-
"""S23 主脑路由临时知识库（hive_memory）单元测试"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hive_memory as mem


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s23m_")
        self._db = mem.MEM_DB
        mem.MEM_DB = Path(self.tmp) / "memory.db"
        self.addCleanup(setattr, mem, "MEM_DB", self._db)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._sem = mem.SEMANTIC
        mem.SEMANTIC = False
        self.addCleanup(setattr, mem, "SEMANTIC", self._sem)

    def test_record_and_keyword_search(self):
        d = {"target": "local", "confidence": 0.9, "task_type": "data",
             "intent_summary": "表格排序", "routing": {"why": "格式能力命中"}}
        r = mem.record("把表格按单价排序", decision=d)
        self.assertTrue(r["ok"])
        rows = mem.search("表格 排序", semantic=False)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["target"], "local")

    def test_context_for(self):
        d = {"target": "sim", "confidence": 0.95, "task_type": "other",
             "intent_summary": "压测", "routing": {"why": "含压测关键词"}}
        mem.record("用 500 并发压测路由", decision=d)
        ctx = mem.context_for("压测系统", semantic=False)
        self.assertIn("历史相关", ctx)
        self.assertIn("sim", ctx)

    def test_stats_and_clear(self):
        mem.record("任务A", decision={"target": "local", "confidence": 0.9})
        mem.record("任务B", decision={"target": "cloud", "confidence": 0.8})
        s = mem.stats()
        self.assertGreaterEqual(s["total"], 2)
        self.assertIn("local", s["targets"])
        c = mem.clear()
        self.assertTrue(c["ok"])
        self.assertEqual(mem.stats()["total"], 0)

    def test_ttl_cleanup(self):
        old_ttl = mem.TTL_H
        mem.TTL_H = -1   # 立即过期
        try:
            mem.record("过期任务", decision={"target": "local"})
        finally:
            mem.TTL_H = old_ttl
        rows = mem.search("过期任务", semantic=False)
        self.assertEqual(rows, [])
        res = mem.cleanup()
        self.assertGreaterEqual(res.get("deleted", 0), 0)
        mem.record("新任务", decision={"target": "sim"})
        self.assertTrue(mem.search("新任务", semantic=False))

    def test_disabled(self):
        old = mem.ENABLED
        mem.ENABLED = False
        try:
            self.assertFalse(mem.record("任务")["ok"])
            self.assertEqual(mem.context_for("任务"), "")
        finally:
            mem.ENABLED = old

    def test_semantic_search_path_mocked(self):
        mem.SEMANTIC = True
        mem.record("语义任务", decision={"target": "cloud"})
        fake = [{"request": "语义命中记录", "target": "cloud", "source": "kb_semantic",
                 "score": 0.91, "why": "", "answer": ""}]
        with mock.patch.object(mem, "_search_semantic", return_value=fake) as ms:
            rows = mem.search("语义任务", semantic=True)
        self.assertEqual(rows[0]["source"], "kb_semantic")
        ms.assert_called_once()


class TestDecideMemoryIntegration(unittest.TestCase):
    def setUp(self):
        import hive_orchestrator as hv
        self.hv = hv
        self._dir, self._file = hv.HIVE_DIR, hv.DECISIONS_FILE
        hv.HIVE_DIR = Path(tempfile.mkdtemp(prefix="s23d_"))
        hv.DECISIONS_FILE = hv.HIVE_DIR / "decisions.jsonl"
        self.addCleanup(shutil.rmtree, str(hv.HIVE_DIR), True)
        self.addCleanup(setattr, hv, "HIVE_DIR", self._dir)
        self.addCleanup(setattr, hv, "DECISIONS_FILE", self._file)
        self.inv = mock.patch.object(hv, "build_inventory", return_value={
            "workers": [{"id": "w1", "status": "ready", "base": "7B"}],
            "loras": [{"id": "base", "score": 0.6, "capability": ["general"]}],
            "cloud": [{"provider": "deepseek", "model": "deepseek-flash", "cost": "low"}],
            "sim": [{"scenario": "http_concurrent"}],
            "tools": [], "kbs": [], "policy": {}})
        self.inv.start()
        self.addCleanup(self.inv.stop)

    def test_prompt_contains_memory_context(self):
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
        import json
        with (mock.patch.object(self.hv, "_llm_json", side_effect=fake_llm),
              mock.patch("hive_memory.context_for", return_value="【历史相关】上次 local 成功"),
              mock.patch("hive_memory.record", return_value={"ok": True})):
            r = self.hv.decide("把表格排序", use_llm=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["meta"]["memory_used"])
        user_msg = captured["messages"][-1]["content"] if captured["messages"][-1]["role"] == "user" \
            else captured["messages"][-2]["content"]
        self.assertIn("历史相关", user_msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
