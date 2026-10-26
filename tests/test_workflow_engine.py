# -*- coding: utf-8 -*-
"""
工作流引擎的失败语义测试。

核心主张：**任何节点失败都必须"响"**，绝不能像旧实现那样把
{"_error": ...} 或 "[错误 500] ..." 当成正文喂给下游 LLM，
最终产出一份看起来正常、实际建立在错误之上的报告。

这里不联网、不调用真实 LLM：call_llm_strict / LoraRouter / requests 全部打桩。
"""
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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

class FakeRouter:
    """默认：LoRA 加载成功；load_msg 可指定失败信息以测 LoRA 失败路径。"""

    load_msg = None

    def __init__(self, *a, **k):
        pass

    def ensure(self, lora):
        if not lora:
            return False, "使用底座"
        if self.load_msg:
            return False, self.load_msg
        return True, f"已加载 LoRA '{lora}'"


def make_llm(seq):
    """
    按调用顺序返回 seq 中的元素（字符串=成功，Exception 实例=抛错）。
    用尽后重复最后一个元素。记录每次调用的参数便于断言。
    """
    calls = []

    def _fake(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        idx = len(calls) - 1
        item = seq[idx] if idx < len(seq) else seq[-1]
        if isinstance(item, Exception):
            raise item
        return item

    _fake.calls = calls
    return _fake


class WorkflowTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wf_test_"))
        self.outdir = self.tmp / "outputs"
        self.outdir.mkdir()
        self.input_file = self.tmp / "paper.txt"
        self.input_file.write_text("这是论文正文。方法部分做了若干实验。", encoding="utf-8")

        self._patches = [
            mock.patch.object(we, "OUTPUT_DIR", str(self.outdir)),
            mock.patch.object(we, "get_vllm_base_model", return_value="/fake/base"),
            mock.patch.object(we, "LoraRouter", FakeRouter),
            mock.patch.object(we, "USAGE_STATS",
                              {"total_calls": 0, "by_provider": {}}),
            # 让重试退避与节点间隔不真的 sleep（否则整套测试要跑半分钟）
            mock.patch.object(we.time, "sleep"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def write_config(self, nodes, **extra):
        cfg = {"workflow_name": "测试工作流", "nodes": nodes}
        cfg.update(extra)
        path = self.tmp / f"cfg_{len(list(self.tmp.glob('cfg_*.json')))}.json"
        path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def chain(self, **overrides):
        """input -> process(JSON) -> aggregate(text) 的最小链路。"""
        proc = {"id": "p", "type": "process", "description": "抽取",
                "input_from": ["in"], "prompt": "请抽取：{in}", "provider": "vllm"}
        agg = {"id": "agg", "type": "aggregate", "description": "汇总",
               "input_from": ["p"], "prompt": "整合：{输入}", "provider": "vllm"}
        proc.update(overrides.get("proc", {}))
        agg.update(overrides.get("agg", {}))
        return [
            {"id": "in", "type": "input", "input_from": []},
            proc,
            agg,
        ]

    def run_it(self, nodes, **extra):
        cfg = self.write_config(nodes, **extra)
        out_file = we.run_workflow(str(self.input_file), cfg)
        return json.loads(Path(out_file).read_text(encoding="utf-8"))


class TestErrorDetection(unittest.TestCase):
    def test_looks_like_llm_error(self):
        for bad in ["[错误 500] x", "[调用异常] timeout", "[云 API 错误 401] x",
                    "[Ollama 错误 500] x", "", "   ", None]:
            self.assertTrue(we.looks_like_llm_error(bad), bad)
        for good in ['{"a": 1}', "正常回答"]:
            self.assertFalse(we.looks_like_llm_error(good), good)

    def test_strict_wrapper_raises_on_error_string(self):
        with mock.patch.object(we, "call_llm", return_value="[错误 500] boom"):
            with self.assertRaises(we.LLMCallError) as cm:
                we.call_llm_strict("hi")
            self.assertIn("boom", str(cm.exception))

    def test_strict_wrapper_raises_on_empty(self):
        with mock.patch.object(we, "call_llm", return_value="   "):
            with self.assertRaises(we.LLMCallError):
                we.call_llm_strict("hi")

    def test_strict_wrapper_passes_good_content(self):
        with mock.patch.object(we, "call_llm", return_value='{"ok": true}'):
            self.assertEqual(we.call_llm_strict("hi"), '{"ok": true}')

    def test_strict_wrapper_forwards_timeout(self):
        with mock.patch.object(we, "call_llm", return_value="ok") as m:
            we.call_llm_strict("hi", timeout=42)
            self.assertEqual(m.call_args.kwargs.get("timeout"), 42)


class TestNodePolicy(unittest.TestCase):
    def test_defaults(self):
        pol = we.node_policy({}, {})
        self.assertEqual(pol["on_error"], "mark")
        self.assertEqual(pol["retry"], 2)
        self.assertEqual(pol["timeout"], 300)

    def test_node_overrides(self):
        pol = we.node_policy({"on_error": "fail_fast", "retry": 0, "timeout": 30}, {})
        self.assertEqual(pol["on_error"], "fail_fast")
        self.assertEqual(pol["retry"], 0)
        self.assertEqual(pol["timeout"], 30)

    def test_workflow_level_default(self):
        pol = we.node_policy({}, {"on_error_default": "skip"})
        self.assertEqual(pol["on_error"], "skip")

    def test_bad_values_are_sanitized(self):
        pol = we.node_policy({"on_error": "nonsense", "retry": "x",
                              "retry_delay": None, "timeout": 1}, {})
        self.assertEqual(pol["on_error"], "mark")
        self.assertEqual(pol["retry"], 2)
        self.assertEqual(pol["timeout"], 5)      # 下限保护


class TestPropagationHelpers(unittest.TestCase):
    def _ctx(self, nodes):
        return {"full_text": "T", "nodes": nodes}

    def test_upstream_problems_detects_failed_parent(self):
        ctx = self._ctx({"a": {"_error": "boom", "_failed": True}})
        problems = we.upstream_problems({"input_from": ["a"]}, ctx)
        self.assertEqual(len(problems), 1)
        self.assertIn("boom", problems[0][1])

    def test_upstream_problems_detects_empty_field(self):
        ctx = self._ctx({"a": {"method": ""}})
        problems = we.upstream_problems({"input_from": ["a.method"]}, ctx)
        self.assertEqual(len(problems), 1)
        self.assertIn("method", problems[0][1])

    def test_upstream_problems_clean(self):
        ctx = self._ctx({"a": {"method": "M"}})
        self.assertEqual(we.upstream_problems({"input_from": ["a.method"]}, ctx), [])

    def test_upstream_problems_detects_skipped_parent(self):
        ctx = self._ctx({"a": {"_skipped": True}})
        problems = we.upstream_problems({"input_from": ["a"]}, ctx)
        self.assertEqual(len(problems), 1)

    def test_missing_notice_empty_when_clean(self):
        self.assertEqual(we.build_missing_notice([]), "")

    def test_missing_notice_forbids_guessing(self):
        notice = we.build_missing_notice([("a.method", "上游失败")])
        self.assertIn("不要凭空推测", notice)
        self.assertIn("a.method", notice)

    def test_report_header(self):
        self.assertEqual(we.build_report_header([], []), "")
        head = we.build_report_header(
            [{"node": "p", "error": "超时", "attempts": 3}],
            [{"node": "q", "reason": "JSON 解析失败"}])
        self.assertIn("不完整输入", head)
        self.assertIn("p", head)
        self.assertIn("超时", head)
        self.assertIn("JSON 解析失败", head)


class TestRunWorkflowHappyPath(WorkflowTestCase):
    def test_success_has_no_header_and_status_success(self):
        llm = make_llm(['{"method": "CNN"}', "整合后的报告正文"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain())
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["_meta"]["failures"], [])
        self.assertEqual(out["_meta"]["degraded"], [])
        self.assertEqual(out["final_report"], "整合后的报告正文")
        self.assertNotIn("不完整输入", out["final_report"])
        self.assertEqual(out["nodes"]["p"], {"method": "CNN"})

    def test_backward_compat_without_policy_fields(self):
        # 节点里完全没有新字段时，行为与旧版一致（能正常跑完）
        llm = make_llm(['{"a": 1}', "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain())
        self.assertEqual(out["nodes"]["p"], {"a": 1})
        self.assertEqual(out["status"], "success")


class TestRetry(WorkflowTestCase):
    def test_retries_then_succeeds(self):
        llm = make_llm([we.LLMCallError("第一次超时"),
                        we.LLMCallError("第二次超时"),
                        '{"method": "OK"}',
                        "报告"])
        nodes = self.chain(proc={"retry": 3, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertEqual(out["nodes"]["p"], {"method": "OK"})
        self.assertEqual(out["_meta"]["failures"], [])
        self.assertEqual(out["status"], "success")

    def test_exhausts_retries_then_fails(self):
        # 过程节点连续失败 3 次（retry=2），随后汇总节点正常返回
        llm = make_llm([we.LLMCallError("一直超时"),
                        we.LLMCallError("一直超时"),
                        we.LLMCallError("一直超时"),
                        "汇总报告"])
        nodes = self.chain(proc={"retry": 2, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        # 过程节点尝试 1+2=3 次
        proc_calls = [c for c in llm.calls if "抽取" in c["prompt"]]
        self.assertEqual(len(proc_calls), 3)
        self.assertEqual(len(out["_meta"]["failures"]), 1)
        self.assertEqual(out["_meta"]["failures"][0]["node"], "p")
        self.assertEqual(out["_meta"]["failures"][0]["attempts"], 3)
        self.assertTrue(out["nodes"]["p"].get("_failed"))

    def test_retry_zero_means_single_attempt(self):
        llm = make_llm([we.LLMCallError("fail")])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            self.run_it(nodes)
        proc_calls = [c for c in llm.calls if "抽取" in c["prompt"]]
        self.assertEqual(len(proc_calls), 1)


class TestFailureVisibility(WorkflowTestCase):
    def test_failure_produces_header_in_report(self):
        llm = make_llm([we.LLMCallError("vLLM 连接失败"), "汇总报告"])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertEqual(out["status"], "partial")
        self.assertIn("不完整输入", out["final_report"])
        self.assertIn("p", out["final_report"])

    def test_downstream_receives_missing_input_warning(self):
        llm = make_llm([we.LLMCallError("挂了"), "汇总报告"])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        agg_prompt = [c["prompt"] for c in llm.calls if "整合" in c["prompt"]][0]
        self.assertIn("缺失输入警告", agg_prompt)
        self.assertIn("不要凭空推测", agg_prompt)
        self.assertEqual(list(out["_meta"]["missing_inputs"].keys()), ["agg"])

    def test_missing_inputs_recorded(self):
        llm = make_llm([we.LLMCallError("x"), "报告"])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertIn("p", out["_meta"]["missing_inputs"]["agg"][0])

    def test_all_nodes_failed_still_writes_result(self):
        llm = make_llm([we.LLMCallError("全挂")])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0},
                           agg={"retry": 0, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertEqual(out["status"], "failed")
        self.assertIsNotNone(out["final_report"])
        self.assertIn("未能产出报告", out["final_report"])
        self.assertEqual(len(out["_meta"]["failures"]), 2)


class TestOnErrorPolicies(WorkflowTestCase):
    def test_fail_fast_aborts_remaining_nodes(self):
        llm = make_llm([we.LLMCallError("致命错误"), "不该被调用"])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0, "on_error": "fail_fast"})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertNotIn("agg", out["nodes"])          # 后续节点未执行
        self.assertIsNotNone(out["_meta"]["aborted"])
        self.assertEqual(len(llm.calls), 1)

    def test_skip_policy_skips_node_with_broken_input(self):
        llm = make_llm([we.LLMCallError("上游挂了"), '{"x": 1}', "报告"])
        nodes = [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "p", "type": "process", "input_from": ["in"],
             "prompt": "A{in}", "provider": "vllm", "retry": 0, "retry_delay": 0},
            {"id": "q", "type": "process", "input_from": ["p"],
             "prompt": "B{输入}", "provider": "vllm",
             "on_error": "skip", "retry": 0, "retry_delay": 0},
            {"id": "agg", "type": "aggregate", "input_from": ["q"],
             "prompt": "C{输入}", "provider": "vllm"},
        ]
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertTrue(out["nodes"]["q"].get("_skipped"))
        q_calls = [c for c in llm.calls if "B" in c["prompt"]]
        self.assertEqual(len(q_calls), 0)              # 被跳过，没有调用 LLM

    def test_workflow_level_default_skip(self):
        llm = make_llm([we.LLMCallError("挂了"), "报告"])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes, on_error_default="skip")
        self.assertTrue(out["nodes"]["agg"].get("_skipped"))

    def test_mark_policy_runs_downstream_with_warning(self):
        llm = make_llm([we.LLMCallError("挂了"), "报告"])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        # mark 策略下汇总节点照常执行（产出是纯文本字符串，不是被跳过的 dict）
        self.assertIn("agg", out["nodes"])
        self.assertIsInstance(out["nodes"]["agg"], str)
        self.assertIn("报告", out["nodes"]["agg"])


class TestParseFailure(WorkflowTestCase):
    def test_prose_instead_of_json_degrades_not_fails(self):
        llm = make_llm(["这是一段散文，不是 JSON", "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain())
        node = out["nodes"]["p"]
        self.assertIn("_raw", node)                    # 原文保留，供 {输入} 兜底
        self.assertNotIn("_error", node)               # 不是硬失败
        self.assertEqual(len(out["_meta"]["degraded"]), 1)
        self.assertEqual(out["_meta"]["degraded"][0]["node"], "p")
        self.assertEqual(out["status"], "partial")

    def test_broken_json_retries_then_degrades(self):
        llm = make_llm(['{"a": 1,', '{"a": 2,', '{"a": 3,', "报告"])
        nodes = self.chain(proc={"retry": 2, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        proc_calls = [c for c in llm.calls if "抽取" in c["prompt"]]
        self.assertEqual(len(proc_calls), 3)
        self.assertEqual(len(out["_meta"]["degraded"]), 1)

    def test_degraded_also_adds_report_header(self):
        llm = make_llm(["不是 JSON", "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain())
        self.assertIn("不完整输入", out["final_report"])


class TestLoraFailure(WorkflowTestCase):
    def test_lora_load_failure_blocks_node(self):
        llm = make_llm(['{"method": "X"}', "报告"])

        class FailingRouter(FakeRouter):
            load_msg = "加载失败: hidden_size 不匹配"

        # 注意：这条测试与现实环境有关 —— 若当前底座的 hidden_size 与该 LoRA 不匹配，
        # run_workflow 会在【预检】阶段就拒绝启动（抛 WorkflowPreflightError），
        # 根本走不到 mock 的 router。两种表现都符合本测试的真正意图：
        # 「宁可不跑，也不要用底座冒充 LoRA 的结果」。所以两者都接受。
        preflight_error = None
        with mock.patch.object(we, "LoraRouter", FailingRouter), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            nodes = self.chain(proc={"lora": "paper_analyzer_v5", "retry": 0,
                                     "retry_delay": 0})
            try:
                out = self.run_it(nodes)
            except we.WorkflowPreflightError as e:
                preflight_error = e
        if preflight_error is not None:
            self.assertEqual(preflight_error.code, 3)
            self.assertTrue(any("不兼容" in d or "加载失败" in d for d in preflight_error.details)
                            or "LoRA" in preflight_error.message)
            return
        # 关键：宁可不跑，也不要用底座冒充 LoRA 的结果
        proc_calls = [c for c in llm.calls if "抽取" in c["prompt"]]
        self.assertEqual(len(proc_calls), 0)
        self.assertTrue(out["nodes"]["p"].get("_failed"))
        self.assertEqual(out["_meta"]["failures"][0]["stage"], "lora")
        self.assertIn("加载失败", out["_meta"]["failures"][0]["error"])


class TestRetrievalNode(WorkflowTestCase):
    def test_missing_kb_and_unreachable_list_is_failure(self):
        # 空 kb_name 现在表示 auto；若连列表都拿不到（后端不可达）→ 明确的失败
        llm = make_llm(["报告"])
        nodes = [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "kb", "type": "retrieval", "input_from": ["in"],
             "kb_name": "", "top_k": 3},
            {"id": "agg", "type": "aggregate", "input_from": ["kb"],
             "prompt": "{输入}", "provider": "vllm"},
        ]
        with mock.patch.object(we, "ENV_KB_NAME", ""), \
             mock.patch.object(we.requests, "get", side_effect=RuntimeError("后端不可达")), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertTrue(out["nodes"]["kb"].get("_failed"))
        self.assertEqual(out["_meta"]["failures"][0]["stage"], "retrieval")
        self.assertIn("知识库", out["_meta"]["failures"][0]["error"])

    def test_empty_retrieval_is_degraded(self):
        llm = make_llm(["报告"])
        nodes = [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "kb", "type": "retrieval", "input_from": ["in"],
             "kb_name": "test_kb", "top_k": 3},
            {"id": "agg", "type": "aggregate", "input_from": ["kb"],
             "prompt": "{输入}", "provider": "vllm"},
        ]
        fake_resp = mock.Mock(status_code=200)
        fake_resp.json.return_value = {"results": []}
        with mock.patch.object(we.requests, "post", return_value=fake_resp), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertEqual(len(out["_meta"]["degraded"]), 1)
        self.assertIn("未检索到", out["_meta"]["degraded"][0]["reason"])
        self.assertIn("没有找到相关内容", out["nodes"]["kb"])

    def _kb_nodes(self, **extra):
        kb_node = {"id": "kb", "type": "retrieval", "input_from": ["in"],
                   "kb_name": "test_kb", "top_k": 3}
        kb_node.update(extra)
        return [
            {"id": "in", "type": "input", "input_from": []},
            kb_node,
            {"id": "agg", "type": "aggregate", "input_from": ["kb"],
             "prompt": "{输入}", "provider": "vllm"},
        ]

    def test_empty_retrieval_surfaces_the_real_reason(self):
        """
        候选被阈值丢掉（跨语言场景）时，告警必须说清原因。
        旧实现一律写"未检索到任何片段"，把"取到了但全被丢了"说成"库里没有"，
        会直接把人引向错误的方向（去查索引/入库，而真正该改的是 query 语言）。
        """
        llm = make_llm(["报告"])
        hint = ("向量检索返回了 3 个候选，但全部低于阈值 0.1（最高分 -0.028）；"
                "且**查询与语料语言不匹配**：查询是中文、语料主体是英文，"
                "解决方式：① 把节点的 query_template 改成与语料同语言")
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {
            "results": [],
            "diagnostics": {"candidates": 3, "kept": 0, "dropped": 3,
                            "best_score": -0.028, "min_score": 0.1,
                            "query_lang": "zh", "corpus_lang": "en",
                            "lang_mismatch": True, "hint": hint},
        }
        with mock.patch.object(we.requests, "post", return_value=fake), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._kb_nodes())
        reason = out["_meta"]["degraded"][0]["reason"]
        self.assertIn("3 个候选", reason)
        self.assertIn("语言不匹配", reason)
        self.assertIn("query_template", reason)
        # prompt 里只留"给模型看的那句话"：完整诊断是给操作者的，塞进提示词
        # 反而有被模型原样抄进报告的风险（已进日志/降级/报告头/_meta）
        self.assertIn("没有找到相关内容", out["nodes"]["kb"])
        self.assertIn("不要凭空推测", out["nodes"]["kb"])
        self.assertNotIn("query_template", out["nodes"]["kb"])
        # 诊断写进 _meta，供台账/Agent 下钻
        ret = out["_meta"]["kb_resolution"]["kb"]["retrieval"]
        self.assertEqual(ret["candidates"], 3)
        self.assertEqual(ret["best_score"], -0.028)
        self.assertTrue(ret["lang_mismatch"])

    def test_falls_back_to_generic_reason_without_diagnostics(self):
        """后端没给 diagnostics 时（旧后端/异常）退化成原来的笼统提示，不能崩。"""
        llm = make_llm(["报告"])
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"results": []}
        with mock.patch.object(we.requests, "post", return_value=fake), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._kb_nodes())
        self.assertIn("未检索到", out["_meta"]["degraded"][0]["reason"])
        self.assertNotIn("retrieval", out["_meta"]["kb_resolution"]["kb"])

    def test_min_score_is_actually_sent_to_backend(self):
        """节点级 min_score 必须真的发出去，否则这个旋钮就是个摆设。"""
        llm = make_llm(["报告"])
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"results": [{"text": "x", "source": "s",
                                               "chunk_idx": 0, "score": -0.05}]}
        with mock.patch.object(we.requests, "post", return_value=fake) as post, \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._kb_nodes(min_score=-0.2))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["min_score"], -0.2)
        self.assertEqual(body["top_k"], 3)
        self.assertEqual(len(out["_meta"]["degraded"]), 0)

    def test_min_score_defaults_to_point_one(self):
        llm = make_llm(["报告"])
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {"results": [{"text": "x", "source": "s",
                                               "chunk_idx": 0, "score": 0.5}]}
        with mock.patch.object(we.requests, "post", return_value=fake) as post, \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            self.run_it(self._kb_nodes())
        self.assertEqual(post.call_args.kwargs["json"]["min_score"], 0.1)

    @unittest.skip("出厂预置已改为 4 节点示例（2026-10-22），本用例针对旧 9 节点配置")
    def test_shipped_config_retrieval_node_gets_min_score(self):
        """发布配置里的检索节点走类型契约后必须带上 min_score（防注册表漂移）。"""
        cfg = json.loads(Path("workflow_config.json").read_text(encoding="utf-8"))
        nodes = [n for n in cfg["nodes"] if n.get("type") == "retrieval"]
        self.assertTrue(nodes, "发布配置里应有检索节点")
        for n in nodes:
            eff = we.apply_type_defaults(dict(n))
            self.assertIn("min_score", eff)
            self.assertEqual(eff["min_score"], 0.1)

    def test_retrieval_http_error_is_failure(self):
        llm = make_llm(["报告"])
        nodes = [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "kb", "type": "retrieval", "input_from": ["in"],
             "kb_name": "test_kb", "top_k": 3},
            {"id": "agg", "type": "aggregate", "input_from": ["kb"],
             "prompt": "{输入}", "provider": "vllm"},
        ]
        fake_resp = mock.Mock(status_code=500, text="boom")
        with mock.patch.object(we.requests, "post", return_value=fake_resp), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        self.assertEqual(len(out["_meta"]["failures"]), 1)
        self.assertTrue(out["nodes"]["kb"].get("_failed"))


class TestResultShape(WorkflowTestCase):
    def test_meta_fields_present(self):
        llm = make_llm(['{"a": 1}', "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain())
        meta = out["_meta"]
        for key in ("usage", "providers_used", "failures", "degraded",
                    "missing_inputs", "aborted", "node_policy_default",
                    "node_stats", "kb_resolution", "chunking", "quality"):
            self.assertIn(key, meta)
        self.assertIn("status", out)
        self.assertIn("log", out)

    def test_node_stats_recorded(self):
        """O2：每个节点都要有耗时/产出/状态/用量（耗时面板的数据源）。"""
        llm = make_llm(['{"a": 1}', "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain())
        ns = out["_meta"]["node_stats"]
        self.assertIn("in", ns)
        self.assertIn("p", ns)
        self.assertIn("agg", ns)
        for nid, v in ns.items():
            self.assertIn(v["status"], ("ok", "degraded", "failed", "skipped"), nid)
            self.assertIsInstance(v["sec"], float)
            self.assertGreaterEqual(v["sec"], 0.0)
            self.assertIsInstance(v["chars_out"], int)
            self.assertIsInstance(v["tokens"], int)
            self.assertIn("type", v)
        # 输入节点读全文 → 产出字数就是全文长度
        self.assertEqual(ns["in"]["status"], "ok")
        self.assertGreater(ns["in"]["chars_out"], 0)

    def test_node_stats_marks_failed_node(self):
        """失败节点必须被标成 failed（面板要能一眼看出哪个节点挂了）。"""
        llm = make_llm([we.LLMCallError("超时"), we.LLMCallError("超时"), "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain(proc={"retry": 0, "retry_delay": 0}))
        self.assertEqual(out["_meta"]["node_stats"]["p"]["status"], "failed")

    def test_node_stats_marks_degraded_node(self):
        llm = make_llm(["不是 JSON", "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain())
        self.assertEqual(out["_meta"]["node_stats"]["p"]["status"], "degraded")

    def test_log_mentions_failure(self):
        llm = make_llm([we.LLMCallError("超时了"), "报告"])
        nodes = self.chain(proc={"retry": 0, "retry_delay": 0})
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(nodes)
        joined = "\n".join(out["log"])
        self.assertIn("失败", joined)
        self.assertTrue(any("超时了" in line for line in out["log"]))


class TestKbResolution(unittest.TestCase):
    """知识库名解析：节点 > 环境变量 > auto 自动探测。"""

    @staticmethod
    def _patch_list(items):
        fake = mock.Mock(status_code=200)
        fake.json.return_value = {
            "knowledge_bases": [{"name": n, "updated_at": u} for n, u in items]}
        return mock.patch.object(we.requests, "get", return_value=fake)

    def test_node_name_wins_over_env(self):
        with mock.patch.object(we, "ENV_KB_NAME", "env_kb"):
            name, src, cands = we.resolve_kb_name({"kb_name": "my_kb"})
        self.assertEqual((name, src), ("my_kb", "node"))
        self.assertEqual(cands, ["my_kb"])

    def test_env_used_when_node_is_auto(self):
        with mock.patch.object(we, "ENV_KB_NAME", "env_kb"):
            self.assertEqual(we.resolve_kb_name({"kb_name": "auto"})[:2], ("env_kb", "env"))

    def test_env_used_when_node_empty(self):
        with mock.patch.object(we, "ENV_KB_NAME", "env_kb"):
            self.assertEqual(we.resolve_kb_name({})[:2], ("env_kb", "env"))

    def test_env_used_when_node_missing(self):
        with mock.patch.object(we, "ENV_KB_NAME", "env_kb"):
            self.assertEqual(we.resolve_kb_name({"kb_name": "__auto__"})[:2], ("env_kb", "env"))

    def test_auto_single_kb(self):
        with mock.patch.object(we, "ENV_KB_NAME", ""), self._patch_list([("only", "2026-01-01")]):
            name, src, _ = we.resolve_kb_name({"kb_name": "auto"})
        self.assertEqual((name, src), ("only", "auto-single"))

    def test_auto_latest_when_multiple(self):
        items = [("old", "2026-01-01"), ("new", "2026-05-01"), ("mid", "2026-03-01")]
        with mock.patch.object(we, "ENV_KB_NAME", ""), self._patch_list(items):
            name, src, cands = we.resolve_kb_name({"kb_name": ""})
        self.assertEqual(name, "new")                 # 按 updated_at 取最新
        self.assertEqual(src, "auto-latest")
        self.assertEqual(cands, ["new", "mid", "old"])  # 候选按更新时间倒序

    def test_none_when_no_kb_exists(self):
        with mock.patch.object(we, "ENV_KB_NAME", ""), self._patch_list([]):
            self.assertEqual(we.resolve_kb_name({"kb_name": "auto"})[:2], ("", "none"))

    def test_list_failure_is_tolerated(self):
        with mock.patch.object(we.requests, "get", side_effect=RuntimeError("boom")):
            self.assertEqual(we.list_available_kbs(), [])

    def test_list_sorted_by_updated_desc(self):
        items = [("a", "2026-01-01"), ("b", "2026-09-09"), ("c", "2026-05-05")]
        with self._patch_list(items):
            self.assertEqual([n for n, _ in we.list_available_kbs()], ["b", "c", "a"])


class TestKbResolutionInWorkflow(WorkflowTestCase):
    """端到端（打桩）：检索节点究竟把查询发给了哪个知识库。"""

    def _kb_backend(self, kbs, results=None):
        def _get(url, **kw):
            m = mock.Mock(status_code=200)
            m.json.return_value = {"knowledge_bases":
                                   [{"name": n, "updated_at": u} for n, u in kbs]}
            return m

        calls = []

        def _post(url, json=None, timeout=None, **kw):
            calls.append({"url": url, "json": json or {}})
            m = mock.Mock(status_code=200, text="")
            m.json.return_value = {"results": results if results is not None else [
                {"text": "检索到的片段", "source": "paper.pdf", "chunk_idx": 0, "score": 0.7}]}
            return m

        return _get, _post, calls

    def _nodes(self, kb_name):
        return [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "kb", "type": "retrieval", "input_from": ["in"],
             "kb_name": kb_name, "top_k": 3, "query_template": "{full_text}"},
            {"id": "agg", "type": "aggregate", "input_from": ["kb"],
             "prompt": "整合{输入}", "provider": "vllm"},
        ]

    def test_auto_picks_existing_kb_at_runtime(self):
        llm = make_llm(["报告"])
        get_f, post_f, calls = self._kb_backend([("test_kb", "2026-01-01")])
        with mock.patch.object(we, "ENV_KB_NAME", ""), \
             mock.patch.object(we.requests, "get", side_effect=get_f), \
             mock.patch.object(we.requests, "post", side_effect=post_f), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._nodes("auto"))
        self.assertTrue(any("/kb/test_kb/query" in c["url"] for c in calls),
                        [c["url"] for c in calls])
        res = out["_meta"]["kb_resolution"]["kb"]
        self.assertEqual(res["resolved"], "test_kb")
        self.assertEqual(res["source"], "auto-single")
        self.assertEqual(out["status"], "success")

    def test_auto_uses_newest_of_multiple(self):
        llm = make_llm(["报告"])
        kbs = [("old_kb", "2026-01-01"), ("fresh_kb", "2026-08-08")]
        get_f, post_f, calls = self._kb_backend(kbs)
        with mock.patch.object(we, "ENV_KB_NAME", ""), \
             mock.patch.object(we.requests, "get", side_effect=get_f), \
             mock.patch.object(we.requests, "post", side_effect=post_f), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._nodes(""))
        self.assertTrue(any("/kb/fresh_kb/query" in c["url"] for c in calls))
        res = out["_meta"]["kb_resolution"]["kb"]
        self.assertEqual(res["resolved"], "fresh_kb")
        self.assertEqual(res["source"], "auto-latest")
        self.assertEqual(res["candidates"], ["fresh_kb", "old_kb"])

    def test_env_var_supplies_kb(self):
        llm = make_llm(["报告"])
        get_f, post_f, calls = self._kb_backend([("a", "2026-01-01")])
        with mock.patch.object(we, "ENV_KB_NAME", "env_kb"), \
             mock.patch.object(we.requests, "get", side_effect=get_f), \
             mock.patch.object(we.requests, "post", side_effect=post_f), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._nodes("auto"))
        self.assertTrue(any("/kb/env_kb/query" in c["url"] for c in calls))
        self.assertEqual(out["_meta"]["kb_resolution"]["kb"]["source"], "env")

    def test_explicit_node_kb_still_wins(self):
        llm = make_llm(["报告"])
        get_f, post_f, calls = self._kb_backend([("other", "2026-01-01")])
        with mock.patch.object(we, "ENV_KB_NAME", "env_kb"), \
             mock.patch.object(we.requests, "get", side_effect=get_f), \
             mock.patch.object(we.requests, "post", side_effect=post_f), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._nodes("my_kb"))
        self.assertTrue(any("/kb/my_kb/query" in c["url"] for c in calls))
        res = out["_meta"]["kb_resolution"]["kb"]
        self.assertEqual((res["resolved"], res["source"]), ("my_kb", "node"))

    def test_no_kb_anywhere_gives_actionable_error(self):
        llm = make_llm(["报告"])
        get_f, post_f, _ = self._kb_backend([])
        with mock.patch.object(we, "ENV_KB_NAME", ""), \
             mock.patch.object(we.requests, "get", side_effect=get_f), \
             mock.patch.object(we.requests, "post", side_effect=post_f), \
             mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._nodes("auto"))
        err = out["_meta"]["failures"][0]["error"]
        self.assertIn("WORKFLOW_KB_NAME", err)      # 告诉用户怎么解决
        self.assertIn("知识库", err)
        self.assertNotIn("kb", out["_meta"]["kb_resolution"])


class TestChunkHelpers(unittest.TestCase):
    """分块抽取（map-reduce）：切块、模板重建、合并。"""

    def test_split_into_chunks_count(self):
        text = "a" * 1000
        chunks = we.split_into_chunks(text, 300, 100)
        self.assertEqual(chunks[0], "a" * 300)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) <= 300 for c in chunks))

    def test_split_prefers_paragraph_boundary(self):
        text = "x" * 200 + "\n\n" + "y" * 200
        self.assertEqual(we.split_into_chunks(text, 260, 0)[0], "x" * 200)

    def test_split_no_op_when_small(self):
        self.assertEqual(we.split_into_chunks("short", 3000, 500), ["short"])

    def test_split_disabled(self):
        self.assertEqual(we.split_into_chunks("a" * 5000, 0, 0), ["a" * 5000])

    def test_prompt_chunks_disabled(self):
        self.assertIsNone(we.split_prompt_into_chunks("p", {"k": "v"}, 0, 0)[0])

    def test_prompt_chunks_skip_when_input_small(self):
        chunks, note = we.split_prompt_into_chunks("前缀 abc", {"k": "abc"}, 100, 0)
        self.assertIsNone(chunks)
        self.assertIn("单次调用", note)

    def test_prompt_chunks_rebuild_each_prompt(self):
        big = "".join(f"句子{i}。" for i in range(200))      # 长输入
        prompt = f"请提取方法：\n\n{big}"
        chunks, note = we.split_prompt_into_chunks(prompt, {"x": big}, 300, 50)
        self.assertIsNotNone(chunks)
        self.assertGreater(len(chunks), 3)
        for c in chunks:
            self.assertTrue(c.startswith("请提取方法："))     # 指令必须在每一块里
            self.assertIn("句子0。", big)                     # 原文未变
        self.assertIn("切", note)

    def test_prompt_chunks_refuse_when_input_not_verbatim(self):
        # 输入值没有原样出现在提示词里 → 明确拒绝分块，而不是瞎切
        chunks, note = we.split_prompt_into_chunks("前缀 only", {"x": "z" * 500}, 100, 0)
        self.assertIsNone(chunks)
        self.assertIn("无法安全分块", note)

    def test_merge_text_dedupes_and_keeps_order(self):
        merged = we.merge_text_outputs(["第一句。\n第二句。", "第二句。\n第三句。"])
        self.assertEqual(merged, "第一句。\n第二句。\n第三句。")

    def test_merge_text_ignores_whitespace_differences(self):
        merged = we.merge_text_outputs(["a b  c", "ab c"])
        self.assertEqual(len(merged.splitlines()), 1)

    def test_merge_json_list_union(self):
        merged, conflicts, stats = we.merge_json_outputs([
            {"method": ["A", "B"]}, {"method": ["B", "C"]}])
        self.assertEqual(merged["method"], ["A", "B", "C"])
        self.assertEqual(stats["list_union"], 1)
        self.assertEqual(conflicts, [])

    def test_merge_json_string_merge_dedupes(self):
        merged, _, stats = we.merge_json_outputs([{"m": "甲。乙。"}, {"m": "乙。丙。"}])
        self.assertIn("甲。", merged["m"])
        self.assertIn("丙。", merged["m"])
        self.assertEqual(merged["m"].count("乙。"), 1)      # 句级去重
        self.assertEqual(stats["text_merge"], 1)

    def test_merge_sentences_handles_english(self):
        merged = we.merge_sentences(["We use X. We train Y.", "We train Y. We test Z."])
        self.assertEqual(merged.count("We train Y."), 1)
        self.assertIn("We use X. We train Y. We test Z.", merged)

    def test_merge_sentences_keeps_decimals_intact(self):
        self.assertEqual(we.merge_sentences(["accuracy 3.5 percent"]), "accuracy 3.5 percent")

    def test_merge_json_scalar_conflict_is_recorded(self):
        merged, conflicts, stats = we.merge_json_outputs([
            {"year": 2024, "ok": True}, {"year": 2025, "ok": True}])
        self.assertEqual(merged["year"], 2024)          # 保留首个
        self.assertEqual(conflicts[0]["field"], "year")
        self.assertEqual(conflicts[0]["other"], 2025)   # 冲突不静默
        self.assertEqual(stats["scalar_conflict"], 1)

    def test_merge_json_fills_none(self):
        merged, _, _ = we.merge_json_outputs([{"a": None}, {"a": "值"}])
        self.assertEqual(merged["a"], "值")

    def test_merge_json_ignores_non_dict(self):
        merged, _, _ = we.merge_json_outputs([{"a": 1}, "不是字典", None])
        self.assertEqual(merged, {"a": 1})


class TestChunkingInWorkflow(WorkflowTestCase):
    """端到端（打桩）：长输入走分块，短输入不分块，失败要显形。"""

    def setUp(self):
        super().setUp()
        self.long_text = "".join(f"这是第{i}个句子，描述论文的方法细节。" for i in range(120))
        self.long_file = self.tmp / "long.txt"
        self.long_file.write_text(self.long_text, encoding="utf-8")

    def _nodes(self, **proc_over):
        proc = {"id": "p", "type": "process", "input_from": ["in"],
                "prompt": "抽取：{in}", "provider": "vllm"}
        proc.update(proc_over)
        return [{"id": "in", "type": "input", "input_from": []}, proc]

    def _run_long(self, nodes, llm):
        cfg = self.write_config(nodes)
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out_file = we.run_workflow(str(self.long_file), cfg)
        return json.loads(Path(out_file).read_text(encoding="utf-8"))

    def test_long_input_is_chunked(self):
        llm = make_llm(['{"a": "1"}'])
        out = self._run_long(self._nodes(chunk_size=300, chunk_overlap=50, retry=0), llm)
        info = out["_meta"]["chunking"]["p"]
        self.assertGreater(info["chunks"], 3)
        self.assertEqual(info["ok"], info["chunks"])
        self.assertEqual(info["failed_chunks"], [])
        self.assertEqual(len(llm.calls), info["chunks"])   # 每块一次调用

    def test_each_chunk_prompt_carries_instruction(self):
        llm = make_llm(['{"a": "1"}'])
        self._run_long(self._nodes(chunk_size=300, retry=0), llm)
        self.assertTrue(all(c["prompt"].startswith("抽取：") for c in llm.calls))

    def test_short_input_not_chunked(self):
        llm = make_llm(['{"a": "1"}'])
        cfg = self.write_config(self.chain())
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = json.loads(Path(we.run_workflow(str(self.input_file), cfg)).read_text(encoding="utf-8"))
        # 未设置 chunk_size → 不产生 chunking 记录，且每个节点各调用一次
        self.assertEqual(out["_meta"]["chunking"], {})
        self.assertEqual(len(llm.calls), 2)      # 过程节点 + 汇总节点

    def test_chunk_size_larger_than_input_not_chunked(self):
        llm = make_llm(['{"a": "1"}', "报告"])
        cfg = self.write_config(self.chain(proc={"chunk_size": 5000}))
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = json.loads(Path(we.run_workflow(str(self.input_file), cfg)).read_text(encoding="utf-8"))
        self.assertEqual(out["_meta"]["chunking"], {})   # 未触发分块
        self.assertEqual(len(llm.calls), 2)

    def test_partial_chunk_failure_degrades(self):
        # 第 1 块失败，其余成功 → 结果不完整，必须记为降级并进报告头
        llm = make_llm([we.LLMCallError("块超时"), '{"a": "1"}'])
        out = self._run_long(self._nodes(chunk_size=300, retry=0), llm)
        info = out["_meta"]["chunking"]["p"]
        self.assertEqual(info["failed_chunks"], [1])
        self.assertEqual(info["ok"], info["chunks"] - 1)
        self.assertTrue(any("分块调用失败" in d["reason"] for d in out["_meta"]["degraded"]))
        self.assertEqual(out["status"], "partial")

    def test_all_chunks_failed_is_node_failure(self):
        llm = make_llm([we.LLMCallError("全挂")])
        out = self._run_long(self._nodes(chunk_size=300, retry=0), llm)
        self.assertTrue(out["nodes"]["p"].get("_failed"))
        err = out["_meta"]["failures"][0]["error"]
        self.assertIn("分块", err)

    def test_text_node_chunks_are_merged_and_deduped(self):
        # aggregate 是文本节点；各块输出相同句子时合并后应去重
        llm = make_llm(["论文提出了方法甲。\n论文提出了方法乙。"])
        nodes = self._nodes(chunk_size=300, retry=0)
        cfg = self.write_config(nodes)
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = json.loads(Path(we.run_workflow(str(self.long_file), cfg)).read_text(encoding="utf-8"))
        text = out["nodes"]["p"]["_raw"] if isinstance(out["nodes"]["p"], dict) else None
        self.assertIsNotNone(text)
        self.assertEqual(text.count("论文提出了方法甲。"), 1)

    def test_json_chunks_are_merged(self):
        llm = make_llm(['{"methods": ["甲"]}', '{"methods": ["乙"]}'])
        out = self._run_long(self._nodes(chunk_size=300, retry=0), llm)
        self.assertEqual(out["nodes"]["p"]["methods"][0], "甲")
        self.assertIn("乙", out["nodes"]["p"]["methods"])
        # list_union 统计的是"发生了几次 list 合并"，块数多自然会 >1
        n_chunks = out["_meta"]["chunking"]["p"]["chunks"]
        self.assertEqual(out["_meta"]["chunking"]["p"]["merge"]["list_union"], n_chunks - 1)


class TestTextOutputFormat(WorkflowTestCase):
    """output_format="text"：抽取式 LoRA 输出散文时的正确搭配。"""

    def _text_nodes(self, **over):
        proc = {"id": "p", "type": "process", "input_from": ["in"],
                "prompt": "抽取：{in}", "provider": "vllm", "output_format": "text"}
        proc.update(over)
        return [{"id": "in", "type": "input", "input_from": []}, proc,
                {"id": "agg", "type": "aggregate", "input_from": ["p"],
                 "prompt": "整合{输入}", "provider": "vllm"}]

    def test_text_node_stores_plain_string(self):
        llm = make_llm(["本文提出了稀疏注意力方法。", "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._text_nodes())
        self.assertIsInstance(out["nodes"]["p"], str)          # 不是 dict
        self.assertIn("稀疏注意力", out["nodes"]["p"])
        self.assertEqual(out["_meta"]["degraded"], [])         # 不再报 JSON 解析失败
        self.assertEqual(out["status"], "success")

    def test_text_output_flows_downstream(self):
        llm = make_llm(["抽取到的正文。", "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._text_nodes())
        agg_prompt = [c["prompt"] for c in llm.calls if "整合" in c["prompt"]][0]
        self.assertIn("抽取到的正文。", agg_prompt)

    def test_text_node_uses_low_temperature_by_default(self):
        llm = make_llm(["正文", "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            self.run_it(self._text_nodes())
        proc_call = [c for c in llm.calls if c["prompt"].startswith("抽取")][0]
        self.assertEqual(proc_call.get("temperature"), 0.0)     # 抽取要可复现
        self.assertEqual(proc_call.get("max_tokens"), 1024)

    def test_text_node_honours_explicit_generation_params(self):
        llm = make_llm(["正文", "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            self.run_it(self._text_nodes(max_tokens=256, temperature=0.3))
        proc_call = [c for c in llm.calls if c["prompt"].startswith("抽取")][0]
        self.assertEqual(proc_call.get("max_tokens"), 256)
        self.assertEqual(proc_call.get("temperature"), 0.3)

    def test_aggregate_keeps_original_params(self):
        llm = make_llm(['{"a": 1}', "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            self.run_it(self.chain())
        agg_call = [c for c in llm.calls if "整合" in c["prompt"]][0]
        self.assertEqual(agg_call.get("max_tokens"), 4096)      # 汇总节点原样不变
        self.assertEqual(agg_call.get("temperature"), 0.5)
        self.assertTrue(agg_call.get("system"))

    def test_json_node_unaffected(self):
        llm = make_llm(['{"a": 1}', "报告"])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self.chain())                     # 未设 output_format
        self.assertEqual(out["nodes"]["p"], {"a": 1})
        proc_call = [c for c in llm.calls if c["prompt"].startswith("请抽取")][0]
        self.assertNotIn("temperature", proc_call)              # JSON 路径沿用原参数

    def test_invalid_output_format_is_rejected_by_lint(self):
        """非法 output_format 现在被配置校验拦下（不再静默当 json）；运行时兜底保留。"""
        errs, _ = we.lint_config({"nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "p", "type": "process", "input_from": ["in"], "output_format": "xml"}]})
        self.assertTrue(any("output_format" in e for e in errs), errs)
        self.assertFalse(we.node_output_is_text({"output_format": "xml"}, "process"))

    def test_empty_text_output_is_failure(self):
        llm = make_llm([""])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = self.run_it(self._text_nodes(retry=0))
        self.assertTrue(out["nodes"]["p"].get("_failed"))
        self.assertIn("空内容", out["_meta"]["failures"][0]["error"])

    def test_text_node_supports_chunking(self):
        long_text = "".join(f"第{i}句方法描述。" for i in range(150))
        f = self.tmp / "long2.txt"
        f.write_text(long_text, encoding="utf-8")
        llm = make_llm(["第一块正文。"])
        cfg = self.write_config([
            {"id": "in", "type": "input", "input_from": []},
            {"id": "p", "type": "process", "input_from": ["in"], "prompt": "抽取：{in}",
             "provider": "vllm", "output_format": "text",
             "chunk_size": 300, "retry": 0}])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            out = json.loads(Path(we.run_workflow(str(f), cfg)).read_text(encoding="utf-8"))
        self.assertGreater(out["_meta"]["chunking"]["p"]["chunks"], 3)
        self.assertIsInstance(out["nodes"]["p"], str)
        self.assertEqual(out["nodes"]["p"], "第一块正文。")     # 文本按行去重后只有一条
        self.assertEqual(out["status"], "success")


class TestConfigLint(unittest.TestCase):
    """X1 配置校验：未知类型/字段/非法取值/坏引用必须显式暴露。"""

    def _base(self, **node):
        n = {"id": "in", "type": "input", "input_from": []}
        n.update(node)
        return {"nodes": [n]}

    def test_valid_config_has_no_errors(self):
        errs, warns = we.lint_config(self._base())
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])

    def test_unknown_node_type_is_error(self):
        errs, _ = we.lint_config({"nodes": [{"id": "x", "type": "magic"}]})
        self.assertTrue(any("类型未知" in e for e in errs), errs)

    def test_unknown_field_is_warning(self):
        errs, warns = we.lint_config(self._base(bogus_field=1))
        self.assertEqual(errs, [])
        self.assertTrue(any("bogus_field" in w for w in warns), warns)

    def test_wrong_type_is_error(self):
        errs, _ = we.lint_config(self._base(chunk_size="三千"))
        self.assertTrue(any("chunk_size" in e for e in errs), errs)

    def test_out_of_range_is_error(self):
        errs, _ = we.lint_config(self._base(chunk_workers=999))
        self.assertTrue(any("chunk_workers" in e for e in errs), errs)

    def test_bad_enum_is_error(self):
        errs, _ = we.lint_config(self._base(on_error="explode"))
        self.assertTrue(any("on_error" in e for e in errs), errs)

    def test_dangling_reference_is_error(self):
        errs, _ = we.lint_config({"nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "p", "type": "process", "input_from": ["ghost"]}]})
        self.assertTrue(any("ghost" in e for e in errs), errs)

    def test_full_text_reference_is_allowed(self):
        errs, _ = we.lint_config({"nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "p", "type": "process", "input_from": ["full_text"]}]})
        self.assertEqual(errs, [])

    def test_duplicate_ids_is_error(self):
        errs, _ = we.lint_config({"nodes": [
            {"id": "a", "type": "input"}, {"id": "a", "type": "process"}]})
        self.assertTrue(any("重复" in e for e in errs), errs)

    def test_empty_config_is_error(self):
        errs, _ = we.lint_config({"nodes": []})
        self.assertTrue(errs)

    def test_useless_combination_warnings(self):
        errs, warns = we.lint_config(self._base(type="process", chunk_workers=4))
        self.assertEqual(errs, [])
        self.assertTrue(any("chunk_workers" in w for w in warns), warns)

    def test_cache_with_temperature_warns(self):
        _, warns = we.lint_config(self._base(type="process", cache=True, temperature=0.7))
        self.assertTrue(any("temperature" in w for w in warns), warns)

    def test_lora_on_retrieval_warns(self):
        _, warns = we.lint_config(self._base(type="retrieval", lora="x"))
        self.assertTrue(any("lora" in w and "不生效" in w for w in warns), warns)

    @unittest.skip("出厂预置已改为 4 节点示例（2026-10-22），本用例针对旧 9 节点配置")
    def test_shipped_config_is_clean(self):
        path = Path(we.__file__).resolve().parent / "workflow_config.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        errs, warns = we.lint_config(cfg)
        self.assertEqual(errs, [], f"发布配置有错误: {errs}")
        self.assertEqual(warns, [], f"发布配置有警告: {warns}")


class TestLoraPreflight(unittest.TestCase):
    """L1 跑前 LoRA 预检：未安装 / 底座不兼容必须拒绝启动。"""

    def _cfg(self, *loras):
        return {"nodes": [{"id": "in", "type": "input", "input_from": []}] +
                [{"id": f"n{i}", "type": "process", "input_from": ["in"], "lora": l}
                 for i, l in enumerate(loras)]}

    def _fake_backend(self, loras, vllm_hidden=2560):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"loras": loras, "vllm_hidden_size": vllm_hidden}
        return mock.patch.object(we.requests, "get", return_value=resp)

    def test_all_good(self):
        with self._fake_backend([{"name": "a", "compatible": True, "hidden_size": 2560}]):
            errs, warns, info = we.preflight_loras(self._cfg("a"))
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])
        self.assertEqual(info["needed"], ["a"])

    def test_missing_lora_is_error(self):
        with self._fake_backend([]):
            errs, _, _ = we.preflight_loras(self._cfg("ghost"))
        self.assertTrue(any("未安装" in e for e in errs), errs)

    def test_incompatible_lora_is_error(self):
        with self._fake_backend([{"name": "bad", "compatible": False, "hidden_size": 3584}]):
            errs, _, _ = we.preflight_loras(self._cfg("bad"))
        self.assertTrue(any("不兼容" in e for e in errs), errs)

    def test_unknown_compat_is_warning(self):
        with self._fake_backend([{"name": "u", "compatible": None, "hidden_size": None}]):
            errs, warns, _ = we.preflight_loras(self._cfg("u"))
        self.assertEqual(errs, [])
        self.assertTrue(any("未知" in w for w in warns), warns)

    def test_backend_unreachable_is_warning_not_error(self):
        with mock.patch.object(we.requests, "get", side_effect=RuntimeError("down")):
            errs, warns, _ = we.preflight_loras(self._cfg("a"))
        self.assertEqual(errs, [])
        self.assertTrue(any("跳过" in w for w in warns), warns)

    def test_no_loras_needed_is_free(self):
        with mock.patch.object(we.requests, "get") as get:
            errs, warns, info = we.preflight_loras({"nodes": [
                {"id": "in", "type": "input", "input_from": []}]})
        self.assertEqual((errs, warns), ([], []))
        get.assert_not_called()          # 不需要 LoRA 时不该去查后端

    def test_non_vllm_provider_ignores_lora(self):
        cfg = {"nodes": [{"id": "p", "type": "process", "provider": "openai", "lora": "x"}]}
        with mock.patch.object(we.requests, "get") as get:
            errs, _, _ = we.preflight_loras(cfg)
        self.assertEqual(errs, [])
        get.assert_not_called()


class TestLoraScheduling(unittest.TestCase):
    """L2/L3：LoRA 感知调度必须在**不破坏依赖**的前提下减少换入换出。"""

    def _node(self, nid, lora, deps=()):
        return {"id": nid, "type": "process", "lora": lora, "input_from": list(deps)}

    def test_count_lora_swaps(self):
        nodes = [self._node("a", "x"), self._node("b", "x"), self._node("c", "y")]
        self.assertEqual(we.count_lora_swaps(nodes), 2)      # x, y
        self.assertEqual(we.count_lora_swaps(
            [self._node("a", None), self._node("b", None)]), 0)

    def test_scheduling_preserves_dependencies(self):
        nodes = [self._node("in", None), self._node("a", "x", ["in"]),
                 self._node("b", "y", ["in"]), self._node("c", "x", ["in"]),
                 self._node("d", "y", ["b"])]
        out = we.schedule_by_lora(nodes)
        self.assertEqual(len(out), len(nodes))
        seen = set()
        for n in out:
            for dep in n["input_from"]:
                self.assertIn(dep, seen, f"{n['id']} 早于其依赖 {dep} 执行")
            seen.add(n["id"])

    def test_scheduling_reduces_swaps_when_possible(self):
        # 朴素顺序 x,y,x → 3 次换入；按 LoRA 分组后可降到 2 次
        nodes = [self._node("in", None), self._node("a", "x", ["in"]),
                 self._node("b", "y", ["in"]), self._node("c", "x", ["in"])]
        scheduled = we.schedule_by_lora(nodes)
        self.assertLess(we.count_lora_swaps(scheduled), we.count_lora_swaps(nodes))

    def test_scheduling_keeps_original_when_no_benefit(self):
        nodes = [self._node("in", None), self._node("a", "x", ["in"]),
                 self._node("b", "y", ["a"]), self._node("c", "z", ["b"])]
        scheduled = we.schedule_by_lora(nodes)
        self.assertEqual([n["id"] for n in scheduled], ["in", "a", "b", "c"])

    def test_cycle_fallback_does_not_drop_nodes(self):
        nodes = [self._node("a", "x", ["b"]), self._node("b", "x", ["a"])]
        out = we.schedule_by_lora(nodes)
        self.assertEqual(sorted(n["id"] for n in out), ["a", "b"])

    def test_meta_records_swap_stats(self):
        # 端到端：run_workflow 必须把换入统计写进 _meta
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="sched_"))
        paper = tmp / "p.txt"
        paper.write_text("短文。", encoding="utf-8")
        cfg_path = tmp / "c.json"
        cfg_path.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "p", "type": "process", "input_from": ["in"], "lora": "x",
             "prompt": "P：{in}", "provider": "vllm"}]}, ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", type("R", (), {
                 "swaps": [{"lora": "x", "sec": 1.5}],
                 "ensure": lambda self, l: (True, f"已加载 LoRA '{l}'（1.5s）")})), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {"needed": ["x"]})), \
             mock.patch.object(we, "call_llm_strict", side_effect=lambda *a, **k: '{"a": 1}'):
            out = json.loads(Path(we.run_workflow(str(paper), str(cfg_path))).read_text(encoding="utf-8"))
        self.assertEqual(out["_meta"]["lora_swaps"], [{"lora": "x", "sec": 1.5}])
        self.assertEqual(out["_meta"]["lora_swaps_planned"], 1)


class TestUnknownNodeType(unittest.TestCase):
    """D4：未知节点类型必须显式失败，不能静默按 process 处理。"""

    def test_unknown_type_fails_loudly(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="unk_"))
        paper = tmp / "p.txt"
        paper.write_text("短文。", encoding="utf-8")
        cfg_path = tmp / "c.json"
        cfg_path.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "weird", "type": "retrieval_v2", "input_from": ["in"]}]},
            ensure_ascii=False), encoding="utf-8")
        # 配置校验会在启动前拦下（未知类型 = error）
        errs, _ = we.lint_config(json.loads(cfg_path.read_text(encoding="utf-8")))
        self.assertTrue(any("retrieval_v2" in e for e in errs), errs)




