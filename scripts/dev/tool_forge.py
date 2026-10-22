#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tool_forge.py — V6.0 半自动版：工具锻造厂（2026-10-21）

★ 文献依据: MetaAgent (arXiv 2508.00271) "Tool Meta-Learning"
   —— 遇到缺工具时自动创建/组合工具 ✓
★ **半自动**（不是全自动 ✗ 安全第一 ✓）:
   ① 从失败轨迹发现"缺什么工具"
   ② LLM 生成工具代码（**只生成·不执行** ✓）
   ③ 静态检查（语法 + 危险调用扫描 ✓）
   ④ **人工审核**（必须 ✓ 不许自动注册 ✗）
   ⑤ 审核通过后才注册进平台 ✓
   ⑥ 注册后 A/B 验证新工具是否真有用 ✓

★ 安全三道闸（缺一不可 ✓）:
   闸 1 静态扫描: 禁 `os.system`/`subprocess`/`eval`/`exec`/`__import__`/网络请求 ✓
   闸 2 白名单: 只允许调 `requests`（读 API）+ 纯 Python 标准库 ✓
   闸 3 人工审核: 强制 `--approve` 才注册 ✓（不给自动通过 ✗）

用法:
  python scripts/tool_forge.py scan                    # 从轨迹找出"缺工具"信号
  python scripts/tool_forge.py draft --need "查天气"    # 生成草案（不注册 ✓）
  python scripts/tool_forge.py check --draft <name>     # 静态检查
  python scripts/tool_forge.py list                    # 列草案
  python scripts/tool_forge.py approve --draft <name>   # 人工审核通过 ⇒ 注册 ✓
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = pathlib.Path(__file__).resolve().parent.parent
FORGE = ROOT / 'data/flywheel/forge'
DRAFTS = FORGE / 'drafts'
TOOL_DOCS = ROOT / 'agent_kb/_tool_docs.json'
VLLM = 'http://127.0.0.1:8000/v1/chat/completions'

# ══════════════════════════════════════════════════════════
# 安全闸 1: 危险模式（生成代码里出现 ⇒ 直接拒绝 ✗）
# ══════════════════════════════════════════════════════════
DANGEROUS_PATTERNS = [
    (r'\bos\.system\b', 'os.system（任意命令执行）'),
    (r'\bos\.popen\b', 'os.popen'),
    (r'\bsubprocess\b', 'subprocess（子进程）'),
    (r'\beval\s*\(', 'eval（动态执行）'),
    (r'\bexec\s*\(', 'exec（动态执行）'),
    (r'__import__', '__import__（动态导入）'),
    (r'\bopen\s*\([^)]*[\'"]w', 'open(..., "w")（写文件）'),
    (r'\bshutil\.(rmtree|move|copy)', 'shutil.rmtree/move/copy（文件操作）'),
    (r'\bsocket\b', 'socket（网络）'),
    (r'\burllib\.request\b', 'urllib.request（网络，应走 requests）'),
    (r'\bpickle\b', 'pickle（反序列化风险）'),
    (r'\bctypes\b', 'ctypes（底层调用）'),
    (r'\b__builtins__\b', '__builtins__'),
    (r'\bimport\s+os\b', 'import os（不允许 ✗）'),
    (r'\bimport\s+sys\b', 'import sys（不允许 ✗）'),
]
ALLOWED_IMPORTS = {'json', 're', 'requests', 'typing', 'datetime', 'collections', 'math'}

# ══════════════════════════════════════════════════════════
# 生成 prompt ✓
# ══════════════════════════════════════════════════════════
FORGE_PROMPT = """你是「平台工具锻造师」。平台缺一个工具，请写它的实现。

【需求】%s

【平台上下文】
- 后端是 FastAPI，跑在 http://127.0.0.1:8001
- 已有工具示例: list_knowledge_bases (GET /kb/list) · get_vllm_status (GET /vllm/status)
- 工具定义格式: {"name": "...", "endpoint": "/path", "method": "GET", "description": "..."}

【要求（必须严格遵守）】
1. 只输出 JSON（不要解释 ✗ 不要 markdown 代码块 ✗）：
   {"name":"工具名（小写蛇形）","endpoint":"/api/路径","method":"GET 或 POST",
    "description":"一句话说明（≤50字）",
    "params":[{"name":"参数名","type":"string","hint":"说明"}],
    "handler_code":"async def handler(args):\\n    import requests\\n    ...\\n    return {...}"}
2. handler_code 里**只允许** import: requests / json / re / math / datetime ✓
3. **绝对禁止**: os / sys / subprocess / eval / exec / __import__ / open 写文件 / socket / pickle / ctypes ✗
4. handler 必须返回 dict（不是字符串 ✗）
5. 出错要 raise（不要静默返回空 ✗）
"""


