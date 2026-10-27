# -*- coding: utf-8 -*-
"""S18 蜂巢中枢调度器单元测试（Mock LLM，不依赖 vLLM）"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hive_orchestrator as hv

INV = {
    "workers": [{"id": "w1", "base": "Qwen2.5-7B-Instruct-AWQ", "status": "ready",
                 "max_loras": 2, "loaded": ["code_v2"], "slots_free": 1,
                 "endpoint": "http://127.0.0.1:8002"}],
    "loras": [{"id": "base", "base": "7B", "rank": 0, "score": 0.6, "capability": ["general"]},
              {"id": "code_v2", "base": "7B", "rank": 16, "score": 0.86, "capability": ["code"]},
              {"id": "format_v1", "base": "7B", "rank": 8, "score": 0.79, "capability": ["format"]}],
    "cloud": [{"provider": "deepseek", "model": "deepseek-flash", "cost": "low"},
              {"provider": "kimi", "model": "kimi-k2.6", "cost": "medium"}],
    "sim": [{"scenario": "http_concurrent", "desc": "并发压测"},
            {"scenario": "dirty_data", "desc": "脏数据隔离"}],
    "tools": ["write_sheet", "read_sheet"],
    "kbs": ["demo_kb"],
    "policy": {"sim_keywords": hv.SIM_KEYWORDS,
               "failover": {"max_errors": 3, "timeout_s": 60},
               "daily_cloud_budget": 200},
}

GOOD_LOCAL = {
    "target": "local", "confidence": 0.9, "task_type": "data",
    "intent_summary": "表格排序", "subtasks": [{"id": 1, "desc": "排序并写回",
                                              "executor": "local:format_v1"}],
    "routing": {"worker_id": "w1", "lora_id": "format_v1", "why": "格式能力命中",
                "timeout_s": 60, "max_retries": 1, "escalate": False},
    "cloud": {"provider": "", "model": "", "context_policy": "digest", "why": "", "est_cost": "low"},
    "sim": {"scenario": "", "users": 0, "duration_s": 0, "dirty_policy": "", "report_to": ""},
    "fallback_chain": ["local:format_v1", "cloud:deepseek/deepseek-flash"],
    "needs_clarification": False, "reason_trace": ["S2: format_v1 命中"],
}

GOOD_CLOUD = {
    "target": "cloud", "confidence": 0.85, "task_type": "rag",
    "intent_summary": "合同法规比对", "subtasks": [{"id": 1, "desc": "逐条比对",
                                                "executor": "cloud:deepseek/deepseek-flash"}],
    "routing": {"worker_id": "", "lora_id": "", "why": "本地能力不足",
                "timeout_s": 180, "max_retries": 1, "escalate": True},
    "cloud": {"provider": "deepseek", "model": "deepseek-flash", "context_policy": "full",
              "why": "长链推理", "est_cost": "low"},
    "sim": {"scenario": "", "users": 0, "duration_s": 0, "dirty_policy": "", "report_to": ""},
    "fallback_chain": ["cloud:deepseek/deepseek-flash", "local:base"],
    "needs_clarification": False, "reason_trace": ["S3: 本地缺工具"],
}


class TestValidate(unittest.TestCase):
    def test_good_local(self):
        d, errs = hv.validate_decision(GOOD_LOCAL, "把表格按单价排序", INV)
        self.assertEqual(errs, [])
        self.assertEqual(d["target"], "local")
        self.assertEqual(d["routing"]["lora_id"], "format_v1")

    def test_good_cloud(self):
        d, errs = hv.validate_decision(GOOD_CLOUD, "合同和法规逐条比对", INV)
        self.assertEqual(errs, [])
        self.assertEqual(d["cloud"]["provider"], "deepseek")

    def test_bad_target(self):
        bad = json.loads(json.dumps(GOOD_LOCAL))
        bad["target"] = "magic"
        d, errs = hv.validate_decision(bad, "普通任务", INV)
        self.assertTrue(errs)
        self.assertIn(d["target"], ("local", "cloud", "sim"))

    def test_sim_keyword_override(self):
        bad = json.loads(json.dumps(GOOD_LOCAL))
        d, errs = hv.validate_decision(bad, "用500并发压测路由", INV)
        self.assertEqual(d["target"], "sim")
        self.assertTrue(any("S1" in e for e in errs))
        self.assertEqual(d["sim"]["scenario"], "http_concurrent")

    def test_unknown_worker_lora_normalized(self):
        bad = json.loads(json.dumps(GOOD_LOCAL))
        bad["routing"]["worker_id"] = "ghost"
        bad["routing"]["lora_id"] = "nope"
        d, errs = hv.validate_decision(bad, "普通本地任务", INV)
        self.assertTrue(errs)
        self.assertEqual(d["routing"]["worker_id"], "w1")
        self.assertEqual(d["routing"]["lora_id"], "base")

    def test_cloud_bad_provider(self):
        bad = json.loads(json.dumps(GOOD_CLOUD))
        bad["cloud"]["provider"] = "ghost"
        bad["cloud"]["model"] = ""
        d, errs = hv.validate_decision(bad, "云任务", INV)
        self.assertTrue(errs)
        self.assertEqual(d["cloud"]["provider"], "deepseek")
        self.assertTrue(d["cloud"]["model"])

    def test_sim_bad_scenario(self):
        bad = json.loads(json.dumps(GOOD_LOCAL))
        bad["target"] = "sim"
        bad["sim"] = {"scenario": "ghost", "users": 0, "duration_s": 0,
                      "dirty_policy": "", "report_to": ""}
        d, errs = hv.validate_decision(bad, "仿真任务", INV)
        self.assertTrue(errs)
        self.assertEqual(d["sim"]["scenario"], "http_concurrent")
        self.assertEqual(d["sim"]["dirty_policy"], "quarantine")

    def test_confidence_clamp_and_defaults(self):
        bad = {"target": "local", "confidence": 9, "routing": {"why": "x"}}
        d, errs = hv.validate_decision(bad, "本地任务", INV)
        self.assertEqual(d["confidence"], 1.0)
        self.assertTrue(d["subtasks"])
        self.assertTrue(d["fallback_chain"])


class TestBasis(unittest.TestCase):
    def test_rule_sim(self):
        d = hv.basis_decision("压测 500 并发并拦截脏数据", INV)
        self.assertEqual(d["target"], "sim")
        self.assertEqual(d["sim"]["scenario"], "http_concurrent")

    def test_rule_cloud(self):
        d = hv.basis_decision("查一下最新论文并分析趋势", INV)
        self.assertEqual(d["target"], "cloud")

    def test_rule_local(self):
        d = hv.basis_decision("列出当前 LoRA 状态", INV)
        self.assertEqual(d["target"], "local")
        self.assertEqual(d["routing"]["worker_id"], "w1")


class TestDecide(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="s18_ut_")
        self._dir = hv.HIVE_DIR
        self._file = hv.DECISIONS_FILE
        hv.HIVE_DIR = Path(self.tmp)
        hv.DECISIONS_FILE = Path(self.tmp) / "decisions.jsonl"
        self.inv = mock.patch.object(hv, "build_inventory", return_value=INV)
        self.inv.start()
        self.addCleanup(self.inv.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, hv, "HIVE_DIR", self._dir)
        self.addCleanup(setattr, hv, "DECISIONS_FILE", self._file)

    def test_valid_llm_local(self):
        with mock.patch.object(hv, "_llm_json",
                               return_value=(json.dumps(GOOD_LOCAL, ensure_ascii=False), {"mode": "guided"})):
            r = hv.decide("把表格按单价排序", use_llm=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["decision"]["target"], "local")
        self.assertFalse(r["meta"]["fallback"])
        self.assertEqual(r["meta"]["attempts"], 1)
        # 审计写入
        lines = hv.DECISIONS_FILE.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["decision"]["target"], "local")

    def test_repair_after_fatal_validation_error(self):
        bad = json.loads(json.dumps(GOOD_LOCAL))
        bad["target"] = "magic"
        calls = [(json.dumps(bad, ensure_ascii=False), {"mode": "guided"}),
                 (json.dumps(GOOD_LOCAL, ensure_ascii=False), {"mode": "guided"})]
        with mock.patch.object(hv, "_llm_json", side_effect=calls):
            r = hv.decide("把表格排序", use_llm=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["meta"]["attempts"], 2)
        self.assertTrue(r["meta"]["repaired"])
        self.assertFalse(r["meta"]["fallback"])

    def test_normalized_errors_no_retry(self):
        bad = json.loads(json.dumps(GOOD_LOCAL))
        bad["routing"]["worker_id"] = "ghost"
        bad["routing"]["lora_id"] = "ghost"
        with mock.patch.object(hv, "_llm_json",
                               return_value=(json.dumps(bad, ensure_ascii=False),
                                             {"mode": "guided"})) as m:
            r = hv.decide("把表格排序", use_llm=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["meta"]["attempts"], 1)
        self.assertEqual(r["decision"]["routing"]["worker_id"], "w1")
        self.assertEqual(r["decision"]["routing"]["lora_id"], "base")
        self.assertTrue(r["meta"]["normalized_errors"])

    def test_fallback_after_two_failures(self):
        calls = [("这不是 JSON", {"mode": "guided"}), ("还是不是", {"mode": "guided"})]
        with mock.patch.object(hv, "_llm_json", side_effect=calls):
            r = hv.decide("列出当前状态", use_llm=True, max_retries=1)
        self.assertTrue(r["ok"])
        self.assertTrue(r["meta"]["fallback"])
        self.assertEqual(r["decision"]["target"], "local")

    def test_sim_keyword_short_circuit_no_llm(self):
        with mock.patch.object(hv, "_llm_json") as m:
            r = hv.decide("用 500 并发压测路由系统", use_llm=True)
        self.assertEqual(r["decision"]["target"], "sim")
        m.assert_not_called()
        self.assertEqual(r["meta"]["rule"], "S1")

    def test_dispatch_is_stage_stub(self):
        d = hv.dispatch({"target": "local"})
        self.assertEqual(d["stage"], "S18")
        self.assertIn("执行", d["note"])

    def test_summary(self):
        s = hv.summary()
        self.assertIn("inventory", s)
        self.assertGreaterEqual(s["inventory"]["loras"], 1)




class TestHighRisk(unittest.TestCase):
    def test_contract_local_forced_cloud(self):
        bad = json.loads(json.dumps(GOOD_LOCAL))
        d, errs = hv.validate_decision(bad, '把这份合同和法规逐条比对给出风险建议', INV)
        self.assertEqual(d['target'], 'cloud')
        self.assertTrue(any('S3' in e for e in errs))
        self.assertTrue(d['cloud']['provider'])

    def test_normal_local_not_forced(self):
        d, errs = hv.validate_decision(GOOD_LOCAL, '把表格按单价排序', INV)
        self.assertEqual(d['target'], 'local')




class TestTimeSensitive(unittest.TestCase):
    def test_latest_news_forced_cloud(self):
        bad = json.loads(json.dumps(GOOD_LOCAL))
        d, errs = hv.validate_decision(bad, '查一下 2026 年最新的大模型论文并分析趋势', INV)
        self.assertEqual(d['target'], 'cloud')
        self.assertTrue(any('S3' in e for e in errs))

    def test_normal_query_not_forced(self):
        d, errs = hv.validate_decision(GOOD_LOCAL, '读取本地 Excel 并汇总', INV)
        self.assertEqual(d['target'], 'local')


if __name__ == "__main__":
    unittest.main(verbosity=2)
