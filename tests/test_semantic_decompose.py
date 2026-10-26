# -*- coding: utf-8 -*-
"""B013 测试：语义拆步（结构标记/段落/思考步骤/v1 回退/统计对照）"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trajectory_distill as td


class TestSemanticSplit(unittest.TestCase):
    def test_numbered_sections(self):
        text = ("1. 先检查 LoRA 列表：共 16 个。\n"
                "2. 再核对训练记录：3 个有记录。\n"
                "3. 结论：13 个没有训练记录。")
        parts = td._semantic_split(text)
        self.assertGreaterEqual(len(parts), 2)

    def test_markdown_headings(self):
        text = ("## 检查结果\nLoRA 列表共 16 个。\n\n"
                "## 训练记录\n只有 3 个有记录，对应 3 个数据集。\n\n"
                "## 建议\n补录缺失的 13 个。")
        parts = td._semantic_split(text)
        self.assertGreaterEqual(len(parts), 2)

    def test_short_text_single(self):
        self.assertEqual(len(td._semantic_split("好的，已完成。")), 1)

    def test_long_paragraph_split(self):
        p1 = "第一段内容：" + "说明测试情况。" * 10
        p2 = "第二段内容：" + "给出最终建议。" * 10
        parts = td._semantic_split(p1 + "\n\n" + p2)
        self.assertGreaterEqual(len(parts), 2)


class TestDecomposeSemantic(unittest.TestCase):
    REC = {
        "task": "核对 LoRA 与训练记录",
        "reason": "completed",
        "steps": [
            {"turn": 1, "text": "我先查 LoRA 列表和训练记录，然后交叉核对哪些没有训练记录。",
             "tool_calls": [{"tool": "paper_lora_list_loras", "input": {}, "result": "16 loras"},
                            {"tool": "paper_lora_train_list", "input": {}, "result": "3 tasks"}]},
            {"turn": 2, "text": "1. LoRA 共 16 个。\n2. 训练记录 3 条。\n3. 13 个没有记录。",
             "tool_calls": []},
        ],
        "final": "## 结论\n13 个 LoRA 没有训练记录。\n\n## 建议\n补录训练数据。",
        "_source": "t.jsonl", "_index": 0,
    }

    def test_semantic_default(self):
        steps = td.decompose(self.REC)
        kinds = [s["kind"] for s in steps]
        self.assertIn("tool", kinds)
        self.assertTrue(any(s.get("semantic") for s in steps))
        self.assertTrue(any(s.get("label") for s in steps))

    def test_thinking_step_kept(self):
        steps = td.decompose(self.REC)
        self.assertTrue(any(s.get("origin") == "thinking" for s in steps))

    def test_v1_fallback(self):
        steps = td.decompose(self.REC, semantic=False)
        self.assertFalse(any(s.get("semantic") for s in steps))
        # v1: 2 tool + 1 text + 1 answer = 4
        self.assertEqual(len(steps), 4)

    def test_semantic_more_steps_than_v1(self):
        sem = td.decompose(self.REC)
        v1 = td.decompose(self.REC, semantic=False)
        self.assertGreater(len(sem), len(v1))

    def test_env_switch(self):
        os.environ["DECOMPOSE_SEMANTIC"] = "0"
        try:
            steps = td.decompose(self.REC)
        finally:
            os.environ.pop("DECOMPOSE_SEMANTIC", None)
        self.assertFalse(any(s.get("semantic") for s in steps))

    def test_no_marker_text_stays_single(self):
        rec = {"task": "x", "reason": "completed",
               "steps": [{"turn": 1, "text": "短说明", "tool_calls": []}],
               "final": "完成"}
        steps = td.decompose(rec)
        self.assertEqual(len([s for s in steps if s["kind"] == "llm"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