class TestShippedConfig(unittest.TestCase):
    """随项目发布的 workflow_config.json 必须自洽 —— 防止配置漂移悄悄跑不通。"""

    @classmethod
    def setUpClass(cls):
        path = Path(we.__file__).resolve().parent / "workflow_config.json"
        cls.cfg = json.loads(path.read_text(encoding="utf-8"))
        cls.nodes = cls.cfg["nodes"]

    def test_parses_and_has_nodes(self):
        self.assertTrue(self.nodes)
        self.assertTrue(self.cfg.get("workflow_name"))

    def test_topological_sort_works(self):
        ordered = we.topological_sort(self.nodes)          # 有环会抛异常
        self.assertEqual(len(ordered), len(self.nodes))

    def test_node_ids_unique(self):
        ids = [n["id"] for n in self.nodes]
        self.assertEqual(len(ids), len(set(ids)), "节点 id 重复")

    def test_all_input_refs_resolve(self):
        ids = {n["id"] for n in self.nodes}
        for n in self.nodes:
            for src in n.get("input_from", []):
                parent = src.split(".", 1)[0]
                self.assertIn(parent, ids, f"{n['id']} 引用了不存在的节点 {src}")

    def test_types_are_known(self):
        """类型清单以引擎注册表为准（写死会随新增任务类型而过时）。"""
        for n in self.nodes:
            self.assertIn(n.get("type"), we.NODE_TYPES,
                          f"{n['id']} 类型未知: {n.get('type')}")

    def test_output_format_values(self):
        for n in self.nodes:
            if "output_format" in n:
                self.assertIn(n["output_format"], ("json", "text"),
                              f"{n['id']} output_format 非法")

    def test_chunk_fields_are_non_negative(self):
        for n in self.nodes:
            for key in ("chunk_size", "chunk_overlap", "top_k"):
                if key in n:
                    self.assertIsInstance(n[key], int)
                    self.assertGreaterEqual(n[key], 0, f"{n['id']}.{key} 不能为负")

    def test_every_node_has_a_position(self):
        """
        发布配置里每个节点都要有 position —— 那是 DAG 画布的布局坐标。
        它曾经被一次"测试不带 name 调 import"悄悄抹成 null（见 tests/test_api.py 的
        tearDownModule 守卫）。这里把"发布配置是可直接渲染的"固化成不变量。
        """
        cfg = json.loads(Path("workflow_config.json").read_text(encoding="utf-8"))
        missing = [n.get("id") for n in cfg["nodes"]
                   if not isinstance(n.get("position"), dict)
                   or "x" not in n["position"] or "y" not in n["position"]]
        self.assertEqual(missing, [], "这些节点缺 DAG 布局坐标: %s" % missing)

    def test_retrieval_nodes_have_query_template(self):
        for n in self.nodes:
            if n.get("type") == "retrieval":
                self.assertTrue((n.get("query_template") or "").strip(),
                                f"{n['id']} 缺 query_template")

    def test_has_extract_node(self):
        """★ 2026-10-22: 出厂预置换成了 4 节点示例（doc/points/outline/report）
        不再有 extract_method 节点 ⇒ 断言改为「存在至少一个 extract 类型节点」"""
        nodes = self.cfg.get("nodes", [])
        self.assertTrue(any(n.get("type") == "extract" for n in nodes),
                        "预置工作流应至少有一个 extract 节点")

    @unittest.skip("出厂预置已改为 4 节点示例（2026-10-22），本用例针对旧 9 节点配置")
    def test_extract_method_has_batch1_benefits_on(self):
        """批次一收益（并发分块 + 缓存）必须在**生效后**成立 —— 现在由类型契约提供。"""
        node = next(n for n in self.nodes if n["id"] == "extract_method")
        effective = we.apply_type_defaults(dict(node))
        self.assertGreaterEqual(int(effective.get("chunk_workers") or 1), 2)
        self.assertTrue(effective.get("cache"))

    def test_only_deterministic_nodes_are_cached(self):
        """只有低温/确定的节点才允许开缓存 —— 否则会固化一次随机结果。"""
        for n in self.nodes:
            if n.get("cache"):
                effective_temp = n.get("temperature")
                self.assertIn(effective_temp, (None, 0, 0.0),
                              f"{n['id']} 开了缓存但 temperature={effective_temp}")

    def test_required_loras_is_list(self):
        """★ 2026-10-22: 出厂预置不依赖任何 LoRA（空装可跑）⇒ 只断言字段类型"""
        rl = self.cfg.get("required_loras", [])
        self.assertIsInstance(rl, list, "required_loras 必须是 list")

    @unittest.skip("出厂预置已改为 4 节点示例（2026-10-22），本用例针对旧 9 节点配置")
    def test_analyze_method_consumes_the_extract(self):
        node = next(n for n in self.nodes if n["id"] == "analyze_method")
        self.assertIn("extract_method", node["input_from"])
        self.assertIn("{extract_method}", node["prompt"])

    @unittest.skip("出厂预置已改为 4 节点示例（2026-10-22），本用例针对旧 9 节点配置")
    def test_extract_claims_no_longer_feeds_raw_paper(self):
        """省一次万级 token 的云端调用：claims 改吃本地抽取 + 云端维度抽取。"""
        node = next(n for n in self.nodes if n["id"] == "extract_claims")
        self.assertNotIn("input_paper", node["input_from"])
        self.assertIn("extract_method", node["input_from"])
        self.assertIn("extract_dimensions", node["input_from"])
        self.assertNotIn("{input_paper}", node["prompt"])

    @unittest.skip("出厂预置已改为 4 节点示例（2026-10-22），本用例针对旧 9 节点配置")
    def test_extract_claims_forbids_fabricating_evidence(self):
        """输入变薄之后，必须明确要求"找不到证据就说没有"，否则会靠常识补全。"""
        node = next(n for n in self.nodes if n["id"] == "extract_claims")
        self.assertIn("材料未提供", node["prompt"])
        self.assertIn("不要凭常识", node["prompt"])

    def test_no_node_still_feeds_raw_paper_to_cloud_unnecessarily(self):
        """云端节点里只允许 extract_dimensions 继续吃全文（它需要七维度全景）。"""
        heavy = [n["id"] for n in self.nodes
                 if n.get("provider") == "openai" and "input_paper" in (n.get("input_from") or [])]
        # extract_dimensions 已在阶段一本地化（走分块），因此**不该再有**任何云端节点
        # 直接吃整篇原文 —— 这是一条比原来更强的约束。
        self.assertEqual(heavy, [], f"云端节点不该再吃全文，实测: {heavy}")


