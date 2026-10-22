# -*- coding: utf-8 -*-
"""真实论文抽检脚本的纯函数测试：无参考质量信号 + Markdown 生成。"""
import unittest

import spot_check as sc
import eval_lora as ev



# ---- 共享状态守卫（见 _guards.py）----
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _guards import snapshot_shared_state as _snap, \
    assert_shared_state_unchanged as _assert_same

_SHARED_BEFORE = _snap()


def tearDownModule():
    _assert_same(_SHARED_BEFORE, __name__)

class TestNoReferenceMetrics(unittest.TestCase):
    SOURCE = ("本文提出 SparseFormer，一种稀疏注意力变体。具体做法：1. 设计可学习稀疏门控；"
              "2. 动态选择键值对；3. 在 16K 上下文下验证。")

    def test_grounding_pure_extraction_is_one(self):
        out = "本文提出 SparseFormer，一种稀疏注意力变体。"
        self.assertEqual(sc.grounding_ratio(out, self.SOURCE), 1.0)

    def test_grounding_detects_fabrication(self):
        out = "本文提出 SparseFormer，并且额外引入了量子纠缠模块与联邦学习框架。"
        g = sc.grounding_ratio(out, self.SOURCE)
        self.assertLess(g, 1.0)
        self.assertGreater(g, 0.0)

    def test_grounding_all_fabricated_is_low(self):
        out = "完全无关的另一段内容，讨论的是蛋白质折叠与气候变化。"
        self.assertLess(sc.grounding_ratio(out, self.SOURCE), 0.5)

    def test_grounding_empty_output(self):
        self.assertEqual(sc.grounding_ratio("", self.SOURCE), 0.0)

    def test_sentence_reuse_full(self):
        out = "本文提出 SparseFormer，一种稀疏注意力变体。"
        self.assertEqual(sc.sentence_reuse_ratio(out, self.SOURCE), 1.0)

    def test_sentence_reuse_partial(self):
        out = "本文提出 SparseFormer，一种稀疏注意力变体。额外还做了别的实验。"
        self.assertAlmostEqual(sc.sentence_reuse_ratio(out, self.SOURCE), 0.5, places=3)

    def test_sentence_reuse_empty(self):
        self.assertEqual(sc.sentence_reuse_ratio("", self.SOURCE), 0.0)

    def test_split_sentences_chinese(self):
        self.assertEqual(len(sc.split_sentences("第一句话很长很长。第二句话也很长很长。")), 2)

    def test_split_sentences_english(self):
        sents = sc.split_sentences("This is the first sentence. And this is the second one.")
        self.assertEqual(len(sents), 2)

    def test_split_ignores_tiny_fragments(self):
        self.assertEqual(sc.split_sentences("嗯。好的。"), [])


class TestPreambleDetection(unittest.TestCase):
    def test_detects_chinese_boilerplate(self):
        self.assertTrue(sc.detect_preamble("方法部分提取如下：本文提出…"))
        self.assertTrue(sc.detect_preamble("以下为论文的方法部分：…"))
        self.assertTrue(sc.detect_preamble("好的，我提取如下：…"))

    def test_detects_english_boilerplate(self):
        self.assertTrue(sc.detect_preamble("Here is the method section: The model…"))

    def test_detects_based_on_provided_paper(self):
        # 实测中 extract_v1 真的这样开头过，必须能识别
        self.assertTrue(sc.detect_preamble(
            "Based on the provided paper, here is a detailed extraction of the method "
            "section, focusing on the core architecture of the Transformer model:"))
        self.assertTrue(sc.detect_preamble(
            "Here is a comprehensive summary of the method described in this paper."))
        self.assertTrue(sc.detect_preamble("根据论文内容，以下是方法部分的提取结果："))

    def test_clean_output(self):
        self.assertFalse(sc.detect_preamble("本文提出一种基于稀疏注意力的方法。"))
        self.assertFalse(sc.detect_preamble(
            "The Transformer follows an encoder-decoder architecture with stacked "
            "self-attention layers and point-wise fully connected layers."))

    def test_empty_is_not_preamble(self):
        self.assertFalse(sc.detect_preamble(""))


class TestScoreNoReference(unittest.TestCase):
    def test_shape_and_values(self):
        src = "本文提出的方法包括：1. 数据预处理；2. 网络结构设计。"
        m = sc.score_no_reference("本文提出的方法包括：1. 数据预处理；2. 网络结构设计。", src)
        for key in ("chars", "empty", "grounding", "sentence_reuse",
                    "preamble", "numbered_items"):
            self.assertIn(key, m)
        self.assertEqual(m["grounding"], 1.0)
        self.assertEqual(m["numbered_items"], 2)

    def test_empty_output(self):
        m = sc.score_no_reference("", "原文")
        self.assertTrue(m["empty"])
        self.assertEqual(m["grounding"], 0.0)


