# -*- coding: utf-8 -*-
"""S20 Failover + 云端教师 + 轨迹回收 单元测试（Mock，不消耗云端 API）"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hive_failover as fo
import hive_trajectory as tj

BASE7B = "/home/luxy/models/Qwen2.5-7B-Instruct-AWQ"
READY = {"ready": True, "base_model": BASE7B, "loaded": [], "slots_free": 2,
         "max_loras": 2, "endpoint": "http://127.0.0.1:8002"}


class FakeCloud:
    def __init__(self, ok=True, provider="deepseek", model="deepseek-flash", text=None):
        self.ok, self.provider, self.model = ok, provider, model
        self.text = text or "这是云端教师给出的完整答案，包含步骤、结论与注意事项。" * 3
        self.calls = []

    def __call__(self, prompt, system=None, max_tokens=2000, temperature=0.3):
        self.calls.append({"prompt": prompt, "system": system})
        if not self.ok:
            raise RuntimeError("cloud down")
        return self.text, {"provider": self.provider, "model": self.model,
                           "duration_s": 1.5, "attempts": 1, "via": "cloud_teacher"}


class TestFailover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s20_ut_")
        self._dir, self._file = tj.TRAJ_DIR, tj.TRAJ_FILE
        tj.TRAJ_DIR = Path(self.tmp)
        tj.TRAJ_FILE = Path(self.tmp) / "t.jsonl"
        self.addCleanup(setattr, tj, "TRAJ_DIR", self._dir)
        self.addCleanup(setattr, tj, "TRAJ_FILE", self._file)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        fo.reset()
        self.cloud = FakeCloud()
        self.p_cloud = mock.patch.object(fo, "cloud_ask", side_effect=self.cloud)
        self.p_cloud.start()
        self.addCleanup(self.p_cloud.stop)
        self.p_ready = mock.patch.object(fo.pool, "worker_status", return_value=dict(READY))
        self.p_ready.start()
        self.addCleanup(self.p_ready.stop)

    def _local_ok(self, **kw):
        return {"ok": True, "content": "本地答案", "lora_id": "json_extract",
                "worker_id": "w1", "model": "json_extract", "duration_s": 1.2,
                "tokens_per_s": 20.0, "usage": {"completion_tokens": 10}}

    def _local_fail(self, err="boom"):
        return {"ok": False, "error": err, "duration_s": 0.1}

    def test_local_success_no_cloud(self):
        with mock.patch.object(fo.pool, "run", return_value=self._local_ok()):
            r = fo.run_with_failover("列一下当前状态", capabilities=["json"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["route"], "local")
        self.assertFalse(r["failover"]["triggered"])
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(fo.stats()["local_ok"], 1)
        self.assertEqual(fo.stats()["consecutive_failures"], 0)

    def test_local_fail_to_cloud_with_trajectory(self):
        with mock.patch.object(fo.pool, "run", return_value=self._local_fail("lora 加载失败")):
            r = fo.run_with_failover("写一份采购清单", capabilities=["json"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["route"], "cloud")
        self.assertTrue(r["failover"]["triggered"])
        self.assertIn("lora 加载失败", self.cloud.calls[0]["prompt"])
        self.assertEqual(fo.stats()["consecutive_failures"], 1)
        lines = tj.TRAJ_FILE.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        traj = json.loads(lines[0])
        self.assertEqual(traj["source"], "cloud")
        self.assertTrue(traj["tags"]["failover"])
        self.assertEqual(traj["failover"]["trigger"], "local_failed")
        self.assertEqual(traj["steps"][-1]["status"], "failed")

    def test_threshold_escalates_next_call_without_local(self):
        fail = self._local_fail("x")
        with mock.patch.object(fo.pool, "run", return_value=fail) as ml:
            fo.run_with_failover("任务A")
            fo.run_with_failover("任务B")
            self.assertEqual(fo.stats()["consecutive_failures"], 2)
            ml.reset_mock()
            r = fo.run_with_failover("任务C")
            ml.assert_not_called()
        self.assertEqual(r["route"], "cloud")
        self.assertIn("consecutive_failures", r["failover"]["reason"])

    def test_worker_unavailable_goes_cloud(self):
        with mock.patch.object(fo.pool, "worker_status",
                               return_value={"ready": False, "error": "conn refused"}), \
             mock.patch.object(fo.pool, "run") as mrun:
            r = fo.run_with_failover("任意任务")
            mrun.assert_not_called()
        self.assertEqual(r["route"], "cloud")
        self.assertEqual(r["failover"]["reason"], "worker_unavailable")

    def test_force_cloud(self):
        with mock.patch.object(fo.pool, "run") as mrun:
            r = fo.run_with_failover("任务", force_cloud=True)
            mrun.assert_not_called()
        self.assertEqual(r["route"], "cloud")
        self.assertEqual(r["failover"]["reason"], "force_cloud")

    def test_force_local_bypasses_threshold(self):
        fail = self._local_fail()
        with mock.patch.object(fo.pool, "run", side_effect=[fail, fail]) as _:
            fo.run_with_failover("x")
            fo.run_with_failover("y")
        self.assertTrue(fo.should_escalate())
        with mock.patch.object(fo.pool, "run", return_value=self._local_ok()) as ml:
            r = fo.run_with_failover("z", force_local=True)
            ml.assert_called_once()
        self.assertEqual(r["route"], "local")

    def test_local_timeout_escalates(self):
        slow = self._local_ok()
        slow["duration_s"] = fo.LOCAL_TIMEOUT_S + 10
        with mock.patch.object(fo.pool, "run", return_value=slow):
            r = fo.run_with_failover("超时任务")
        self.assertEqual(r["route"], "cloud")
        self.assertIn("local_timeout", str(r["failover"].get("local_error")))

    def test_cloud_failure_returns_error(self):
        bad = FakeCloud(ok=False)
        with mock.patch.object(fo, "cloud_ask", side_effect=bad), \
             mock.patch.object(fo.pool, "run", return_value=self._local_fail()):
            r = fo.run_with_failover("任务")
        self.assertFalse(r["ok"])
        self.assertIn("cloud down", r["error"])
        self.assertEqual(fo.stats()["cloud_fail"], 1)

    def test_success_resets_threshold(self):
        fail = self._local_fail()
        with mock.patch.object(fo.pool, "run", return_value=fail):
            fo.run_with_failover("a")
            fo.run_with_failover("b")
        self.assertTrue(fo.should_escalate())
        with mock.patch.object(fo.pool, "run", return_value=self._local_ok()):
            fo.run_with_failover("c", force_local=True)
        self.assertFalse(fo.should_escalate())
        self.assertEqual(fo.stats()["consecutive_failures"], 0)

    def test_context_included_in_cloud_prompt(self):
        with mock.patch.object(fo.pool, "run", return_value=self._local_fail()):
            fo.run_with_failover("任务", context="本地文件: 采购清单.xlsx；预算 5000")
        prompt = self.cloud.calls[0]["prompt"]
        self.assertIn("采购清单.xlsx", prompt)
        self.assertIn("5000", prompt)


class TestCloudAsk(unittest.TestCase):
    def test_provider_fallback(self):
        provs = [{"provider": "p1", "api_base": "https://x1/v1", "api_key": "k1", "model": "m1"},
                 {"provider": "p2", "api_base": "https://x2/v1", "api_key": "k2", "model": "m2"}]
        fail = mock.Mock(status_code=500, text="boom")
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"choices": [{"message": {"content": "云端答案"}}]}
        with mock.patch.object(fo, "_provider_candidates", return_value=provs), \
             mock.patch("requests.post", side_effect=[fail, ok]):
            content, info = fo.cloud_ask("问题")
        self.assertEqual(content, "云端答案")
        self.assertEqual(info["provider"], "p2")
        self.assertEqual(info["attempts"], 2)

    def test_all_providers_fail(self):
        provs = [{"provider": "p1", "api_base": "https://x1/v1", "api_key": "k1", "model": "m1"}]
        fail = mock.Mock(status_code=500, text="boom")
        with mock.patch.object(fo, "_provider_candidates", return_value=provs), \
             mock.patch("requests.post", return_value=fail):
            with self.assertRaises(RuntimeError):
                fo.cloud_ask("问题")


class TestTrajectory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s20_tj_")
        self._dir, self._file = tj.TRAJ_DIR, tj.TRAJ_FILE
        tj.TRAJ_DIR = Path(self.tmp)
        tj.TRAJ_FILE = Path(self.tmp) / "t.jsonl"
        self.addCleanup(setattr, tj, "TRAJ_DIR", self._dir)
        self.addCleanup(setattr, tj, "TRAJ_FILE", self._file)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_build_and_validate(self):
        t = tj.build_trajectory("任务", "答案" * 80, provider="deepseek",
                                model="deepseek-flash", duration_s=2.0,
                                context="ctx", failover={"trigger": "local_failed"})
        self.assertEqual(tj.validate(t), [])
        self.assertEqual(t["tags"]["quality"], "high")
        self.assertTrue(t["tags"]["failover"])

    def test_record_list_stats(self):
        for i in range(3):
            t = tj.build_trajectory("任务%d" % i, "答案" * 10, provider="deepseek",
                                    model="deepseek-flash", task_type="data")
            self.assertTrue(tj.record(t)["ok"])
        self.assertEqual(len(tj.list_trajectories()), 3)
        self.assertEqual(tj.list_trajectories(limit=2)[0]["task"], "任务2")
        s = tj.stats()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_provider"]["deepseek"], 3)
        self.assertEqual(s["by_task_type"]["data"], 3)

    def test_record_invalid(self):
        r = tj.record({"task": "x"})
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
