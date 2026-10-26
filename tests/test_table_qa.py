# -*- coding: utf-8 -*-
"""S3 表格答案模式测试：结构化存储 / 规则问答 / 数字校验 / 串位修复"""
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

XLS = '/home/luxy/paper_lora_project/data/uploads/pilot_qiaojiao_toys.xls'


def _fake_parsed():
    headers = ["NO", "产品编号", "产品名称", "市场价(元)", "安装培训"]
    rows = [
        ["1", "BFB899a", "菲行动儿童运动跑酷（86件）", "45000", "线上培训"],
        ["2", "BFJ201", "户外体能训练组合", "－", "－"],
        ["3", "BFC101", "蒙台梭利教具", "10000", "线上培训"],
        ["4", "BFC102", "蒙台梭利教具108件", "13000", ""],
        ["5", "BQC001", "波菲运动魔方（松木）", "70000", "线下入园培训"],
        ["6", "BQC002", "波菲运动魔方防腐版(58件)", "65000", "线上培训"],
    ]
    return {"file": "fake.xls", "total_rows": len(rows),
            "sheets": [{"name": "【装备板块】", "headers": headers, "rows": rows}]}


class TestTableStoreAndQA(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tqa_ut_"))
        self._orig = ts.DB_PATH
        ts.DB_PATH = self.tmp / "table_store.db"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, ts, "DB_PATH", self._orig)
        ts.register_table("test_kb", _fake_parsed())

    def test_store(self):
        self.assertTrue(ts.has_table("test_kb"))
        meta = ts.get_meta("test_kb")
        self.assertEqual(meta["n_rows"], 6)
        rows = ts.get_rows("test_kb")
        self.assertEqual(len(rows), 6)
        self.assertEqual(ts.num("¥4,500 元"), 4500.0)

    def test_lookup_by_code(self):
        r = tqa.try_answer("test_kb", "BFB899a 的价格是多少？")
        self.assertIsNotNone(r)
        self.assertEqual(r["mode"], "structured")
        self.assertIn("45000", r["answer"])
        self.assertIn("数据第 1 行", r["answer"])

    def test_lookup_no_crosstalk(self):
        # 关键回归：BFJ201 价格是"－"，不能把 BFC101 的 10000 串给它
        r = tqa.try_answer("test_kb", "BFJ201 的价格")
        self.assertIn("BFJ201", r["answer"])
        self.assertNotIn("10000", r["answer"])

    def test_max(self):
        r = tqa.try_answer("test_kb", "市场价最高的产品是什么")
        self.assertIn("70000", r["answer"])
        self.assertIn("波菲运动魔方（松木）", r["answer"])

    def test_topn(self):
        r = tqa.try_answer("test_kb", "市场价最高的 3 个产品")
        self.assertIn("70000", r["answer"])
        self.assertIn("65000", r["answer"])
        self.assertIn("45000", r["answer"])

    def test_min(self):
        r = tqa.try_answer("test_kb", "最便宜的产品")
        self.assertIn("10000", r["answer"])

    def test_count(self):
        r = tqa.try_answer("test_kb", "一共有多少个产品")
        self.assertIn("6", r["answer"])

    def test_filter_training(self):
        r = tqa.try_answer("test_kb", "哪些产品包含安装培训")
        self.assertIn("安装培训", r["answer"])
        self.assertIn("BFB899a", r["answer"])
        self.assertNotIn("BFC102", r["answer"])  # 空值不算包含

    def test_keyword_search(self):
        r = tqa.try_answer("test_kb", "跑酷")
        self.assertIn("BFB899a", r["answer"])

    def test_unmatched_returns_none(self):
        self.assertIsNone(tqa.try_answer("test_kb", "帮我写一段周报"))

    def test_non_table_kb_returns_none(self):
        self.assertIsNone(tqa.try_answer("no_such_kb", "BFB899a 价格"))

    def test_check_numbers_ok(self):
        r = tqa.check_numbers("BFB899a 价格 45000 元", "test_kb")
        self.assertTrue(r["ok"])

    def test_check_numbers_catch_wrong(self):
        r = tqa.check_numbers("BFB899a 价格 12345 元", "test_kb")
        self.assertFalse(r["ok"])
        self.assertIn(12345, [round(x) for x in r["missing"]])

    def test_search_rows_text(self):
        text = tqa.search_rows_text("test_kb", "跑酷", limit=3)
        self.assertIn("数据第", text)
        self.assertIn("BFB899a", text)


class TestRealExcelRegistration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tqa_real_"))
        self._orig = ts.DB_PATH
        ts.DB_PATH = self.tmp / "table_store.db"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, ts, "DB_PATH", self._orig)
        import table_kb
        parsed = table_kb.parse_table(XLS)
        ts.register_table("qiaojiao_real", parsed)
        self.parsed = parsed

    def test_real_table_qa(self):
        self.assertEqual(len(ts.get_rows("qiaojiao_real")), 88)
        r = tqa.try_answer("qiaojiao_real", "BFB899a 的价格")
        self.assertIsNotNone(r)
        self.assertIn("45000", r["answer"])
        r2 = tqa.try_answer("qiaojiao_real", "市场价最高的 3 个产品")
        self.assertIn("75000", r2["answer"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
