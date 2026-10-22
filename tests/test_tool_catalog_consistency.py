#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P-10: 工具口径一致性测试（防漂移 ✓ 不手写数字 ✗）

三处真值:
  ① `webui/index3.html` 的 `AGENT_TOOLS.<name> = {`  ← **唯一可执行工具集**（权威 ✓）
  ② `agent_kb/10_tool_catalog.md`                      ← 给模型看的目录（由 ① 生成 ✓）
  ③ `agent_kb/_tools.json`                             ← 机器可读（由 ① 生成 ✓）
另有 `AGENT_TOOL_DOCS`（schema 描述 ✓）与后端 `/docs/tool/list`（文档类工具 ✓）：
它们与 ① 天然不是同一集合 ✓（实测差集 46 ✓ 多为后端文本处理类工具）——
所以本测试只【严格校验 ②③ == ①】✓，另两处只做记录（写进 reports ✓ 供人工判断 ✓）
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / 'webui' / 'index3.html'
MD = ROOT / 'agent_kb' / '10_tool_catalog.md'
JS = ROOT / 'agent_kb' / '_tools.json'
REPORT = ROOT / 'training_output' / 'tool_catalog_diff.json'


def code_tools():
    """从 index3.html 抽真实可执行工具名（AGENT_TOOLS.<name> = { ✓ 唯一权威 ✓）"""
    t = HTML.read_text(encoding='utf-8')
    return sorted(set(re.findall(r'AGENT_TOOLS\.([a-z_][a-z0-9_]*)\s*=\s*\{', t)))


def docs_tools():
    """从 index3.html 抽 AGENT_TOOL_DOCS 的键（schema 描述 ✓）"""
    t = HTML.read_text(encoding='utf-8')
    m = re.search(r'AGENT_TOOL_DOCS\s*=\s*\{(.*?)\n\s*\};', t, re.S)
    if not m:
        return []
    return sorted(set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s*:", m.group(1), re.M)))


def md_tools():
    """从目录 md 抽工具名 —— 真实格式: `| `name` | 危险 | 参数 |` ✓"""
    t = MD.read_text(encoding='utf-8')
    return sorted(set(re.findall(r'^\|\s*`([a-z_][a-z0-9_]*)`\s*\|', t, re.M)))


def json_tools():
    d = json.loads(JS.read_text(encoding='utf-8'))
    if isinstance(d, list):
        return sorted(x.get('name') if isinstance(x, dict) else str(x) for x in d)
    if isinstance(d, dict) and 'tools' in d and isinstance(d['tools'], list):
        return sorted(x.get('name') if isinstance(x, dict) else str(x) for x in d['tools'])
    return sorted(d.keys())


class TestToolCatalogConsistency(unittest.TestCase):
    def test_catalog_md_matches_code(self):
        """★ 目录 md 必须与代码里的 AGENT_TOOLS 完全一致（防"文档过期"✗）"""
        code, md = set(code_tools()), set(md_tools())
        self.assertTrue(code, '没从 index3.html 抽到工具（正则或代码结构变了 ✗）')
        self.assertTrue(md, '没从目录 md 抽到工具（格式变了 ✗ 跑 scripts/gen_agent_kb.py 重生成 ✓）')
        self.assertEqual(sorted(code - md), [],
                         f'代码有、目录缺: {sorted(code - md)} ⇒ 跑 scripts/gen_agent_kb.py ✓')
        self.assertEqual(sorted(md - code), [], f'目录有、代码缺（幽灵工具 ✗）: {sorted(md - code)}')

    def test_tools_json_matches_code(self):
        code, js = set(code_tools()), set(json_tools())
        self.assertEqual(sorted(code - js), [], f'_tools.json 缺: {sorted(code - js)}')
        self.assertEqual(sorted(js - code), [], f'_tools.json 多: {sorted(js - code)}')

    def test_docs_diff_is_recorded(self):
        """AGENT_TOOL_DOCS 与 AGENT_TOOLS 的差集【允许存在】✓ 但要落盘记录（供人工判断 ✓）"""
        code, docs = set(code_tools()), set(docs_tools())
        self.assertTrue(code and docs, '两边都必须非空（否则正则过期 ✗）')
        diff = {
            'code_count': len(code), 'docs_count': len(docs),
            'data_only': sorted(code - docs),      # 有实现、DOCS 未写（schema 生成器兜底 ✓）
            'docs_only': sorted(docs - code),      # DOCS 有、无实现体（别名/后端工具 ✓）
            'generated_by': 'tests/test_tool_catalog_consistency.py',
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(diff, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # 只断言"差集不是 100% 错位"（防止两边抽的是完全不同的东西 ✗）
        self.assertGreater(len(code & docs), 3,
                           f'两集合交集过小 ⇒ 结构可能错位: code∩docs={sorted(code & docs)[:8]}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