def _call_llm(prompt: str, max_tokens: int = 1500) -> str:
    import urllib.request
    body = json.dumps({'model': '/home/luxy/models/Qwen2.5-7B-Instruct-AWQ',
                       'messages': [{'role': 'user', 'content': prompt}],
                       'max_tokens': max_tokens, 'temperature': 0.1}).encode()
    req = urllib.request.Request(VLLM, data=body, headers={'Content-Type': 'application/json'})
    r = json.loads(urllib.request.urlopen(req, timeout=180).read())
    return ((r.get('choices') or [{}])[0].get('message') or {}).get('content', '') or ''


def _parse_json_obj(text: str) -> dict:
    if not text:
        return {}
    text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.MULTILINE)
    i, j = text.find('{'), text.rfind('}')
    if i < 0 or j <= i:
        return {}
    try:
        d = json.loads(text[i:j + 1])
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════
# ① scan: 从轨迹找"缺工具"信号 ✓
# ══════════════════════════════════════════════════════════
def scan(limit: int = 200) -> dict:
    print('=' * 70)
    print('  V6.0 工具锻造厂 · ① 扫描缺工具信号')
    print('=' * 70)
    traj_dir = ROOT / 'data/trajectories'
    signals = []
    for f in sorted(traj_dir.glob('*.jsonl')):
        for line in f.open(encoding='utf-8', errors='replace'):
            if len(signals) >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            # 信号 1: 未知工具 ✗
            for te in (t.get('tool_errors') or []):
                s = str(te)
                if 'unknown' in s.lower() or '未知工具' in s or 'not found' in s.lower():
                    signals.append({'kind': 'unknown_tool', 'task': str(t.get('task'))[:80],
                                    'detail': s[:120], 'src': f.name})
            # 信号 2: 工具调用失败 ✓
            for te in (t.get('tool_errors') or []):
                signals.append({'kind': 'tool_error', 'task': str(t.get('task'))[:80],
                                'detail': str(te)[:120], 'src': f.name})
    # 去重 ✓
    uniq = {}
    for s_ in signals:
        k = (s_['kind'], s_['detail'][:60])
        uniq.setdefault(k, s_)
    out = list(uniq.values())
    print('  扫描: 信号 **%d 条**（去重后）' % len(out))
    from collections import Counter
    print('  分布: %s' % dict(Counter(x['kind'] for x in out)))
    print('')
    for x in out[:10]:
        print('    [%s] %s' % (x['kind'], x['detail'][:90]))
    FORGE.mkdir(parents=True, exist_ok=True)
    (FORGE / 'signals.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print('')
    print('  ✅ 已存: %s' % (FORGE / 'signals.json'))
    return {'ok': True, 'n': len(out)}


# ══════════════════════════════════════════════════════════
# ② draft: 生成工具草案（**不注册** ✓）
# ══════════════════════════════════════════════════════════
def draft(need: str) -> dict:
    print('=' * 70)
    print('  V6.0 工具锻造厂 · ② 生成草案')
    print('=' * 70)
    print('  需求: %s' % need)
    print('  ★ 只生成·不注册 ✓（要 --approve 才注册 ✗）')
    try:
        raw = _call_llm(FORGE_PROMPT % need, 1500)
    except Exception as e:
        print('  ✗ LLM 调用失败: %s' % str(e)[:100])
        return {'ok': False}
    d = _parse_json_obj(raw)
    if not d or not d.get('name'):
        print('  ✗ 解析失败 ✗ 原始输出前 300 字:')
        print('    %s' % raw[:300].replace('\n', ' '))
        return {'ok': False}
    name = re.sub(r'[^a-z0-9_]', '', str(d['name']).lower())[:40]
    if not name:
        print('  ✗ 工具名非法')
        return {'ok': False}
    rec = {
        'draft': name,
        'need': need,
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'spec': d,
        'status': 'draft',        # draft → checked → approved → registered
        'checks': {},
        'model': 'Qwen2.5-7B-Instruct-AWQ',
    }
    DRAFTS.mkdir(parents=True, exist_ok=True)
    p = DRAFTS / ('%s.json' % name)
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding='utf-8')
    print('')
    print('  ✅ 草案: **%s**' % name)
    print('    endpoint: %s %s' % (d.get('method', 'GET'), d.get('endpoint')))
    print('    description: %s' % str(d.get('description'))[:80])
    print('    params: %s' % [x.get('name') for x in (d.get('params') or [])])
    print('    handler 代码行数: %d' % len(str(d.get('handler_code') or '').split('\n')))
    print('')
    print('  下一步: python scripts/tool_forge.py check --draft %s' % name)
    return {'ok': True, 'draft': name, 'path': str(p)}


