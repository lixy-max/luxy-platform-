# -*- coding: utf-8 -*-
"""
纯函数单元测试：不启动服务、不写业务数据、不碰 GPU。

覆盖 workflow_engine 的图/提示词/解析逻辑、train_lora 的参数推导、
kb 的分块逻辑 —— 这些都是"改错了不会立刻报错，但结果会变差"的地方。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import workflow_engine as we
import train_lora as tl
import kb



# ---- 共享状态守卫（见 _guards.py）----
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _guards import snapshot_shared_state as _snap, \
    assert_shared_state_unchanged as _assert_same

_SHARED_BEFORE = _snap()


def tearDownModule():
    _assert_same(_SHARED_BEFORE, __name__)

class TestTopologicalSort(unittest.TestCase):
    def _fork_join(self):
        return [
            {"id": "a", "type": "input", "input_from": []},
            {"id": "b", "type": "process", "input_from": ["a"]},
            {"id": "c", "type": "terminal", "input_from": ["a"]},
            {"id": "d", "type": "aggregate", "input_from": ["b", "c"]},
        ]

    def test_order_respects_dependencies(self):
        order = [n["id"] for n in we.topological_sort(self._fork_join())]
        self.assertEqual(len(order), 4)
        self.assertLess(order.index("a"), order.index("b"))
        self.assertLess(order.index("a"), order.index("c"))
        self.assertLess(order.index("b"), order.index("d"))
        self.assertLess(order.index("c"), order.index("d"))

    def test_fork_allows_multiple_downstream(self):
        # 一个节点被多个下游依赖，不应重复执行
        order = [n["id"] for n in we.topological_sort(self._fork_join())]
        self.assertEqual(sorted(order), ["a", "b", "c", "d"])

    def test_cycle_raises(self):
        nodes = [
            {"id": "x", "type": "process", "input_from": ["y"]},
            {"id": "y", "type": "process", "input_from": ["x"]},
        ]
        with self.assertRaises(RuntimeError):
            we.topological_sort(nodes)

    def test_dotted_dependency_uses_parent_node(self):
        nodes = [
            {"id": "p", "type": "process", "input_from": []},
            {"id": "q", "type": "terminal", "input_from": ["p.method"]},
        ]
        order = [n["id"] for n in we.topological_sort(nodes)]
        self.assertLess(order.index("p"), order.index("q"))

    def test_full_text_dependency_is_not_a_node(self):
        nodes = [
            {"id": "z", "type": "process", "input_from": ["full_text"]},
        ]
        order = [n["id"] for n in we.topological_sort(nodes)]
        self.assertEqual(order, ["z"])


class TestRenderPrompt(unittest.TestCase):
    def test_plain_substitution(self):
        out = we.render_prompt("方法：{a.method}", {"a.method": "CNN"})
        self.assertEqual(out, "方法：CNN")

    def test_missing_key_left_untouched(self):
        out = we.render_prompt("X{nope}Y", {"a": "b"})
        self.assertEqual(out, "X{nope}Y")

    def test_input_placeholder_joins_all(self):
        out = we.render_prompt("{输入}", {"a": "1", "b": "2"})
        self.assertIn("【a】", out)
        self.assertIn("【b】", out)
        self.assertIn("1", out)
        self.assertIn("2", out)

    def test_english_input_alias(self):
        out = we.render_prompt("{input}", {"a": "1"})
        self.assertIn("1", out)


class TestBuildNodeInput(unittest.TestCase):
    def _ctx(self):
        return {
            "full_text": "FULLTEXT",
            "nodes": {
                "p": {"method": "M1", "data": "D1"},
                "t": "plain string",
            },
        }

    def test_full_text(self):
        out = we.build_node_input({"input_from": ["full_text"]}, self._ctx())
        self.assertEqual(out["full_text"], "FULLTEXT")

    def test_dotted_extracts_dict_field(self):
        out = we.build_node_input({"input_from": ["p.method"]}, self._ctx())
        self.assertEqual(out["p.method"], "M1")

    def test_dotted_missing_field_is_empty(self):
        out = we.build_node_input({"input_from": ["p.nope"]}, self._ctx())
        self.assertEqual(out["p.nope"], "")

    def test_dict_value_is_json_dumped(self):
        out = we.build_node_input({"input_from": ["p"]}, self._ctx())
        self.assertIn("method", out["p"])

    def test_scalar_value_stringified(self):
        out = we.build_node_input({"input_from": ["t"]}, self._ctx())
        self.assertEqual(out["t"], "plain string")


class TestParseJsonResponse(unittest.TestCase):
    def test_clean_json(self):
        self.assertEqual(we.parse_json_response('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        raw = '```json\n{"a": 2}\n```'
        self.assertEqual(we.parse_json_response(raw), {"a": 2})

    def test_json_after_prose(self):
        raw = '好的，结果如下：\n{"a": 3}\n以上。'
        self.assertEqual(we.parse_json_response(raw), {"a": 3})

    def test_think_block_stripped(self):
        # 用 chr(60) 拼 '<'：某些写入通道会把形如 <think> 的字面量当标签吃掉
        LT = chr(60)
        raw = f"{LT}think>\n我先想一想\n{LT}/think>\n{{\"a\": 4}}"
        self.assertEqual(we.parse_json_response(raw), {"a": 4})

    def test_no_braces_returns_raw(self):
        out = we.parse_json_response("没有 JSON")
        self.assertIn("_raw", out)

    def test_broken_json_flagged(self):
        # 有完整花括号但解析失败时，会带 _parse_error 标记
        out = we.parse_json_response('{"a": 1, }')
        self.assertTrue(out.get("_parse_error"))

    def test_truncated_json_returns_raw_without_flag(self):
        # 已知的不对称行为：缺右花括号时只返回 _raw，不带 _parse_error。
        # 下游靠 _raw 兜底，不会崩，但调用方无法区分"没 JSON"和"JSON 被截断"。
        out = we.parse_json_response('{"a": 1,')
        self.assertIn("_raw", out)
        self.assertFalse(out.get("_parse_error"))


class TestStripThink(unittest.TestCase):
    LT = chr(60)  # '<'

    def test_removes_paired_think_block(self):
        raw = f"{self.LT}think>\n推理中\n{self.LT}/think>\n答"
        self.assertEqual(we.strip_think(raw), "答")

    def test_unclosed_think_strips_to_first_gt(self):
        """无闭合标签时的兜底：截到第一个 '>' 之后。行为粗糙但已明确。"""
        raw = f"{self.LT}think>残留正文"
        self.assertEqual(we.strip_think(raw), "残留正文")

    def test_removes_leading_boilerplate(self):
        self.assertEqual(we.strip_think("好的，下面是结果：正文"), "正文")

    def test_passthrough_when_clean(self):
        self.assertEqual(we.strip_think("正文"), "正文")


class TestMigrateOldConfig(unittest.TestCase):
    def test_new_format_untouched(self):
        cfg = {"nodes": [{"id": "a", "type": "input"}]}
        self.assertIs(we.migrate_old_config(cfg), cfg)

    def test_old_format_migrated(self):
        old = {
            "workflow_name": "老流程",
            "field_extraction": {"id": "fe", "prompt": "P", "lora": "L"},
            "nodes": [{"id": "n1", "description": "d", "input_from": ["fe"], "prompt": "Q"}],
        }
        out = we.migrate_old_config(old)
        self.assertTrue(out.get("_migrated_from_old"))
        types = {n["id"]: n["type"] for n in out["nodes"]}
        self.assertEqual(types["fe"], "process")
        self.assertEqual(types["n1"], "terminal")


class TestTrainLoraParams(unittest.TestCase):
    def test_recommend_rank_boundaries(self):
        self.assertEqual(tl.recommend_rank(50), 8)
        self.assertEqual(tl.recommend_rank(100), 16)
        self.assertEqual(tl.recommend_rank(500), 32)
        self.assertEqual(tl.recommend_rank(2000), 64)

    def test_recommend_epochs_boundaries(self):
        self.assertEqual(tl.recommend_epochs(50), 5)
        self.assertEqual(tl.recommend_epochs(100), 3)
        self.assertEqual(tl.recommend_epochs(500), 2)

    def test_compute_save_steps_at_least_one(self):
        self.assertGreaterEqual(tl.compute_save_steps(1, 1), 1)

    def test_compute_save_steps_scales(self):
        self.assertEqual(tl.compute_save_steps(100, 3, grad_accum=8), 18)


class TestModelDetection(unittest.TestCase):
    def _model_dir(self, config):
        d = tempfile.mkdtemp(prefix="fake_model_")
        with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f)
        return d

    def test_template_qwen3(self):
        d = self._model_dir({"model_type": "qwen3"})
        self.assertEqual(tl.detect_template(d), "qwen3_nothink")

    def test_template_qwen2(self):
        d = self._model_dir({"model_type": "qwen2"})
        self.assertEqual(tl.detect_template(d), "qwen")

    def test_template_llama(self):
        d = self._model_dir({"model_type": "llama"})
        self.assertEqual(tl.detect_template(d), "llama3")

    def test_template_unknown_falls_back(self):
        d = self._model_dir({"model_type": "mystery"})
        self.assertEqual(tl.detect_template(d), "qwen")

    def test_template_missing_config(self):
        self.assertEqual(tl.detect_template("/nonexistent/model"), "qwen")

    def test_quantization_awq(self):
        d = self._model_dir({"quantization_config": {"quant_method": "awq"}})
        self.assertEqual(tl.detect_model_quantization(d), "awq")

    def test_quantization_gptq(self):
        d = self._model_dir({"quantization_config": {"quant_method": "gptq"}})
        self.assertEqual(tl.detect_model_quantization(d), "gptq")

    def test_quantization_fp8(self):
        d = self._model_dir({"quantization_config": {"quant_method": "fp8"}})
        self.assertEqual(tl.detect_model_quantization(d), "fp8")

    def test_quantization_none_for_fp16(self):
        d = self._model_dir({"model_type": "qwen3"})
        self.assertIsNone(tl.detect_model_quantization(d))

    def test_quantized_base_must_be_rejected_by_train(self):
        """AWQ 底座必须被拒绝 —— 这是"静默训出废 LoRA"的防线。"""
        d = self._model_dir({"quantization_config": {"quant_method": "awq"}})
        with self.assertRaises(RuntimeError) as cm:
            tl.train(d, "/nonexistent/dataset.jsonl", "/tmp/out", "x", 100)
        self.assertIn("AWQ", str(cm.exception).upper())


class TestLlamafactoryCliResolution(unittest.TestCase):
    def test_resolves_next_to_interpreter(self):
        """回归：后端用绝对路径 python 启动时，也必须能找到 CLI（PATH 里没有 venv/bin）。"""
        d = tempfile.mkdtemp(prefix="fakevenv_")
        fake_py = os.path.join(d, "python")
        open(fake_py, "w").close()
        cli = os.path.join(d, "llamafactory-cli")
        open(cli, "w").close()
        with mock.patch.object(sys, "executable", fake_py):
            self.assertEqual(tl.resolve_llamafactory_cli(), cli)

    def test_falls_back_to_path_lookup(self):
        empty = tempfile.mkdtemp(prefix="emptyvenv_")
        fake_py = os.path.join(empty, "python")
        open(fake_py, "w").close()
        with mock.patch.object(sys, "executable", fake_py), \
             mock.patch("shutil.which", return_value="/usr/bin/llamafactory-cli"):
            self.assertEqual(tl.resolve_llamafactory_cli(), "/usr/bin/llamafactory-cli")

    def test_raises_clear_error_when_missing(self):
        empty = tempfile.mkdtemp(prefix="emptyvenv2_")
        fake_py = os.path.join(empty, "python")
        open(fake_py, "w").close()
        with mock.patch.object(sys, "executable", fake_py), \
             mock.patch("shutil.which", return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                tl.resolve_llamafactory_cli()
            self.assertIn("llamafactory-cli", str(cm.exception))

    def test_real_environment_has_cli(self):
        """在当前 venv 里应当真实可用（用 venv python 跑测试时）。"""
        try:
            path = tl.resolve_llamafactory_cli()
        except RuntimeError:
            self.skipTest("当前解释器旁边没有 llamafactory-cli")
        self.assertTrue(os.path.exists(path))


class TestKbSplitText(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(kb.split_text(""), [])

    def test_short_text_single_chunk(self):
        self.assertEqual(kb.split_text("短文本"), ["短文本"])

    def test_paragraphs_batched(self):
        text = "\n\n".join(["段落" + str(i) for i in range(5)])
        chunks = kb.split_text(text, chunk_size=20)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertTrue(c.strip())

    def test_long_paragraph_split_by_sentence(self):
        # _split_sentences 要求标点后跟空白，否则整段切不开
        text = " ".join(["这是一句话。"] * 40)
        chunks = kb.split_text(text, chunk_size=60)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 80)

    def test_content_preserved(self):
        text = "第一段内容。\n\n第二段内容。"
        joined = "".join(kb.split_text(text, chunk_size=1000))
        self.assertIn("第一段内容", joined)
        self.assertIn("第二段内容", joined)


if __name__ == "__main__":
    unittest.main()
