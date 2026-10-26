# -*- coding: utf-8 -*-
"""S16 测试：编排路由/工具选择/主脑执行/tool:write_sheet/上下文注入"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import orchestrator as orch
import route_table as rt


class TestOrchestratorFixes(unittest.TestCase):
    def test_external_source_routes_cloud(self):
        for task in ("从京东和淘宝抓取价格做采购清单", "用 deepseek 生成采购清单目标结果",
                     "在阿里巴巴上查供应商报价"):
            r = orch.route_model(task)
            self.assertEqual(r["model"], "cloud", task)

    def test_workflow_tools_prioritized(self):
        plan = orch.build_plan("做一个自动生成采购清单excel的工作流")
        tools = []
        for p in plan:
            if p.get("action") == "call_tool" and isinstance(p.get("target"), list):
                tools = p["target"]
        self.assertTrue(tools)
        for need in ("create_workflow_config", "add_workflow_node", "write_sheet"):
            self.assertIn(need, tools)
        self.assertLessEqual(len(tools), 15)

    def test_plain_task_not_forced(self):
        plan = orch.build_plan("列出当前 LoRA")
        tools = []
        for p in plan:
            if p.get("action") == "call_tool" and isinstance(p.get("target"), list):
                tools = p["target"]
        self.assertNotIn("create_workflow_config", tools)


class TestWriteSheetTarget(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s16_ut_"))
        self._orig = rt.DB_PATH
        rt.DB_PATH = self.tmp / "rt.db"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, rt, "DB_PATH", self._orig)
        self._env = mock.patch.dict(os.environ, {"WORKFLOW_REUSE": "0"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_rows_from_json(self):
        rows, header = rt._rows_from_ctx_or_json('[["1","积木"],["2","拼图"]]', "")
        self.assertEqual(rows[0], ["1", "积木"])
        self.assertEqual(len(header), 2)

    def test_rows_from_ctx_lines(self):
        rows, header = rt._rows_from_ctx_or_json("整理清单", "商品A\n商品B")
        self.assertEqual(rows, [["商品A"], ["商品B"]])
        self.assertEqual(header, ["内容"])

    def test_tool_write_sheet_mocked(self):
        item = {"content": "生成采购清单：[[\"1\",\"积木\"]]", "category": "local",
                "target": "tool:write_sheet"}
        fake = mock.Mock()
        fake.json.return_value = {"filename": "采购清单.xlsx", "rows": 2, "cols": 2}
        fake.raise_for_status.return_value = None
        with mock.patch("requests.post", return_value=fake) as m:
            ok, result, how = rt._local_execute(item, "前序结果", task="采购清单工作流")
        self.assertTrue(ok)
        self.assertEqual(how, "tool")
        self.assertIn("采购清单.xlsx", result)
        self.assertIn("/docs/write_sheet", m.call_args.args[0])

    def test_unknown_tool_fails_honestly(self):
        ok, result, how = rt._local_execute(
            {"content": "x", "category": "local", "target": "tool:no_such"}, "", task="t")
        self.assertFalse(ok)
        self.assertIn("未知平台工具", result)

    def test_direct_context_injected(self):
        item = {"content": "提取需求", "category": "local", "target": "direct"}
        seen = {}

        def fake_execute(dec, content):
            seen["content"] = content
            return {"ok": True, "content": "ok"}

        with mock.patch("swarm.execute", side_effect=fake_execute):
            rt._local_execute(item, "前序", task="原始采购任务")
        self.assertIn("原始采购任务", seen["content"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
