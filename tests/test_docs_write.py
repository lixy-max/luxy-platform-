# -*- coding: utf-8 -*-
"""
文档助手 P2 的测试。核心是**安全约束**：写操作只产生新文件，绝不覆盖已有文件。
"""
import json as json_lib
import sys
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


class TestDocsOutPath(unittest.TestCase):
    """docs_out_path 必须在 uploads/ 内、且同名时自动加序号 —— 绝不覆盖。"""

    def test_inside_uploads(self):
        p = lm.docs_out_path("x", ".xlsx")
        self.assertEqual(p.parent.resolve(), lm.UPLOAD_DIR.resolve())

    def test_sanitizes_bad_chars(self):
        p = lm.docs_out_path("a/b\\c:d*e?f", ".xlsx")
        self.assertEqual(p.parent.resolve(), lm.UPLOAD_DIR.resolve())
        self.assertNotIn("/", p.name)
        self.assertNotIn("\\", p.name)
        self.assertTrue(p.name.endswith(".xlsx"))

    def test_never_overwrites(self):
        """已存在同名文件时，必须换一个名字，而不是覆盖。"""
        first = lm.docs_out_path("collision_test", ".xlsx")
        first.write_text("ORIGINAL", encoding="utf-8")
        try:
            second = lm.docs_out_path("collision_test", ".xlsx")
            self.assertNotEqual(first, second, "同名时必须换名字（绝不覆盖）")
            self.assertTrue(second.name.startswith("collision_test"))
            self.assertEqual(first.read_text(encoding="utf-8"), "ORIGINAL",
                             "原文件内容必须原封不动")
        finally:
            first.unlink(missing_ok=True)
            if 'second' in dir():
                second.unlink(missing_ok=True)


class TestWriteSheet(unittest.TestCase):

    def test_creates_new_file(self):
        try:
            import openpyxl  # noqa
        except Exception as e:
            self.skipTest(f"openpyxl 未安装: {e}")
        d = lm.write_sheet_new([["研发", 200], ["市场", 250]], sheet_name="汇总",
                               name="p2_unit_sheet", header=["部门", "金额"])
        out = lm.UPLOAD_DIR / d["filename"]
        try:
            self.assertTrue(out.exists())
            self.assertEqual(d["rows"], 3, "1 行表头 + 2 行数据")
            wb = openpyxl.load_workbook(str(out))
            ws = wb.active
            self.assertEqual(ws.title, "汇总")
            self.assertEqual([c.value for c in ws[1]], ["部门", "金额"])
            self.assertEqual(ws["B3"].value, 250)
            wb.close()
        finally:
            out.unlink(missing_ok=True)

    def test_rejects_empty(self):
        with self.assertRaises(Exception):
            lm.write_sheet_new([], header=None)

    def test_accepts_json_string_rows(self):
        """★ P-49 回归: agent 按 schema 会把 rows 传成 JSON 字符串 ✗ 原来直接写空表 ✗"""
        try:
            import openpyxl  # noqa
        except Exception as e:
            self.skipTest(f"openpyxl 未安装: {e}")
        rows = json_lib.dumps([["研发", 200], ["市场", 250]], ensure_ascii=False)
        d = lm.write_sheet_new(rows, sheet_name="字符串行", name="p2_unit_sheet_rowsstr",
                               header=["部门", "金额"])
        out = lm.UPLOAD_DIR / d["filename"]
        try:
            self.assertEqual(d["rows"], 3, "JSON 字符串 rows 应被解析成 1 行表头 + 2 行数据")
        finally:
            out.unlink(missing_ok=True)


class TestWriteDocx(unittest.TestCase):

    def test_roundtrip_read(self):
        try:
            import docx  # noqa
        except Exception as e:
            self.skipTest(f"python-docx 未安装: {e}")
        d = lm.write_docx_new(title="汇报", paragraphs=["第一段内容够长以便读出。", "第二段。"],
                              table=[["部门", "金额"], ["研发", "200"]], name="p2_unit_docx")
        out = lm.UPLOAD_DIR / d["filename"]
        try:
            self.assertTrue(out.exists())
            got = lm.read_docx_text(out.name)
            self.assertIn("第一段内容", got["text"])
            self.assertIn("研发", got["text"], "表格内容也该读出来")
        finally:
            out.unlink(missing_ok=True)

    def test_accepts_json_string_table(self):
        """★ P-49 回归: agent 把 table 传成 JSON 字符串时，原来【静默丢表】却返回成功 ✗
        （实测 practical-2 的 Q5 追踪表 30 行整张丢失）"""
        try:
            import docx  # noqa
        except Exception as e:
            self.skipTest(f"python-docx 未安装: {e}")
        tbl = [["Iteration", "Divisor", "Sum"], ["1", "1", "1"], ["2", "2", "3"]]
        d = lm.write_docx_new(title="表格字符串测试",
                              paragraphs=["正文够长以便读出。"],
                              table=json_lib.dumps(tbl, ensure_ascii=False),
                              name="p2_unit_docx_tblstr")
        out = lm.UPLOAD_DIR / d["filename"]
        try:
            self.assertEqual(d.get("table_rows"), len(tbl),
                             "JSON 字符串表格应被解析成真 Word 表格，而不是静默丢弃")
            got = lm.read_docx_text(out.name)
            self.assertIn("Iteration", got["text"], "表头应写进 Word")
            self.assertIn("Sum", got["text"], "表头第二列也应写进 Word")
        finally:
            out.unlink(missing_ok=True)

    def test_read_docx_rejects_traversal(self):
        with self.assertRaises(Exception):
            lm.docs_resolve_doc("../etc/passwd", lm.WORD_EXTS)

    def test_read_docx_rejects_wrong_ext(self):
        with self.assertRaises(Exception):
            lm.docs_resolve_doc("a.xlsx", lm.WORD_EXTS)


if __name__ == "__main__":
    unittest.main()
