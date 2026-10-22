# -*- coding: utf-8 -*-
"""
PDF 脏数据清洗（A1）的测试。

规则都来自真实语料统计。这里最要紧的是**不误删正文** ——
清洗过头会把论文内容吃掉，比留垃圾更糟。
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
import lora_manager as lm  # noqa: E402


class TestStripJunkRemoves(unittest.TestCase):

    def test_template_tags(self):
        t = "Before <think> we reason </think> after <action> act </action> end."
        out = lm.strip_pdf_junk(t)
        self.assertNotIn("<think>", out)
        self.assertNotIn("<action>", out)
        self.assertIn("Before", out)
        self.assertIn("end.", out)

    def test_role_tags_with_gt_lt(self):
        out = lm.strip_pdf_junk(">system< you are an agent >user< hello")
        self.assertNotIn(">system<", out)
        self.assertNotIn(">user<", out)
        self.assertIn("hello", out)

    def test_cid_font_junk(self):
        out = lm.strip_pdf_junk("value (cid:64) threshold (cid:12) done")
        self.assertNotIn("(cid:", out)
        self.assertIn("threshold", out)

    def test_repeated_header_footer(self):
        """像 'Preprint' 这种重复 41 次的页眉必须清掉，但正文里的 'Preprint' 若单独出现要保留。"""
        body = "\n".join(["正文段落 %d：这是真正的论文内容，长度足够。" % i for i in range(3)])
        t = "\n".join(["Preprint"] * 8 + [body])
        out = lm.strip_pdf_junk(t)
        self.assertNotIn("Preprint", out)
        self.assertIn("正文段落 0", out)

    def test_page_numbers_when_paginated(self):
        t = "\n".join(["1", "real paragraph one is here", "2", "real paragraph two is here",
                        "3", "real paragraph three is here", "4", "5", "6"])
        out = lm.strip_pdf_junk(t)
        self.assertNotIn("\n3\n", out)
        self.assertIn("real paragraph one", out)

    def test_single_letter_lines(self):
        t = "\n".join(["s"] * 6 + ["This is a real sentence with words."])
        out = lm.strip_pdf_junk(t)
        self.assertNotIn("\ns\n", out)
        self.assertIn("real sentence", out)

    def test_separator_lines(self):
        out = lm.strip_pdf_junk("above\n===========\nbelow")
        self.assertNotIn("=====", out)
        self.assertIn("above", out)
        self.assertIn("below", out)

    def test_interleaved_garbage_line(self):
        junk = "w I i a th m a l o m o i k c i n in g t f h o e r e co a l r o p r h b o l n u e e s"
        out = lm.strip_pdf_junk("good line here\n" + junk + "\nnext good line")
        self.assertNotIn("th m a l o m", out)
        self.assertIn("good line here", out)
        self.assertIn("next good line", out)


class TestStripJunkKeeps(unittest.TestCase):
    """不误删正文 —— 这组比上面那组更重要。"""

    def test_keeps_normal_prose(self):
        t = ("Transformer 完全基于注意力机制，摒弃了循环结构。"
             "The model was trained on WMT 2014 English-German with 4.5M sentence pairs.")
        self.assertIn("Transformer", lm.strip_pdf_junk(t))
        self.assertIn("4.5M sentence pairs", lm.strip_pdf_junk(t))

    def test_keeps_single_occurrence_short_line(self):
        """只出现一次的行不是页眉页脚，不能删。"""
        t = "Chapter 1\nThis chapter introduces the problem in detail with enough words."
        out = lm.strip_pdf_junk(t)
        self.assertIn("Chapter 1", out)

    def test_keeps_short_numeric_when_not_paginated(self):
        """没有分页特征的文档里，两三位的数字行可能是表格数据，不能删。"""
        out = lm.strip_pdf_junk("128\n256\n512")
        self.assertIn("128", out)
        self.assertIn("512", out)

    def test_keeps_legitimate_angle_brackets(self):
        """语法论文里 <string> 可能是正文内容 —— 不在已知模板标签列表里就保留。"""
        out = lm.strip_pdf_junk("The type <string> is used for text values.")
        self.assertIn("<string>", out)

    def test_keeps_line_with_real_words_even_if_repeated(self):
        """重复但含实词的行（例如反复出现的术语）不删。"""
        t = "\n".join(["KV Size = 5%"] * 6 + ["正文内容在这里，长度足够。"])
        out = lm.strip_pdf_junk(t)
        self.assertIn("KV Size", out, "含实词且像参数说明，删了会丢信息")

    def test_empty_and_none(self):
        self.assertEqual(lm.strip_pdf_junk(""), "")
        self.assertIsNone(lm.strip_pdf_junk(None))


if __name__ == "__main__":
    unittest.main()
