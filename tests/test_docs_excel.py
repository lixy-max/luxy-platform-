# -*- coding: utf-8 -*-
"""
文档助手 P1（Excel 只读）的测试。

为什么测纯函数而不是接口：核心逻辑（列表/概览/筛选/汇总）都抽成了纯函数，
接口只是薄包装 —— 直接测逻辑更快也更稳。
另外必须测**路径安全**：文档名只能落在 uploads/ 内。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _guards  # noqa: F401
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import lora_manager as lm  # noqa: E402


def make_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "销售"
    ws.append(["部门", "地区", "金额", "数量"])
    for d, r, a, n in (("研发", "北京", 120, 3), ("研发", "上海", 80, 2),
                       ("市场", "北京", 200, 5), ("市场", "上海", 50, 1),
                       ("运营", "北京", 60, 4)):
        ws.append([d, r, a, n])
    ws2 = wb.create_sheet("说明")
    ws2.append(["这是第二个 sheet"])
    wb.save(path)


class TestDocsHelpers(unittest.TestCase):

    def setUp(self):
        try:
            import openpyxl  # noqa
        except Exception as e:
            self.skipTest(f"openpyxl 未安装: {e}")
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "t.xlsx"
        make_xlsx(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_list_sheets(self):
        d = lm.list_sheets_of(self.path)
        self.assertEqual([s["name"] for s in d["sheets"]], ["销售", "说明"])

    def test_read_sheet_summary(self):
        d = lm.read_sheet_summary(self.path, None, 2)
        self.assertEqual(d["header"], ["部门", "地区", "金额", "数量"])
        self.assertEqual(d["rows"][0][0], "研发")
        self.assertEqual(len(d["rows"]), 2, "rows 参数应限制返回行数")

    def test_filter_rows_equals(self):
        d = lm.filter_rows_of(self.path, None, "部门", "==", "研发", 10)
        self.assertEqual(d["matched"], 2)
        self.assertTrue(all(r[0] == "研发" for r in d["rows"]))

    def test_filter_rows_numeric_and_contains(self):
        gt = lm.filter_rows_of(self.path, None, "金额", ">", "100", 10)
        self.assertEqual(gt["matched"], 2, "120 与 200 应命中")
        ct = lm.filter_rows_of(self.path, None, "地区", "contains", "北京", 10)
        self.assertEqual(ct["matched"], 3)

    def test_filter_unknown_column_is_reported(self):
        d = lm.filter_rows_of(self.path, None, "不存在的列", "==", "x", 10)
        self.assertEqual(d["matched"], 0)
        self.assertIn("找不到列", d.get("error", ""))

    def test_group_sum(self):
        d = lm.group_sum_of(self.path, None, "部门", "金额")
        got = {g["group"]: g["sum"] for g in d["groups"]}
        self.assertEqual(got.get("研发"), 200)
        self.assertEqual(got.get("市场"), 250)
        self.assertEqual(got.get("运营"), 60)
        # 应按汇总值降序：市场(250) > 研发(200) > 运营(60)
        self.assertEqual(d["groups"][0]["group"], "市场")

    def test_group_sum_bad_column(self):
        with self.assertRaises(Exception):
            lm.group_sum_of(self.path, None, "部门", "不存在的列")


class TestDocsPathSafety(unittest.TestCase):
    """文档名必须落在 uploads/ 内 —— 否则就是任意文件读取漏洞。"""

    def test_rejects_traversal(self):
        for bad in ("../etc/passwd", "a/../b.xlsx", "sub/dir.xlsx", "..\\x.xlsx"):
            with self.assertRaises(Exception):
                lm.docs_resolve(bad)

    def test_rejects_non_excel(self):
        for bad in ("a.txt", "a.pdf", "a.docx", "a"):
            with self.assertRaises(Exception):
                lm.docs_resolve(bad)

    def test_rejects_empty(self):
        with self.assertRaises(Exception):
            lm.docs_resolve("")


if __name__ == "__main__":
    unittest.main()
