# -*- coding: utf-8 -*-
"""
工具目录生成器的测试。

为什么需要：生成器曾用固定 2500 字符窗口取工具块，导致 read_docx 沾到
write_sheet 的 dangerous:true（危险标记错误），这类错误只在生成物里可见、
不会让任何测试变红，所以必须显式断言生成结果。
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "gen_agent_kb", ROOT / "scripts" / "gen_agent_kb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestToolGen(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = load_mod()
        cls.tools = cls.mod.extract()

    def test_all_tools_found(self):
        self.assertGreaterEqual(len(self.tools), 40, "工具数明显偏少，抽取可能坏了")

    def test_dangerous_set_is_exactly_expected(self):
        danger = {n for n, v in self.tools.items() if v.get("dangerous")}
        expect = {"delete_workflow_node", "load_lora", "run_doc_flow", "save_doc_flow",
              "start_training", "unload_lora", "write_docx", "write_sheet",
              "upload_dataset", "clean_dataset", "rebuild_knowledge_base"}
        self.assertEqual(danger, expect,
                         f"危险标记不对。多出 {sorted(danger - expect)}，"
                         f"少了 {sorted(expect - danger)}")

    def test_readonly_tools_not_marked_dangerous(self):
        for n in ("read_docx", "read_sheet", "list_loras", "group_sum",
                  "filter_rows", "get_quality_summary"):
            self.assertFalse(self.tools.get(n, {}).get("dangerous"),
                             f"{n} 是只读工具，不应标成危险")

    def test_http_tools_have_endpoint_and_method(self):
        for n, v in self.tools.items():
            if n == "switch_model":
                continue          # 纯前端逻辑（操作 DOM 选择器），没有后端路由
            if v.get("endpoint"):
                self.assertIn(v.get("method"), ("GET", "POST", "PUT", "DELETE"),
                              f"{n} 的 method 异常: {v.get('method')}")

    def test_write_tools_are_post(self):
        for n in ("write_sheet", "write_docx", "start_training", "group_sum"):
            self.assertEqual(self.tools[n]["method"], "POST", f"{n} 应该是 POST")


if __name__ == "__main__":
    unittest.main()
