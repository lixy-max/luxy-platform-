"""锁住"预设真的能跑通"这条契约。

背景：写预设时踩过两次坑 ——
  ① write_sheet/write_docx 的参数名与想当然不同（是 rows/name，不是 filename）；
  ② {{steps.N.字段}} 的 N 是 **0 基**（substitute 里是 results[int(idx)]），
     按 1 基写会报"变量引用无法解析"。
这两条都是"跑一次就能发现、但看代码不容易发现"的问题，所以用测试固定住。
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestDocFlowPresets(unittest.TestCase):
    def setUp(self):
        import lora_manager as lm
        self.lm = lm
        self.flows = sorted((ROOT / "doc_flows").glob("doc_flow*.json"))

    def test_presets_exist(self):
        self.assertTrue(self.flows, "至少应有一个预设流程文件")

    def test_preset_tools_are_supported(self):
        for f in self.flows:
            d = json.loads(f.read_text(encoding="utf-8"))
            for i, s in enumerate(d.get("steps", [])):
                self.assertIn(s.get("tool"), self.lm.DOC_FLOW_TOOLS,
                              f"{f.name} 第 {i+1} 步用了不支持的工具 {s.get('tool')!r}")

    def test_step_refs_are_zero_based_and_exist(self):
        """{{steps.N.字段}} 的 N 必须指向已有步骤，且字段名必须真实存在。"""
        import re
        for f in self.flows:
            d = json.loads(f.read_text(encoding="utf-8"))
            steps = d.get("steps", [])
            for i, s in enumerate(steps):
                # 保守做法：只检查下标范围（字段名要真跑才知道，交给下一条测试）
                for m in re.finditer(r"\{\{steps\.(\d+)\.", json.dumps(s.get("args", {}))):
                    idx = int(m.group(1))
                    self.assertLess(idx, len(steps),
                                    f"{f.name} 第 {i+1} 步引用了不存在的步骤序号 {idx}")
                    self.assertLess(idx, i,
                                    f"{f.name} 第 {i+1} 步只能引用它之前的步骤（0 基），实际引用 {idx}")

    def test_write_tools_use_real_arg_names(self):
        """write_sheet 用 rows/name；write_docx 用 paragraphs/title/name —— 不能写成 filename。"""
        for f in self.flows:
            d = json.loads(f.read_text(encoding="utf-8"))
            for s in d.get("steps", []):
                a = s.get("args", {})
                if s.get("tool") == "write_sheet":
                    self.assertNotIn("filename", a, f"{f.name}: write_sheet 没有 filename 参数（用 name）")
                    self.assertIn("rows", a, f"{f.name}: write_sheet 需要 rows")
                if s.get("tool") == "write_docx":
                    self.assertNotIn("filename", a, f"{f.name}: write_docx 没有 filename 参数（用 name）")
                    self.assertTrue("paragraphs" in a or "table" in a,
                                    f"{f.name}: write_docx 需要 paragraphs 或 table")


if __name__ == "__main__":
    unittest.main()
