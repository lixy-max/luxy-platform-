# -*- coding: utf-8 -*-
"""S22 热加载流水线 + 仿真节点 单元测试（Mock，不依赖 GPU/网络）"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hive_hotload as hl
import hive_sim as sim

READY = {"ready": True, "base_model": "/home/luxy/models/Qwen2.5-7B-Instruct-AWQ",
         "loaded": [], "slots_free": 2, "max_loras": 2,
         "endpoint": "http://127.0.0.1:8002"}


class TestHotload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s22h_")
        self._state = hl.STATE_FILE
        hl.STATE_FILE = Path(self.tmp) / "lora_state.json"
        self.addCleanup(setattr, hl, "STATE_FILE", self._state)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = mock.patch.object(hl.pool, "worker_status", return_value=dict(READY))
        self.p.start()
        self.addCleanup(self.p.stop)

    def _mock_runs(self, content="这是模型给出的有效回答，长度足够通过质量门槛。"):
        def fake_run(request=None, lora_id=None, max_tokens=64, capabilities=None):
            return {"ok": True, "content": content, "duration_s": 0.5,
                    "tokens_per_s": 30.0, "lora_id": lora_id or "base"}
        return fake_run

    def _eval_ok(self):
        hl.register_candidate("json_extract")
        with (mock.patch.object(hl.pool, "ensure_loaded", return_value={"ok": True}),
              mock.patch.object(hl.pool, "run", side_effect=self._mock_runs())):
            return hl.evaluate("json_extract")

    def test_register_candidate(self):
        r = hl.register_candidate("json_extract")
        self.assertTrue(r["ok"])
        self.assertEqual(r["candidate"]["status"], "shadow")

    def test_register_unknown(self):
        r = hl.register_candidate("no_such_lora_xyz")
        self.assertFalse(r["ok"])

    def test_evaluate_pass(self):
        hl.register_candidate("json_extract")
        with (mock.patch.object(hl.pool, "ensure_loaded",
                                return_value={"ok": True, "loaded_now": True}),
              mock.patch.object(hl.pool, "run", side_effect=self._mock_runs())):
            r = hl.evaluate("json_extract")
        self.assertTrue(r["passed"])
        self.assertEqual(r["status"], "shadow")
        self.assertEqual(r["evaluation"]["quality"], 1.0)
        self.assertTrue(r["evaluation"]["gate"]["passed"])

    def test_evaluate_fail_short_content(self):
        hl.register_candidate("json_extract")
        with (mock.patch.object(hl.pool, "ensure_loaded", return_value={"ok": True}),
              mock.patch.object(hl.pool, "run", side_effect=self._mock_runs(content="短"))):
            r = hl.evaluate("json_extract")
        self.assertFalse(r["passed"])
        self.assertEqual(r["status"], "failed")

    def test_promote_requires_shadow_and_eval(self):
        hl.register_candidate("json_extract")
        r = hl.promote("json_extract")
        self.assertFalse(r["ok"])
        self._eval_ok()
        r2 = hl.promote("json_extract", ratio=0.05)
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["candidate"]["status"], "canary")
        self.assertAlmostEqual(r2["candidate"]["canary_ratio"], 0.05)

    def test_promote_active(self):
        self._eval_ok()
        hl.promote("json_extract", ratio=0.05)
        r = hl.promote_active("json_extract")
        self.assertTrue(r["ok"])
        self.assertEqual(r["candidate"]["status"], "active")

    def test_rollback(self):
        self._eval_ok()
        hl.promote("json_extract", ratio=0.05)
        loaded_status = dict(READY, loaded=["json_extract"])
        with (mock.patch.object(hl.pool, "worker_status", return_value=loaded_status),
              mock.patch.object(hl.pool, "_unload", return_value=(True, "", 0.05)) as mu):
            r = hl.rollback("json_extract", "测试回滚")
        self.assertTrue(r["ok"])
        self.assertEqual(r["candidate"]["status"], "rolled_back")
        mu.assert_called_once_with("json_extract")

    def test_canary_routing_ratio(self):
        self._eval_ok()
        hl.promote("json_extract", ratio=0.05)
        hits = 0
        for i in range(4000):
            r = hl.route_with_canary("base", "请求 %d" % i)
            if r["is_canary"]:
                hits += 1
                self.assertEqual(r["lora_id"], "json_extract")
        ratio = hits / 4000
        self.assertGreater(ratio, 0.02)
        self.assertLess(ratio, 0.09)

    def test_route_when_no_canary(self):
        r = hl.route_with_canary("base", "请求")
        self.assertFalse(r["is_canary"])
        self.assertEqual(r["lora_id"], "base")

    def test_status(self):
        s = hl.status()
        self.assertTrue(s["ok"])
        self.assertIn("states", s)


class TestSim(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s22s_")
        self._dirs = (sim.SIM_DIR, sim.REPORT_DIR, sim.QUARANTINE_FILE, sim.KB_DIR)
        sim.SIM_DIR = Path(self.tmp)
        sim.REPORT_DIR = Path(self.tmp) / "reports"
        sim.QUARANTINE_FILE = Path(self.tmp) / "quarantine.jsonl"
        sim.KB_DIR = Path(self.tmp) / "kbs"
        sim.KB_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self._restore, self.tmp)

    def _restore(self, tmp):
        sim.SIM_DIR, sim.REPORT_DIR, sim.QUARANTINE_FILE, sim.KB_DIR = self._dirs
        shutil.rmtree(tmp, ignore_errors=True)

    def test_dirty_data_isolation(self):
        r = sim.run_dirty_data(14)
        self.assertEqual(r["generated"], 14)
        self.assertGreater(r["injected"], 0)
        self.assertEqual(r["quarantined"], r["injected"])
        self.assertFalse(r["kb_written"])
        lines = sim.QUARANTINE_FILE.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), r["quarantined"])

    def test_http_concurrent_mocked(self):
        fake = {"ok": True, "latency_ms": 12.5, "status": 200}
        with mock.patch.object(sim, "_http_post", return_value=fake):
            r = sim.run_http_concurrent(users=2, duration_s=1)
        self.assertGreater(r["requests"], 0)
        self.assertEqual(r["errors"], 0)
        self.assertGreater(r["qps"], 0)
        self.assertEqual(r["latency_ms"]["p50"], 12.5)

    def test_run_report_saved(self):
        fake = {"ok": True, "latency_ms": 8.0, "status": 200}
        with mock.patch.object(sim, "_http_post", return_value=fake):
            r = sim.run("mixed", users=2, duration_s=1, n=7)
        self.assertTrue(r["ok"])
        self.assertIn("traffic", r)
        self.assertIn("dirty", r)
        self.assertTrue(Path(r["report_path"]).exists())
        reports = sim.list_reports()
        self.assertGreaterEqual(len(reports), 1)

    def test_unknown_scenario(self):
        r = sim.run("nope")
        self.assertFalse(r["ok"])

    def test_stats(self):
        s = sim.stats()
        self.assertIn("http_concurrent", s["scenarios"])
        self.assertIn("dirty_data", s["scenarios"])




class TestHotloadGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s22g_")
        self._state = hl.STATE_FILE
        hl.STATE_FILE = Path(self.tmp) / "lora_state.json"
        self.addCleanup(setattr, hl, "STATE_FILE", self._state)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = mock.patch.object(hl.pool, "worker_status", return_value=dict(READY))
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_reject_broken_adapter(self):
        r = hl.register_candidate("swk_persona_v1")   # 路径缺失
        self.assertFalse(r["ok"])
        self.assertIn("不可用", r["error"])

    def test_reject_family_mismatch(self):
        r = hl.register_candidate("workflow_author_v1")  # 1.5B vs 7B
        self.assertFalse(r["ok"])
        self.assertIn("家族不匹配", r["error"])

    def test_allow_mismatch_flag(self):
        r = hl.register_candidate("workflow_author_v1", allow_mismatch=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["candidate"]["status"], "shadow")


if __name__ == "__main__":
    unittest.main(verbosity=2)