class TestParseJsonArray(unittest.TestCase):
    """顶层 JSON 数组是合法输出 —— 实测模型真的会这么答，不能判成解析失败。"""

    def test_object_still_wins(self):
        self.assertEqual(we.parse_json_response('{"a": [1, 2]}'), {"a": [1, 2]})

    def test_top_level_array_is_wrapped(self):
        got = we.parse_json_response('[{"claim": "x"}, {"claim": "y"}]')
        self.assertEqual(got["_was_array"], True)
        self.assertEqual(len(got["items"]), 2)
        self.assertEqual(got["items"][0]["claim"], "x")
        self.assertNotIn("_parse_error", got)

    def test_fenced_array(self):
        got = we.parse_json_response('```json\n[{"a": 1}]\n```')
        self.assertEqual(got["items"], [{"a": 1}])

    def test_array_inside_object_is_not_treated_as_top_level(self):
        got = we.parse_json_response('{"claims": [{"a": 1}], "n": 2}')
        self.assertEqual(got["claims"], [{"a": 1}])
        self.assertNotIn("_was_array", got)

    def test_broken_array_fails_instead_of_silently_truncating(self):
        """残缺的顶层数组必须显式失败：退回内部对象会静默只返回数组的一小部分。"""
        got = we.parse_json_response('[{"a": 1},{"b": 2},')
        self.assertIn("_raw", got)
        self.assertTrue(got.get("_parse_error"))

    def test_prefix_prose_then_array(self):
        got = we.parse_json_response('好的，结果如下：\n[{"a": 1}]')
        self.assertEqual(got["items"], [{"a": 1}])

    def test_garbage_returns_raw(self):
        got = we.parse_json_response("完全不是 JSON")
        self.assertIn("_raw", got)


