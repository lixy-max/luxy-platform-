#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同步新工具到前端 + 支持路径参数（2026-10-19）"""
import json
import re
import sys
import pathlib

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = str(pathlib.Path(__file__).resolve().parent.parent)
HTML = ROOT + '/webui/index3.html'
html = open(HTML, encoding='utf-8').read()

tools = json.loads(open(ROOT + '/agent_kb/_tools.json', encoding='utf-8').read())
docs = json.loads(open(ROOT + '/agent_kb/_tool_docs.json', encoding='utf-8').read())['tools']


def js_str(s):
    return "'" + str(s).replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ') + "'"


NEW_TOOLS = ('rebuild_knowledge_base', 'delete_knowledge_base')

# ① 更新 AGENT_TOOL_ENDPOINTS（加两个新工具 ✓）
for name in NEW_TOOLS:
    spec = tools.get(name)
    if not spec:
        continue
    ep = spec['endpoint']
    line = '        %s: [%s, %s],' % (name, js_str(spec.get('method', 'GET')), js_str(ep))
    if re.search(r'^\s{8}' + re.escape(name) + r':', html, re.M):
        html = re.sub(r'^\s{8}' + re.escape(name) + r':\s*\[.*?\],\s*$', line, html, count=1, flags=re.M)
        print('  ✓ 更新 endpoint: %s' % name)
    else:
        # 插到 AGENT_TOOL_ENDPOINTS 块末尾
        m = re.search(r'(    const AGENT_TOOL_ENDPOINTS\s*=\s*\{.*?)(\n    \};)', html, re.S)
        if m:
            html = html[:m.end(1)] + '\n' + line + html[m.end(1):]
            print('  ✓ 新增 endpoint: %s' % name)

# ② 更新 AGENT_TOOL_DOCS（加两个新工具 ✓）
for name in NEW_TOOLS:
    d = docs.get(name)
    if not d:
        continue
    params = [p['name'] for p in (d.get('params') or []) if p.get('name')]
    pj = '{' + ', '.join("%s: 'string'" % p for p in params) + '}'
    line = "        %s: [%s, %s]," % (name, js_str(d['desc']), pj)
    if re.search(r'^\s{8}' + re.escape(name) + r':\s*\[', html, re.M):
        html = re.sub(r'^\s{8}' + re.escape(name) + r':\s*\[.*?\],\s*$', line, html, count=1, flags=re.M)
        print('  ✓ 更新 docs: %s' % name)
    else:
        m = re.search(r'(    const AGENT_TOOL_DOCS\s*=\s*\{.*?)(\n    \};)', html, re.S)
        if m:
            html = html[:m.end(1)] + '\n' + line + html[m.end(1):]
            print('  ✓ 新增 docs: %s' % name)

# ③ 通用分发器支持路径参数 {param} ✓
old = '''                          const _a = args || {};
                          const _method = (_ep[0] || 'GET').toUpperCase();
                          const _url = API_BASE + _ep[1];'''
new = '''                          const _a = Object.assign({}, args || {});
                          const _method = (_ep[0] || 'GET').toUpperCase();
                          // ★ 路径参数替换：/kb/{kb_name}/rebuild → /kb/xxx/rebuild ✓
                          let _path = _ep[1];
                          _path = _path.replace(/\\{([a-zA-Z_][a-zA-Z0-9_]*)\\}/g, function (m, k) {
                              const v = _a[k];
                              delete _a[k];            // 已进路径 ✓ 不再进 body ✓
                              return encodeURIComponent(v === undefined || v === null ? '' : v);
                          });
                          const _url = API_BASE + _path;'''
if old in html:
    html = html.replace(old, new, 1)
    print('  ✓ 通用分发器支持 {path param} 替换 ✓')
else:
    print('  ⚠ 找不到分发器锚点（可能已改）')

open(HTML, 'w', encoding='utf-8').write(html)
print('  ✓ index3.html: %d 行' % html.count('\n'))

import ast
# JS 语法粗查（括号/引号平衡 ✓ 不能完全替代 node ✓）
print('  括号平衡:', '✓' if html.count('{') == html.count('}') else '✗ 不平衡（可能字符串里有）')
