# -*- coding: utf-8 -*-
"""
LoRA 评测脚本的纯函数测试：打分、评测集加载、数据泄漏检查、汇总统计。

不调用任何模型；HTTP 与训练集目录都通过打桩/临时目录隔离。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

class TestSimilarity(unittest.TestCase):
    def test_normalize_collapses_whitespace(self):
        self.assertEqual(ev.normalize("  a\n\n b\t c "), "a b c")

    def test_normalize_none(self):
        self.assertEqual(ev.normalize(None), "")

    def test_bigrams_short(self):
        self.assertEqual(ev.char_bigrams("a"), {"a"})
        self.assertEqual(ev.char_bigrams(""), set())

    def test_identical_is_one(self):
        self.assertEqual(ev.similarity("本文提出稀疏注意力", "本文提出稀疏注意力"), 1.0)

    def test_disjoint_is_zero(self):
        self.assertEqual(ev.similarity("aaaa", "bbbb"), 0.0)

    def test_partial_between(self):
        s = ev.similarity("本文提出稀疏注意力方法", "本文提出稀疏注意力机制")
        self.assertGreater(s, 0.3)
        self.assertLess(s, 1.0)

    def test_both_empty_is_one(self):
        self.assertEqual(ev.similarity("", ""), 1.0)

    def test_one_empty_is_zero(self):
        self.assertEqual(ev.similarity("abc", ""), 0.0)


class TestParseJsonObject(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(ev.parse_json_object('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(ev.parse_json_object('```json\n{"a": 2}\n```'), {"a": 2})

    def test_prose_around(self):
        self.assertEqual(ev.parse_json_object('好的：\n{"a": 3}\n以上'), {"a": 3})

    def test_invalid(self):
        self.assertIsNone(ev.parse_json_object('{"a": 1,'))

    def test_non_object(self):
        self.assertIsNone(ev.parse_json_object("[1, 2, 3]"))

    def test_empty(self):
        self.assertIsNone(ev.parse_json_object(""))
        self.assertIsNone(ev.parse_json_object(None))


class TestScoreOutput(unittest.TestCase):
    def test_text_reference_metrics(self):
        ref = "本文方法包括：1. 数据预处理；2. 网络设计。"
        out = "本文方法包括：1. 数据预处理；2. 网络设计。"
        m = ev.score_output(out, ref)
        self.assertEqual(m["kind"], "text")
        self.assertEqual(m["similarity"], 1.0)
        self.assertIsNone(m["json_valid"])
        self.assertTrue(m["has_numbered_list"])

    def test_decimal_number_is_not_a_list(self):
        m = ev.score_output("准确率提升到 3.5 个百分点", "参考")
        self.assertFalse(m["has_numbered_list"])

    def test_single_item_is_not_a_list(self):
        m = ev.score_output("只有 1. 一项", "参考")
        self.assertFalse(m["has_numbered_list"])

    def test_json_reference_full_match(self):
        ref = json.dumps({"method": "CNN", "data": "ImageNet"}, ensure_ascii=False)
        out = '{"method": "CNN", "data": "ImageNet"}'
        m = ev.score_output(out, ref)
        self.assertEqual(m["kind"], "json")
        self.assertTrue(m["json_valid"])
        self.assertEqual(m["field_coverage"], 1.0)
        self.assertEqual(m["non_empty_ratio"], 1.0)
        self.assertEqual(m["similarity"], 1.0)
        self.assertEqual(m["extra_keys"], [])

    def test_json_reference_partial(self):
        ref = json.dumps({"a": "xxx", "b": "yyy", "c": "zzz"})
        out = '{"a": "xxx", "b": ""}'
        m = ev.score_output(out, ref)
        self.assertTrue(m["json_valid"])
        self.assertAlmostEqual(m["field_coverage"], 2 / 3, places=3)
        self.assertAlmostEqual(m["non_empty_ratio"], 1 / 3, places=3)

    def test_json_reference_invalid_output(self):
        ref = json.dumps({"a": "1"})
        m = ev.score_output("这不是 JSON", ref)
        self.assertFalse(m["json_valid"])
        self.assertEqual(m["field_coverage"], 0.0)
        self.assertIn("similarity", m)

    def test_extra_keys_flagged(self):
        ref = json.dumps({"a": "1"})
        out = '{"a": "1", "hallucinated": "x"}'
        self.assertEqual(ev.score_output(out, ref)["extra_keys"], ["hallucinated"])

    def test_empty_output(self):
        m = ev.score_output("", "参考文本")
        self.assertTrue(m["empty"])
        self.assertEqual(m["length_ratio"], 0.0)

    def test_length_ratio(self):
        m = ev.score_output("abcd", "abcdefgh")
        self.assertEqual(m["length_ratio"], 0.5)


class TestCompositeAndStats(unittest.TestCase):
    def test_composite(self):
        self.assertEqual(ev.composite_score({"similarity": 0.8}), 0.8)
        self.assertEqual(ev.composite_score({"similarity": 0.8, "empty": True}), 0.0)
        self.assertEqual(ev.composite_score({}), 0.0)

    def test_paired_stats(self):
        base = [{"similarity": 0.2}, {"similarity": 0.5}, {"similarity": 0.4},
                {"similarity": 0.6}]
        lora = [{"similarity": 0.5}, {"similarity": 0.5}, {"similarity": 0.1},
                {"similarity": 0.9}]
        p = ev.paired_stats(base, lora)
        self.assertEqual(p["improved"], 2)   # 0.2->0.5, 0.6->0.9
        self.assertEqual(p["worse"], 1)      # 0.4->0.1
        self.assertEqual(p["tie"], 1)        # 0.5->0.5
        self.assertAlmostEqual(p["mean_delta"], (0.3 + 0 - 0.3 + 0.3) / 4, places=4)

    def test_paired_stats_empty(self):
        self.assertIsNone(ev.paired_stats([], [])["mean_delta"])

    def test_aggregate_means(self):
        rows = {
            "base": [{"similarity": 0.2, "chars": 10, "empty": False},
                     {"similarity": 0.4, "chars": 20, "empty": False}],
            "lora": [{"similarity": 0.6, "chars": 30, "empty": False},
                     {"similarity": 0.0, "chars": 0, "empty": True}],
        }
        agg = ev.aggregate(rows)
        self.assertAlmostEqual(agg["base"]["similarity"], 0.3, places=4)
        self.assertAlmostEqual(agg["lora"]["similarity"], 0.3, places=4)
        self.assertEqual(agg["lora"]["empty_count"], 1)
        self.assertAlmostEqual(agg["base"]["avg_chars"], 15.0, places=2)

    def test_aggregate_json_valid_ratio(self):
        rows = {"lora": [{"json_valid": True, "similarity": 1.0, "chars": 5},
                         {"json_valid": False, "similarity": 0.1, "chars": 5}]}
        agg = ev.aggregate(rows)
        self.assertEqual(agg["lora"]["json_valid_ratio"], 0.5)

    def test_aggregate_skips_text_mode_for_json_ratio(self):
        rows = {"lora": [{"json_valid": None, "similarity": 0.5, "chars": 5}]}
        self.assertIsNone(ev.aggregate(rows)["lora"]["json_valid_ratio"])


class TestLoadEvalSamples(unittest.TestCase):
    def _write(self, lines):
        p = Path(tempfile.mkdtemp(prefix="eval_ds_")) / "ds.jsonl"
        p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines),
                     encoding="utf-8")
        return p

    def _conv(self, human, gpt, system="sys"):
        return {"conversations": [
            {"from": "system", "value": system},
            {"from": "human", "value": human},
            {"from": "gpt", "value": gpt}]}

    def test_loads_valid(self):
        p = self._write([self._conv("q1", "a1"), self._conv("q2", "a2")])
        samples, skipped = ev.load_eval_samples(str(p))
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0], ("sys", "q1", "a1"))
        self.assertEqual(skipped, 0)

    def test_limit(self):
        p = self._write([self._conv(f"q{i}", f"a{i}") for i in range(10)])
        self.assertEqual(len(ev.load_eval_samples(str(p), limit=3)[0]), 3)

    def test_skips_broken_and_incomplete(self):
        p = self._write([
            self._conv("q1", "a1"),
            {"conversations": [{"from": "human", "value": "只有提问"}]},
            {"conversations": [{"from": "human", "value": "q"}, {"from": "gpt", "value": "a"}]},
        ])
        with p.open("a", encoding="utf-8") as f:
            f.write("\n{ 这不是 json\n")
        samples, skipped = ev.load_eval_samples(str(p))
        self.assertEqual(len(samples), 2)
        self.assertEqual(skipped, 2)

    def test_supports_messages_role_format(self):
        p = self._write([{"messages": [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"}]}])
        samples, _ = ev.load_eval_samples(str(p))
        self.assertEqual(samples[0], ("s", "u", "a"))


class TestLeakageCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="leak_"))
        self.train_dir = self.tmp / "training_data"
        self.train_dir.mkdir()
        self._patch = mock.patch.object(ev, "TRAINING_DATA_DIR", self.train_dir)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _sample(self, human, gpt, system="s"):
        return (system, human, gpt)

    def _write_train(self, lora, samples):
        p = self.train_dir / f"{lora}.jsonl"
        p.write_text("\n".join(json.dumps({"conversations": [
            {"from": "system", "value": s},
            {"from": "human", "value": h},
            {"from": "gpt", "value": g}]}, ensure_ascii=False) for s, h, g in samples),
            encoding="utf-8")
        return p

    def test_same_file_warns(self):
        p = self._write_train("mylora", [self._sample("q", "a")])
        warn, detail = ev.leakage_check(str(p), "mylora", [self._sample("q", "a")])
        self.assertTrue(warn)
        self.assertIn("同一个文件", detail)

    def test_overlapping_content_warns(self):
        train = self._write_train("mylora", [self._sample(f"q{i}", f"a{i}") for i in range(4)])
        eval_path = self.tmp / "other_name.jsonl"       # 文件名不同，内容重叠
        eval_path.write_text(train.read_text(encoding="utf-8"), encoding="utf-8")
        samples = [self._sample(f"q{i}", f"a{i}") for i in range(4)]
        warn, detail = ev.leakage_check(str(eval_path), "mylora", samples)
        self.assertTrue(warn)
        self.assertIn("出现在训练集", detail)

    def test_disjoint_does_not_warn(self):
        self._write_train("mylora", [self._sample("train_q", "train_a")])
        eval_path = self.tmp / "fresh.jsonl"
        eval_path.write_text("{}", encoding="utf-8")
        warn, detail = ev.leakage_check(str(eval_path), "mylora",
                                        [self._sample("new_q", "new_a")])
        self.assertFalse(warn)
        self.assertIn("无重叠", detail)

    def test_partial_overlap_below_threshold_does_not_warn(self):
        self._write_train("mylora", [self._sample("q0", "a0")])
        eval_path = self.tmp / "fresh.jsonl"
        eval_path.write_text("{}", encoding="utf-8")
        samples = [self._sample("q0", "a0")] + \
                  [self._sample(f"n{i}", f"m{i}") for i in range(9)]
        warn, detail = ev.leakage_check(str(eval_path), "mylora", samples)
        self.assertFalse(warn)
        self.assertIn("低于 50%", detail)

    def test_missing_training_file_no_warning(self):
        eval_path = self.tmp / "fresh.jsonl"
        eval_path.write_text("{}", encoding="utf-8")
        warn, detail = ev.leakage_check(str(eval_path), "no_such_lora",
                                        [self._sample("q", "a")])
        self.assertFalse(warn)
        self.assertIn("未找到训练集", detail)


class TestModelHelpers(unittest.TestCase):
    def test_resolve_base_model_prefers_served_path(self):
        fake = mock.Mock()
        fake.json.return_value = {"data": [
            {"id": "test_agent_v1"}, {"id": "/opt/models/Qwen3-4B-Instruct"}]}
        with mock.patch.object(ev.requests, "get", return_value=fake):
            self.assertEqual(ev.resolve_base_model(), "/opt/models/Qwen3-4B-Instruct")

    def test_resolve_base_model_explicit_wins(self):
        self.assertEqual(ev.resolve_base_model("/explicit"), "/explicit")

    def test_ensure_lora_loaded_skips_when_present(self):
        fake = mock.Mock()
        fake.json.return_value = {"loaded": ["mylora"]}
        with mock.patch.object(ev.requests, "get", return_value=fake), \
             mock.patch.object(ev.requests, "post") as post:
            ok, msg = ev.ensure_lora_loaded("mylora")
        self.assertTrue(ok)
        self.assertIn("已在显存", msg)
        post.assert_not_called()

    def test_ensure_lora_loaded_posts_when_missing(self):
        get_resp = mock.Mock()
        get_resp.json.return_value = {"loaded": []}
        post_resp = mock.Mock(status_code=200)
        with mock.patch.object(ev.requests, "get", return_value=get_resp), \
             mock.patch.object(ev.requests, "post", return_value=post_resp) as post:
            ok, _ = ev.ensure_lora_loaded("mylora")
        self.assertTrue(ok)
        self.assertEqual(post.call_args.kwargs["json"]["lora_name"], "mylora")

    def test_ensure_lora_loaded_failure_reported(self):
        get_resp = mock.Mock()
        get_resp.json.return_value = {"loaded": []}
        post_resp = mock.Mock(status_code=500, text="boom")
        with mock.patch.object(ev.requests, "get", return_value=get_resp), \
             mock.patch.object(ev.requests, "post", return_value=post_resp):
            ok, msg = ev.ensure_lora_loaded("mylora")
        self.assertFalse(ok)
        self.assertIn("500", msg)


class TestTemplateOverlap(unittest.TestCase):
    """模板重叠：输入不同、但参考输出与训练集参考近乎相同（合成数据的常见陷阱）。"""

    def test_detects_near_identical_reference(self):
        train_refs = ["本文提出一种基于卷积神经网络的图像分类模型，通过注意力机制提升准确率。"]
        samples = [("s", "q1", "本文提出一种基于卷积神经网络的图像分类模型，通过注意力机制提升准确率。"),
                   ("s", "q2", "完全不同的另一段参考文本内容。")]
        ratio, hits = ev.template_overlap_ratio(samples, train_refs)
        self.assertEqual(hits, 1)
        self.assertAlmostEqual(ratio, 0.5, places=3)

    def test_no_overlap(self):
        train_refs = ["训练集里的参考文本。"]
        samples = [("s", "q", "评测集里完全不同的参考文本，用词与句式都不一样。")]
        ratio, hits = ev.template_overlap_ratio(samples, train_refs)
        self.assertEqual(hits, 0)
        self.assertEqual(ratio, 0.0)

    def test_empty_training_refs(self):
        self.assertEqual(ev.template_overlap_ratio([("s", "q", "r")], []), (0.0, 0))

    def test_threshold_is_respected(self):
        train_refs = ["aaaabbbbcccc"]
        samples = [("s", "q", "aaaabbbbcccc")]          # 完全相同
        self.assertEqual(ev.template_overlap_ratio(samples, train_refs, threshold=1.0)[1], 1)
        samples2 = [("s", "q", "aaaabbbbdddd")]         # 接近但阈值 1.0 下不算
        self.assertEqual(ev.template_overlap_ratio(samples2, train_refs, threshold=1.0)[1], 0)

    def test_skips_empty_reference(self):
        self.assertEqual(ev.template_overlap_ratio([("s", "q", "")], ["x"], )[1], 0)


class TestTrainingReferences(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tref_"))
        self._patch = mock.patch.object(ev, "TRAINING_DATA_DIR", self.tmp)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _write(self, lora, pairs):
        (self.tmp / f"{lora}.jsonl").write_text(
            "\n".join(json.dumps({"conversations": [
                {"from": "human", "value": h}, {"from": "gpt", "value": g}]},
                ensure_ascii=False) for h, g in pairs), encoding="utf-8")

    def test_reads_gpt_turns(self):
        self._write("lora_a", [("q1", "a1"), ("q2", "a2")])
        self.assertEqual(ev.training_references("lora_a"), ["a1", "a2"])

    def test_missing_file_returns_empty(self):
        self.assertEqual(ev.training_references("nope"), [])

    def test_tolerates_broken_lines(self):
        p = self.tmp / "lora_b.jsonl"
        p.write_text('{"conversations": [{"from": "gpt", "value": "ok"}]}\n{ bad json\n',
                     encoding="utf-8")
        self.assertEqual(ev.training_references("lora_b"), ["ok"])


class TestInputOverlap(unittest.TestCase):
    """论文级输入重叠：同一篇论文同时进训练集与评测集时必须能查出来。
    （指纹检查比的是整体 (system, human, reference)，两次构建窗口不同就查不出，
      所以这个检查是必需的 —— 实测中真的发生过这种泄漏。）"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ino_"))
        self._patch = mock.patch.object(ev, "TRAINING_DATA_DIR", self.tmp)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _write_train(self, stem, humans):
        p = self.tmp / f"{stem}.jsonl"
        p.write_text("\n".join(json.dumps({"conversations": [
            {"from": "system", "value": "s"},
            {"from": "human", "value": h},
            {"from": "gpt", "value": "t"}]}, ensure_ascii=False) for h in humans),
            encoding="utf-8")
        return p

    def _sample(self, human):
        return ("s", human, "t")

    def test_detects_same_paper(self):
        shared = "METHOD SECTION " + ("alpha beta gamma delta " * 40)
        self._write_train("lora_x", ["请提取方法：\n\n" + shared])
        ds = self.tmp / "lora_x_eval.jsonl"
        ds.write_text("{}", encoding="utf-8")
        warn, ratio, detail = ev.input_overlap_check(str(ds), [self._sample(shared)])
        self.assertTrue(warn)
        self.assertEqual(ratio, 1.0)
        self.assertIn("同一批文本", detail)

    def test_clean_when_disjoint(self):
        self._write_train("lora_x", ["请提取方法：\n\n" + ("zzz yyy xxx " * 40)])
        ds = self.tmp / "lora_x_eval.jsonl"
        ds.write_text("{}", encoding="utf-8")
        warn, ratio, _ = ev.input_overlap_check(
            str(ds), [self._sample("完全不同的一篇论文内容 " * 40)])
        self.assertFalse(warn)
        self.assertEqual(ratio, 0.0)

    def test_missing_training_file_is_not_a_warning(self):
        ds = self.tmp / "nothing_eval.jsonl"
        ds.write_text("{}", encoding="utf-8")
        warn, ratio, detail = ev.input_overlap_check(str(ds), [self._sample("x" * 400)])
        self.assertFalse(warn)
        self.assertIn("未找到训练集", detail)

    def test_stem_matching_strips_eval_suffix(self):
        # 训练集名应为 <lora>.jsonl，而评测集是 <lora>_eval.jsonl
        self._write_train("extract_v2", ["请提取方法：\n\n" + ("qqq www eee " * 40)])
        ds = self.tmp / "extract_v2_eval.jsonl"
        ds.write_text("{}", encoding="utf-8")
        warn, _, detail = ev.input_overlap_check(
            str(ds), [self._sample("请提取方法：\n\n" + ("qqq www eee " * 40))])
        self.assertTrue(warn, detail)


if __name__ == "__main__":
    unittest.main()
