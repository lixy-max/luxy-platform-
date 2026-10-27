# -*- coding: utf-8 -*-
"""S21 质检 / 训练调度（3060→5060 回退）/ 阈值自动训练 单元测试"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hive_autotune as at
import hive_quality as qa
import hive_trajectory as tj
import hive_trainer as trainer

GOOD = {
    "trajectory_id": "tr_x", "task": "解释 LoRA 原理", "task_type": "rag",
    "source": "cloud", "provider": "deepseek", "model": "deepseek-flash",
    "steps": [{"turn": 1, "thought": "x", "tool": "write_sheet", "input": {},
               "result": "ok", "status": "completed"}],
    "final": "完整答案" * 50, "tools_used": ["write_sheet"],
    "tags": {"task_type": "rag", "quality": "high", "failover": True},
}


class TestQuality(unittest.TestCase):
    def _ctx(self):
        tmp = tempfile.mkdtemp(prefix="s21q_")
        self._qd = (qa.QUALITY_DIR, qa.PASS_FILE, qa.REJECT_FILE)
        qa.QUALITY_DIR = Path(tmp)
        qa.PASS_FILE = Path(tmp) / "passed.jsonl"
        qa.REJECT_FILE = Path(tmp) / "rejected.jsonl"
        self.addCleanup(self._restore, tmp)
        return tmp

    def _restore(self, tmp):
        qa.QUALITY_DIR, qa.PASS_FILE, qa.REJECT_FILE = self._qd
        shutil.rmtree(tmp, ignore_errors=True)

    def test_pass_good(self):
        ev = qa.evaluate(GOOD)
        self.assertTrue(ev["pass"])
        self.assertEqual(ev["tags"]["quality"], "high")

    def test_reject_unknown_tool(self):
        bad = json.loads(json.dumps(GOOD))
        bad["steps"][0]["tool"] = "rm_rf_everything"
        ev = qa.evaluate(bad)
        self.assertFalse(ev["pass"])
        self.assertTrue(any("工具" in r for r in ev["reasons"]))

    def test_reject_too_many_steps(self):
        bad = json.loads(json.dumps(GOOD))
        bad["steps"] = [{"turn": i, "tool": "", "result": "x", "status": "completed"}
                        for i in range(qa.MAX_STEPS + 3)]
        ev = qa.evaluate(bad)
        self.assertFalse(ev["pass"])
        self.assertTrue(any("步数" in r for r in ev["reasons"]))

    def test_reject_short_and_long(self):
        short = json.loads(json.dumps(GOOD)); short["final"] = "短"
        long = json.loads(json.dumps(GOOD)); long["final"] = "长" * (qa.MAX_FINAL + 10)
        self.assertFalse(qa.evaluate(short)["pass"])
        self.assertFalse(qa.evaluate(long)["pass"])

    def test_run_qa_persist(self):
        tmp = self._ctx()
        # 用临时轨迹文件
        self._td = (tj.TRAJ_DIR, tj.TRAJ_FILE)
        tj.TRAJ_DIR = Path(tmp)
        tj.TRAJ_FILE = Path(tmp) / "traj.jsonl"
        self.addCleanup(self._restore_tj)
        for i in range(2):
            t = tj.build_trajectory("任务%d" % i, "答案" * 60, provider="deepseek",
                                    model="deepseek-flash", task_type="rag",
                                    failover={"trigger": "local_failed"})
            tj.record(t)
        res = qa.run_qa(persist=True)
        self.assertEqual(res["passed"], 2)
        self.assertEqual(qa.stats()["passed"], 2)
        samples = qa.build_training_samples(task_type="rag", min_quality="high")
        self.assertEqual(len(samples), 2)
        self.assertIn("assistant", samples[0]["conversations"][-1]["role"])

    def _restore_tj(self):
        tj.TRAJ_DIR, tj.TRAJ_FILE = self._td


class TestTrainerPlan(unittest.TestCase):
    def _patch(self, gpus, svc):
        self.p1 = mock.patch.object(trainer, "gpu_status", return_value=gpus)
        self.p2 = mock.patch.object(trainer, "services", return_value=svc)
        self.p1.start(); self.p2.start()
        self.addCleanup(self.p1.stop); self.addCleanup(self.p2.stop)

    def test_3060_direct(self):
        self._patch({1: {"free_gb": 9.0, "total_gb": 12.0},
                     0: {"free_gb": 0.5, "total_gb": 16.0}},
                    {"worker": False, "main_brain": True})
        p = trainer.plan_training(6.0)
        self.assertEqual(p["action"], "direct")
        self.assertEqual(p["gpu"], 1)
        self.assertFalse(p["fallback_used"])

    def test_3060_stop_worker(self):
        self._patch({1: {"free_gb": 2.0, "total_gb": 12.0},
                     0: {"free_gb": 0.5, "total_gb": 16.0}},
                    {"worker": True, "main_brain": True})
        p = trainer.plan_training(6.0)
        self.assertEqual(p["action"], "stop_worker")
        self.assertEqual(p["gpu"], 1)

    def test_fallback_5060_direct(self):
        self._patch({1: {"free_gb": 2.0, "total_gb": 12.0},
                     0: {"free_gb": 10.0, "total_gb": 16.0}},
                    {"worker": True, "main_brain": False})
        p = trainer.plan_training(6.0, allow_stop_worker=False)
        self.assertEqual(p["action"], "direct")
        self.assertEqual(p["gpu"], 0)
        self.assertTrue(p["fallback_used"])

    def test_fallback_5060_stop_main(self):
        self._patch({1: {"free_gb": 2.0, "total_gb": 12.0},
                     0: {"free_gb": 0.5, "total_gb": 16.0}},
                    {"worker": True, "main_brain": True})
        p = trainer.plan_training(6.0, allow_stop_worker=False, allow_stop_main=True)
        self.assertEqual(p["action"], "stop_main_brain")
        self.assertEqual(p["gpu"], 0)
        self.assertTrue(p["fallback_used"])

    def test_defer_when_no_resource(self):
        self._patch({1: {"free_gb": 2.0, "total_gb": 12.0},
                     0: {"free_gb": 0.5, "total_gb": 16.0}},
                    {"worker": True, "main_brain": True})
        p = trainer.plan_training(6.0, allow_stop_worker=False, allow_stop_main=False)
        self.assertEqual(p["action"], "defer")
        self.assertIsNone(p["gpu"])
        self.assertIn("OOM", p["reason"])

    def test_required_exceeds_both(self):
        self._patch({1: {"free_gb": 2.0, "total_gb": 12.0},
                     0: {"free_gb": 0.5, "total_gb": 16.0}},
                    {"worker": False, "main_brain": False})
        p = trainer.plan_training(40.0)
        self.assertEqual(p["action"], "defer")


class TestTrainerExecute(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="s21t_")
        self._jd = trainer.JOB_DIR
        trainer.JOB_DIR = Path(tmp)
        self.addCleanup(self._restore, tmp)

    def _restore(self, tmp):
        trainer.JOB_DIR = self._jd
        shutil.rmtree(tmp, ignore_errors=True)

    def test_dry_run_planned(self):
        p = {"action": "direct", "gpu": 1, "reason": "ok", "fallback_used": False}
        with mock.patch.object(trainer, "plan_training", return_value=p):
            r = trainer.execute_training("d.jsonl", dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "planned")

    def test_deferred(self):
        p = {"action": "defer", "gpu": None, "reason": "no resource", "fallback_used": False}
        with mock.patch.object(trainer, "plan_training", return_value=p):
            r = trainer.execute_training("d.jsonl", dry_run=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "deferred")

    def test_oom_fallback_retry(self):
        p = {"action": "direct", "gpu": 1, "reason": "ok", "fallback_used": False}
        oom = {"exit_code": 1, "log_tail": "CUDA out of memory", "duration_s": 3}
        ok = {"exit_code": 0, "log_tail": "done", "duration_s": 5}
        with mock.patch.object(trainer, "plan_training", return_value=p), \
             mock.patch.object(trainer, "_wait_vram", return_value=(True, 8.0)), \
             mock.patch.object(trainer, "_launch_train", side_effect=[oom, ok]), \
             mock.patch.object(trainer, "_service_up", return_value=False), \
             mock.patch.object(trainer, "_restore") as mrestore:
            r = trainer.execute_training("d.jsonl", allow_gpu0=True)
        self.assertTrue(r["ok"])
        self.assertTrue(r.get("oom_retry"))
        self.assertEqual(r["gpu"], 0)
        self.assertIn("restored", r)
        mrestore.assert_called()


class TestAutotune(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="s21a_")
        self.tmp = Path(tmp)
        self._paths = {
            "tj": (tj.TRAJ_DIR, tj.TRAJ_FILE),
            "qa": (qa.QUALITY_DIR, qa.PASS_FILE, qa.REJECT_FILE),
            "at": (at.AUTO_DIR, at.DATA_DIR, at.JOBS_FILE),
        }
        tj.TRAJ_DIR = self.tmp
        tj.TRAJ_FILE = self.tmp / "traj.jsonl"
        qa.QUALITY_DIR = self.tmp / "q"
        qa.PASS_FILE = qa.QUALITY_DIR / "passed.jsonl"
        qa.REJECT_FILE = qa.QUALITY_DIR / "rejected.jsonl"
        at.AUTO_DIR = self.tmp / "auto"
        at.DATA_DIR = self.tmp / "data"
        at.JOBS_FILE = at.AUTO_DIR / "jobs.jsonl"
        self.addCleanup(self._restore, tmp)
        for i in range(3):
            t = tj.build_trajectory("任务%d" % i, "高质量答案" * 40, provider="deepseek",
                                    model="deepseek-flash", task_type="rag",
                                    failover={"trigger": "local_failed"})
            tj.record(t)

    def _restore(self, tmp):
        tj.TRAJ_DIR, tj.TRAJ_FILE = self._paths["tj"]
        qa.QUALITY_DIR, qa.PASS_FILE, qa.REJECT_FILE = self._paths["qa"]
        at.AUTO_DIR, at.DATA_DIR, at.JOBS_FILE = self._paths["at"]
        shutil.rmtree(tmp, ignore_errors=True)

    def test_candidates_and_check(self):
        p = {"action": "stop_worker", "gpu": 1, "reason": "ok", "fallback_used": False}
        with mock.patch.object(trainer, "plan_training", return_value=p):
            c = at.check(dry_run=True)
        self.assertGreaterEqual(c["quality"]["passed"], 3)
        self.assertIn("rag", [x["task_type"] for x in c["candidates"] if x["eligible"]])
        self.assertEqual(c["plan"]["action"], "stop_worker")

    def test_trigger_dry_run(self):
        fake = {"ok": True, "status": "planned", "job": "tr_x"}
        with mock.patch.object(trainer, "execute_training", return_value=fake) as me:
            job = at.trigger(task_type="rag", dry_run=True, lora_name="auto_rag_test")
        self.assertTrue(job["ok"])
        self.assertEqual(job["status"], "planned")
        self.assertEqual(job["samples"], 3)
        self.assertTrue(Path(job["dataset"]).exists())
        me.assert_called_once()
        self.assertTrue(at.list_jobs())

    def test_trigger_not_eligible(self):
        job = at.trigger(task_type="unknown_type", dry_run=True)
        self.assertFalse(job["ok"])
        self.assertEqual(job["status"], "not_eligible")

    def test_stats(self):
        s = at.stats()
        self.assertIn("config", s)
        self.assertIn("quality", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
