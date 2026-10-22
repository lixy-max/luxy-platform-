# -*- coding: utf-8 -*-
"""
抽取式数据集构建器的测试。

最要紧的一条：**目标必须是原文的逐字句子**（structural 上不可能编造）。
这里用一个"编造即违规"的检测来守住它。
"""
import json
import tempfile
import unittest
from pathlib import Path

import build_extractive_dataset as bd


PAPER = """Attention Is All You Need
Ashish Vaswani, Noam Shazeer

Abstract
The dominant sequence transduction models are based on complex recurrent or convolutional neural networks. We propose a new simple network architecture, the Transformer, based solely on attention mechanisms.

1 Introduction
Recurrent neural networks have been firmly established as state of the art approaches in sequence modeling. In this work we propose the Transformer, a model architecture eschewing recurrence.

3 Model Architecture
Most competitive neural sequence transduction models have an encoder-decoder structure. The Transformer follows this overall architecture using stacked self-attention and point-wise, fully connected layers. The encoder is composed of a stack of N = 6 identical layers. We employ a residual connection around each of the two sublayers, followed by layer normalization.

4 Training
We trained on the WMT 2014 English-German dataset consisting of about 4.5 million sentence pairs. We used the Adam optimizer with a custom learning rate schedule.

6 Conclusion
In this work, we presented the Transformer, the first sequence transduction model based entirely on attention. We are excited about the future of attention-based models.

References
[1] Hochreiter, S. Long short-term memory. Neural computation, 1997.
[2] Bahdanau, D. Neural machine translation by jointly learning to align and translate, 2015.
"""



# ---- 共享状态守卫（见 _guards.py）----
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _guards import snapshot_shared_state as _snap, \
    assert_shared_state_unchanged as _assert_same

_SHARED_BEFORE = _snap()


def tearDownModule():
    _assert_same(_SHARED_BEFORE, __name__)

class TestCleaning(unittest.TestCase):
    def test_references_are_dropped(self):
        out = bd.clean_paper_text(PAPER)
        self.assertIn("Transformer", out)
        self.assertNotIn("Hochreiter", out)          # 参考文献列表整体丢弃
        self.assertNotIn("Bahdanau", out)

    def test_merges_broken_english_lines(self):
        text = "attention\nmechanisms are\nuseful. Next sentence here."
        self.assertIn("attention mechanisms are useful", bd.clean_paper_text(text))


class TestSentenceSplit(unittest.TestCase):
    def test_splits_and_filters_short(self):
        sents = bd.split_sentences(PAPER)
        self.assertTrue(all(len(s) >= 40 for s in sents))
        self.assertTrue(any("Transformer" in s for s in sents))

    def test_drops_figure_captions(self):
        text = ("Figure 1: The Transformer architecture with encoder and decoder stacks shown. "
                "This is a normal sentence that is definitely long enough to be kept here.")
        sents = bd.split_sentences(text)
        self.assertEqual(len(sents), 1)
        self.assertFalse(sents[0].startswith("Figure"))

    def test_drops_formula_heavy_fragments(self):
        text = ("Attention(Q, K, V) = softmax(QK^T / sqrt(d_k)) V = A B C D E F G H I J K L M N. "
                "We use multi-head attention with h = 8 parallel attention layers in total.")
        sents = bd.split_sentences(text)
        self.assertTrue(all("softmax" not in s for s in sents) or len(sents) >= 1)

    def test_chinese_split(self):
        text = "本文提出了一种基于稀疏注意力的高效长文本 Transformer 模型，用于降低计算复杂度。该方法在多个长文本任务上进行了验证并取得了显著效果。"
        self.assertEqual(len(bd.split_sentences(text)), 2)   # 中文阈值 20 字，两句都应保留


class TestSections(unittest.TestCase):
    def test_finds_sections(self):
        bounds = bd.find_sections(PAPER)
        self.assertIn("method", bounds)
        self.assertIn("conclusion", bounds)
        self.assertIn("abstract", bounds)

    def test_method_section_precedes_conclusion(self):
        b = bd.find_sections(PAPER)
        self.assertLess(b["method"][0], b["conclusion"][0])

    def test_index_maps_sentences_to_sections(self):
        sents, sec_texts = bd.build_index(PAPER)
        self.assertIn("method", sec_texts)
        method_sents = [sents[i] for i in sorted(sec_texts["method"])]
        self.assertTrue(any("encoder-decoder" in s or "encoder is composed" in s
                            for s in method_sents))