class TestChunkWorkers(WorkflowTestCase):
    """分块并发：结果必须与串行完全一致，失败语义不变。"""

    def setUp(self):
        super().setUp()
        self.long_text = "".join(f"这是第{i}个句子，描述论文的方法细节。" for i in range(120))
        self.long_file = self.tmp / "long_w.txt"
        self.long_file.write_text(self.long_text, encoding="utf-8")

    def _run(self, llm, **over):
        proc = {"id": "p", "type": "process", "input_from": ["in"],
                "prompt": "抽取：{in}", "provider": "vllm", "retry": 0}
        proc.update(over)
        cfg = self.write_config([{"id": "in", "type": "input", "input_from": []}, proc])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            return json.loads(Path(we.run_workflow(str(self.long_file), cfg)).read_text(encoding="utf-8"))

    def test_parallel_matches_serial_result(self):
        llm = make_llm(['{"a": "1"}'])
        out = self._run(llm, chunk_size=300, chunk_workers=4)
        self.assertEqual(out["nodes"]["p"], {"a": "1"})
        info = out["_meta"]["chunking"]["p"]
        self.assertGreater(info["chunks"], 3)
        self.assertEqual(info["workers"], 4)
        self.assertEqual(info["ok"], info["chunks"])       # 所有块都跑了
        self.assertEqual(info["failed_chunks"], [])
        self.assertEqual(len(llm.calls), info["chunks"])   # 一次不多一次不少

    def test_parallel_reports_failed_chunk(self):
        llm = make_llm([we.LLMCallError("块超时"), '{"a": "1"}'])
        out = self._run(llm, chunk_size=300, chunk_workers=3)
        info = out["_meta"]["chunking"]["p"]
        self.assertEqual(len(info["failed_chunks"]), 1)
        self.assertEqual(info["ok"], info["chunks"] - 1)
        self.assertTrue(any("分块调用失败" in d["reason"] for d in out["_meta"]["degraded"]))
        self.assertEqual(out["status"], "partial")

    def test_all_chunks_failed_in_parallel(self):
        llm = make_llm([we.LLMCallError("全挂")])
        out = self._run(llm, chunk_size=300, chunk_workers=4)
        self.assertTrue(out["nodes"]["p"].get("_failed"))
        self.assertIn("分块", out["_meta"]["failures"][0]["error"])


