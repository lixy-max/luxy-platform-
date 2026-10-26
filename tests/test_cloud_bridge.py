# -*- coding: utf-8 -*-
"""cloud_bridge 测试：Kimi 参数适配 / 429 退避 / reasoning 兜底（全打桩）"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cloud_bridge


def _resp(code=200, payload=None):
    m = mock.Mock()
    m.status_code = code
    m.json.return_value = payload or {}
    if code >= 400:
        m.raise_for_status.side_effect = Exception("HTTP %d" % code)
    else:
        m.raise_for_status.return_value = None
    return m


class TestCloudBridge(unittest.TestCase):
    def test_available_false_without_key(self):
        with mock.patch.object(cloud_bridge, "_provider", return_value={}):
            self.assertFalse(cloud_bridge.available())

    def test_available_true(self):
        with mock.patch.object(cloud_bridge, "_provider",
                               return_value={"api_base": "https://api.moonshot.cn/v1",
                                             "api_key": "k", "model": "kimi-k2.6"}):
            self.assertTrue(cloud_bridge.available())

    def test_kimi_temperature_forced_1(self):
        calls = []

        def fake_post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            return _resp(200, {"choices": [{"message": {"content": "hi"}}]})

        with mock.patch.object(cloud_bridge, "_provider",
                               return_value={"api_base": "https://api.moonshot.cn/v1",
                                             "api_key": "k", "model": "kimi-k2.6"}), \
             mock.patch("requests.post", side_effect=fake_post):
            out = cloud_bridge.ask("hi", temperature=0.1)
        self.assertEqual(out, "hi")
        self.assertEqual(calls[0]["temperature"], 1)

    def test_non_kimi_uses_given_temperature(self):
        calls = []

        def fake_post(url, headers=None, json=None, timeout=None):
            calls.append(json)
            return _resp(200, {"choices": [{"message": {"content": "x"}}]})

        with mock.patch.object(cloud_bridge, "_provider",
                               return_value={"api_base": "https://api.deepseek.com/v1",
                                             "api_key": "k", "model": "deepseek-chat"}), \
             mock.patch("requests.post", side_effect=fake_post):
            cloud_bridge.ask("hi", temperature=0.3)
        self.assertEqual(calls[0]["temperature"], 0.3)

    def test_reasoning_fallback(self):
        payload = {"choices": [{"message": {"content": "", "reasoning_content": "思考后答案"}}]}
        with mock.patch.object(cloud_bridge, "_provider",
                               return_value={"api_base": "https://api.moonshot.cn/v1",
                                             "api_key": "k", "model": "kimi-k2.6"}), \
             mock.patch("requests.post", return_value=_resp(200, payload)):
            out = cloud_bridge.ask("hi")
        self.assertIn("思考后答案", out)

    def test_retry_on_429(self):
        seq = [_resp(429, {"error": "rate"}), _resp(200, {"choices": [{"message": {"content": "ok"}}]})]
        idx = {"i": 0}

        def fake_post(*a, **k):
            r = seq[min(idx["i"], len(seq) - 1)]
            idx["i"] += 1
            return r

        with mock.patch.object(cloud_bridge, "_provider",
                               return_value={"api_base": "https://api.moonshot.cn/v1",
                                             "api_key": "k", "model": "kimi-k2.6"}), \
             mock.patch("requests.post", side_effect=fake_post), \
             mock.patch("time.sleep"):
            out = cloud_bridge.ask("hi")
        self.assertEqual(out, "ok")

    def test_no_key_raises(self):
        with mock.patch.object(cloud_bridge, "_provider", return_value={}):
            with self.assertRaises(RuntimeError):
                cloud_bridge.ask("hi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