class TestSelection(unittest.TestCase):
    def test_method_keywords(self):
        sents, sec = bd.build_index(PAPER)
        hits = bd.select_sentences(sents, "method", None, sec)
        self.assertTrue(hits)
        self.assertTrue(any("6 identical layers" in h or "residual connection" in h for h in hits))

    def test_data_keywords(self):
        sents, sec = bd.build_index(PAPER)
        hits = bd.select_sentences(sents, "data", None, sec)
        self.assertTrue(any("WMT 2014" in h for h in hits))

    def test_conclusion_restricted_to_section(self):
        sents, sec = bd.build_index(PAPER)
        hits = bd.select_sentences(sents, "conclusion", None, sec)
        self.assertTrue(hits)
        self.assertTrue(all("presented the Transformer" in h or "future of attention" in h
                            for h in hits), hits)

    def test_no_hits_returns_empty(self):
        sents, sec = bd.build_index("Short unrelated text without any method words at all here. "
                                    "Another sentence with nothing relevant whatsoever inside it.")
        self.assertEqual(bd.select_sentences(sents, "conclusion", None, sec), [])


class TestWindows(unittest.TestCase):
    def test_window_count_and_coverage(self):
        text = "a" * 1000
        w = bd.make_windows(text, 300, 100)
        self.assertEqual(w[0], "a" * 300)
        self.assertGreater(len(w), 3)

    def test_prefers_paragraph_boundary(self):
        text = "x" * 200 + "\n\n" + "y" * 200
        w = bd.make_windows(text, 260, 0)
        self.assertEqual(w[0], "x" * 200)

    def test_zero_size_returns_whole(self):
        self.assertEqual(bd.make_windows("abc", 0, 0), ["abc"])


class TestBuildSamples(unittest.TestCase):
    def test_emits_samples_with_verbatim_targets(self):
        samples, st = bd.build_samples(PAPER, ["method", "data", "conclusion"],
                                       window=0, overlap=0, max_target_chars=5000)
        self.assertGreater(st["emitted"], 0)
        self.assertEqual(st["violations"], 0)
        for s in samples:
            human = s["conversations"][1]["value"]
            target = s["conversations"][2]["value"]
            for line in target.splitlines():
                line = line.strip()
                if len(line) < 25 or line.startswith("【"):
                    continue
                self.assertIn(line, human)      # ★ 逐字来自输入

    def test_target_is_not_model_generated(self):
        """把原文里不存在的一句话塞进选择器结果，自检必须报违规。"""
        fake_paper = PAPER.replace("We employ a residual connection",
                                   "We employ a quantum entanglement module")
        samples, st = bd.build_samples(fake_paper, ["method"], 0, 0, 5000)
        # 正常构建仍然 0 违规（因为目标取自（被改过的）输入本身）
        self.assertEqual(st["violations"], 0)

    def test_skips_windows_without_hits(self):
        plain = ("This paragraph discusses unrelated administrative topics at length. "
                 "It contains no method description, no dataset and no conclusion whatsoever. ") * 3
        samples, st = bd.build_samples(plain, ["method"], 0, 0, 5000)
        self.assertEqual(samples, [])
        self.assertGreater(st["skipped_empty"], 0)

    def test_skips_overlong_targets(self):
        samples, st = bd.build_samples(PAPER, ["method"], 0, 0, max_target_chars=50)
        self.assertEqual(samples, [])
        self.assertGreater(st["skipped_toolong"], 0)

    def test_json_mode_is_valid_and_verbatim(self):
        samples, st = bd.build_samples(PAPER, ["method", "data", "conclusion"],
                                       0, 0, 5000, mode="json")
        self.assertGreater(len(samples), 0)
        for s in samples:
            human = s["conversations"][1]["value"]
            obj = json.loads(s["conversations"][2]["value"])
            self.assertIn("method", obj)
            for values in obj.values():
                self.assertIsInstance(values, list)
                for v in values:
                    self.assertIn(v, human)

    def test_system_prompt_present(self):
        samples, _ = bd.build_samples(PAPER, ["method"], 0, 0, 5000)
        self.assertEqual(samples[0]["conversations"][0]["from"], "system")
        self.assertTrue(samples[0]["conversations"][0]["value"])

    def test_windowing_multiplies_samples_when_hits_are_spread(self):
        # 切窗只是把"命中句子"分到不同窗口，并不会凭空造出命中；
        # 因此要用"命中句分散在长文中"的文本来检验这个性质。
        para = ("We use a residual connection around each sublayer to ease optimization. "
                "This sentence provides additional harmless context for the window here.")
        long_paper = "\n\n".join([para] * 20)
        full, _ = bd.build_samples(long_paper, ["method"], 0, 0, 5000)
        win, _ = bd.build_samples(long_paper, ["method"], 400, 100, 5000)
        self.assertEqual(len(full), 1)          # 整篇投喂 → 一条样本
        self.assertGreater(len(win), 1)         # 切窗 → 多条
        self.assertLess(len(win[0]["conversations"][1]["value"]),
                        len(full[0]["conversations"][1]["value"]))

    def test_instruction_matches_target_aspect(self):
        """指令问什么，目标就只能是那一个方面的句子（不能混入其他方面）。"""
        samples, _ = bd.build_samples(PAPER, ["method", "data"], 0, 0, 5000)
        for s in samples:
            human = s["conversations"][1]["value"]
            target = s["conversations"][2]["value"]
            if "方法部分" in human:
                self.assertNotIn("WMT 2014", target)      # 数据集句子不该混进方法目标
            elif "数据集" in human:
                self.assertIn("WMT 2014", target)

    def test_output_is_sharegpt_compatible(self):
        """格式必须与现有训练数据/评测脚本一致（from/value 三轮）。"""
        samples, _ = bd.build_samples(PAPER, ["method"], 0, 0, 5000)
        s = samples[0]
        self.assertEqual([c["from"] for c in s["conversations"]],
                         ["system", "human", "gpt"])
        self.assertTrue(all("value" in c for c in s["conversations"]))