class TestLanguageAndFactRetention(unittest.TestCase):
    """跨语言是这里的核心难点：英文论文 → 中文摘要，字符重合会失真。"""

    SOURCE_EN = ("The encoder is composed of N = 6 identical layers with d_model = 512. "
                 "We use the FFN with inner dimension 2048 and report BLEU 28.4 on WMT 2014.")

    def test_language_of(self):
        self.assertEqual(sc.language_of("纯中文的一段话"), "zh")
        self.assertEqual(sc.language_of("This is English text only"), "en")
        self.assertEqual(sc.language_of(""), "unknown")

    def test_fact_retention_all_kept(self):
        out = "编码器由 N=6 层组成，d_model=512，FFN 内层维度 2048，BLEU 28.4。"
        ratio, n, hit = sc.number_acronym_retention(out, self.SOURCE_EN)
        # 数字 6/512/2048/28.4 + 缩写 FFN/BLEU = 6 个关键事实，全部可在原文找到
        self.assertEqual((n, hit), (6, 6))
        self.assertEqual(ratio, 1.0)

    def test_fact_retention_catches_invented_numbers(self):
        out = "编码器由 N=6 层组成，d_model=512，FFN 内层维度 4096，BLEU 35.0。"
        ratio, n, hit = sc.number_acronym_retention(out, self.SOURCE_EN)
        self.assertEqual(n, 6)
        self.assertEqual(hit, 4)              # 4096 与 35.0 是编造的
        self.assertAlmostEqual(ratio, 4 / 6, places=3)

    def test_fact_retention_no_facts_in_output(self):
        # 输出里没有数字/缩写 → 返回 None（无从判断），不能报 0.0 让人误以为全是编造
        self.assertEqual(sc.number_acronym_retention("没有数字也没有缩写。", self.SOURCE_EN),
                         (None, 0, 0))

    def test_cross_language_flagged(self):
        m = sc.score_no_reference("本文提出 N=6 层的编码器。", self.SOURCE_EN)
        self.assertTrue(m["cross_language"])
        self.assertEqual(m["output_language"], "zh")

    def test_same_language_not_flagged(self):
        m = sc.score_no_reference("The encoder has 6 layers.", self.SOURCE_EN)
        self.assertFalse(m["cross_language"])

    def test_chars_per_1k_input_exposes_under_extraction(self):
        src = "x" * 10000
        m = sc.score_no_reference("摘要" * 10, src)          # 20 字 / 10000 字输入
        self.assertAlmostEqual(m["chars_per_1k_input"], 2.0, places=1)

    def test_score_contains_new_keys(self):
        m = sc.score_no_reference("text", "source")
        for k in ("key_fact_retention", "key_facts_in_output", "key_facts_matched",
                  "output_language", "cross_language", "chars_per_1k_input"):
            self.assertIn(k, m)


class TestBuildMarkdown(unittest.TestCase):
    def _case(self):
        return [{"label": "用例A", "kind": "in-distribution", "note": "说明",
                 "content": "标题：X\n摘要：本文提出 Y。"}]

    def _results(self):
        return [{
            "label": "用例A",
            "base": {"chars": 100, "grounding": 0.7, "sentence_reuse": 0.5,
                     "preamble": True, "text": "方法部分提取如下：Y", "empty": False,
                     "key_fact_retention": 0.6, "chars_per_1k_input": 50.0,
                     "output_language": "zh", "cross_language": True},
            "lora": {"chars": 50, "grounding": 1.0, "sentence_reuse": 1.0,
                     "preamble": False, "text": "Y", "empty": False,
                     "key_fact_retention": 1.0, "chars_per_1k_input": 25.0,
                     "output_language": "zh", "cross_language": True},
        }]

    def test_markdown_contains_key_sections(self):
        md = sc.build_markdown("lora_x", "/base", self._case(), self._results(),
                               {"temperature": 0.0, "max_tokens": 1024})
        self.assertIn("# LoRA 真实论文抽检", md)
        self.assertIn("结论速览（自动指标）", md)
        self.assertIn("人工评分表（请填写）", md)
        self.assertIn("用例 1：用例A", md)
        self.assertIn("### 底座输出", md)
        self.assertIn("### LoRA输出", md)
        self.assertIn("关键事实保留", md)
        self.assertIn("每千字输入产出", md)

    def test_markdown_explains_cross_language_caveat(self):
        md = sc.build_markdown("lora_x", "/base", self._case(), self._results(), {})
        self.assertIn("失真", md)

    def test_markdown_shows_preamble_flag(self):
        md = sc.build_markdown("lora_x", "/base", self._case(), self._results(), {})
        # 套话一列应为「底座 是 / LoRA 否」
        row = [ln for ln in md.splitlines() if ln.startswith("| 1 | 用例A")][0]
        self.assertIn("| 是 / 否 |", row)
        # 关键事实保留应成对展示
        self.assertIn("0.6 → **1.0**", row)

    def test_markdown_truncates_long_content(self):
        case = [{"label": "长", "kind": "k", "note": "", "content": "字" * 5000}]
        md = sc.build_markdown("l", "/b", case, self._results(), {})
        self.assertIn("已截断", md)



class TestSharedMetrics(unittest.TestCase):
    """
    S5：spot_check 与引擎共用同一份度量实现。
    这条测试防的是"两份实现各自漂移" —— 合并前后必须逐字节等价。
    """

    def test_same_module_object(self):
        import text_metrics as tm
        self.assertIs(sc.grounding_ratio, tm.grounding_ratio)
        self.assertIs(sc.number_acronym_retention, tm.number_acronym_retention)
        self.assertIs(sc.language_of, tm.language_of)

    def test_results_agree_on_samples(self):
        import text_metrics as tm
        src = "The encoder has N = 6 layers, d_model = 512, BLEU 28.4 on WMT."
        for out in ("编码器有 N=6 层，d_model=512，BLEU 28.4。",
                    "The encoder has 6 layers.",
                    "量子纠缠与联邦学习框架在火星数据集上验证。",
                    ""):
            self.assertEqual(sc.grounding_ratio(out, src), tm.grounding_ratio(out, src))
            self.assertEqual(sc.number_acronym_retention(out, src),
                             tm.number_acronym_retention(out, src))
            self.assertEqual(sc.language_of(out), tm.language_of(out))

    def test_spot_check_specific_helpers_still_local(self):
        """只该合并通用度量；spot_check 自己的句切/套话检测必须保留。"""
        self.assertTrue(callable(sc.split_sentences))
        self.assertTrue(callable(sc.detect_preamble))
        self.assertTrue(callable(sc.sentence_reuse_ratio))


if __name__ == "__main__":
    unittest.main()
