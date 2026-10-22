# -*- coding: utf-8 -*-
"""
A4 拼行（de-wrap）的测试。重点同样是**不误拼** —— 误拼会把两段不同内容粘在一起，
比留碎片更糟（它与 A1 的"不误删"是同一类风险）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _guards  # noqa: F401
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build_extractive_dataset as bd  # noqa: E402


class TestJoinWrapped(unittest.TestCase):

    def test_joins_lowercase_continuation(self):
        t = "This paper studies a new method\nfor training models efficiently."
        out = bd.join_wrapped_lines(t)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("method for training", out)

    def test_dehyphenates_at_line_end(self):
        t = "We use a distilla-\ntion objective here."
        out = bd.join_wrapped_lines(t)
        self.assertIn("distillation objective", out)
        self.assertNotIn("distilla-", out)

    def test_does_not_join_capital_start(self):
        """下一行以大写开头 -> 可能是新段落/标题，不拼。"""
        t = "We present results\nThe Model section describes it."
        out = bd.join_wrapped_lines(t)
        self.assertEqual(len(out.splitlines()), 2)

    def test_does_not_join_after_sentence_end(self):
        t = "This is a complete sentence.\nand this line starts lowercase."
        out = bd.join_wrapped_lines(t)
        self.assertEqual(len(out.splitlines()), 2, "上一行已结句就不该拼")

    def test_blank_line_breaks(self):
        t = "some text here\n\nanother paragraph starts."
        out = bd.join_wrapped_lines(t)
        self.assertEqual(len(out.splitlines()), 3)

    def test_does_not_join_table_rows(self):
        t = "Model F1 BLEU Acc EM\n12.3 4.5 6.7 8.9 1.2"
        out = bd.join_wrapped_lines(t)
        self.assertEqual(len(out.splitlines()), 2, "数字占比高的行不拼")

    def test_does_not_join_after_colon(self):
        t = "The results are as follows:\nthe first is better."
        out = bd.join_wrapped_lines(t)
        self.assertEqual(len(out.splitlines()), 2)

    def test_empty_and_none(self):
        self.assertEqual(bd.join_wrapped_lines(""), "")
        self.assertIsNone(bd.join_wrapped_lines(None))

    def test_char_loss_is_small(self):
        """拼行几乎不该丢字符（只去掉行末连字符与换行）。"""
        t = "alpha beta gamma\ndelta epsilon zeta\ntheta iota kappa."
        out = bd.join_wrapped_lines(t)
        a = len(t.replace("\n", " "))
        b = len(out)
        self.assertGreaterEqual(b, a - 3)


if __name__ == "__main__":
    unittest.main()