# ══════════════════════════════════════════════════════════
# ③ check: 静态检查（安全闸 1+2 ✓ 不执行代码 ✗）
# ══════════════════════════════════════════════════════════
def check(draft_name: str) -> dict:
    print('=' * 70)
    print('  V6.0 工具锻造厂 · ③ 静态检查（**不执行代码** ✗ 只静态分析 ✓）')
    print('=' * 70)
    p = DRAFTS / ('%s.json' % draft_name)
    if not p.exists():
        print('  ✗ 草案不存在: %s' % p)
        return {'ok': False}
    rec = json.loads(p.read_text(encoding='utf-8'))
    code = str((rec.get('spec') or {}).get('handler_code') or '')
    issues, warnings = [], []

    # 闸 1: 危险模式 ✓
    print('  闸 1: 危险模式扫描')
    for pat, desc in DANGEROUS_PATTERNS:
        if re.search(pat, code):
            issues.append('危险: %s' % desc)
    print('    %s' % ('✗ 发现问题' if issues else '✓ 通过'))

    # 闸 2: import 白名单 ✓
    print('  闸 2: import 白名单')
    imports = re.findall(r'^\s*(?:import|from)\s+([a-zA-Z_][\w.]*)', code, re.MULTILINE)
    bad_imports = [i for i in imports if i.split('.')[0] not in ALLOWED_IMPORTS]
    if bad_imports:
        issues.append('不允许的 import: %s（白名单: %s）' % (
            bad_imports, sorted(ALLOWED_IMPORTS)))
    print('    imports: %s' % (imports or '（无）'))
    print('    %s' % ('✗ 有不允许的' if bad_imports else '✓ 通过'))

    # 闸 3: 语法检查 ✓（ast.parse ✓ 不执行 ✗）
    print('  闸 3: 语法检查（ast.parse ✓ 不执行）')
    syntax_ok = True
    try:
        __import__('ast').parse(code)
        print('    ✓ 语法正确')
    except SyntaxError as e:
        syntax_ok = False
        issues.append('语法错误: %s' % e)
        print('    ✗ %s' % e)

    # 闸 4: 结构检查 ✓
    print('  闸 4: 结构检查')
    if not re.search(r'^\s*(async\s+)?def\s+handler\s*\(', code, re.MULTILINE):
        issues.append('缺少 handler 函数 ✗')
        print('    ✗ 没有 handler')
    else:
        print('    ✓ 有 handler 函数')
    if 'return' not in code:
        warnings.append('没有 return ✗（应该返回 dict ✓）')
        print('    ⚠ 没有 return')
    else:
        print('    ✓ 有 return')

    ok = not issues
    rec['checks'] = {'issues': issues, 'warnings': warnings, 'syntax_ok': syntax_ok,
                     'checked_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    rec['status'] = 'checked' if ok else 'rejected'
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding='utf-8')

    print('')
    if issues:
        print('  ❌ **检查未通过**（%d 个问题）:' % len(issues))
        for i in issues:
            print('     ✗ %s' % i)
        print('')
        print('  ⇒ 可重新生成: python scripts/tool_forge.py draft --need "<需求>"')
    else:
        print('  ✅ **检查通过** ✓')
        for w in warnings:
            print('     ⚠ %s' % w)
        print('')
        print('  ★ 下一步是【人工审核】（必须 ✗ 不能自动 ✗）:')
        print('     ① 人工读代码: %s' % p)
        print('     ② 确认安全后: python scripts/tool_forge.py approve --draft %s' % draft_name)
    return {'ok': ok, 'issues': issues, 'warnings': warnings}