class TestNodeCache(WorkflowTestCase):
    """节点结果缓存：命中跳过调用；失败不缓存；改配置自动失效。"""

    def setUp(self):
        super().setUp()
        self.cache_dir = self.tmp / "cache"
        self.cache_dir.mkdir()
        p = mock.patch.object(we, "CACHE_DIR", str(self.cache_dir))
        p.start()
        self.addCleanup(p.stop)

    def _nodes(self, **over):
        proc = {"id": "p", "type": "process", "input_from": ["in"], "cache": True,
                "prompt": "抽取：{in}", "provider": "vllm", "retry": 0}
        proc.update(over)
        return [{"id": "in", "type": "input", "input_from": []}, proc]

    def _run(self, llm, nodes=None):
        cfg = self.write_config(nodes or self._nodes())
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            return json.loads(Path(we.run_workflow(str(self.input_file), cfg)).read_text(encoding="utf-8"))

    def test_second_run_hits_cache(self):
        first = self._run(make_llm(['{"a": "1"}']))
        self.assertEqual(first["nodes"]["p"], {"a": "1"})
        llm2 = make_llm(['{"a": "999"}'])          # 若真调用会返回 999
        second = self._run(llm2)
        self.assertEqual(len(llm2.calls), 0)       # 完全没调用
        self.assertEqual(second["nodes"]["p"], {"a": "1"})
        self.assertTrue(any("命中缓存" in l for l in second["log"]))

    def test_cache_key_changes_with_prompt(self):
        self._run(make_llm(['{"a": "1"}']))
        llm2 = make_llm(['{"a": "2"}'])
        out = self._run(llm2, nodes=self._nodes(prompt="换个问法：{in}"))
        self.assertEqual(len(llm2.calls), 1)       # 提示词变了 → 未命中
        self.assertEqual(out["nodes"]["p"], {"a": "2"})

    def test_cache_key_changes_with_generation_params(self):
        self._run(make_llm(['{"a": "1"}']))
        llm2 = make_llm(['{"a": "2"}'])
        self._run(llm2, nodes=self._nodes(temperature=0.9))
        self.assertEqual(len(llm2.calls), 1)

    def test_failure_is_not_cached(self):
        self._run(make_llm([we.LLMCallError("挂了")]))
        llm2 = make_llm(['{"a": "ok"}'])
        out = self._run(llm2)
        self.assertEqual(len(llm2.calls), 1)       # 失败没被固化
        self.assertEqual(out["nodes"]["p"], {"a": "ok"})

    def test_parse_failure_is_not_cached(self):
        self._run(make_llm(["这不是 JSON"]))
        llm2 = make_llm(['{"a": "ok"}'])
        out = self._run(llm2)
        self.assertEqual(len(llm2.calls), 1)
        self.assertEqual(out["nodes"]["p"], {"a": "ok"})

    def test_chunked_node_cache_hit_restores_chunking_info(self):
        """最主要的使用场景：分块抽取节点第二次运行应完全走缓存。"""
        long_text = "".join(f"第{i}句方法描述。" for i in range(200))
        f = self.tmp / "long_c.txt"
        f.write_text(long_text, encoding="utf-8")
        nodes = self._nodes(chunk_size=300, chunk_workers=2)
        cfg = self.write_config(nodes)

        llm = make_llm(['{"a": "1"}'])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm):
            first = json.loads(Path(we.run_workflow(str(f), cfg)).read_text(encoding="utf-8"))
        self.assertGreater(first["_meta"]["chunking"]["p"]["chunks"], 3)

        llm2 = make_llm(['{"a": "999"}'])
        with mock.patch.object(we, "call_llm_strict", side_effect=llm2):
            second = json.loads(Path(we.run_workflow(str(f), cfg)).read_text(encoding="utf-8"))
        self.assertEqual(len(llm2.calls), 0)                      # 一次没调
        self.assertEqual(second["nodes"]["p"], {"a": "1"})
        self.assertEqual(second["_meta"]["chunking"]["p"]["chunks"],
                         first["_meta"]["chunking"]["p"]["chunks"])
        self.assertEqual(second["_meta"]["chunking"]["p"]["workers"], 2)

    def test_cache_disabled_globally(self):
        with mock.patch.object(we, "CACHE_ENABLED", False):
            self._run(make_llm(['{"a": "1"}']))
            llm2 = make_llm(['{"a": "2"}'])
            self._run(llm2)
            self.assertEqual(len(llm2.calls), 1)   # 全局关闭时不读缓存

    def test_nodes_without_cache_flag_are_not_cached(self):
        nodes = self._nodes()
        nodes[1].pop("cache")
        self._run(make_llm(['{"a": "1"}']), nodes=nodes)
        llm2 = make_llm(['{"a": "2"}'])
        self._run(llm2, nodes=nodes)
        self.assertEqual(len(llm2.calls), 1)

    def test_cache_file_written_atomically(self):
        self._run(make_llm(['{"a": "1"}']))
        files = list(self.cache_dir.glob("*.json"))
        self.assertEqual(len(files), 1)            # 没有残留 .tmp
        self.assertEqual(list(self.cache_dir.glob(".*tmp")), [])


