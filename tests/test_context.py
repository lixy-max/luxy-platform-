# -*- coding: utf-8 -*-
"""
上下文预算测试：消息裁剪逻辑 + vLLM 上下文窗口自动推导。

背景：旧实现只看模型磁盘体积分档，把 4B FP16（7.6GB）当成 14B AWQ，
给到 4096 上下文 —— 这是"上下文长度不够"的根因，这里用测试钉住正确行为。
"""
import json
import tempfile
import unittest
from pathlib import Path

import db

_TMP = Path(tempfile.mkdtemp(prefix="lora_ctx_test_"))
db.DB_PATH = _TMP / "t.db"          # 必须在 import lora_manager 之前

import lora_manager as lm           # noqa: E402
import vllm_manager as vm           # noqa: E402



# ---- 共享状态守卫（见 _guards.py）----
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _guards import snapshot_shared_state as _snap, \
    assert_shared_state_unchanged as _assert_same

_SHARED_BEFORE = _snap()


def tearDownModule():
    _assert_same(_SHARED_BEFORE, __name__)

class TestEstimateTokens(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(lm.estimate_tokens(""), 0)

    def test_none(self):
        self.assertEqual(lm.estimate_tokens(None), 0)

    def test_grows_with_length(self):
        self.assertLess(lm.estimate_tokens("a" * 100), lm.estimate_tokens("a" * 1000))

    def test_chinese_estimate_is_conservative(self):
        # 1000 汉字应估在 600~1200 tokens（宁可多算，避免超窗）
        n = lm.estimate_tokens("中" * 1000)
        self.assertGreater(n, 600)
        self.assertLess(n, 1200)


class TestFitMessages(unittest.TestCase):
    def _msgs(self, n, each=100):
        out = [{"role": "system", "content": "S" * 200}]
        for i in range(n):
            role = "user" if i % 2 == 0 else "assistant"
            out.append({"role": role, "content": f"m{i}-" + "x" * each})
        return out

    def test_no_trim_when_it_fits(self):
        msgs = self._msgs(3)
        out, dropped, _used, trunc = lm.fit_messages_to_context(msgs, 100000, 2048)
        self.assertEqual(dropped, 0)
        self.assertFalse(trunc)
        self.assertEqual(len(out), len(msgs))

    def test_system_message_always_kept(self):
        out, dropped, _used, _t = lm.fit_messages_to_context(self._msgs(50), 4096, 1024)
        self.assertGreater(dropped, 0)
        self.assertEqual(out[0]["role"], "system")
        self.assertIn("S", out[0]["content"])

    def test_newest_message_kept(self):
        msgs = self._msgs(50)
        out, _d, _u, _t = lm.fit_messages_to_context(msgs, 4096, 1024)
        self.assertEqual(out[-1]["content"], msgs[-1]["content"])

    def test_dropped_count_consistent(self):
        msgs = self._msgs(50)
        out, dropped, _u, _t = lm.fit_messages_to_context(msgs, 4096, 1024)
        self.assertEqual(len(out), len(msgs) - dropped)

    def test_result_within_budget(self):
        limit, max_tokens = 4096, 1024
        out, _d, used, _t = lm.fit_messages_to_context(self._msgs(50), limit, max_tokens)
        reserve = max(512, min(max_tokens, max(256, limit // 3)))
        self.assertLessEqual(used, limit - reserve)

    def test_oversized_single_message_is_truncated_not_dropped(self):
        msgs = [{"role": "system", "content": "sys"},
                {"role": "user", "content": "x" * 100000}]
        out, _d, _u, trunc = lm.fit_messages_to_context(msgs, 4096, 1024)
        self.assertTrue(trunc)
        self.assertIn("已按上下文预算截断", out[-1]["content"])
        self.assertLess(len(out[-1]["content"]), 100000)

    def test_tiny_limit_does_not_crash(self):
        try:
            lm.fit_messages_to_context([{"role": "user", "content": "hi"}], 100, 8192)
        except Exception as e:
            self.fail(f"极小 limit 不应抛异常: {e}")


class TestContextNotices(unittest.TestCase):
    def test_notice_when_dropped(self):
        n = lm.build_context_notices(4096, 3, False)
        self.assertEqual(len(n), 1)
        self.assertIn("3", n[0]["notice"])

    def test_no_notice_when_clean(self):
        self.assertEqual(lm.build_context_notices(4096, 0, False), [])

    def test_notice_when_truncated(self):
        self.assertEqual(len(lm.build_context_notices(4096, 0, True)), 1)


class TestAutoConfig(unittest.TestCase):
    def _fake_model(self, layers=36, heads=32, kv_heads=8, head_dim=128,
                    max_pos=262144, hidden=None):
        d = Path(tempfile.mkdtemp(prefix="fake_model_"))
        if hidden is None:
            hidden = heads * (head_dim or 128)
        cfg = {
            "model_type": "qwen3",
            "num_hidden_layers": layers,
            "num_attention_heads": heads,
            "num_key_value_heads": kv_heads,
            "hidden_size": hidden,
            "max_position_embeddings": max_pos,
        }
        if head_dim is not None:
            cfg["head_dim"] = head_dim
        (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        return d

    def test_kv_bytes_per_token(self):
        # 2(K/V) × 36层 × 8头 × 128 × 2B = 147456
        self.assertEqual(vm.kv_bytes_per_token(self._fake_model()), 147456)

    def test_kv_bytes_fallback_to_hidden_over_heads(self):
        got = vm.kv_bytes_per_token(self._fake_model(heads=32, head_dim=None))
        self.assertEqual(got, 2 * 36 * 8 * 128 * 2)

    def test_kv_bytes_missing_config(self):
        self.assertIsNone(vm.kv_bytes_per_token("/nonexistent/model"))

    def test_4b_fp16_gets_32k_context(self):
        """回归：4B FP16（7.6GB 磁盘）曾被误判为 14B AWQ 档，压到 4096。"""
        cfg = vm.auto_config_for_model(self._fake_model(), size_gb=7.6)
        self.assertEqual(cfg["max_model_len"], 32768)

    def test_large_awq_model_gets_smaller_context(self):
        d = self._fake_model(layers=48, kv_heads=8, head_dim=128)
        cfg = vm.auto_config_for_model(d, size_gb=9.4)
        self.assertLessEqual(cfg["max_model_len"], 16384)
        self.assertGreaterEqual(cfg["max_model_len"], 2048)

    def test_context_capped_by_model_max_position(self):
        cfg = vm.auto_config_for_model(self._fake_model(max_pos=8192), size_gb=7.6)
        self.assertLessEqual(cfg["max_model_len"], 8192)

    def test_falls_back_to_size_buckets_without_config(self):
        cfg = vm.auto_config_for_model("/nonexistent/model", size_gb=7.6)
        self.assertEqual(cfg["max_model_len"], 4096)      # 旧分档兜底
        cfg2 = vm.auto_config_for_model("/nonexistent/model", size_gb=2.5)
        self.assertEqual(cfg2["max_model_len"], 16384)

    def test_bigger_model_never_gets_more_context(self):
        small = vm.auto_config_for_model(self._fake_model(), size_gb=2.5)
        big = vm.auto_config_for_model(self._fake_model(), size_gb=12.0)
        self.assertGreaterEqual(small["max_model_len"], big["max_model_len"])


if __name__ == "__main__":
    unittest.main()
