# -*- coding: utf-8 -*-
"""Bug 修复批次回归测试：多sheet / 数字 / 实体 / tool_call / 治理 / 陈旧 / 评分口径 / lock"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import table_store as ts
import table_qa as tqa
import swarm
import trajectory_distill as td
import chimera_v2 as cv2


def _multi_sheet():
    return {"file": "multi.xlsx", "total_rows": 5, "sheets": [
        {"name": "S1", "headers": ["产品编号", "产品名称", "市场价(元)", "安装培训"],
         "rows": [["A001", "积木", "1000", "线上培训"], ["A002", "拼图", "2000", "－"]]},
        {"name": "S2", "headers": ["产品编号", "产品名称", "市场价(元)", "安装培训"],
         "rows": [["B001", "滑梯", "1.5万", "线下培训"], ["B002", "秋千", "3000-5000", ""],
                  ["B003", "沙池", "待定", "－"]]},
    ]}


class TestMultiSheet(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ms_ut_"))
        self._orig = ts.DB_PATH
        ts.DB_PATH = self.tmp / "ts.db"
        ts.register_table("multi", _multi_sheet())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, ts, "DB_PATH", self._orig)

    def test_register_multi(self):
        meta = ts.get_meta("multi")
        self.assertEqual(meta["n_rows"], 5)
        self.assertEqual(len(meta["sheets"]), 2)
        rows = ts.get_rows("multi")
        self.assertEqual(len(rows), 5)
        self.assertEqual({r["sheet"] for r in rows}, {"S1", "S2"})

    def test_lookup_second_sheet(self):
        r = tqa.try_answer("multi", "B001 的价格")
        self.assertIsNotNone(r)
        self.assertIn("1.5万", r["answer"])
        self.assertIn("sheet=S2", r["answer"])

    def test_max_across_sheets(self):
        r = tqa.try_answer("multi", "市场价最高的产品")
        self.assertIn("15000", r["answer"])
        self.assertIn("sheet=S2", r["answer"])

    def test_count_multi(self):
        r = tqa.try_answer("multi", "一共多少产品")
        self.assertIn("5", r["answer"])

    def test_num_parse_enhanced(self):
        self.assertEqual(ts.num("1.5万"), 15000.0)
        self.assertEqual(ts.num("1000-2000"), 1500.0)
        self.assertEqual(ts.num("¥1,234 元"), 1234.0)
        self.assertIsNone(ts.num("待定"))
        self.assertIsNone(ts.num("－"))

    def test_entities_check(self):
        ok = tqa.check_entities("A001 和 B003 都在表里", "multi")
        self.assertTrue(ok["ok"])
        bad = tqa.check_entities("X999 不在表里", "multi")
        self.assertFalse(bad["ok"])
        self.assertIn("X999", bad["missing"])


class TestToolCallFormat(unittest.TestCase):
    def test_format_tool_call(self):
        raw = '思考完毕。<tool_call>{"name": "list_loras", "arguments": {}}</tool_call>'
        out = swarm._format_tool_calls(raw)
        self.assertIn("list_loras", out)
        self.assertIn("平台未自动执行", out)

    def test_clean_text_unchanged(self):
        self.assertEqual(swarm._format_tool_calls("普通回答"), "普通回答")

    def test_invalid_json_stripped(self):
        out = swarm._format_tool_calls('x<tool_call>not json</tool_call>y')
        self.assertNotIn("<tool_call>", out)


class TestWorkflowGovernance(unittest.TestCase):
    def test_frozen_blocks_workflow(self):
        wf = {"wf_id": "wf1", "path": "/tmp/wf.json", "workflow_name": "x"}
        with mock.patch.object(td, "get_workflow", return_value=wf), \
             mock.patch.object(Path, "exists", return_value=True), \
             mock.patch("governance.is_frozen", return_value=True):
            r = td.run_workflow("wf1", input_text="t")
        self.assertFalse(r["ok"])
        self.assertIn("冻结", r["error"])

    def test_stale_flag(self):
        import tempfile
        import pathlib as _pl
        _tmp = _pl.Path(tempfile.mkdtemp())
        _f = _tmp / "wf_stale.json"
        _f.write_text("{}", encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(_tmp, True))
        rows = [{"wf_id": "wf1", "task": "旧任务", "path": str(_f), "workflow_name": "x",
                 "created_at": "2020-01-01T00:00:00", "steps": 3}]
        with mock.patch.object(td, "list_workflows", return_value=rows), \
             mock.patch("kb._encode", return_value=[[1.0, 0.0]]):
            hits = td.search_workflows("旧任务", limit=1)
        self.assertTrue(hits[0]["stale"])
        self.assertGreater(hits[0]["age_days"], 7)


class TestScoringOrigin(unittest.TestCase):
    def _rec(self, tools):
        return {"task": "任务", "reason": "completed", "final": "结果",
                "tools_used": tools, "tool_errors": [], "steps": [
                    {"turn": 1, "text": "步骤", "tool_calls": []},
                    {"turn": 2, "text": "步骤2", "tool_calls": []},
                    {"turn": 3, "text": "步骤3", "tool_calls": []}]}

    def test_dev_trajectory(self):
        rec = self._rec(["pwsh", "read", "grep"])
        steps = td.decompose(rec)
        s, feat = td.score_record(rec, steps, {}, set())
        self.assertEqual(feat["tool_origin"], "dev")
        self.assertTrue(feat["dev_trajectory"])

    def test_platform_trajectory(self):
        rec = self._rec(["paper_lora_kb_query"])
        steps = td.decompose(rec)
        s, feat = td.score_record(rec, steps, {}, {"paper_lora_kb_query"})
        self.assertEqual(feat["tool_origin"], "platform")
        self.assertFalse(feat["dev_trajectory"])


class TestLockFile(unittest.TestCase):
    def test_lock_written(self):
        tmp = Path(tempfile.mkdtemp(prefix="lock_ut_"))
        self._orig = (cv2.DEPS_DIR, cv2.ADAPTERS_DIR, cv2.INSTALLED_FILE, cv2.AUDIT_FILE)
        cv2.DEPS_DIR = tmp / "deps"
        cv2.ADAPTERS_DIR = tmp / "adapters"
        cv2.INSTALLED_FILE = tmp / "installed.jsonl"
        cv2.AUDIT_FILE = tmp / "audit.jsonl"
        cv2.ADAPTERS_DIR.mkdir(parents=True)
        report = {"install": [{"metadata": {"name": "pkg", "version": "1.0"},
                               "download_info": {"url": "u", "archive_info": {
                                   "hashes": {"sha256": "abc"}}}}]}
        try:
            def fake_run(cmd, **kw):
                ridx = cmd.index("--report")
                Path(cmd[ridx + 1]).write_text(json.dumps(report), encoding="utf-8")
                return mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch("subprocess.run", side_effect=fake_run), \
                 mock.patch.object(cv2, "_smoke", return_value={"ok": True}), \
                 mock.patch.object(cv2, "_dir_mb", return_value=1.0):
                r = cv2.install_with_deps("pkg", approve=True, label="locktest")
            lock = Path(r["requirements_lock"])
            self.assertTrue(lock.exists())
            self.assertIn("pkg==1.0 --hash=sha256:abc", lock.read_text(encoding="utf-8"))
        finally:
            for k, v in zip(("DEPS_DIR", "ADAPTERS_DIR", "INSTALLED_FILE", "AUDIT_FILE"), self._orig):
                setattr(cv2, k, v)
            shutil.rmtree(tmp, True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
