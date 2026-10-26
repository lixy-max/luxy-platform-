# -*- coding: utf-8 -*-
"""S5 命中即执行测试：检索命中→执行工作流→结果回任务表 + 聊天快路径"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import route_table as rt
import trajectory_distill as td

HIT = {"wf_id": "wf_test1", "task": "核对 LoRA 训练记录", "path": "/tmp/wf_test1.json",
       "workflow_name": "测试工作流", "score": 0.82, "steps": 5}


def _fake_ok(content="工作流结果"):
    return {"ok": True, "content": content, "path": "/tmp/result.json",
            "duration": 1.2, "status": "success", "wf_id": "wf_test1"}


class TestRouteTableReuse(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reuse_ut_"))
        self._orig = (rt.DB_PATH, rt.ARTIFACT_DIR)
        rt.DB_PATH = self.tmp / "rt.db"
        rt.ARTIFACT_DIR = self.tmp / "artifacts"
        self._embed = mock.patch.object(rt, "_embed", return_value=None)
        self._embed.start()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, rt, "DB_PATH", self._orig[0])
        self.addCleanup(setattr, rt, "ARTIFACT_DIR", self._orig[1])
        self.addCleanup(self._embed.stop)

    def test_create_hit_uses_workflow_item(self):
        with mock.patch("trajectory_distill.search_workflows", return_value=[HIT]), \
             mock.patch.dict(os.environ, {"WORKFLOW_REUSE": "1"}):
            t = rt.create_table("核对 LoRA 训练记录")
        self.assertEqual(t["items_count"], 1)
        self.assertEqual(t["items"][0]["target"], "workflow:wf_test1")
        self.assertEqual((t["meta"].get("reuse_workflow") or {}).get("wf_id"), "wf_test1")

    def test_below_threshold_falls_back_to_brain(self):
        low = dict(HIT, score=0.3)
        items = [{"content": "直接答", "category": "local", "target": "direct"}]
        with mock.patch("trajectory_distill.search_workflows", return_value=[low]), \
             mock.patch.object(rt, "generate_items", return_value=items) as m, \
             mock.patch.dict(os.environ, {"WORKFLOW_REUSE": "1"}):
            t = rt.create_table("一个全新的任务")
        self.assertTrue(m.called)
        self.assertEqual(t["items"][0]["target"], "direct")

    def test_reuse_disabled(self):
        items = [{"content": "直接答", "category": "local", "target": "direct"}]
        with mock.patch("trajectory_distill.search_workflows", return_value=[HIT]), \
             mock.patch.object(rt, "generate_items", return_value=items) as m, \
             mock.patch.dict(os.environ, {"WORKFLOW_REUSE": "0"}):
            rt.create_table("核对 LoRA 训练记录")
        self.assertTrue(m.called)

    def test_execute_workflow_item(self):
        with mock.patch("trajectory_distill.search_workflows", return_value=[HIT]), \
             mock.patch("trajectory_distill.run_workflow", return_value=_fake_ok()), \
             mock.patch.dict(os.environ, {"WORKFLOW_REUSE": "1"}):
            t = rt.create_table("核对 LoRA 训练记录")
            r = rt.execute_table(t["id"])
        self.assertEqual(r["table"]["status"], "done")
        it = r["table"]["items"][0]
        self.assertEqual(it["status"], "done")
        self.assertIn("工作流结果", it["result"])
        self.assertTrue(rt.get_artifact(t["id"])["exists"])


class TestSwarmFastPath(unittest.TestCase):
    def test_orchestrate_hits_workflow(self):
        import swarm
        with mock.patch("trajectory_distill.search_workflows", return_value=[HIT]), \
             mock.patch("trajectory_distill.run_workflow", return_value=_fake_ok("本地工作流输出")), \
             mock.patch.object(swarm, "ask_brain") as brain, \
             mock.patch.dict(os.environ, {"WORKFLOW_REUSE": "1"}):
            r = swarm.orchestrate("核对 LoRA 训练记录")
        self.assertTrue(r["ok"])
        self.assertEqual(r["route"], "workflow")
        self.assertIn("本地工作流输出", r["content"])
        self.assertFalse(brain.called, "命中后不应再调主脑")

    def test_orchestrate_miss_uses_brain(self):
        import swarm
        low = dict(HIT, score=0.2)
        brain_out = json.dumps({"route": "direct", "kb_names": [], "capability_id": None,
                                "need_worker": False, "load_lora": False, "reason": "普通问答"})
        with mock.patch("trajectory_distill.search_workflows", return_value=[low]), \
             mock.patch.object(swarm, "ask_brain", return_value=brain_out), \
             mock.patch.object(swarm, "main_answer", return_value="直答内容"), \
             mock.patch.dict(os.environ, {"WORKFLOW_REUSE": "1"}):
            r = swarm.orchestrate("1+1 等于几")
        self.assertEqual(r["route"], "direct")
        self.assertEqual(r["content"], "直答内容")


class TestRunWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="runwf_ut_"))
        self._orig = (td.DISTILL_DIR, td.WF_RUN_DIR if hasattr(td, "WF_RUN_DIR") else None)
        td.DISTILL_DIR = self.tmp
        if hasattr(td, "WF_RUN_DIR"):
            td.WF_RUN_DIR = self.tmp / "runs"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, td, "DISTILL_DIR", self._orig[0])
        if hasattr(td, "WF_RUN_DIR"):
            self.addCleanup(setattr, td, "WF_RUN_DIR", self._orig[1])

    def test_run_workflow_parses_result(self):
        cfg = self.tmp / "wf.json"
        cfg.write_text("{}", encoding="utf-8")
        result = self.tmp / "result.json"
        result.write_text(json.dumps({"status": "success", "final_report": "最终报告",
                                      "nodes": {"n1": "a"}, "_meta": {"usage": {"vllm": 1}}}),
                          encoding="utf-8")
        with mock.patch.object(td, "get_workflow",
                               return_value={"wf_id": "wf1", "path": str(cfg),
                                             "workflow_name": "测试"}), \
             mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(stdout=json.dumps(str(result)), stderr="", returncode=0)
            r = td.run_workflow("wf1", input_text="hello")
        self.assertTrue(r["ok"])
        self.assertEqual(r["content"], "最终报告")
        self.assertEqual(r["status"], "success")

    def test_run_workflow_missing(self):
        with mock.patch.object(td, "get_workflow", return_value=None):
            with self.assertRaises(ValueError):
                td.run_workflow("nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
