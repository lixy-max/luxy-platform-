# -*- coding: utf-8 -*-
"""文本度量 + 节点级质量指标的测试。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import text_metrics as tm
import workflow_engine as we



# ---- 共享状态守卫（见 _guards.py）----
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _guards import snapshot_shared_state as _snap, \
    assert_shared_state_unchanged as _assert_same

_SHARED_BEFORE = _snap()


def tearDownModule():
    _assert_same(_SHARED_BEFORE, __name__)

class TestTextMetrics(unittest.TestCase):
    def test_normalize_and_bigrams(self):
        self.assertEqual(tm.normalize("  a\n b "), "a b")
        self.assertEqual(tm.char_bigrams("a"), {"a"})
        self.assertEqual(tm.char_bigrams(""), set())

    def test_grounding_full_and_partial(self):
        src = "本文提出 SparseFormer，一种稀疏注意力变体。"
        self.assertEqual(tm.grounding_ratio(src, src), 1.0)
        out = "本文提出 SparseFormer，并额外引入量子纠缠模块。"
        g = tm.grounding_ratio(out, src)
        self.assertLess(g, 1.0)
        self.assertGreater(g, 0.0)

    def test_grounding_empty_output(self):
        self.assertEqual(tm.grounding_ratio("", "任意输入"), 0.0)

    def test_key_fact_retention_cross_language(self):
        src = "The encoder has N = 6 layers with d_model = 512 and uses FFN."
        out = "编码器有 N=6 层，维度 d_model=512，使用 FFN。"
        ratio, n, hit = tm.number_acronym_retention(out, src)
        self.assertGreater(n, 0)
        self.assertEqual(hit, n, "输出里的数字/缩写应全部能在原文找到")
        self.assertEqual(ratio, 1.0)

    def test_key_fact_retention_catches_invented(self):
        src = "BLEU 28.4 with 6 layers."
        out = "BLEU 41.8 with 12 layers."
        ratio, n, hit = tm.number_acronym_retention(out, src)
        self.assertEqual(n, 3)                 # 41.8 / 12 / BLEU
        self.assertEqual(hit, 1)               # 只有 BLEU 是原文里有的
        self.assertLess(ratio, 1.0)

    def test_key_fact_retention_none_when_no_facts(self):
        self.assertEqual(tm.number_acronym_retention("没有数字", "src")[0], None)

    def test_language_of(self):
        self.assertEqual(tm.language_of("纯中文"), "zh")
        self.assertEqual(tm.language_of("pure english text"), "en")
        self.assertEqual(tm.language_of(""), "unknown")

    def test_grounding_report_shape(self):
        r = tm.grounding_report("输出 A", "输入 输出 A")
        for k in ("grounding", "key_fact_retention", "chars_per_1k_input",
                  "output_chars", "input_chars", "output_language"):
            self.assertIn(k, r)
        self.assertGreater(r["chars_per_1k_input"], 0)


    # ---- 数量级感知（2026-10-06 修口径）----
    # 背景：报告写「3600 万句」、论文写「36M」，旧的裸字符串比对判为无关，
    # 于是聚合节点的关键事实保留被系统性低估，并制造"报告数字没有出处"的假警报。

    def test_scale_units_are_equivalent(self):
        """中文数量级词与英文缩写必须等价：450万 ≡ 4.5M，3600万 ≡ 36M。"""
        cases = [
            ("英德数据集 450 万句对", "WMT 2014 En-De with 4.5M sentence pairs"),
            ("英法数据集 3600 万句", "WMT 2014 En-Fr with 36M sentences"),
            ("语料 1700 万句", "17M sentences"),
            ("规模 1.2 亿参数", "120M parameters"),
            ("共 36,000,000 条", "36M records"),
            ("上下文 16K", "16K context"),
        ]
        for out, src in cases:
            ratio, n, hit = tm.number_acronym_retention(out, src)
            self.assertEqual(hit, n, f"{out!r} 与 {src!r} 应等价，实际 {hit}/{n}")
            self.assertEqual(ratio, 1.0)

    def test_scale_unit_does_not_match_bare_number(self):
        """
        刻意的取舍：`16K`（=16000）与裸 `16` 数量级不同，**不应**算作同一事实。
        旧口径把 "16K" 里的 "16" 抠出来，于是裸 16 能"顶替" 16K —— 那是 bug。
        """
        ratio, n, hit = tm.number_acronym_retention("上下文 16K", "共有 16 个样本")
        self.assertEqual((n, hit), (1, 0))
        self.assertEqual(ratio, 0.0)

    def test_tampered_scale_is_still_detected(self):
        """改数量级（把 4.5M 说成 4500 万）必须能被抓到 —— 修口径不能修钝。"""
        ratio, n, hit = tm.number_acronym_retention("数据集 4500 万句", "4.5M sentence pairs")
        self.assertEqual((n, hit), (1, 0))
        self.assertEqual(ratio, 0.0)

    def test_fact_tokens_groups_equivalent_writings(self):
        """断言**语义等价**，不绑定规范化后的具体字符串格式（那是实现细节）。"""
        self.assertEqual(tm.fact_tokens("16K"), tm.fact_tokens("16000"))
        self.assertEqual(tm.fact_tokens("3600万"), tm.fact_tokens("36M"))
        self.assertEqual(tm.fact_tokens("36,000,000"), tm.fact_tokens("36M"))
        self.assertEqual(tm.fact_tokens("450万"), tm.fact_tokens("4.5M"))
        self.assertNotEqual(tm.fact_tokens("16K"), tm.fact_tokens("16"))
        self.assertIn("BLEU", tm.fact_tokens("BLEU 28.4"))

    def test_chinese_units_are_not_confused(self):
        """千万/百万/十万 不能被 千/万 抢先匹配（词表顺序错误会让数值差 10~1000 倍）。"""
        self.assertEqual(tm.fact_tokens("1千万"), tm.fact_tokens("10000000"))
        self.assertEqual(tm.fact_tokens("1百万"), tm.fact_tokens("1000000"))
        self.assertEqual(tm.fact_tokens("1十万"), tm.fact_tokens("100000"))
        self.assertNotEqual(tm.fact_tokens("1千万"), tm.fact_tokens("1万"))
        self.assertNotEqual(tm.fact_tokens("1百万"), tm.fact_tokens("1万"))


class FakeRouterQ:
    swaps = []

    def __init__(self, *a, **k):
        pass

    def ensure(self, lora):
        return (False, "使用底座")


# 一段真实规模的论文正文（> 500 字），用于触发质量判定
_PARA = ("本文提出了一种基于稀疏注意力的高效长文本处理方法，通过可学习门控动态选择键值对，"
         "在 16K 上下文下把注意力复杂度从平方级降到线性对数级，并在多个长文本任务上完成验证。"
         "方法包括三个组件：可学习门控模块、混合注意力层以及鼓励多样选择的训练策略；"
         "门控模块由一个小型多层感知机参数化，为每个键值对输出相关性得分，"
         "再通过 top-k 选择保留最相关的部分，从而显著降低有效注意力的计算复杂度。")
PAD = _PARA * 3
assert len(PAD) > 500


class TestNodeQuality(unittest.TestCase):
    """抽取类节点必须算可溯源率，偏低要显形并进报告头。"""

    def _run(self, node, llm_out, source):
        tmp = Path(tempfile.mkdtemp(prefix="qual_"))
        paper = tmp / "p.txt"
        paper.write_text(source, encoding="utf-8")
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []}, node]}, ensure_ascii=False),
            encoding="utf-8")
        with mock.patch.object(we, "CACHE_ENABLED", False), \
             mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouterQ), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {})), \
             mock.patch.object(we, "call_llm_strict", side_effect=lambda *a, **k: llm_out):
            return json.loads(Path(we.run_workflow(str(paper), str(cfg))).read_text(encoding="utf-8"))

    def test_extract_node_records_quality(self):
        src = PAD
        out = self._run({"id": "e", "type": "extract", "input_from": ["in"],
                         "prompt": "抽取：{in}", "lora": "x"}, src, source=src)
        q = out["_meta"]["quality"]["e"]
        self.assertEqual(q["grounding"], 1.0)      # 输出逐字来自输入
        self.assertEqual(q["input_chars"], len(src))
        self.assertEqual(out["_meta"]["quality_warnings"], [])
        self.assertEqual(out["status"], "success")

    def test_fabricated_content_is_flagged(self):
        """
        ⚠️ 这条测试**推翻了原来写在这里的结论**。原文断言是"编造句能拿 grounding 1.0，
        所以该口径不能当编造检测器" —— 复算发现那个 1.0 是**测试缓存污染**的产物：
        `extract` 类型默认 cache=true，而 CACHE_DIR 是项目里的真实目录，于是本用例
        读到了上一个用例缓存下来的输出（整篇源文），根本没测到编造句。
        把缓存关掉后实测：编造句 grounding = 0.3235 → **会被判为不达标**。
        """
        fabricated = "本文提出了量子纠缠与联邦学习框架，并在火星遥感数据集上完成端到端验证。"
        out = self._run({"id": "e", "type": "extract", "input_from": ["in"],
                         "prompt": "抽取：{in}", "lora": "x"}, fabricated, source=PAD)
        q = out["_meta"]["quality"]["e"]
        self.assertLess(q["grounding"], we.QUALITY_GROUNDING_MIN)
        warns = out["_meta"]["quality_warnings"]
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("可溯源率", warns[0]["reason"])
        self.assertEqual(out["status"], "partial")

    def test_grounding_real_limitation_is_word_order_sensitivity(self):
        """
        grounding 的**真**局限（与上面那条的旧说法不同）：它是"二元组集合包含度"，
        衡量**用没用原文的词汇**，对**顺序**无感 —— 把原文句子重排，照样接近满分，
        而语义可能已经被破坏。这才是它抓不住的那类问题（改动词序/拼接原文原句）。
        """
        src = ("本文提出了一种基于稀疏注意力的高效长文本处理方法。"
               "该方法在多个长文本任务上完成了验证。"
               "门控模块由一个小型多层感知机参数化。"
               "训练策略鼓励模型做出多样化的选择。")
        sents = [x for x in src.split("。") if x.strip()]
        reordered = "。".join(reversed(sents)) + "。"
        self.assertNotEqual(reordered, src)            # 顺序确实变了
        out = self._run({"id": "e", "type": "extract", "input_from": ["in"],
                         "prompt": "抽取：{in}", "lora": "x"}, reordered, source=src)
        q = out["_meta"]["quality"]["e"]
        self.assertGreaterEqual(q["grounding"], we.QUALITY_GROUNDING_MIN)   # 词汇全在 → 高分
        self.assertEqual(out["_meta"]["quality_warnings"], [])              # 于是漏报
        self.assertEqual(out["status"], "success")

    def test_analyze_node_has_no_grounding_metric(self):
        out = self._run({"id": "a", "type": "analyze", "input_from": ["in"],
                         "prompt": "分析：{in}", "lora": "x"}, '{"结论": "自由发挥"}', source=PAD)
        self.assertEqual(out["_meta"]["quality"], {})        # 分析类不适用可溯源率
        self.assertEqual(out["status"], "success")

    def test_plain_process_node_has_no_grounding_metric(self):
        out = self._run({"id": "p", "type": "process", "input_from": ["in"],
                         "prompt": "处理：{in}"}, '{"a": 1}', source=PAD)
        self.assertEqual(out["_meta"]["quality"], {})

    def test_tiny_input_records_noisy_metric_but_stays_success(self):
        """输入极短时指标有噪声，但因为它不参与判定，运行仍是 success。"""
        out = self._run({"id": "e", "type": "extract", "input_from": ["in"],
                         "prompt": "抽取：{in}", "lora": "x"}, "迷你输出。", source="短文。")
        self.assertIn("e", out["_meta"]["quality"])
        self.assertEqual(out["status"], "success")

    def test_quality_compares_against_input_not_instruction(self):
        """可溯源率要比对"输入材料"，不能把中文指令算进去当依据。"""
        src = "Attention Is All You Need. " + PAD
        out = self._run({"id": "e", "type": "extract", "input_from": ["in"],
                         "prompt": "请提取方法：\n\n{in}", "lora": "x"},
                        "Attention Is All You Need.", source=src)
        q = out["_meta"]["quality"]["e"]
        # 英文输出 vs 中文为主的源文 → 跨语言，grounding **不适用**
        # （原断言 1.0 是把跨语言噪声当成了质量信号）
        self.assertIsNone(q["grounding"])
        self.assertFalse(q["grounding_applicable"])
        self.assertEqual(q["grounding_raw"], 1.0)   # 原始值仍可查：输出确实逐字来自输入
        self.assertEqual(q["input_chars"], len(src))


    def test_few_fabricated_facts_are_flagged_by_absolute_count(self):
        """
        比率型判据抓不住"长文本里掺几个假数字"：掺 2~3 个假事实时保留率仍 ≥0.9。
        所以补第四条判据 —— **无出处事实的绝对条数**。本用例把这一点固定下来：
        同一个输出上，比率判据放过、绝对条数判据抓住。
        """
        lines = [f"第 {i} 组：隐藏维度 {100 + i}，注意力头 {i * 2}，训练步数 {10000 * i}，"
                 f"学习率 0.000{i}，dropout 0.{i}，warmup {500 * i}。" for i in range(1, 7)]
        src = "".join(lines)                      # ≈42 个互不相同的事实
        honest = src                              # 逐字来自输入

        node = {"id": "e", "type": "extract", "input_from": ["in"],
                "prompt": "抽取：{in}", "lora": "x"}
        out = self._run(node, honest, source=src)
        q = out["_meta"]["quality"]["e"]
        self.assertGreaterEqual(q["key_facts"], we.QUALITY_UNMATCHED_MIN_FACTS)
        self.assertEqual(q["key_fact_retention"], 1.0)
        self.assertEqual(out["_meta"]["quality_warnings"], [])

        fabricated = honest + "本文还使用了 99999 块 TPU 与 8888 层网络。"
        out2 = self._run(node, fabricated, source=src)
        q2 = out2["_meta"]["quality"]["e"]
        # 先证明"比率判据会放过"（这正是要补第四条的理由）
        self.assertGreaterEqual(q2["key_fact_retention"], we.QUALITY_FACT_RETENTION_MIN)
        unmatched = q2["key_facts"] - q2["key_facts_matched"]
        self.assertGreaterEqual(unmatched, we.QUALITY_MAX_UNMATCHED_FACTS)
        warns = out2["_meta"]["quality_warnings"]
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("找不到出处", warns[0]["reason"])
        self.assertEqual(out2["status"], "partial")

    def test_single_unmatched_fact_is_tolerated(self):
        """只容忍 1 条格式差异 —— 1 条无出处事实不足以判定编造（避免噪声告警）。"""
        lines = [f"第 {i} 组：隐藏维度 {100 + i}，注意力头 {i * 2}，训练步数 {10000 * i}。"
                 for i in range(1, 7)]
        # 源文要足够长：否则 1 句假后缀会把 grounding 压到 0.9 以下，
        # 变成"另一条判据先触发"，就测不到本条判据的容忍度了（我第一版就是这么写错的）
        src = "".join(lines) + PAD * 4
        node = {"id": "e", "type": "extract", "input_from": ["in"],
                "prompt": "抽取：{in}", "lora": "x"}
        out = self._run(node, src + "另见 77777 号附录。", source=src)
        q = out["_meta"]["quality"]["e"]
        self.assertEqual(q["key_facts"] - q["key_facts_matched"], 1)      # 恰好 1 条无出处
        self.assertGreaterEqual(q["grounding"], we.QUALITY_GROUNDING_MIN)  # 其它判据不干扰
        self.assertGreaterEqual(q["key_fact_retention"], we.QUALITY_FACT_RETENTION_MIN)
        self.assertEqual(out["_meta"]["quality_warnings"], [])
    def test_cross_language_grounding_is_not_applicable(self):
        """
        报告中文、论文英文时，字符二元组重合度是**噪声**（实测恒在 0.07 附近）。
        曾据此差点得出"没用原文词汇"的错误结论，故显式标为不适用。
        """
        en_src = "The encoder has 6 layers with d_model 512 and uses FFN."
        zh_out = "编码器有 6 层，隐藏维度 512，使用 FFN 与前馈网络。" * 3
        r = tm.grounding_report(zh_out, en_src)
        self.assertIsNone(r["grounding"])
        self.assertFalse(r["grounding_applicable"])
        self.assertIsNotNone(r["grounding_raw"])
        self.assertEqual(r["output_language"], "zh")
        self.assertEqual(r["source_language"], "en")

    def test_same_language_grounding_still_applies(self):
        src = "本文提出稀疏注意力方法，通过可学习门控降低复杂度。" * 3
        r = tm.grounding_report(src, src)
        self.assertTrue(r["grounding_applicable"])
        self.assertEqual(r["grounding"], 1.0)

    def test_mixed_or_unknown_language_is_applicable(self):
        """mixed/unknown 不作跨语言处理（否则会把"中文夹英文"的正常输入判成不适用）。"""
        self.assertTrue(tm.grounding_report("BLEU 28.4 分", "BLEU 28.4 分")["grounding_applicable"])
        self.assertTrue(tm.grounding_report("1234 5678", "1234 5678")["grounding_applicable"])

    def test_cross_language_extract_node_not_flagged_for_grounding(self):
        """跨语言时只有 grounding 那条不适用；不应因此告警。"""
        en_src = ("The encoder has 6 layers, d_model 512, 8 heads, 100000 steps, "
                  "batch 25000, lr 0.0001. " * 8)
        node = {"id": "e", "type": "extract", "input_from": ["in"],
                "prompt": "抽取：{in}", "lora": "x"}
        out = self._run(node, "编码器有 6 层，维度 512。", source=en_src)
        q = out["_meta"]["quality"]["e"]
        self.assertIsNone(q["grounding"])
        self.assertEqual(out["_meta"]["quality_warnings"], [], "不应因跨语言而告警")
        self.assertEqual(out["status"], "success")
    def test_scale_unit_followed_by_chinese_is_recognized(self):
        """
        中文里最常见的写法是"数字+K"紧跟汉字（200K规模、16K上下文、4K预算）。
        这里曾因 \b 与汉字冲突而识别失败 → 退化成裸数字 → 制造"无出处事实"的假警报
        （本轮 A/B 的 0.33 就是它）。本用例把修复固定下来。
        """
        for out, src in (("KV2 在 200K规模区间仍优于基线", "KV2 evaluated up to 200K context"),
                         ("支持 16K上下文", "supports 16K context"),
                         ("4K预算下的表现", "performance at 4K budget")):
            ratio, n, hit = tm.number_acronym_retention(out, src)
            self.assertEqual(hit, n, f"{out!r} vs {src!r} 应等价，实际 {hit}/{n}")
        self.assertEqual(tm.fact_tokens("200K规模"), tm.fact_tokens("200000"))
        self.assertEqual(tm.fact_tokens("16K上下文"), tm.fact_tokens("16000"))

    def test_mb_guard_still_holds(self):
        """
        护栏的真实场景是 **8MB**（M 是数量级字母，后跟字母 B 必须挡住），
        而不是 8GB —— G 根本不在数量级字母表（只有 k/m/b）里，所以一直安全。
        """
        self.assertEqual(tm.fact_tokens("8MB"), {"8"})          # 不读成 8M=8e6
        self.assertEqual(tm.fact_tokens("8GB"), {"8"})
        self.assertEqual(tm.fact_tokens("8M"), tm.fact_tokens("8000000"))   # 单独 8M 正常识别

class TestQualityEligibility(unittest.TestCase):
    """质量观测的适用范围：extract 类型，以及"文本输出且有输入材料"的节点。"""

    def _run(self, node, llm_out, source):
        tmp = Path(tempfile.mkdtemp(prefix="elig_"))
        paper = tmp / "p.txt"
        paper.write_text(source, encoding="utf-8")
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []}, node]}, ensure_ascii=False),
            encoding="utf-8")
        with mock.patch.object(we, "CACHE_ENABLED", False), \
             mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouterQ), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {})), \
             mock.patch.object(we, "call_llm_strict", side_effect=lambda *a, **k: llm_out):
            return json.loads(Path(we.run_workflow(str(paper), str(cfg))).read_text(encoding="utf-8"))

    def test_process_with_text_output_is_eligible(self):
        """发布配置就是 process+output_format=text —— 必须能产出标定样本。"""
        out = self._run({"id": "e", "type": "process", "input_from": ["in"],
                         "prompt": "抽取：{in}", "output_format": "text"},
                        PAD[:80], source=PAD)
        q = out["_meta"]["quality"]["e"]
        self.assertEqual(q["source"], "text-output")
        self.assertEqual(q["grounding"], 1.0)
        self.assertTrue(q["eligible"])
        self.assertEqual(out["status"], "success")        # 仍然不参与判定

    def test_json_output_node_not_eligible(self):
        out = self._run({"id": "p", "type": "process", "input_from": ["in"],
                         "prompt": "处理：{in}"}, '{"a": 1}', source=PAD)
        self.assertEqual(out["_meta"]["quality"], {})

    def test_extract_type_marked_by_type(self):
        out = self._run({"id": "e", "type": "extract", "input_from": ["in"],
                         "prompt": "抽取：{in}", "lora": "x"}, PAD[:80], source=PAD)
        self.assertEqual(out["_meta"]["quality"]["e"]["source"], "type")



class TestQualityJudging(unittest.TestCase):
    """
    已标定的判定：**只对 extract 类型**生效。
    标定数据：extract_method 事实保留 1.0 / grounding 1.0；
              finaloutcome（汇总改写）0.75 / 0.4615 —— 后者不该被判为质量问题。
    """

    def _run(self, node, llm_out, source):
        tmp = Path(tempfile.mkdtemp(prefix="judge_"))
        paper = tmp / "p.txt"
        paper.write_text(source, encoding="utf-8")
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []}, node]}, ensure_ascii=False),
            encoding="utf-8")
        with mock.patch.object(we, "CACHE_ENABLED", False), \
             mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouterQ), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {})), \
             mock.patch.object(we, "call_llm_strict", side_effect=lambda *a, **k: llm_out):
            return json.loads(Path(we.run_workflow(str(paper), str(cfg))).read_text(encoding="utf-8"))

    def _extract_node(self):
        return {"id": "e", "type": "extract", "input_from": ["in"],
                "prompt": "抽取：{in}", "lora": "x"}

    def test_clean_extraction_passes(self):
        out = self._run(self._extract_node(), PAD[:120], source=PAD)
        self.assertEqual(out["_meta"]["quality"]["e"]["key_fact_retention"], 1.0)  # 含 16K，且逐字来自输入
        self.assertEqual(out["_meta"]["quality"]["e"]["grounding"], 1.0)
        self.assertEqual(out["_meta"]["quality_warnings"], [])
        self.assertEqual(out["status"], "success")

    def test_tampered_facts_are_flagged(self):
        """数字被改过的"抽取"必须判为不达标（标定显示事实保留会掉到 0.16 左右）。"""
        src = "The encoder has 6 layers with d_model 512 and BLEU 28.4 on WMT."
        out = self._run(self._extract_node(),
                        "The encoder has 43 layers with d_model 549 and BLEU 65.4 on WMT.",
                        source=src + PAD)
        warns = out["_meta"]["quality_warnings"]
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("关键事实保留", warns[0]["reason"])
        self.assertEqual(out["status"], "partial")
        self.assertIn("抽取质量不达标", out["final_report"])

    def test_severe_under_extraction_is_flagged(self):
        """
        第三条判定（每千字产出下限）。为什么必须有它：grounding 与事实保留只回答
        "输出里的东西是不是来自原文"，对"输出太少"完全无感 —— 实测把 1455 字的合格
        抽取截断到 200 字（每千字 5.0），两项指标仍是 1.0/1.0，判定为"通过"。
        而"4 万字只输出 94 字"正是本项目已知的欠抽取故障。
        """
        long_src = PAD * 10                       # ≈6000 字，超过充分度门槛
        short_out = PAD[:20]                      # 逐字来自原文，但只产出了 20 字
        out = self._run(self._extract_node(), short_out, source=long_src)
        q = out["_meta"]["quality"]["e"]
        self.assertEqual(q["grounding"], 1.0)     # 两项老指标都是满分 —— 所以老判定抓不到
        self.assertIsNone(q["key_fact_retention"])
        self.assertLess(q["chars_per_1k_input"], we.QUALITY_MIN_CHARS_PER_1K)
        warns = out["_meta"]["quality_warnings"]
        self.assertEqual(len(warns), 1, warns)
        self.assertIn("欠抽取", warns[0]["reason"])
        self.assertIn("每千字", warns[0]["reason"])
        self.assertEqual(out["status"], "partial")

    def test_short_input_is_exempt_from_sufficiency_check(self):
        """输入太短时"每千字产出"没有意义（一句话随便产出几个字都能过）→ 不参与判定。"""
        out = self._run(self._extract_node(), PAD[:3], source=PAD)   # 源 ~600 字 < 门槛
        q = out["_meta"]["quality"]["e"]
        self.assertLess(len(PAD), we.QUALITY_SUFFICIENCY_MIN_INPUT)
        self.assertLess(q["chars_per_1k_input"], we.QUALITY_MIN_CHARS_PER_1K)  # 比值确实很低
        self.assertEqual(out["_meta"]["quality_warnings"], [])                # 但不告警
        self.assertEqual(out["status"], "success")

    def test_reason_names_exactly_which_check_failed(self):
        """告警理由要说清是**哪一条**不达标，别让人去猜。"""
        src = "The encoder has 6 layers with d_model 512 and BLEU 28.4 on WMT." + PAD
        out = self._run(self._extract_node(),
                        "The encoder has 43 layers with d_model 549 and BLEU 65.4 on WMT.",
                        source=src)
        warns = out["_meta"]["quality_warnings"]
        self.assertEqual(len(warns), 1)
        self.assertIn("关键事实保留", warns[0]["reason"])
        self.assertNotIn("欠抽取", warns[0]["reason"])   # 这条没触发，就不该出现

    def test_rewriting_node_is_not_judged(self):
        """汇总/改写型节点的低分是正常的 —— 套同一标准会误报（标定数据佐证）。"""
        node = {"id": "agg", "type": "aggregate", "input_from": ["in"], "prompt": "{输入}"}
        out = self._run(node, "这是一份重新组织过的中文报告，措辞与原文完全不同。" * 3, source=PAD)
        q = out["_meta"]["quality"]["agg"]
        self.assertEqual(q["source"], "text-output")      # 非 type → 不判
        self.assertLess(q["grounding"], 0.8)              # 分数确实很低
        self.assertEqual(out["_meta"]["quality_warnings"], [])   # 但不告警
        self.assertEqual(out["status"], "success")


if __name__ == "__main__":
    unittest.main()
