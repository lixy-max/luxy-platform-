# -*- coding: utf-8 -*-
"""S4 测试：Bug 台账 / 轨迹拆步 / 工作流生成 / 数据集 / 复用检索"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bug_ledger as bl
import trajectory_distill as td


class TestBugLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bugs_ut_"))
        self._orig = (bl.LEDGER, bl.DOC)
        bl.LEDGER = self.tmp / "ledger.jsonl"
        bl.DOC = self.tmp / "BUG_LEDGER.md"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, bl, "LEDGER", self._orig[0])
        self.addCleanup(setattr, bl, "DOC", self._orig[1])

    def test_add_list(self):
        r = bl.add("测试 bug", area="test", detail="d", severity="P1")
        self.assertTrue(r["id"].startswith("B"))
        rows = bl.list_bugs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "parked")
        self.assertEqual(rows[0]["severity"], "P1")

    def test_update_status(self):
        r = bl.add("测试 bug2")
        bl.update(r["id"], status="fixed", note="done")
        rows = bl.list_bugs()
        self.assertEqual(rows[0]["status"], "fixed")

    def test_stats(self):
        bl.add("a")
        bl.add("b")
        bl.update("B001", status="fixed")
        s = bl.stats()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["by_status"].get("fixed"), 1)
        self.assertEqual(s["parked"], 1)

    def test_render_doc(self):
        bl.add("文档 bug", detail="详情")
        p = bl.render_doc()
        self.assertTrue(Path(p).exists())
        self.assertIn("文档 bug", Path(p).read_text(encoding="utf-8"))

    def test_invalid_update(self):
        with self.assertRaises(ValueError):
            bl.update("B999", status="fixed")


REC = {
    "task": "先用 list_loras 再查 vllm 状态，判断能否挂 LoRA",
    "reason": "completed", "error": None, "final": "结论：可以挂载，依据如下……",
    "tools_used": ["paper_lora_list_loras", "paper_lora_status"],
    "tool_errors": [],
    "steps": [
        {"turn": 1, "text": "先查 LoRA 列表",
         "tool_calls": [{"tool": "paper_lora_list_loras", "input": {}, "result": "16 个 LoRA", "status": "completed"}]},
        {"turn": 2, "text": "再查 vLLM 状态",
         "tool_calls": [{"tool": "paper_lora_status", "input": {}, "result": "running", "status": "completed"}]},
        {"turn": 3, "text": "综合分析", "tool_calls": []},
    ],
    "_source": "test.jsonl", "_index": 0,
}


class TestTrajectoryDistill(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="td_ut_"))
        self._orig = (td.DISTILL_DIR, td.WF_DIR, td.WF_INDEX, td.DATASET_DIR)
        td.DISTILL_DIR = self.tmp / "distilled"
        td.WF_DIR = self.tmp / "distilled" / "workflows"
        td.WF_INDEX = self.tmp / "distilled" / "workflows.jsonl"
        td.DATASET_DIR = self.tmp / "training_data"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for k, v in zip(("DISTILL_DIR", "WF_DIR", "WF_INDEX", "DATASET_DIR"), self._orig):
            self.addCleanup(setattr, td, k, v)

    def test_decompose(self):
        steps = td.decompose(REC)
        self.assertEqual(len(steps), 4)  # 2 工具 + 1 文本 + 1 最终答案
        self.assertEqual(steps[0]["kind"], "tool")
        self.assertEqual(steps[0]["tool"], "paper_lora_list_loras")
        self.assertEqual(steps[-1]["kind"], "answer")
        self.assertEqual([s["seq"] for s in steps], [1, 2, 3, 4])

    def test_workflow_from_record(self):
        wf = td.workflow_from_record(REC)
        self.assertTrue(Path(wf["path"]).exists())
        self.assertEqual(wf["lint"]["errors"], [])
        self.assertGreaterEqual(wf["nodes"], 5)  # input + 4 steps
        cfg = wf["config"]
        self.assertEqual(cfg["nodes"][0]["type"], "input")
        self.assertEqual(cfg["nodes"][-1]["type"], "aggregate")
        self.assertEqual(cfg["required_loras"], [])
        self.assertTrue(any(i["input_from"] for i in cfg["nodes"][1:]))

    def test_dataset_from_record(self):
        ds = td.dataset_from_record(REC)
        self.assertEqual(ds["samples"], 4)
        self.assertEqual(ds["format"], "sharegpt")
        lines = Path(ds["train_path"]).read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertIn("conversations", rec)
        self.assertEqual(rec["conversations"][0]["role"], "user")
        self.assertTrue(Path(ds["eval_path"]).exists())

    def test_stats_small(self):
        with mock.patch.object(td, "iter_trajectories", return_value=iter([REC])):
            st = td.stats()
        self.assertEqual(st["records"], 1)
        self.assertEqual(st["steps"]["max"], 4)
        self.assertEqual(st["success_rate"], 1.0)

    def test_search_workflows(self):
        td.workflow_from_record(REC)
        with mock.patch("kb._encode", side_effect=lambda texts, **kw: [[1.0, 0.0] for _ in texts]):
            hits = td.search_workflows("查 LoRA 能否挂载", limit=3)
        self.assertTrue(hits)
        self.assertTrue(hits[0]["wf_id"])

    def test_list_workflows(self):
        td.workflow_from_record(REC)
        rows = td.list_workflows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["path"])

    def test_build_no_trajectories(self):
        with mock.patch.object(td, "iter_trajectories", return_value=iter([])):
            with self.assertRaises(ValueError):
                td.build()


if __name__ == "__main__":
    unittest.main(verbosity=2)
