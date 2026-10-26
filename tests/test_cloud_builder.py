# -*- coding: utf-8 -*-
"""S15 测试：云端建工作流 → 沉淀本地 → 本地执行映射"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cloud_workflow_builder as cwb
import workflow_engine as we


GOOD = {
    "workflow_name": "采购清单生成",
    "description": "云端设计，本地执行",
    "nodes": [
        {"id": "n0", "type": "input", "input_from": [], "prompt": "", "position": {"x": 0, "y": 0}},
        {"id": "n1", "type": "process", "input_from": ["n0"],
         "prompt": "根据需求整理采购清单：{n0}", "provider": "vllm",
         "output_format": "text", "max_tokens": 800, "temperature": 0.2,
         "position": {"x": 240, "y": 0}},
        {"id": "n2", "type": "tool", "input_from": ["n1"], "tool": "write_sheet",
         "tool_args": {"rows": [["1", "积木", "大颗粒", "20", "89", "1780", "本地Excel", ""]],
                       "header": ["序号", "商品名称", "规格", "数量", "单价", "小计", "来源平台", "备注"],
                       "name": "采购清单", "sheet": "采购清单"},
         "tool_timeout": 60, "position": {"x": 480, "y": 0}},
        {"id": "n3", "type": "aggregate", "input_from": ["n2"],
         "prompt": "汇总：{n2}", "provider": "vllm", "output_format": "text",
         "max_tokens": 1000, "temperature": 0.2, "position": {"x": 720, "y": 0}},
    ],
    "golden_purchase_list": {
        "columns": ["序号", "商品名称", "规格", "数量", "单价", "小计", "来源平台", "备注"],
        "rows": [["1", "积木", "大颗粒", "20", "89", "1780", "本地Excel", ""]],
        "notes": "云端目标结果"},
}


class TestBuildFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cwb_ut_"))
        self._orig = (cwb.DISTILL_DIR, cwb.WF_DIR, cwb.TARGETS_DIR, cwb.BUILDS_LOG)
        cwb.DISTILL_DIR = self.tmp / "distilled"
        cwb.WF_DIR = cwb.DISTILL_DIR / "workflows"
        cwb.TARGETS_DIR = cwb.DISTILL_DIR / "targets"
        cwb.BUILDS_LOG = self.tmp / "data" / "cloud" / "workflow_builds.jsonl"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for k, v in zip(("DISTILL_DIR", "WF_DIR", "TARGETS_DIR", "BUILDS_LOG"), self._orig):
            self.addCleanup(setattr, cwb, k, v)

    def test_extract_json_fenced(self):
        txt = '```json\n' + json.dumps(GOOD, ensure_ascii=False) + '\n```'
        d = cwb._extract_json(txt)
        self.assertEqual(d["workflow_name"], "采购清单生成")

    def test_extract_json_with_prose(self):
        txt = '这是设计：\n' + json.dumps(GOOD, ensure_ascii=False) + '\n完毕'
        d = cwb._extract_json(txt)
        self.assertIsNotNone(d)

    def test_sanitize_and_lint(self):
        cfg = cwb._sanitize_config(GOOD)
        errs, warns = we.lint_config(cfg)
        self.assertEqual(errs, [], errs)
        self.assertEqual([n["type"] for n in cfg["nodes"]],
                         ["input", "process", "tool", "aggregate"])

    def test_sanitize_rejects_unknown_tool(self):
        bad = json.loads(json.dumps(GOOD))
        bad["nodes"][2]["tool"] = "hack_tool"
        with self.assertRaises(ValueError):
            cwb._sanitize_config(bad)

    def test_build_with_mocked_cloud(self):
        with mock.patch.object(cwb, "local_hit", return_value=(False, {"candidates": []})), \
             mock.patch.object(cwb, "_cloud_ask",
                               return_value=(json.dumps(GOOD, ensure_ascii=False),
                                             {"provider": "glm", "model": "glm-4-flash"})):
            r = cwb.build("做一个自动生成采购清单excel的工作流", execute=False)
        self.assertTrue(r["ok"])
        self.assertTrue(Path(r["workflow_path"]).exists())
        self.assertTrue(Path(r["golden_path"]).exists())
        idx = Path(cwb.DISTILL_DIR / "workflows.jsonl")
        self.assertTrue(idx.exists())
        rec = json.loads(idx.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["wf_id"], r["wf_id"])
        self.assertEqual(rec["source"], "cloud_built")
        self.assertEqual(rec["cloud"]["builder"], "glm")

    def test_build_repair_loop(self):
        bad = json.loads(json.dumps(GOOD))
        bad["nodes"][0]["type"] = "process"   # 首节点非 input → lint 报错
        calls = [("不是JSON", {}), (json.dumps(bad, ensure_ascii=False), {}),
                 (json.dumps(GOOD, ensure_ascii=False), {})]
        with mock.patch.object(cwb, "local_hit", return_value=(False, {})), \
             mock.patch.object(cwb, "_cloud_ask", side_effect=calls):
            r = cwb.build("采购清单工作流", execute=False, max_repair=2)
        self.assertTrue(r["ok"])

    def test_build_fail_raises(self):
        with mock.patch.object(cwb, "local_hit", return_value=(False, {})), \
             mock.patch.object(cwb, "_cloud_ask", return_value=("垃圾输出", {})):
            with self.assertRaises(RuntimeError):
                cwb.build("x", max_repair=1)

    def test_should_cloud_build(self):
        with mock.patch.object(cwb, "local_hit", return_value=(False, {})), \
             mock.patch.dict(os.environ, {"CLOUD_BUILD_AUTO": "1"}):
            need, why, _ = cwb.should_cloud_build("做一个自动生成采购清单excel的本地工作流")
            self.assertTrue(need)
            need2, why2, _ = cwb.should_cloud_build("今天天气怎么样")
            self.assertFalse(need2)

    def test_should_cloud_build_hit(self):
        with mock.patch.object(cwb, "local_hit", return_value=(True, {"hit": {"wf_id": "wf1"}})):
            need, why, _ = cwb.should_cloud_build("做一个工作流")
        self.assertFalse(need)


class TestToolNodeMapping(unittest.TestCase):
    def test_write_sheet_registered(self):
        self.assertIn("write_sheet", we.TOOL_NODE_SUPPORTED)
        self.assertIn("read_sheet", we.TOOL_NODE_SUPPORTED)

    def test_call_write_sheet_mocked(self):
        fake = mock.Mock()
        fake.json.return_value = {"filename": "采购清单.xlsx", "rows": 2, "cols": 8}
        fake.raise_for_status.return_value = None
        with mock.patch("requests.post", return_value=fake) as m:
            out = we.call_platform_tool("write_sheet", {
                "rows": [["1", "积木"]], "header": ["序号", "商品"], "name": "采购清单",
                "sheet": "Sheet1"}, {})
        self.assertIn("采购清单", out)
        body = m.call_args.kwargs["json"]
        self.assertEqual(body["rows"], [["1", "积木"]])
        self.assertIn("/docs/write_sheet", m.call_args.args[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestSearchFallback(unittest.TestCase):
    def test_keyword_fallback_can_hit(self):
        import trajectory_distill as td
        import tempfile, pathlib as _pl
        tmp = _pl.Path(tempfile.mkdtemp())
        wf = tmp / "wf1.json"
        wf.write_text("{}", encoding="utf-8")
        row = {"wf_id": "wf1", "task": "做一个自动生成采购清单excel的本地工作流",
               "path": str(wf), "workflow_name": "x", "created_at": "2026-10-27T00:00:00"}
        with mock.patch.object(td, "list_workflows", return_value=[row]), \
             mock.patch("kb._encode", side_effect=RuntimeError("cold")):
            hits = td.search_workflows("做一个自动生成采购清单excel的本地工作流", limit=1)
        self.assertTrue(hits)
        self.assertGreaterEqual(hits[0]["score"], 0.65)
        self.assertEqual(hits[0]["match"], "keyword")


class TestSearchWithMissingPath(unittest.TestCase):
    def test_missing_path_skipped(self):
        import trajectory_distill as td
        row = {"wf_id": "ghost", "task": "x", "path": "/tmp/definitely_missing_wf.json",
               "workflow_name": "x", "created_at": "2026-10-27T00:00:00"}
        with mock.patch.object(td, "list_workflows", return_value=[row]), \
             mock.patch("kb._encode", return_value=[[1.0, 0.0]]):
            hits = td.search_workflows("x", limit=1)
        self.assertEqual(hits, [])




class TestKBValidation(unittest.TestCase):
    def test_rejects_nonexistent_kb(self):
        bad = json.loads(json.dumps(GOOD))
        bad["nodes"].insert(2, {"id": "n1b", "type": "tool", "input_from": ["n1"],
                                "tool": "paper_lora_kb_query",
                                "tool_args": {"kb_name": "no_such_kb_zzz", "query": "x", "top_k": 3},
                                "tool_timeout": 30, "position": {"x": 300, "y": 0}})
        good = json.loads(json.dumps(GOOD))
        calls = [(json.dumps(bad, ensure_ascii=False), {}),
                 (json.dumps(good, ensure_ascii=False), {})]
        with mock.patch.object(cwb, "local_hit", return_value=(False, {})), \
             mock.patch.object(cwb, "_cloud_ask", side_effect=calls), \
             mock.patch.object(cwb, "_existing_kbs", return_value=["agent_use"]):
            r = cwb.build("采购清单工作流", execute=False, max_repair=1)
        self.assertTrue(r["ok"])
        self.assertNotIn("no_such_kb_zzz",
                         json.dumps(r.get("workflow"), ensure_ascii=False))

    def test_prompt_lists_existing_kbs(self):
        with mock.patch.object(cwb, "_existing_kbs", return_value=["agent_use", "bcpcn"]):
            p = cwb._build_prompt("做采购清单工作流")
        self.assertIn("agent_use", p)
        self.assertIn("bcpcn", p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