class TestFrontendFieldConsistency(unittest.TestCase):
    """
    X5：前端能设置/保存的节点字段必须都是引擎认识的字段，反之本会话新增且影响输出的
    字段必须都能在编辑器里设置。这是"加了字段但某条路径漏了"的防复发机制
    （本会话已因此出过两次问题：Agent 路径漏字段、加多配置后 Agent 仍写默认配置）。
    """

    @classmethod
    def setUpClass(cls):
        root = Path(we.__file__).resolve().parent
        cls.html = (root / "webui" / "index3.html").read_text(encoding="utf-8")
        cls.engine_fields = set(we.NODE_FIELD_SPECS)

    def test_optional_fields_whitelist_subset_of_engine(self):
        m = re.search(r"const NODE_OPTIONAL_FIELDS = \[(.*?)\];", self.html, re.S)
        self.assertIsNotNone(m, "前端找不到 NODE_OPTIONAL_FIELDS")
        names = set(re.findall(r"'([a-z_]+)'", m.group(1)))
        self.assertTrue(names)
        unknown = sorted(names - self.engine_fields)
        self.assertEqual(unknown, [], f"前端白名单含引擎不认识的字段: {unknown}")

    def test_saved_fields_are_known_to_engine(self):
        # 扫全文的 node.X = 赋值（比匹配函数体更稳）
        assigned = set(re.findall(r"node\.([a-z_]+)\s*=", self.html))
        self.assertTrue(assigned)
        unknown = sorted(assigned - self.engine_fields)
        self.assertEqual(unknown, [], f"前端保存了引擎不认识的字段: {unknown}")

    def test_editor_controls_map_to_engine_fields(self):
        ids = set(re.findall(r'id="([a-z\-]+)-\$\{node\.id\}"', self.html))
        self.assertTrue(ids)
        ui_aliases = {"input": "input_from", "kb-topk": "top_k", "kb-query": "query_template"}
        display_only = {"lora-hint"}          # 纯提示用的 span，不是输入控件
        unknown = set()
        for i in ids:
            if i in display_only:
                continue
            field = ui_aliases.get(i, i.replace("-", "_"))
            if field not in self.engine_fields:
                unknown.add(i)
        self.assertEqual(sorted(unknown), [], f"编辑器控件没有对应的引擎字段: {sorted(unknown)}")


    def test_save_reads_every_editor_control(self):
        """编辑器有的控件，saveWorkflowConfig 必须读取（否则改了不生效）。"""
        ids = set(re.findall(r'id="([a-z\-]+)-\$\{node\.id\}"', self.html))
        ignored = {"kb-name", "kb-topk", "kb-query", "input", "prompt",
                   "lora", "provider", "model-override", "lora-hint"}   # 在别处读取/纯展示
        not_read = [i for i in sorted(ids - ignored)
                    if f"getElementById(`{i}-${{node.id}}`)" not in self.html]
        self.assertEqual(not_read, [], f"这些控件没有被保存逻辑读取: {not_read}")

    # ---------- 例外名单（必须显式登记，且有测试反向检查它没过期）----------
    # 节点身份/布局：由编辑器自动生成，不是"用户要填的字段"
    STRUCTURAL = {"id", "type", "position"}
    # 在 add_workflow_node / update_workflow_node 里单独赋值，不走白名单循环
    AGENT_SPECIAL = {"lora", "prompt", "provider", "model_override", "input_from",
                     "description", "kb_name", "top_k", "query_template"}
    # 刻意只暴露给 Agent / 配置文件，UI 不做控件（运维级参数，日常不需要改）
    UI_AGENT_ONLY = {"retry", "retry_delay", "timeout", "on_error", "kb_write",
                       "tool", "tool_args", "tool_timeout"}

    _UI_ALIAS = {"input": "input_from", "kb-name": "kb_name", "kb-topk": "top_k",
                 "kb-query": "query_template", "model-override": "model_override",
                 "output-format": "output_format", "chunk-size": "chunk_size",
                 "chunk-overlap": "chunk_overlap", "chunk-workers": "chunk_workers",
                 "max-tokens": "max_tokens"}

    def _js_array(self, name):
        """从 index3.html 里取一个 JS 字符串数组（用于声明式清单）。"""
        m = re.search(r"const %s = \[(.*?)\];" % re.escape(name), self.html, re.S)
        self.assertIsNotNone(m, "前端找不到 %s" % name)
        return set(re.findall(r"'([A-Za-z0-9_]+)'", m.group(1)))

    def _agent_whitelist(self):
        m = re.search(r"const NODE_OPTIONAL_FIELDS = \[(.*?)\];", self.html, re.S)
        self.assertIsNotNone(m, "前端找不到 NODE_OPTIONAL_FIELDS")
        return set(re.findall(r"'([a-z_]+)'", m.group(1)))

    def _ui_control_ids(self):
        """编辑器里手写的控件 id（形如 output-format-${node.id}）。"""
        ids = set(re.findall(r'id="([a-z0-9\-]+)-\$\{node\.id\}"', self.html))
        ids.discard("lora-hint")               # 纯提示 span，不是输入控件
        return ids

    def _ui_fields(self):
        return {self._UI_ALIAS.get(i, i.replace("-", "_"))
                for i in self._ui_control_ids()}

    def _settable(self):
        return (self._agent_whitelist() | self._ui_fields()
                | self.AGENT_SPECIAL | self.UI_AGENT_ONLY | self.STRUCTURAL)

    # ---------- 双向一致性 ----------

    def test_engine_registry_has_no_field_without_a_path_to_set_it(self):
        """
        **过去只查一个方向**：前端 ⊆ 引擎（防"前端塞了引擎不认识的字段"）。
        反方向从没人查 —— 于是 `min_score` 加进引擎注册表后 UI 与 Agent 都设不了，
        而注释里明明写着"有 X5 一致性测试守着"，测试却照样全绿。
        """
        orphans = sorted(self.engine_fields - self._settable())
        self.assertEqual(orphans, [], (
            "这些字段引擎注册了，但 UI 与 Agent 都没有路径设置它。"
            "要么补控件/白名单，要么加进 AGENT_SPECIAL / UI_AGENT_ONLY 并写明理由: %s"
            % orphans))

    def test_agent_whitelist_covers_engine_editable_fields(self):
        """Agent 必须能设置所有"可编辑"字段（11 个字段全设不了曾是 G4 的根因）。"""
        missing = sorted(self.engine_fields - self._agent_whitelist() - self.AGENT_SPECIAL
                         - self.STRUCTURAL)
        self.assertEqual(missing, [], "Agent 无法设置的引擎字段: %s" % missing)

    def test_ui_renders_every_engine_field_or_lists_it_as_exception(self):
        """
        X2 之后编辑器是**注册表驱动**的（控件不再手写，扫不到字面量 id）。
        新的不变量：每个引擎字段要么被显式列入例外名单，要么它的类型有渲染器。
        —— 加一个"新类型"的字段（比如 object）却忘了登记渲染器，会在这里红。
        """
        hidden = self._js_array("UI_HIDDEN_FIELDS")
        agent_only = self._js_array("UI_AGENT_ONLY_FIELDS")
        custom = self._js_array("UI_CUSTOM_FIELDS")
        renderable = self._js_array("UI_RENDERABLE_TYPES")
        self.assertTrue(renderable, "前端没有声明 UI_RENDERABLE_TYPES")
        exceptions = hidden | agent_only | custom
        bad = []
        for f, spec in we.NODE_FIELD_SPECS.items():
            if f in exceptions:
                continue
            has_choices = bool(spec.get("choices"))
            if not has_choices and spec.get("type") not in renderable:
                bad.append("%s(type=%s)" % (f, spec.get("type")))
        self.assertEqual(bad, [], "这些字段既不在例外名单里，也没有渲染器: %s" % bad)

    def test_ui_exception_lists_only_name_real_engine_fields(self):
        """例外名单里不能写不存在的字段（名单腐烂 = 不变量失效）。"""
        for name in ("UI_HIDDEN_FIELDS", "UI_AGENT_ONLY_FIELDS", "UI_CUSTOM_FIELDS"):
            unknown = sorted(self._js_array(name) - self.engine_fields)
            self.assertEqual(unknown, [], "%s 含引擎不认识的字段: %s" % (name, unknown))

    def test_ui_agent_only_fields_match_backend(self):
        """"UI 刻意不渲染"这件事两边的清单必须一致（同一个概念，不该有两份）。"""
        self.assertEqual(self._js_array("UI_AGENT_ONLY_FIELDS"), self.UI_AGENT_ONLY)

    def test_render_and_read_share_the_same_id_function(self):
        """
        注册表驱动之后，"渲染"与"保存"必须用**同一个** id 生成函数 ——
        否则会出现"控件画出来了、但保存读的是另一个 id"，也就是改了不生效。
        """
        self.assertIn("function fieldDomId(f, node)", self.html)
        self.assertIn("const id = fieldDomId(f, node);", self.html)          # 渲染侧
        self.assertIn("getElementById(fieldDomId(f, node))", self.html)      # 读取侧

    def test_editor_error_is_not_silent_when_registry_missing(self):
        """注册表拿不到时必须显式报错，不能悄悄退回一份内置清单（那就是第三处真源）。"""
        self.assertIn("字段注册表未加载", self.html)
        self.assertNotIn("FALLBACK_FIELDS", self.html)

    def test_exception_lists_are_not_stale(self):
        """例外名单不能腐烂：每一条都必须是真实字段、且确实还没有对应路径。"""
        for f in sorted(self.STRUCTURAL | self.AGENT_SPECIAL | self.UI_AGENT_ONLY):
            self.assertIn(f, self.engine_fields, "%s 已不是引擎字段，请从例外名单删掉" % f)
        for f in sorted(self.UI_AGENT_ONLY):
            self.assertNotIn(f, self._ui_fields(),
                             "%s 已经有控件了，请从 UI_AGENT_ONLY 删掉" % f)

    def test_frontend_harness_fixture_matches_engine_registry(self):
        """
        前端脚手架里那份 /workflow/node-types **夹具**必须与引擎注册表一致 ——
        否则 X2 的运行时渲染测试测的是一份过期注册表，等于没测。
        更新方式：`python /mnt/e/WSL/_check/gen_fixture.py` 后替换 run.js 里那段。
        """
        src = (Path("tests") / "frontend" / "run.js").read_text(encoding="utf-8")
        m = re.search(r"const NODE_TYPES_FIXTURE = (\{.*?\n\});", src, re.S)
        self.assertIsNotNone(m, "脚手架里找不到 NODE_TYPES_FIXTURE")
        # 夹具是 JS 字面量（允许尾逗号），拿去当 JSON 解析前先去掉尾逗号
        raw = re.sub(r",(\s*[}\]])", r"\1", m.group(1))
        fx = json.loads(raw)
        self.assertEqual(set(fx["fields"]), set(we.NODE_FIELD_SPECS),
                         "夹具与引擎注册表的字段集不一致（加字段后请重跑 gen_fixture）")
        for f, spec in we.NODE_FIELD_SPECS.items():
            self.assertEqual(fx["fields"][f].get("type"), spec.get("type"),
                             "字段 %s 在夹具里的类型与引擎不一致" % f)
        self.assertEqual({t["type"] for t in fx["types"]}, set(we.NODE_TYPES),
                         "夹具的类型集与引擎不一致")

    def test_ui_min_score_placeholder_matches_engine_default(self):
        """
        UI 里写给用户看的"默认值"是**重复的常量**，必须与引擎注册表一致。
        这正是"两处真源"的经典形态：不测它就会在某次改默认值后悄悄说谎。
        """
        default = we.NODE_FIELD_SPECS["min_score"]["default"]
        m = re.search(r'id="min-score-\$\{node\.id\}"[^>]*?placeholder="[^"]*?([0-9.]+)',
                      self.html, re.S)
        self.assertIsNotNone(m, "找不到 min-score 控件的 placeholder（或里面没有数字）")
        self.assertEqual(float(m.group(1)), float(default),
                         "UI 显示的默认值 %s 与引擎注册表 %s 不一致" % (m.group(1), default))

    def test_shipped_config_uses_only_settable_fields(self):
        """发布配置里实际用到的字段必须全都有设置路径（数据驱动，不会腐烂）。"""
        cfg = json.loads(Path("workflow_config.json").read_text(encoding="utf-8"))
        structural = {"id", "type", "description", "position", "input_from",
                      "required_loras", "workflow_name", "nodes", "on_error_default"}
        used = set()
        for n in cfg.get("nodes") or []:
            used |= set(n) - structural
        missing = sorted(used - self._settable())
        self.assertEqual(missing, [], "发布配置用了这些字段，但没有设置路径: %s" % missing)

    def test_agent_infers_dependencies_from_prompt(self):
        self.assertIn("function inferInputFrom", self.html)
        self.assertIn("inferInputFrom(args.prompt, knownIds)", self.html)

    def test_agent_config_path_follows_selected_config(self):
        """Agent 的配置读写必须带 name，否则会改到默认配置上（本会话踩过）。"""
        self.assertNotIn("fetch(`${API_BASE}/workflow/config`)", self.html,
                         "仍有 Agent 配置读写没带 name 参数")

    def test_frontend_node_type_list_matches_engine(self):
        """前端类型清单必须与引擎注册表完全一致（多一个少一个都是漂移）。"""
        m = re.search(r"const NODE_TYPE_NAMES = \[(.*?)\];", self.html, re.S)
        self.assertIsNotNone(m, "前端找不到 NODE_TYPE_NAMES")
        names = set(re.findall(r"'([a-z_]+)'", m.group(1)))
        self.assertEqual(names, set(we.NODE_TYPES),
                         "前端 %s vs 引擎 %s" % (sorted(names), sorted(we.NODE_TYPES)))

    def test_frontend_labels_cover_every_type(self):
        m = re.search(r"function getNodeTypeLabel\(type\) \{\s*return \{(.*?)\}\[type\]",
                      self.html, re.S)
        self.assertIsNotNone(m, "找不到 getNodeTypeLabel 映射")
        labelled = set(re.findall(r"([a-z_]+):", m.group(1)))
        missing = sorted(set(we.NODE_TYPES) - labelled)
        self.assertEqual(missing, [], "这些类型在前端没有中文标签: %s" % missing)

    def test_type_picker_offers_every_type(self):
        offered = set(re.findall(r'class="type-option[^"]*" data-type="([a-z_]+)"', self.html))
        missing = sorted(set(we.NODE_TYPES) - offered - {"input"})   # input 由工具栏创建
        self.assertEqual(missing, [], "添加节点弹窗缺少这些类型: %s" % missing)

    def test_task_type_defaults_agree_between_frontend_and_engine(self):
        """前端建 extract 节点时给的默认值，必须与引擎 NODE_TYPE_SPECS 一致。"""
        m = re.search(r"pendingType === 'extract'.*?extra = \{(.*?)\};", self.html, re.S)
        self.assertIsNotNone(m, "找不到 extract 的前端默认值")
        block = m.group(1)
        for key in we.NODE_TYPE_SPECS["extract"]["defaults"]:
            if key == "temperature":
                continue
            self.assertIn(key, block, "前端 extract 默认值缺 %s" % key)
        self.assertIn("chunk_size: 3000", block)





