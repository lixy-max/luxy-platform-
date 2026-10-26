# -*- coding: utf-8 -*-
"""S14 测试：任务表对话隔离 + 外部能力参数推断"""
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


def _items(n=1):
    return [{"content": "测试任务 %d" % i, "category": "local", "target": "direct",
             "reason": "", "depends_on": []} for i in range(1, n + 1)]


class TestConversationIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="conv_ut_"))
        self._orig = rt.DB_PATH
        rt.DB_PATH = self.tmp / "route_tables.db"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, rt, "DB_PATH", self._orig)
        # 禁用工作流复用，保证走 items 分支
        self._env = mock.patch.dict(os.environ, {"WORKFLOW_REUSE": "0"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_create_and_list_isolated(self):
        a = rt.create_table("对话A的任务", items=_items(2), conversation_id="convA")
        b = rt.create_table("对话B的任务", items=_items(1), conversation_id="convB")
        la = rt.list_tables(conversation_id="convA")
        lb = rt.list_tables(conversation_id="convB")
        allt = rt.list_tables()
        self.assertEqual([x["id"] for x in la], [a["id"]])
        self.assertEqual([x["id"] for x in lb], [b["id"]])
        self.assertGreaterEqual(len(allt), 2)
        self.assertEqual(la[0]["conversation_id"], "convA")

    def test_search_isolated(self):
        rt.create_table("LoRA 训练统计任务", items=_items(1), conversation_id="convA")
        rt.create_table("LoRA 训练统计任务2", items=_items(1), conversation_id="convB")
        ra = rt.search_tables("LoRA 训练统计", conversation_id="convA")
        rb = rt.search_tables("LoRA 训练统计", conversation_id="convB")
        self.assertTrue(ra and all("convA" not in x["id"] for x in ra))
        self.assertEqual(len(ra), 1)
        self.assertEqual(len(rb), 1)
        self.assertNotEqual(ra[0]["id"], rb[0]["id"])

    def test_get_table_has_conversation(self):
        t = rt.create_table("隔离表", items=_items(1), conversation_id="convX")
        got = rt.get_table(t["id"])
        self.assertEqual(got["conversation_id"], "convX")

    def test_clone_keeps_conversation(self):
        t = rt.create_table("原始表", items=_items(1), conversation_id="convA")
        c = rt.clone_table(t["id"])
        self.assertEqual(c["conversation_id"], "convA")
        self.assertEqual(len(rt.list_tables(conversation_id="convA")), 2)

    def test_list_conversations(self):
        rt.create_table("A1", items=_items(1), conversation_id="convA")
        rt.create_table("A2", items=_items(1), conversation_id="convA")
        rt.create_table("B1", items=_items(1), conversation_id="convB")
        convs = {x["conversation_id"]: x["tables"] for x in rt.list_conversations()}
        self.assertEqual(convs.get("convA"), 2)
        self.assertEqual(convs.get("convB"), 1)


class TestCapabilityInput(unittest.TestCase):
    def _cap(self, schema):
        return {"cap_id": "test", "args_schema": schema}

    def test_text_schema(self):
        cap = self._cap({"positional": ["text"], "chunk_size": "x"})
        out = rt.build_capability_input(cap, "hello world", "prev")
        self.assertEqual(out["text"], "hello world\n\n前序结果：\nprev")

    def test_data_schema(self):
        cap = self._cap({"positional": ["data"], "headers": "x"})
        out = rt.build_capability_input(cap, "任务", "第一行\n第二行")
        self.assertEqual(out["data"], [["第一行"], ["第二行"]])
        self.assertEqual(out["headers"], ["内容"])

    def test_pdf_path_schema(self):
        cap = self._cap({"positional": ["pdf_path"], "max_pages": "x"})
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        f = tmp / "a.pdf"
        f.write_bytes(b"%PDF-1.4 test")
        out = rt.build_capability_input(cap, "提取 %s 的文本" % f, "")
        self.assertEqual(out["pdf_path"], str(f))
        self.assertEqual(out["max_pages"], 3)

    def test_docx_named_schema(self):
        cap = self._cap({"out_path": "x", "title": "x", "sections": "x"})
        out = rt.build_capability_input(cap, "生成报告", "内容一\n内容二")
        self.assertTrue(out["out_path"].endswith(".docx"))
        self.assertEqual(out["title"], "生成报告")
        self.assertTrue(out["sections"][0]["lines"])

    def test_json_args_in_content(self):
        cap = self._cap({"positional": ["text"]})
        out = rt.build_capability_input(cap, '{"chunk_size": 100}', "")
        self.assertEqual(out["chunk_size"], 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