# ══════════════════════════════════════════════════════════
# ④ list / approve ✓
# ══════════════════════════════════════════════════════════
def list_drafts() -> dict:
    print('=' * 70)
    print('  V6.0 工具锻造厂 · 草案列表')
    print('=' * 70)
    if not DRAFTS.exists():
        print('  （无草案）')
        return {'n': 0}
    rows = []
    for p in sorted(DRAFTS.glob('*.json')):
        try:
            rec = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        rows.append(rec)
    print('  共 %d 个草案' % len(rows))
    print('  %-24s %-12s %-6s %s' % ('名称', '状态', '问题', '需求'))
    for r in rows:
        n_iss = len((r.get('checks') or {}).get('issues') or [])
        print('  %-24s %-12s %-6s %s' % (
            r.get('draft'), r.get('status'), n_iss, str(r.get('need'))[:34]))
    return {'n': len(rows)}


def approve(draft_name: str, force: bool = False) -> dict:
    """★ 人工审核通过 ⇒ 注册 ✓（唯一注册入口 ✓）"""
    print('=' * 70)
    print('  V6.0 工具锻造厂 · ④ 人工审核通过 ⇒ 注册')
    print('=' * 70)
    p = DRAFTS / ('%s.json' % draft_name)
    if not p.exists():
        print('  ✗ 草案不存在')
        return {'ok': False}
    rec = json.loads(p.read_text(encoding='utf-8'))
    if rec.get('status') != 'checked' and not force:
        print('  ✗ 状态是 "%s" ⇒ 必须先通过 check（或用 --force ⚠）' % rec.get('status'))
        return {'ok': False}
    spec = rec.get('spec') or {}
    name = spec.get('name')
    print('  工具: **%s**' % name)
    print('  endpoint: %s %s' % (spec.get('method'), spec.get('endpoint')))

    # 注册进 _tool_docs.json ✓（备份 ✓）
    import shutil
    if TOOL_DOCS.exists():
        bak = TOOL_DOCS.parent / ('_tool_docs.json.bak_%s' % datetime.now().strftime('%Y%m%d_%H%M%S'))
        shutil.copy2(TOOL_DOCS, bak)
        print('  备份: %s' % bak.name)
        d = json.loads(TOOL_DOCS.read_text(encoding='utf-8'))
        tools = d.get('tools') or []
        # 去重 ✓
        tools = [t for t in tools if (t.get('name') if isinstance(t, dict) else None) != name]
        tools.append({
            'name': name,
            'endpoint': spec.get('endpoint'),
            'method': spec.get('method', 'GET'),
            'description': str(spec.get('description'))[:200],
            'params': spec.get('params') or [],
            'source': 'tool_forge',
            'registered_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })
        d['tools'] = tools
        TOOL_DOCS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
        print('  ✅ 已注册进 %s（工具总数 %d ✓）' % (TOOL_DOCS.name, len(tools)))
    rec['status'] = 'registered'
    rec['registered_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding='utf-8')
    print('')
    print('  ★ 注册后必须 A/B 验证（铁律 ✓）:')
    print('    1. 在页面上让 agent 用新工具完成任务 ✓')
    print('    2. 对比"有/无新工具"的成功率 ✓')
    print('    3. 没提升 ⇒ 撤销注册 ✗（回滚 .bak ✓）')
    return {'ok': True, 'name': name}


# ══════════════════════════════════════════════════════════
# CLI ✓
# ══════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description='V6.0 工具锻造厂（半自动 ✓ 人工审核强制 ✓）')
    ap.add_argument('cmd', choices=['scan', 'draft', 'check', 'list', 'approve'])
    ap.add_argument('--need', default='')
    ap.add_argument('--draft', default='')
    ap.add_argument('--limit', type=int, default=200)
    ap.add_argument('--force', action='store_true', help='跳过 check 状态检查（⚠ 危险）')
    a = ap.parse_args()
    if a.cmd == 'scan':
        scan(a.limit)
    elif a.cmd == 'draft':
        if not a.need:
            print('  ✗ 需要 --need "<需求>"')
            return 1
        draft(a.need)
    elif a.cmd == 'check':
        if not a.draft:
            print('  ✗ 需要 --draft <name>')
            return 1
        r = check(a.draft)
        return 0 if r.get('ok') else 2
    elif a.cmd == 'list':
        list_drafts()
    elif a.cmd == 'approve':
        if not a.draft:
            print('  ✗ 需要 --draft <name>')
            return 1
        r = approve(a.draft, a.force)
        return 0 if r.get('ok') else 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
