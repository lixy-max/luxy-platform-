# -*- coding: utf-8 -*-
"""B014 测试：workflow_engine tool 节点（真实工具调用）+ 轨迹映射 tool 节点"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workflow_engine as we
import trajectory_distill as td


def _tool_cfg():
    return {"workflow_name": "tool 测试", "nodes": [
        {"id": "n0", "type": "input", "input_from": [], "prompt": ""},
        {"id": "n1", "type": "tool", "input_from": ["n0"], "tool": "paper_lora_list_loras",
         "tool_args": {}, "tool_timeout": 30},
        {"id": "n2", "type": "aggregate", "input_from": ["n1"], "prompt": "总结 {n1}"},
    ]}


class TestToolNodeSpec(unittest.TestCase):
    def test_tool_registered(self):
        self.assertIn("tool", we.NODE_TYPES)
        self.assertIn("tool", we.NODE_TYPE_SPECS)
        self.assertIn("paper_lora_list_loras", we.TOOL_NODE_SUPPORTED)

    def test_lint_accepts_tool(self):
        errors, warnings = we.lint_config(_tool_cfg())
        self.assertEqual(errors, [], errors)

    def test_lint_unknown_field_ok(self):
        # tool_args / tool_timeout 必须是引擎认识字段
        errors, _ = we.lint_config(_tool_cfg())
        self.assertEqual(errors, [])

    def test_unknown_tool_not_in_registry(self):
        self.assertNotIn("no_such_tool", we.TOOL_NODE_SUPPORTED)


class TestCallPlatformTool(unittest.TestCase):
    def test_get_mocked(self):
        fake = mock.Mock()
        fake.json.return_value = {"data": [{"lora": "a"}]}
        fake.raise_for_status.return_value = None
        with mock.patch("requests.get", return_value=fake) as m:
            out = we.call_platform_tool("paper_lora_list_loras", {}, {})
        self.assertIn("lora", out)
        self.assertTrue(m.called)

    def test_post_kb_query_mocked(self):
        fake = mock.Mock()
        fake.json.return_value = {"results": [{"text": "x"}]}
        fake.raise_for_status.return_value = None
        ctx = {"nodes": {"n0": "问题文本"}, "full_text": "问题文本"}
        with mock.patch("requests.post", return_value=fake) as m:
            out = we.call_platform_tool("paper_lora_kb_query",
                                        {"kb_name": "bcpcn", "query": "{n0}", "top_k": 2}, ctx)
        self.assertIn("results", out)
        args, kwargs = m.call_args
        self.assertIn("/kb/bcpcn/query", args[0])
        self.assertEqual(kwargs["json"]["top_k"], 2)

    def test_unknown_tool_raises(self):
        with self.assertRaises(ValueError):
            we.call_platform_tool("no_such_tool", {}, {})

    def test_render_placeholders(self):
        ctx = {"nodes": {"n0": "上游结果"}}
        v = we._render_tool_value({"query": "基于 {n0} 回答"}, ctx)
        self.assertEqual(v["query"], "基于 上游结果 回答")


class TestTrajectoryMapping(unittest.TestCase):
    REC = {
        "task": "先查 LoRA 列表再查状态", "reason": "completed", "final": "结论",
        "tools_used": ["paper_lora_list_loras", "paper_lora_status"],
        "tool_errors": [], "steps": [
            {"turn": 1, "tool_calls": [{"tool": "paper_lora_list_loras", "input": {},
                                        "result": "16", "status": "completed"}]},
            {"turn": 2, "tool_calls": [{"tool": "paper_lora_status", "input": {},
                                        "result": "running", "status": "completed"}]},
            {"turn": 3, "text": "综合分析", "tool_calls": []},
        ],
        "_source": "test.jsonl", "_index": 0,
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="toolnode_ut_"))
        self._orig = (td.DISTILL_DIR, td.WF_DIR, td.WF_INDEX)
        td.DISTILL_DIR = self.tmp / "distilled"
        td.WF_DIR = td.DISTILL_DIR / "workflows"
        td.WF_INDEX = td.DISTILL_DIR / "workflows.jsonl"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for k, v in zip(("DISTILL_DIR", "WF_DIR", "WF_INDEX"), self._orig):
            self.addCleanup(setattr, td, k, v)

    def test_tool_nodes_generated(self):
        wf = td.workflow_from_record(self.REC)
        types = [n["type"] for n in wf["config"]["nodes"]]
        self.assertIn("tool", types)
        tools = [n.get("tool") for n in wf["config"]["nodes"] if n["type"] == "tool"]
        self.assertIn("paper_lora_list_loras", tools)
        self.assertIn("paper_lora_status", tools)
        self.assertEqual(types[-1], "aggregate")
        self.assertEqual(wf["tool_nodes"], 2)
        self.assertEqual(wf["lint"]["errors"], [])

    def test_unknown_tool_falls_back_to_process(self):
        rec = dict(self.REC)
        rec["steps"] = [{"turn": 1, "tool_calls": [{"tool": "custom_unknown_tool",
                                                    "input": {}, "result": "x"}]}]
        rec["tools_used"] = ["custom_unknown_tool"]
        wf = td.workflow_from_record(rec)
        types = [n["type"] for n in wf["config"]["nodes"]]
        self.assertNotIn("tool", types)
        self.assertEqual(types[-1], "aggregate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
