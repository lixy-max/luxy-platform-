# -*- coding: utf-8 -*-
"""
文档流程（P3）的测试：变量替换、顺序执行、出错即停、流程名安全。

为什么重点测替换与"出错即停"：
- 替换错了 → 流程"跑成功但结果是错的"（最难发现的一类问题）
- 出错不停 → 会拿着一堆空值继续往下写文件
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


class TestSubstitute(unittest.TestCase):

    def test_inputs_file(self):
        got = lm.doc_flow_substitute("{{file}}", {"file": "a.xlsx"}, [])
        self.assertEqual(got, "a.xlsx")
        self.assertEqual(lm.doc_flow_substitute("{{ filename }}", {"filename": "b.xlsx"}, []), "b.xlsx")

    def test_steps_reference(self):
        results = [{"sheets": [{"name": "销售"}]}, {"groups": [{"group": "研发", "sum": 200}]}]
        self.assertEqual(lm.doc_flow_substitute("{{steps.1.groups}}", {}, results),
                         [{"group": "研发", "sum": 200}])
        self.assertEqual(lm.doc_flow_substitute("{{steps.0.sheets.0.name}}", {}, results), "销售")

    def test_nested_structures(self):
        results = [{}]
        args = {"a": ["{{file}}", 3], "b": {"c": "{{file}}"}}
        got = lm.doc_flow_substitute(args, {"file": "x.xlsx"}, results)
        self.assertEqual(got, {"a": ["x.xlsx", 3], "b": {"c": "x.xlsx"}})

    def test_unknown_reference_raises(self):
        """未知引用必须报错，不能静默留空。"""
        for bad in ("{{nope}}", "{{steps.9.groups}}", "{{steps.0.missing}}"):
            with self.assertRaises(Exception):
                lm.doc_flow_substitute(bad, {"file": "a"}, [{}])

    def test_plain_string_untouched(self):
        self.assertEqual(lm.doc_flow_substitute("部门", {}, []), "部门")


class TestFlowRun(unittest.TestCase):

    def setUp(self):
        try:
            import openpyxl  # noqa
        except Exception as e:
            self.skipTest(f"openpyxl 未安装: {e}")
        # 注意：文档工具**只在 uploads/ 内解析**（这是刻意的安全约束），
        # 所以测试表也必须建在 uploads/ 里 —— 建在临时目录会得到"文件不存在"。
        import uuid
        import openpyxl
        self.name = f"_unit_flow_{uuid.uuid4().hex[:8]}.xlsx"
        self.path = lm.UPLOAD_DIR / self.name
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "销售"
        ws.append(["部门", "金额"])
        for d, a in (("研发", 120), ("研发", 80), ("市场", 250), ("运营", 60)):
            ws.append([d, a])
        wb.save(str(self.path))

    def tearDown(self):
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass

    def _flow(self, steps):
        return {"name": "t", "steps": steps}

    def test_run_read_then_group(self):
        flow = self._flow([
            {"tool": "read_sheet", "args": {"filename": "{{file}}", "rows": 2}},
            {"tool": "group_sum", "args": {"filename": "{{file}}",
                                           "group_by": "部门", "sum_column": "金额"}},
        ])
        out = lm.doc_flow_run(flow, {"file": self.name})
        self.assertEqual(out["steps"], 2)
        self.assertTrue(all(t["ok"] for t in out["trace"]))
        groups = {g["group"]: g["sum"] for g in out["results"][1]["groups"]}
        self.assertEqual(groups, {"研发": 200.0, "市场": 250.0, "运营": 60.0})
        self.assertTrue(all("ms" in t for t in out["trace"]), "每步都要有时耗")

    def test_stops_on_first_error(self):
        """第 2 步引用不存在的列 → 必须整体失败，且不产生第 3 步的结果。"""
        flow = self._flow([
            {"tool": "read_sheet", "args": {"filename": "{{file}}"}},
            {"tool": "group_sum", "args": {"filename": "{{file}}",
                                           "group_by": "不存在的列", "sum_column": "金额"}},
            {"tool": "write_sheet", "args": {"rows": [["x"]], "name": "不该被写到"}},
        ])
        with self.assertRaises(Exception) as cm:
            lm.doc_flow_run(flow, {"file": self.name})
        self.assertIn("第 2 步", str(cm.exception))
        self.assertFalse((lm.UPLOAD_DIR / "不该被写到.xlsx").exists(),
                         "出错后不该继续执行后续步骤")

    def test_unknown_tool_rejected(self):
        with self.assertRaises(Exception):
            lm.doc_flow_run(self._flow([{"tool": "rm_rf", "args": {}}]), {})

    def test_empty_steps_rejected(self):
        with self.assertRaises(Exception):
            lm.doc_flow_run({"steps": []}, {})

    def test_write_step_creates_new_file(self):
        flow = self._flow([
            {"tool": "group_sum", "args": {"filename": "{{file}}",
                                           "group_by": "部门", "sum_column": "金额"}},
            {"tool": "write_sheet", "args": {"name": "p3_unit_flow",
                                             "header": ["部门", "汇总金额"],
                                             "rows": "{{steps.0.groups}}"}},
        ])
        out = lm.doc_flow_run(flow, {"file": self.name})
        fn = out["results"][1]["filename"]
        out_path = lm.UPLOAD_DIR / fn
        try:
            self.assertTrue(out_path.exists())
            self.assertIn("p3_unit_flow", fn)
        finally:
            out_path.unlink(missing_ok=True)


class TestFlowNameSafety(unittest.TestCase):

    def test_rejects_traversal(self):
        for bad in ("../x.json", "a/b.json", "..\\x.json"):
            with self.assertRaises(Exception):
                lm._flow_path(bad)

    def test_rejects_wrong_namespace(self):
        with self.assertRaises(Exception):
            lm._flow_path("evil.json")

    def test_accepts_doc_flow_prefix(self):
        p = lm._flow_path("doc_flow_demo.json")
        self.assertEqual(p.parent.resolve(), lm.DOC_FLOW_DIR.resolve())


if __name__ == "__main__":
    unittest.main()