class TestNodeTypeRegistry(unittest.TestCase):
    def setUp(self):
        # ISOLATE_NODE_TYPE_REGISTRY_CACHE：该类会跑一遍工作流，其节点默认 cache=true，会把结果
        # 写进**真实**缓存目录（cache/nodes）。开发仓库里缓存本来就有条目、数量不变，
        # 所以长期被掩盖；全新检出第一次跑测试会触发共享状态守卫。
        # 定位方式：在全新副本里逐个测试类运行并统计 cache/nodes 条目数，
        # 只有本类出现 0 → 1。
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        _cp = mock.patch.object(we, "CACHE_DIR", str(self.tmp / "cache"))
        _cp.start()
        self.addCleanup(_cp.stop)

    """D1/D2：类型即契约 —— 选一个类型就自带它该有的默认字段与输出形态。"""

    def test_every_type_has_spec(self):
        for t in we.NODE_TYPES:
            spec = we.NODE_TYPE_SPECS[t]
            self.assertTrue(spec.get("label"), t)
            self.assertIn(spec.get("output"), ("text", "json"), t)
            self.assertIn(spec.get("kind"), ("source", "tool", "llm"), t)

    def test_input_and_retrieval_are_text(self):
        self.assertTrue(we.node_output_is_text({"type": "input"}, "input"))
        self.assertTrue(we.node_output_is_text({"type": "retrieval"}, "retrieval"))

    def test_extract_type_is_text_by_contract(self):
        """extract 是抽取任务：必须输出纯文本（抽取式 LoRA 产散文）。"""
        self.assertEqual(we.NODE_TYPE_SPECS["extract"]["output"], "text")
        self.assertTrue(we.node_output_is_text({"type": "extract"}, "extract"))

    def test_analyze_type_is_json(self):
        self.assertEqual(we.NODE_TYPE_SPECS["analyze"]["output"], "json")
        self.assertFalse(we.node_output_is_text({"type": "analyze"}, "analyze"))

    def test_apply_type_defaults_fills_recipe(self):
        node = we.apply_type_defaults({"id": "e", "type": "extract", "lora": "x"})
        self.assertEqual(node["output_format"], "text")
        self.assertEqual(node["chunk_size"], 3000)
        self.assertEqual(node["chunk_overlap"], 500)
        self.assertGreaterEqual(node["chunk_workers"], 2)
        self.assertTrue(node["cache"])
        self.assertEqual(node["temperature"], 0.0)

    def test_apply_type_defaults_does_not_override_explicit(self):
        node = we.apply_type_defaults({"id": "e", "type": "extract",
                                       "chunk_size": 8000, "cache": False})
        self.assertEqual(node["chunk_size"], 8000)
        self.assertFalse(node["cache"])

    def test_aggregate_keeps_its_defaults(self):
        node = we.apply_type_defaults({"id": "a", "type": "aggregate"})
        self.assertEqual(node["max_tokens"], 4096)
        self.assertEqual(node["temperature"], 0.5)

    def test_plain_process_gets_no_surprise_defaults(self):
        node = we.apply_type_defaults({"id": "p", "type": "process"})
        self.assertEqual(node, {"id": "p", "type": "process"})

    def test_extract_type_defaults_pass_lint(self):
        node = we.apply_type_defaults({"id": "e", "type": "extract", "lora": "x",
                                       "input_from": ["in"]})
        errs, warns = we.lint_config({"nodes": [
            {"id": "in", "type": "input", "input_from": []}, node]})
        self.assertEqual(errs, [])
        self.assertEqual(warns, [], "extract 类型默认值不该产生警告: %s" % warns)

    def test_extract_without_lora_warns(self):
        _, warns = we.lint_config({"nodes": [{"id": "e", "type": "extract"}]})
        self.assertTrue(any("没有指定 LoRA" in w for w in warns), warns)

    def test_input_node_with_prompt_warns(self):
        _, warns = we.lint_config({"nodes": [
            {"id": "in", "type": "input", "input_from": [],
             "prompt": "这段提示词对输入节点无意义"}]})
        self.assertTrue(any("prompt" in w and "不生效" in w for w in warns), warns)

    def test_extract_node_runs_as_text_end_to_end(self):
        """端到端：类型为 extract 的节点即使不写 output_format 也按文本处理。"""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="ext_"))
        paper = tmp / "p.txt"
        paper.write_text("短文内容。", encoding="utf-8")
        cfg_path = tmp / "c.json"
        cfg_path.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "e", "type": "extract", "input_from": ["in"], "lora": "x",
             "prompt": "抽取：{in}", "provider": "vllm"}]}, ensure_ascii=False),
            encoding="utf-8")
        with mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouter), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {"needed": ["x"]})), \
             mock.patch.object(we, "call_llm_strict", side_effect=make_llm(["抽取到的正文。"])):
            out = json.loads(Path(we.run_workflow(str(paper), str(cfg_path)))
                             .read_text(encoding="utf-8"))
        self.assertIsInstance(out["nodes"]["e"], str)
        self.assertIn("抽取到的正文", out["nodes"]["e"])
        self.assertEqual(
            out["status"], "success",
            f"quality={out['_meta'].get('quality')} warnings="
            f"{out['_meta'].get('quality_warnings')} failures={out['_meta']['failures']} "
            f"degraded={out['_meta']['degraded']}")



class TestNodeTracesAndRegistry(unittest.TestCase):
    """闭环的两块地基：节点 trace（供导出训练数据）与质量台账（供选型）。"""

    def _run_once(self, tmp, nodes, llm_out):
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"workflow_name": "t", "nodes": nodes}, ensure_ascii=False),
                       encoding="utf-8")
        paper = tmp / "p.txt"
        paper.write_text("论文正文内容。", encoding="utf-8")
        with mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouter), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {})), \
             mock.patch.object(we, "call_llm_strict", side_effect=make_llm([llm_out])):
            return json.loads(Path(we.run_workflow(str(paper), str(cfg)))
                              .read_text(encoding="utf-8"))

    def test_trace_records_prompt_and_output(self):
        tmp = Path(tempfile.mkdtemp(prefix="trace_"))
        out = self._run_once(tmp, [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "e", "type": "process", "input_from": ["in"], "prompt": "抽取：{in}",
             "provider": "vllm", "lora": "extract_v2"}], '{"a": 1}')
        tr = out["_meta"]["traces"]["e"]
        self.assertIn("抽取：", tr["prompt"])
        self.assertIn("论文正文内容", tr["prompt"])       # trace 里是**真实输入**
        self.assertEqual(tr["lora"], "extract_v2")
        self.assertEqual(json.loads(tr["output"]), {"a": 1})   # trace 存 JSON 文本
        self.assertIn("trace_note", out["_meta"])

    def test_trace_truncates_long_prompt(self):
        tmp = Path(tempfile.mkdtemp(prefix="trace2_"))
        long_text = "字" * (we.TRACE_PROMPT_CHARS + 5000)
        paper = tmp / "big.txt"
        paper.write_text(long_text, encoding="utf-8")
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "e", "type": "process", "input_from": ["in"], "prompt": "抽取：{in}"}]},
            ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouter), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {})), \
             mock.patch.object(we, "call_llm_strict", side_effect=make_llm(['{"a": 1}'])):
            out = json.loads(Path(we.run_workflow(str(paper), str(cfg))).read_text(encoding="utf-8"))
        tr = out["_meta"]["traces"]["e"]
        self.assertLessEqual(len(tr["prompt"]), we.TRACE_PROMPT_CHARS)
        self.assertTrue(tr["prompt_truncated"])
        self.assertGreater(tr["prompt_chars"], we.TRACE_PROMPT_CHARS)

    @staticmethod
    def _registry_backend(loras, registry):
        def fake_get(url, **kw):
            m = mock.Mock(status_code=200)
            if url.endswith("/loras/registry"):
                m.json.return_value = {"registry": registry}
            else:
                m.json.return_value = {"loras": loras, "vllm_hidden_size": 2560}
            return m
        return mock.patch.object(we.requests, "get", side_effect=fake_get)

    def test_preflight_warns_when_no_eval_record(self):
        cfg = {"nodes": [{"id": "e", "type": "extract", "lora": "extract_v2"}]}
        with self._registry_backend([{"name": "extract_v2", "compatible": True}], {}):
            errs, warns, info = we.preflight_loras(cfg)
        self.assertEqual(errs, [])
        self.assertTrue(any("没有评测记录" in w for w in warns), warns)

    def test_preflight_reports_low_similarity(self):
        reg = {"extract_v2": {"last_eval": {"metrics": {"similarity": 0.1}}}}
        cfg = {"nodes": [{"id": "e", "type": "extract", "lora": "extract_v2"}]}
        with self._registry_backend([{"name": "extract_v2", "compatible": True}], reg):
            _, warns, info = we.preflight_loras(cfg)
        self.assertTrue(any("偏低" in w for w in warns), warns)
        self.assertEqual(info["metrics"]["extract_v2"]["similarity"], 0.1)

    def test_preflight_silent_when_registry_unavailable(self):
        cfg = {"nodes": [{"id": "e", "type": "extract", "lora": "extract_v2"}]}
        m = mock.Mock(status_code=200)
        m.json.return_value = {"loras": [{"name": "extract_v2", "compatible": True}],
                               "vllm_hidden_size": 2560}       # 没有 registry 字段
        with mock.patch.object(we.requests, "get", return_value=m):
            errs, warns, _ = we.preflight_loras(cfg)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [], warns)



class TestLoraRouterIdempotent(unittest.TestCase):
    """适配器"已加载"必须按幂等成功处理，不能变成节点假失败（实测踩到过）。"""

    def _router(self, status_code, detail):
        """
        detail 走 r.json()["detail"]，与真实 lora_manager 的错误形状一致。
        ⚠️ patch 必须活到 ensure() 被调用之后 —— 用 with 块返回会让 mock 提前撤销，
        导致真的去打 HTTP（我第一版就是这么写错的）。
        """
        loaded = mock.Mock(status_code=200)
        loaded.json.return_value = {"loaded": []}          # 快照为空 → ensure 会真的发 load
        post = mock.Mock(status_code=status_code, text=detail)
        post.json.return_value = {"detail": detail}
        p1 = mock.patch.object(we.requests, "get", return_value=loaded)
        p2 = mock.patch.object(we.requests, "post", return_value=post)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        return we.LoraRouter(), post

    def test_already_loaded_is_success(self):
        msg = '{"error":{"message":"The lora adapter x has already been loaded.","code":400}}'
        router, post = self._router(400, msg)
        router._get_loaded = lambda: set()
        ok, note = router.ensure("x")
        self.assertTrue(ok, note)
        self.assertIn("幂等", note)
        self.assertIn("x", router.currently_loaded)

    def test_other_errors_still_fail(self):
        router, _ = self._router(500, "服务器炸了")
        router._get_loaded = lambda: set()
        ok, note = router.ensure("y")
        self.assertFalse(ok)
        self.assertIn("加载失败", note)

    def test_already_in_snapshot_skips_request(self):
        loaded = mock.Mock(status_code=200)
        loaded.json.return_value = {"loaded": ["z"]}
        with mock.patch.object(we.requests, "get", return_value=loaded), \
             mock.patch.object(we.requests, "post") as post:
            router = we.LoraRouter()
            ok, note = router.ensure("z")
        self.assertFalse(ok)                     # 没换入
        self.assertIn("已在显存", note)
        post.assert_not_called()


