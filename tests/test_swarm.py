# -*- coding: utf-8 -*-
"""P2.5 蜂群编排模块测试（全部打桩，不依赖真实 vLLM/网络）"""
import json
import sys
import os
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import swarm  # noqa: E402


CAPS = [
    {"cap_id": "lora:agri_expert_v1", "name": "agri_expert_v1", "status": "shadow",
     "tags": ["lora", "single-step", "农业"], "compatible": True},
    {"cap_id": "lora:t1_law_lora_v1", "name": "t1_law_lora_v1", "status": "shadow",
     "tags": ["lora", "single-step", "法律"], "compatible": True},
]
KBS = [("bcpcn", "BCPCN 协议知识"), ("kb_toolchain", "平台工具链")]


class TestPrompt(unittest.TestCase):
    def test_build_prompt_contains_assets(self):
        p = swarm.build_prompt(CAPS, KBS)
        self.assertIn("lora:agri_expert_v1", p)
        self.assertIn("bcpcn", p)
        self.assertIn("capability_id", p)
        self.assertIn("chimera", p)

    def test_kb_allow_default(self):
        self.assertIn("bcpcn", swarm.KB_ALLOW)
        self.assertIn("kb_toolchain", swarm.KB_ALLOW)


class TestDecision(unittest.TestCase):
    def test_parse_decision_plain_and_markdown(self):
        d = swarm.parse_decision('{"route":"direct"}')
        self.assertEqual(d["route"], "direct")
        d2 = swarm.parse_decision('说明\n```json\n{"route":"kb","kb_names":["bcpcn"]}\n```')
        self.assertEqual(d2["route"], "kb")
        self.assertIsNone(swarm.parse_decision("没有 JSON"))

    def test_validate_bare_cap_name(self):
        dec = swarm.validate_decision(
            {"route": "capability", "capability_id": "agri_expert_v1",
             "need_worker": True, "load_lora": True}, CAPS, KBS)
        self.assertEqual(dec["capability_id"], "lora:agri_expert_v1")
        self.assertTrue(dec["need_worker"] and dec["load_lora"])

    def test_validate_unknown_cap_becomes_chimera(self):
        dec = swarm.validate_decision(
            {"route": "capability", "capability_id": "lora:no_such",
             "need_worker": True, "load_lora": True}, CAPS, KBS)
        self.assertEqual(dec["route"], "chimera")
        self.assertIsNone(dec["capability_id"])

    def test_validate_filters_unknown_kb(self):
        dec = swarm.validate_decision(
            {"route": "kb", "kb_names": ["bcpcn", "no_such_kb"]}, CAPS, KBS)
        self.assertEqual(dec["kb_names"], ["bcpcn"])

    def test_validate_bad_route(self):
        self.assertIsNone(swarm.validate_decision({"route": "bogus"}, CAPS, KBS))

    def test_validate_direct_no_worker(self):
        dec = swarm.validate_decision(
            {"route": "direct", "need_worker": True, "load_lora": True}, CAPS, KBS)
        self.assertFalse(dec["need_worker"])
        self.assertFalse(dec["load_lora"])


class TestExecute(unittest.TestCase):
    def test_execute_direct(self):
        with mock.patch.object(swarm, "main_answer", return_value="答案"):
            r = swarm.execute({"route": "direct"}, "问题")
        self.assertTrue(r["ok"])
        self.assertEqual(r["content"], "答案")
        self.assertEqual(r["route"], "direct")

    def test_execute_kb(self):
        with mock.patch.object(swarm, "kb_query", return_value=[{"text": "片段"}]):
            with mock.patch.object(swarm, "main_answer", return_value="基于片段的回答") as m:
                r = swarm.execute({"route": "kb", "kb_names": ["bcpcn"]}, "问")
        self.assertTrue(r["ok"])
        self.assertIn("[bcpcn]", m.call_args[0][1])
        self.assertEqual(r["meta"]["hits"], 1)

    def test_execute_capability_worker_fail(self):
        with mock.patch.object(swarm, "worker_up", return_value=True):
            with mock.patch.object(swarm, "activate_lora", return_value={"ok": False}):
                r = swarm.execute({"route": "capability", "capability_id": "lora:agri_expert_v1",
                                   "need_worker": True, "load_lora": True}, "问")
        self.assertFalse(r["ok"])
        self.assertIn("LoRA 装载失败", r["meta"]["error"])

    def test_execute_capability_ok(self):
        with mock.patch.object(swarm, "worker_up", return_value=True), \
             mock.patch.object(swarm, "activate_lora", return_value={"ok": True, "lora_name": "agri_expert_v1"}), \
             mock.patch.object(swarm, "worker_chat", return_value="专业回答"), \
             mock.patch.object(swarm, "deactivate_lora", return_value={}):
            r = swarm.execute({"route": "capability", "capability_id": "lora:agri_expert_v1",
                               "need_worker": True, "load_lora": True}, "玉米缺氮")
        self.assertTrue(r["ok"])
        self.assertEqual(r["content"], "专业回答")
        self.assertEqual(r["meta"]["capability_id"], "lora:agri_expert_v1")

    def test_execute_chimera(self):
        fake_plan = {"gap": "pdf 表格抽取", "candidates": [], "next_action": "x"}
        with mock.patch("chimera.plan", return_value=fake_plan), \
             mock.patch("chimera.render_plan_text", return_value="【奇美拉缺口计划】..."):
            r = swarm.execute({"route": "chimera"}, "做个 PDF 表格抽取工具")
        self.assertTrue(r["ok"])
        self.assertEqual(r["route"], "chimera")
        self.assertIn("奇美拉", r["content"])

    def test_orchestrate_decide_fail_fallback(self):
        with mock.patch.object(swarm, "decide", return_value=(None, (CAPS, KBS))), \
             mock.patch.object(swarm, "main_answer", return_value="直接答案"):
            r = swarm.orchestrate("你好")
        self.assertEqual(r["route"], "direct")
        self.assertEqual(r["content"], "直接答案")
        self.assertIn("fallback", r["meta"])

    def test_orchestrate_empty_task(self):
        r = swarm.orchestrate("  ")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
