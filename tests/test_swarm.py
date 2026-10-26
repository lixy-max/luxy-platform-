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
        with mock.patch.object(swarm, "_models_text", return_value="模型基准库测试文本"):
            p = swarm.build_prompt(CAPS, KBS)
        self.assertIn("lora:agri_expert_v1", p)
        self.assertIn("bcpcn", p)
        self.assertIn("capability_id", p)
        self.assertIn("chimera", p)
        self.assertIn("模型基准库测试文本", p)

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


class TestMultiStep(unittest.TestCase):
    def test_validate_multi(self):
        dec = swarm.validate_decision({
            "route": "multi",
            "steps": [
                {"route": "kb", "kb_names": ["bcpcn"], "instruction": "查资料"},
                {"route": "capability", "capability_id": "agri_expert_v1", "instruction": "总结"},
                {"route": "direct", "instruction": "写摘要"},
            ],
            "reason": "多步",
        }, CAPS, KBS)
        self.assertEqual(dec["route"], "multi")
        self.assertEqual(len(dec["steps"]), 3)
        self.assertEqual(dec["steps"][0]["kb_names"], ["bcpcn"])
        self.assertEqual(dec["steps"][1]["capability_id"], "lora:agri_expert_v1")
        self.assertEqual(dec["steps"][2]["route"], "direct")

    def test_validate_multi_filters_invalid(self):
        dec = swarm.validate_decision({
            "route": "multi",
            "steps": [
                {"route": "bogus"},
                {"route": "kb", "kb_names": ["bcpcn"]},
                {"route": "capability", "capability_id": "lora:no_such"},
            ],
        }, CAPS, KBS)
        self.assertEqual(dec["route"], "multi")
        # bogus 被丢；无匹配 capability 的步骤退化为 chimera 被丢弃；剩 1 个 kb
        self.assertEqual(len(dec["steps"]), 1)
        self.assertEqual(dec["steps"][0]["route"], "kb")

    def test_validate_multi_all_invalid(self):
        self.assertIsNone(swarm.validate_decision(
            {"route": "multi", "steps": [{"route": "bogus"}]}, CAPS, KBS))

    def test_execute_multi_ok(self):
        with mock.patch.object(swarm, "kb_query", return_value=[{"text": "路由器资料"}]), \
             mock.patch.object(swarm, "main_answer", return_value="综合结果"), \
             mock.patch.object(swarm, "worker_up", return_value=True), \
             mock.patch.object(swarm, "activate_lora", return_value={"ok": True, "lora_name": "agri_expert_v1"}), \
             mock.patch.object(swarm, "worker_chat", return_value="农学总结"), \
             mock.patch.object(swarm, "deactivate_lora", return_value={}):
            dec = {"route": "multi", "steps": [
                {"route": "kb", "kb_names": ["bcpcn"], "instruction": "查资料"},
                {"route": "capability", "capability_id": "lora:agri_expert_v1",
                 "need_worker": True, "load_lora": True, "instruction": "总结"},
                {"route": "direct", "instruction": "写摘要"},
            ]}
            r = swarm.execute(dec, "多步任务")
        self.assertTrue(r["ok"])
        self.assertEqual(r["route"], "multi")
        self.assertEqual(len(r["meta"]["steps"]), 3)
        self.assertEqual(r["meta"]["steps"][1]["route"], "capability")
        self.assertEqual(r["content"], "综合结果")

    def test_execute_multi_failure_stops(self):
        with mock.patch.object(swarm, "kb_query", return_value=[]), \
             mock.patch.object(swarm, "main_answer", return_value="ok"), \
             mock.patch.object(swarm, "worker_up", return_value=True), \
             mock.patch.object(swarm, "activate_lora", return_value={"ok": False}):
            dec = {"route": "multi", "steps": [
                {"route": "kb", "kb_names": ["bcpcn"], "instruction": "查"},
                {"route": "capability", "capability_id": "lora:agri_expert_v1",
                 "need_worker": True, "load_lora": True, "instruction": "总结"},
            ]}
            r = swarm.execute(dec, "多步任务")
        self.assertFalse(r["ok"])
        self.assertEqual(r["meta"]["failed_step"], 2)


class TestExternalCapability(unittest.TestCase):
    EXTCAP = {"cap_id": "ext:github:astanin-python-tabulate", "name": "astanin/python-tabulate",
              "kind": "external", "status": "shadow", "installed": True, "executable": True,
              "entry": {"module": "tabulate", "function": "tabulate"},
              "args_schema": {"positional": ["data"], "headers": "表头"},
              "tags": ["external", "installed", "表格"]}

    def test_route_filled_with_cap_id_tolerated(self):
        caps = CAPS + [self.EXTCAP]
        dec = swarm.validate_decision(
            {"route": "ext:github:astanin-python-tabulate",
             "capability_input": {"data": [["A", 1]], "headers": ["名称", "值"]},
             "need_worker": True}, caps, KBS)
        self.assertIsNotNone(dec)
        self.assertEqual(dec["route"], "capability")
        self.assertEqual(dec["capability_id"], "ext:github:astanin-python-tabulate")
        self.assertEqual(dec["capability_input"]["headers"], ["名称", "值"])

    def test_execute_external_capability(self):
        caps = CAPS + [self.EXTCAP]
        with mock.patch("capabilities.get_capability", return_value=self.EXTCAP), \
             mock.patch("sandbox_exec.execute_capability",
                        return_value={"ok": True, "result": "| A | 1 |", "meta": {}}):
            r = swarm.execute({"route": "capability",
                               "capability_id": "ext:github:astanin-python-tabulate",
                               "capability_input": {"data": [["A", 1]]}}, "排表格")
        self.assertTrue(r["ok"])
        self.assertEqual(r["content"], "| A | 1 |")
        self.assertEqual(r["meta"]["kind"], "external")

    def test_execute_external_not_installed(self):
        cap = dict(self.EXTCAP, installed=False)
        with mock.patch("capabilities.get_capability", return_value=cap):
            r = swarm.execute({"route": "capability",
                               "capability_id": "ext:github:astanin-python-tabulate"}, "排表格")
        self.assertFalse(r["ok"])
        self.assertIn("未安装", r["meta"]["error"])

    def test_prompt_marks_external(self):
        caps = CAPS + [self.EXTCAP]
        with mock.patch.object(swarm, "_models_text", return_value=""):
            p = swarm.build_prompt(caps, KBS)
        self.assertIn("外部工具", p)
        self.assertIn("capability_input", p)


class TestKbQueryClean(unittest.TestCase):
    def test_strip_kb_name(self):
        self.assertEqual(swarm._clean_kb_query("从知识库 bcpcn 路由器是什么", ["bcpcn"]), "路由器是什么")

    def test_strip_kb_prefix(self):
        self.assertEqual(swarm._clean_kb_query("知识库：bcpcn 里的 Hub", ["bcpcn"]), "里的 Hub")

    def test_keep_normal(self):
        self.assertEqual(swarm._clean_kb_query("路由器是什么", ["bcpcn"]), "路由器是什么")


if __name__ == "__main__":
    unittest.main(verbosity=2)