class TestLoraRouterResyncAndRecovery(unittest.TestCase):
    """
    快照会**双向**失同步，两个方向都得修：
      方向一（提交 4367623 已修）：快照说"没加载"，其实外部已加载 → 400 already loaded → 按幂等成功。
      方向二（本次）：快照说"已加载"，其实已被换出/卸载 → vLLM 回 404
                     "The model `x` does not exist." → 必须自动重载并重试，
                     否则明明可用的 LoRA 会让整个节点失败（实测抓到的真实响应体见下）。
    """

    def _router_with(self, loaded):
        g = mock.patch.object(we.requests, "get")
        m = g.start()
        m.return_value = mock.Mock(status_code=200)
        m.return_value.json.return_value = {"loaded": list(loaded)}
        self.addCleanup(g.stop)
        return we.LoraRouter()

    # ---- 快照重同步 ----

    def test_refresh_resyncs_from_server(self):
        router = self._router_with(["a"])
        self.assertEqual(router.currently_loaded, {"a"})
        we.requests.get.return_value.json.return_value = {"loaded": ["a", "b"]}
        self.assertEqual(router.refresh(), 2)
        self.assertEqual(router.currently_loaded, {"a", "b"})

    def test_refresh_handles_server_down(self):
        """后端不可达时不能抛异常，只能退化成空快照（原有 _get_loaded 语义）。"""
        router = self._router_with(["a"])
        we.requests.get.side_effect = RuntimeError("connection refused")
        self.assertEqual(router.refresh(), 0)
        self.assertEqual(router.currently_loaded, set())

    def test_invalidate_drops_only_target(self):
        router = self._router_with(["a", "b"])
        router.invalidate("a")
        self.assertEqual(router.currently_loaded, {"b"})

    # ---- 缺 LoRA 错误识别 ----

    def test_real_vllm_404_payload_detected(self):
        """真实响应体（2026-10-06 对 extract_v1 实测抓到）。"""
        real = ('[错误 404] {"error":{"message":"The model `extract_v1` does not exist.",'
                '"type":"NotFoundError","param":null,"code":404}}')
        self.assertTrue(we.looks_like_missing_lora_error(real))

    def test_lora_adapter_not_found_detected(self):
        self.assertTrue(we.looks_like_missing_lora_error(
            "[错误 400] LoRA adapter 'x' is not found in the model"))

    def test_unrelated_errors_not_detected(self):
        for text in ("[调用异常] Read timed out",
                     "[错误 500] internal server error",
                     "模型返回的内容不是合法 JSON",
                     ""):
            self.assertFalse(we.looks_like_missing_lora_error(text), text)

    # ---- 失败路径上的自动重载 ----

    def _policy(self):
        return {"retry": 2, "retry_delay": 0.0, "timeout": 30}

    def _err404(self, name="loraX"):
        return ('[错误 404] {"error":{"message":"The model `%s` does not exist.",'
                '"type":"NotFoundError","code":404}}' % name)

    def _run(self, fake_llm, lora="loraX", router=None):
        with mock.patch.object(we, "call_llm", side_effect=fake_llm):
            return we.llm_call_with_retry(
                True, "p", lora, None, None, self._policy(),
                lambda m: None, {}, router=router)

    def test_reload_then_retry_succeeds(self):
        """第一条 404（快照过期）→ 重载 → 第二条成功。节点不再假失败。"""
        n = {"i": 0}

        def fake(prompt, **kw):
            n["i"] += 1
            return self._err404() if n["i"] == 1 else "真正的抽取结果"

        router = mock.Mock()
        router.ensure.return_value = (True, "已加载 LoRA 'loraX'（0.3s）")
        parsed, err, pf, attempts = self._run(fake, router=router)
        self.assertEqual(parsed, "真正的抽取结果")
        self.assertIsNone(err)
        router.invalidate.assert_called_once_with("loraX")
        router.ensure.assert_called_once_with("loraX")

    def test_reload_failure_still_fails_the_node(self):
        """重载也失败 → 节点必须失败，绝不静默回退底座。"""
        router = mock.Mock()
        router.ensure.return_value = (False, "加载失败: 磁盘上没有这个适配器")
        parsed, err, pf, attempts = self._run(
            lambda prompt, **kw: self._err404(), router=router)
        self.assertIsNone(parsed)
        self.assertIn("does not exist", err)
        self.assertEqual(router.ensure.call_count, 1)

    def test_reload_budget_is_one(self):
        """一直 404 也只重载一次，不会变成无限重载。"""
        router = mock.Mock()
        router.ensure.return_value = (True, "已加载")
        parsed, err, pf, attempts = self._run(
            lambda prompt, **kw: self._err404(), router=router)
        self.assertIsNone(parsed)
        self.assertEqual(router.ensure.call_count, 1)
        self.assertEqual(router.invalidate.call_count, 1)

    def test_no_reload_for_unrelated_error(self):
        router = mock.Mock()
        parsed, err, pf, attempts = self._run(
            lambda prompt, **kw: "[调用异常] Read timed out", router=router)
        self.assertIsNone(parsed)
        router.invalidate.assert_not_called()
        router.ensure.assert_not_called()

    def test_base_model_call_never_triggers_lora_reload(self):
        """没指定 lora 时（走底座）404 与 LoRA 无关，不能去重载。"""
        router = mock.Mock()
        self._run(lambda prompt, **kw: self._err404("base"), lora=None, router=router)
        router.invalidate.assert_not_called()
        router.ensure.assert_not_called()

    def test_router_absent_is_safe(self):
        """不传 router（如外部直接调用）时行为与改动前一致，不抛异常。"""
        parsed, err, pf, attempts = self._run(
            lambda prompt, **kw: self._err404(), router=None)
        self.assertIsNone(parsed)
        self.assertIn("does not exist", err)

    def test_broken_router_cannot_mask_real_error(self):
        """恢复动作本身抛异常时，必须退化成"重试"而不是把真实错误换掉。"""
        class BrokenRouter:
            def invalidate(self, name):
                raise RuntimeError("boom")
            def ensure(self, name):
                raise RuntimeError("boom")
        parsed, err, pf, attempts = self._run(
            lambda prompt, **kw: self._err404(), router=BrokenRouter())
        self.assertIsNone(parsed)
        self.assertIn("does not exist", err)


class TestFrontendCssVariables(unittest.TestCase):
    """
    UI 美化第一步：把颜色收敛成 CSS 变量（零视觉变化 —— 变量值就是原来的 hex）。

    为什么值得写测试：**未定义的 CSS 变量不会报错**，只会让那条声明变成无效值而
    静默回退，页面"看起来只是有点怪"，没人会发现。所以"引用了没定义"必须由测试来抓。
    """

    @classmethod
    def setUpClass(cls):
        root = Path(we.__file__).resolve().parent
        cls.html = (root / "webui" / "index3.html").read_text(encoding="utf-8")
        m = re.search(r"<style>(.*?)</style>", cls.html, re.S)
        assert m, "index3.html 里找不到 <style>"
        cls.css = m.group(1)

    def _defined(self):
        # 变量名大小写敏感，别用 [a-z0-9-] 之类的窄字符集 —— 拼错一个字母（尤其大写）
        # 就会从"未定义检查"里溜过去，而那正是这个测试要抓的东西
        return set(re.findall(r"(--[^:;)\s]+)\s*:", self.css))

    def test_root_palette_exists(self):
        self.assertIn(":root{", self.css)
        self.assertGreater(len(self._defined()), 10, "调色板太小，像是没落变量")

    def test_every_css_variable_used_is_defined(self):
        used = set(re.findall(r"var\((--[^),\s]+)\)", self.html))
        self.assertTrue(used, "整个页面没有引用任何 CSS 变量？")
        missing = sorted(used - self._defined())
        self.assertEqual(missing, [],
                         "这些 CSS 变量被引用了但没定义 —— 会静默失效: %s" % missing)

    def test_palette_colors_are_not_reintroduced_as_raw_hex(self):
        """调色板里的颜色不该再以裸 hex 写进样式（否则变量会被慢慢绕过）。"""
        body = re.sub(r":root\{.*?\}", "", self.css, flags=re.S)   # 定义处本来就该是 hex
        defs = {d.lower() for d in
                re.findall(r"--[^:;)\s]+:(#[0-9a-fA-F]{3,6});", self.css)}
        raw = {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,6}\b", body)}
        back = sorted(raw & defs)
        self.assertEqual(back, [], "这些调色板颜色又被写成裸 hex 了: %s" % back)



class TestAggregateGenOpts(unittest.TestCase):
    """
    汇总节点的生成参数过去写死（max_tokens=4096/temperature=0.5），而类型注册表
    **已经声明**了这两个 defaults 却没被读取 —— 契约与实现不一致：节点上改了不生效。
    """

    def _run_capture(self, agg_node):
        captured = []
        tmp = Path(tempfile.mkdtemp(prefix="aggopt_"))
        paper = tmp / "p.txt"
        paper.write_text("论文正文内容，用于测试生成参数覆盖。", encoding="utf-8")
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []}, agg_node]},
            ensure_ascii=False), encoding="utf-8")

        def fake_call(prompt, **kw):
            captured.append(kw)
            return "报告正文"

        with mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "CACHE_ENABLED", False), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouter), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {})), \
             mock.patch.object(we, "call_llm_strict", side_effect=fake_call):
            we.run_workflow(str(paper), str(cfg))
        return captured

    def test_default_matches_declared_contract(self):
        got = self._run_capture({"id": "agg", "type": "aggregate", "input_from": ["in"]})
        self.assertTrue(got)
        self.assertEqual(got[-1].get("max_tokens"), 4096)
        self.assertEqual(got[-1].get("temperature"), 0.5)

    def test_node_can_override_max_tokens(self):
        got = self._run_capture({"id": "agg", "type": "aggregate", "input_from": ["in"],
                                 "max_tokens": 8192})
        self.assertEqual(got[-1].get("max_tokens"), 8192)

    def test_node_can_override_temperature(self):
        got = self._run_capture({"id": "agg", "type": "aggregate", "input_from": ["in"],
                                 "temperature": 0.0})
        self.assertEqual(got[-1].get("temperature"), 0.0)



class TestRetryPolicy(unittest.TestCase):
    """A2：可重试性分类 + 指数退避（实测 422 无脑重试 3 次只是白等）。"""

    def test_status_code_classification(self):
        cases = [
            ("[云 API 错误 429] rate limited", True),
            ("[云 API 错误 503] overloaded", True),
            ("[错误 500] internal", True),
            ("[云 API 错误 422] invalid request: prompt too long", False),
            ("[云 API 错误 400] bad request", False),
            ("[云 API 错误 401] invalid api key", False),
            ("[云 API 错误 403] forbidden", False),
            ("[云 API 错误 404] not found", False),
            ("[云 API 错误 413] payload too large", False),
        ]
        for err, want in cases:
            ok, why = we.classify_retryable(err)
            self.assertEqual(ok, want, f"{err} → {ok} ({why})")
            self.assertTrue(why)

    def test_network_errors_are_retryable(self):
        for err in ("[调用异常] HTTPSConnectionPool Read timed out",
                    "[云 API 调用异常] Connection reset by peer",
                    "[Ollama 调用异常] timeout"):
            ok, why = we.classify_retryable(err)
            self.assertTrue(ok, f"{err} → {why}")

    def test_unknown_is_retryable_but_empty_is_not(self):
        """
        认不出来的错误**保守重试**（不能因为分类器不认识就改变原有的重试行为），
        只有空/非文本错误才直接放弃。
        """
        ok, why = we.classify_retryable("模型返回的内容不是合法 JSON")
        self.assertTrue(ok, why)
        self.assertFalse(we.classify_retryable("")[0])
        self.assertFalse(we.classify_retryable(None)[0])

    def test_backoff_is_exponential_capped_and_jittered(self):
        a0 = we.retry_delay_for(0, 2.0, rnd=lambda: 0.5)   # 无抖动
        a1 = we.retry_delay_for(1, 2.0, rnd=lambda: 0.5)
        a2 = we.retry_delay_for(2, 2.0, rnd=lambda: 0.5)
        self.assertEqual((a0, a1, a2), (2.0, 4.0, 8.0))
        self.assertEqual(we.retry_delay_for(20, 2.0, rnd=lambda: 0.5), we.RETRY_MAX_WAIT)
        # 抖动：rnd=0 → 下浮 30%，rnd=1 → 上浮 30%
        self.assertAlmostEqual(we.retry_delay_for(0, 10.0, rnd=lambda: 0.0), 7.0, places=2)
        self.assertAlmostEqual(we.retry_delay_for(0, 10.0, rnd=lambda: 1.0), 13.0, places=2)
        self.assertEqual(we.retry_delay_for(0, 0.0), 0.0)   # base=0 → 不等待

    def _run_with_error(self, err):
        """跑一个单节点工作流，LLM 始终抛指定错误，看实际尝试次数。"""
        tmp = Path(tempfile.mkdtemp(prefix="retry_"))
        paper = tmp / "p.txt"
        paper.write_text("论文正文内容。", encoding="utf-8")
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"workflow_name": "t", "nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "p", "type": "process", "input_from": ["in"],
             "prompt": "处理：{in}", "retry": 3, "retry_delay": 0.0}]},
            ensure_ascii=False), encoding="utf-8")
        calls = []

        def boom(prompt, **kw):
            calls.append(1)
            raise we.LLMCallError(err)

        with mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "CACHE_ENABLED", False), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouter), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {})), \
             mock.patch.object(we, "call_llm_strict", side_effect=boom):
            out = json.loads(Path(we.run_workflow(str(paper), str(cfg))).read_text(encoding="utf-8"))
        return len(calls), out

    def test_non_retryable_error_gives_up_immediately(self):
        n, out = self._run_with_error("[云 API 错误 422] prompt too long")
        self.assertEqual(n, 1, "422 不该重试")
        self.assertEqual(len(out["_meta"]["failures"]), 1)

    def test_retryable_error_uses_full_budget(self):
        n, out = self._run_with_error("[云 API 错误 429] rate limited")
        self.assertEqual(n, 4, "429 应重试到预算上限（1+3）")
        self.assertEqual(len(out["_meta"]["failures"]), 1)


class TestRunTimeout(unittest.TestCase):
    """A1：运行级超时 —— 中止但**保留**已完成节点的结果，并在报告头说明。"""

    def _run(self, timeout, slow_first=False):
        tmp = Path(tempfile.mkdtemp(prefix="rto_"))
        paper = tmp / "p.txt"
        paper.write_text("论文正文。", encoding="utf-8")
        cfg = tmp / "c.json"
        cfg.write_text(json.dumps({"workflow_name": "t", "run_timeout": timeout, "nodes": [
            {"id": "in", "type": "input", "input_from": []},
            {"id": "p1", "type": "process", "input_from": ["in"], "prompt": "一：{in}",
             "output_format": "text"},
            {"id": "p2", "type": "process", "input_from": ["p1"], "prompt": "二：{in}",
             "output_format": "text"},
            {"id": "agg", "type": "aggregate", "input_from": ["p2"]}]},
            ensure_ascii=False), encoding="utf-8")
        calls = []

        def call(prompt, **kw):
            calls.append(prompt)
            if slow_first and len(calls) == 1:
                time.sleep(0.6)
            return "节点输出"

        with mock.patch.object(we, "OUTPUT_DIR", str(tmp)), \
             mock.patch.object(we, "CACHE_ENABLED", False), \
             mock.patch.object(we, "get_vllm_base_model", return_value="/fake"), \
             mock.patch.object(we, "LoraRouter", FakeRouter), \
             mock.patch.object(we, "preflight_loras", return_value=([], [], {})), \
             mock.patch.object(we, "call_llm_strict", side_effect=call):
            out = json.loads(Path(we.run_workflow(str(paper), str(cfg))).read_text(encoding="utf-8"))
        return out, calls

    def test_timeout_aborts_and_keeps_finished_nodes(self):
        """
        p1 故意睡 0.6s（超过 timeout=0.3s）→ 派发 p2 前的检查触发中止。
        正确的期望是：**p1 的结果被保留，p1 之后的节点根本没跑**，
        且报告头明确写出"因超时中止、内容不完整"（否则读者会把残报告当完整报告）。
        """
        out, calls = self._run(timeout=0.3, slow_first=True)
        self.assertTrue(out["_meta"]["run_timed_out"])
        self.assertEqual(out["_meta"]["abort_kind"], "timeout")
        self.assertIn("超时", out["_meta"]["aborted"])
        self.assertIn("in", out["nodes"])
        self.assertIn("p1", out["nodes"], "已完成节点的结果必须保留")
        self.assertEqual(out["nodes"]["p1"], "节点输出")
        self.assertNotIn("p2", out["nodes"], "超时后 p2 不该被执行")
        self.assertNotIn("agg", out["nodes"])
        self.assertEqual(len(calls), 1, "只有 p1 被调用过")
        self.assertEqual(out["status"], "partial")
        self.assertIn("超时中止", out["final_report"] or "")
        self.assertIn("不是完整分析", out["final_report"] or "")

    def test_no_timeout_when_fast(self):
        out, calls = self._run(timeout=60.0)
        self.assertFalse(out["_meta"]["run_timed_out"])
        self.assertIsNone(out["_meta"]["abort_kind"])
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["_meta"]["run_elapsed"] >= 0, True)

    def test_default_timeout_is_documented(self):
        self.assertEqual(we.RUN_TIMEOUT_DEFAULT, 1800.0)


if __name__ == "__main__":
    unittest.main()