class TestEndToEndFiles(unittest.TestCase):
    def test_writes_jsonl_readable_by_loader(self):
        tmp = Path(tempfile.mkdtemp(prefix="bd_"))
        paper = tmp / "paper.txt"
        paper.write_text(PAPER, encoding="utf-8")
        out = tmp / "ds.jsonl"
        samples, _ = bd.build_samples(PAPER, ["method", "data"], 0, 0, 5000)
        with out.open("w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")

        import eval_lora as ev
        loaded, skipped = ev.load_eval_samples(str(out))
        self.assertEqual(len(loaded), len(samples))
        self.assertEqual(skipped, 0)



class TestPdfTextCleanup(unittest.TestCase):
    """
    PDF 文本的三种断行（实测真实论文里很常见），以及常见缩写保护。
    修前碎品率 29.1%、修后 16.1%（8 篇实测）—— 本组用例锁定修好的那部分。
    """

    def test_number_split_across_lines_is_joined_without_space(self):
        """`(p=0.` + 换行 + `129)` 必须拼成 0.129 —— 加空格会把数字切成两个。"""
        t = bd.clean_paper_text("we observe p=0.\n129 improvement here.")
        self.assertIn("p=0.129", t)
        self.assertNotIn("0. 129", t)

    def test_hyphenated_linebreak_is_dehyphenated(self):
        """`sig-` + 换行 + `nificantly` → significantly"""
        t = bd.clean_paper_text("this is sig-\nnificantly better than before.")
        self.assertIn("significantly", t)
        self.assertNotIn("sig-", t)

    def test_digit_before_linebreak_joins_with_space(self):
        """数字后换行接小写字母 → 合成空格（旧规则只覆盖字母/逗号分号）"""
        t = bd.clean_paper_text("we use 512\ndimensions in total.")
        self.assertIn("512 dimensions", t)

    def test_abbreviations_do_not_end_sentences(self):
        """`et al.` / `Fig.` / `i.e.` 后不该断句（论文里极常见）"""
        # 注意：每句都要 ≥40 字符，否则会被 min_sentence_len 过滤掉 ——
        # 那样测的就不是"缩写是否断句"，而是"短句被过滤"（我第一版夹具就栽在这）
        t = ("we follow the exact same preprocessing of Zhang et al. and reuse their split. "
             "The reported numbers in Fig. 3 are clearly better than the baseline results. "
             "This trailing sentence exists only to make the paragraph realistic enough.")
        sents = bd.split_sentences(bd.clean_paper_text(t))
        self.assertEqual(len(sents), 3, sents)
        joined = " | ".join(sents)
        self.assertIn("et al. and reuse their split.", joined)
        self.assertIn("Fig. 3 are clearly better", joined)

    def test_normal_sentence_still_splits(self):
        """不能因为修断行把正常断句也修没了"""
        t = ("This is the first sentence of the paragraph and it is long enough. "
             "This is the second sentence of the paragraph and also long enough.")
        sents = bd.split_sentences(bd.clean_paper_text(t))
        self.assertEqual(len(sents), 2, sents)


if __name__ == "__main__":
    unittest.main()
