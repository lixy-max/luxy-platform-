# -*- coding: utf-8 -*-
"""S12 测试：图谱/多模态检测与云端路由 + 总架构自检"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import modality_router as mr
import route_table as rt
import architecture_status as ast


class TestModalityDetect(unittest.TestCase):
    def test_vision(self):
        d = mr.detect("识别这张图片/扫描件里的文字并整理成表格")
        self.assertIn("vision", d["kinds"])
        self.assertTrue(d["is_modality"])

    def test_graph(self):
        d = mr.detect("把资料做知识图谱，抽取实体关系三元组")
        self.assertIn("graph", d["kinds"])
        self.assertTrue(d["is_graph"])

    def test_plain_text(self):
        d = mr.detect("帮我统计一下 LoRA 数量")
        self.assertEqual(d["kinds"], [])
        self.assertFalse(d["needs_cloud"])

    def test_multiple(self):
        d = mr.detect("把视频里的语音转写，再做知识图谱")
        self.assertIn("video", d["kinds"])
        self.assertIn("audio", d["kinds"])
        self.assertIn("graph", d["kinds"])


class TestProviderSelection(unittest.TestCase):
    def test_no_provider_blocked(self):
        with mock.patch.object(mr, "list_providers", return_value=[]):
            sel = mr.select_provider(["graph"])
        self.assertFalse(sel["ok"])

    def test_text_provider_graph_ok(self):
        provs = [{"name": "kimi", "api_base": "https://x", "model": "m",
                  "modalities": ["text"], "key_masked": "****"}]
        with mock.patch.object(mr, "list_providers", return_value=provs):
            sel = mr.select_provider(["graph"])
        self.assertTrue(sel["ok"])
        self.assertEqual(sel["provider"]["name"], "kimi")

    def test_vision_requires_vision_provider(self):
        provs = [{"name": "kimi", "api_base": "https://x", "model": "m",
                  "modalities": ["text"], "key_masked": "****"}]
        with mock.patch.object(mr, "list_providers", return_value=provs):
            sel = mr.select_provider(["vision"])
            blk = mr.blocked_reason(["vision"])
        self.assertFalse(sel["ok"])
        self.assertIn("视觉", blk)

    def test_vision_provider_ok(self):
        provs = [{"name": "vlm", "api_base": "https://x", "model": "m",
                  "modalities": ["text", "vision"], "key_masked": "****"}]
        with mock.patch.object(mr, "list_providers", return_value=provs):
            sel = mr.select_provider(["vision", "graph"])
        self.assertTrue(sel["ok"])
        self.assertIn("vision", sel["required"])


class TestRoute(unittest.TestCase):
    def test_plain_local(self):
        with mock.patch.object(mr, "list_providers", return_value=[]):
            r = mr.route("统计 LoRA 数量")
        self.assertEqual(r["route"], "local")

    def test_graph_cloud(self):
        provs = [{"name": "kimi", "api_base": "https://x", "model": "m",
                  "modalities": ["text"], "key_masked": "****"}]
        with mock.patch.object(mr, "list_providers", return_value=provs):
            r = mr.route("做一个知识图谱")
        self.assertEqual(r["route"], "cloud")
        self.assertIn("cloud:", r["target"])

    def test_vision_blocked(self):
        provs = [{"name": "kimi", "api_base": "https://x", "model": "m",
                  "modalities": ["text"], "key_masked": "****"}]
        with mock.patch.object(mr, "list_providers", return_value=provs):
            r = mr.route("识别这张图片")
        self.assertEqual(r["route"], "blocked")
        self.assertIn("视觉", r["reason"])

    def test_summary(self):
        s = mr.summary()
        self.assertIn("keywords", s)
        self.assertIn("providers", s)


class TestNormalizeForcesCloud(unittest.TestCase):
    def test_graph_item_forced_cloud(self):
        n = rt._normalize_item({"content": "对资料做知识图谱关系抽取", "category": "local",
                                "target": "direct"}, 1)
        self.assertEqual(n["category"], "cloud")
        self.assertTrue(n["target"].startswith("cloud:graph"))

    def test_vision_item_forced_cloud(self):
        n = rt._normalize_item({"content": "识别图片里的文字", "category": "local",
                                "target": "direct"}, 1)
        self.assertEqual(n["category"], "cloud")
        self.assertIn("vision", n["target"])

    def test_plain_item_stays_local(self):
        n = rt._normalize_item({"content": "统计 LoRA 数量", "category": "local",
                                "target": "direct"}, 1)
        self.assertEqual(n["category"], "local")


class TestArchitectureStatus(unittest.TestCase):
    def setUp(self):
        self._orig = [(cid, fn) for cid, name, fn in ast.CHECKS]

    def test_status_structure(self):
        st = ast.status(checks=["modality", "metrics"])
        self.assertIn("overall", st)
        self.assertIn("score", st["overall"])
        self.assertEqual(len(st["checks"]), 2)
        self.assertTrue(all("status" in c for c in st["checks"]))

    def test_summary_text(self):
        txt = ast.summary_text()
        self.assertIn("总架构完成度", txt)

    def test_bad_check_is_missing(self):
        def boom():
            raise RuntimeError("x")
        with mock.patch.object(ast, "CHECKS", [("boom", "坏检查", boom)]):
            st = ast.status(checks=["boom"])
        self.assertEqual(st["checks"][0]["status"], "missing")
        self.assertEqual(st["overall"]["missing"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
