#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具一致性检查（P-85 · 2026-10-19）

检查 5 处一致性（★ 防止「KB 写了没暴露」这类 bug 再发生 ✗）:
  ① agent_kb/_tools.json        ← 单一真源（路由 ✓ args ✓）
  ② agent_kb/_tool_docs.json    ← 工具文档（描述/参数/示例/错误 ✓）
  ③ agent_kb/tool_docs.md       ← KB 内容（供检索 ✓）
  ④ webui/index3.html AGENT_TOOL_DOCS      ← 前端 schema 描述 ✓
  ⑤ webui/index3.html AGENT_TOOL_ENDPOINTS ← 通用分发器 ✓
  + 本地 handler（AGENT_TOOLS.xxx = {...} ✓）

判定规则:
  · 每个 _tools.json 工具 ⇒ 必须有 docs ✓ 必须在前端 DOCS ✓
  · 每个前端 DOCS 工具 ⇒ 必须有 endpoint 或本地 handler ✓（否则模型调了也失败 ✗）
  · **KB(tool_docs.md) 里出现的工具 ⇒ 必须是暴露的** ✗✗（这条就是 P-84 的根因 ✓）
  · 未暴露的工具绝不能写进 KB ✗（否则模型从 KB 学到 → 去调 → 未知 ✗）

用法:
  python scripts/check_tools.py            # 检查
  python scripts/check_tools.py --fix-kb   # 自动从 KB 删除未暴露的工具
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent.parent


def load():
    tools = json.loads((ROOT / 'agent_kb/_tools.json').read_text(encoding='utf-8'))
    docs = json.loads((ROOT / 'agent_kb/_tool_docs.json').read_text(encoding='utf-8')).get('tools', {})
    html = (ROOT / 'webui/index3.html').read_text(encoding='utf-8')

    # 前端 AGENT_TOOL_DOCS
    m = re.search(r'const AGENT_TOOL_DOCS\s*=\s*\{(.*?)\n    \};', html, re.S)
    fe_docs = set(re.findall(r'^\s{8}([a-z_][a-z0-9_]*):\s*\[', m.group(1) if m else '', re.M))

    # 前端 AGENT_TOOL_ENDPOINTS
    m2 = re.search(r'const AGENT_TOOL_ENDPOINTS\s*=\s*\{(.*?)\n    \};', html, re.S)
    fe_eps = set(re.findall(r'^\s{8}([a-z_][a-z0-9_]*):\s*\[', m2.group(1) if m2 else '', re.M))

    # 本地 handler（AGENT_TOOLS.xxx = {）
    handlers = set(re.findall(r'AGENT_TOOLS\.([a-z_][a-z0-9_]*)\s*=\s*\{', html))

    # KB 内容（tool_docs.md 的 ## 标题）
    kb = set()
    kb_path = ROOT / 'agent_kb/tool_docs.md'
    if kb_path.exists():
        kb = set(re.findall(r'^## ([a-z_][a-z0-9_]*)$', kb_path.read_text(encoding='utf-8'), re.M))

    # 文档工具（走 /docs/tool 通用分发 ✓）也算可执行 ✓
    doc_names = set()
    try:
        import urllib.request as _u
        with _u.urlopen('http://127.0.0.1:8001/docs/tool/list', timeout=10) as _r:
            doc_names = {t['name'] for t in json.loads(_r.read()).get('tools', [])}
    except Exception:
        pass
    handlers = handlers | doc_names
    return tools, docs, fe_docs, fe_eps, handlers, kb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fix-kb', action='store_true', help='自动从 KB 删除未暴露的工具 ✗')
    args = ap.parse_args()

    tools, docs, fe_docs, fe_eps, handlers, kb = load()
    problems = []

    print('=' * 70)
    print('  工具一致性检查（P-85 规范）')
    print('=' * 70)
    print('  _tools.json（真源）        : %d' % len(tools))
    print('  _tool_docs.json（文档）    : %d' % len(docs))
    print('  前端 AGENT_TOOL_DOCS      : %d' % len(fe_docs))
    print('  前端 AGENT_TOOL_ENDPOINTS : %d' % len(fe_eps))
    print('  本地 handler              : %d' % len(handlers))
    print('  KB tool_docs.md           : %d' % len(kb))
    print('')

    # ① 每个真源工具必须有文档 ✓
    miss_docs = sorted(set(tools) - set(docs))
    if miss_docs:
        problems.append(('缺少工具文档', len(miss_docs), miss_docs[:8]))
    # ② 每个真源工具必须在前端 DOCS ✓
    miss_fe = sorted(set(tools) - fe_docs)
    if miss_fe:
        problems.append(('前端 schema 缺少', len(miss_fe), miss_fe[:8]))
    # ③ 每个前端 DOCS 工具必须可执行 ✓（有 endpoint 或 handler ✓）
    not_exec = sorted([t for t in fe_docs if t not in fe_eps and t not in handlers])
    if not_exec:
        problems.append(('前端暴露但不可执行 ✗', len(not_exec), not_exec[:8]))
    # ④ ★ KB 里的工具必须都暴露 ✓（P-84 根因 ✓）
    kb_unexposed = sorted(kb - fe_docs - fe_eps - handlers)
    if kb_unexposed:
        problems.append(('KB 里有但未暴露 ✗（会误导模型）', len(kb_unexposed), kb_unexposed[:8]))
    # ⑤ 暴露的但 KB 没写（不致命 ⚠ 只是检索不到 ✓）
    exposed = fe_docs | fe_eps | handlers
    kb_missing = sorted(exposed - kb)
    if kb_missing:
        problems.append(('暴露但 KB 没写 ⚠（检索不到）', len(kb_missing), kb_missing[:8]))

    if not problems:
        print('  ✅ 全部一致 ✓（5 项检查全过）')
        return 0

    for name, n, sample in problems:
        print('  ⚠ %-30s %d 个' % (name, n))
        for t in sample:
            print('      - %s' % t)
    print('')

    # 自动修 KB ✓（删掉未暴露的 ✗ 这是 P-84 的修法 ✓）
    if args.fix_kb and kb_unexposed:
        kb_path = ROOT / 'agent_kb/tool_docs.md'
        txt = kb_path.read_text(encoding='utf-8')
        for t in kb_unexposed:
            txt = re.sub(r'\n## ' + re.escape(t) + r'\n.*?(?=\n## |\Z)', '', txt, flags=re.S)
            print('  ✓ 已从 KB 删除未暴露工具: %s' % t)
        kb_path.write_text(txt, encoding='utf-8')
        print('  ✓ KB 已修（%d 个工具）' % len(re.findall(r'^## ', txt, re.M)))
        return 0

    print('  ⇒ 修复建议: python scripts/check_tools.py --fix-kb（自动清 KB ✗）')
    print('  ⇒ 或按 docs/TOOL_DEV_SPEC.md 的 3 步流程新增工具 ✓')
    return 1


if __name__ == '__main__':
    sys.exit(main())
