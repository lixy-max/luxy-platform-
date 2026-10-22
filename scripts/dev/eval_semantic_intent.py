#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语义理解 LoRA · 留出集评测（四项指标）。

为什么要先评测: 训练 loss 0.0003 = 背下模板 ✗（query_knowledge_base 占 55% 且目标雷同）
  ⇒ 只看得见的指标: schema 合法率 / 工具名正确率 / **该追问命中率** / 多要求覆盖率

流程:
  1. 把训练产物登记为 LoRA（复制到 ~/loras/<name> + 写 registry.json）
  2. 通过后端 /loras/load 挂上（用当前底座；FP16-7B 与 AWQ-7B 的 hidden_size 都是 3584 ✓）
  3. 用 vLLM 逐条生成 52 条留出样本的输出（temperature=0 ✓ 可复现）
  4. 四项指标打分 + 给失败样例
用法: /home/luxy/.venv/bin/python scripts/eval_semantic_intent.py [--limit N]
"""
from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent.parent
API = 'http://127.0.0.1:8001'
SRC = ROOT / 'training_output' / 'semantic_intent_v1'
LORAS = Path.home() / 'loras'
NAME = 'semantic_intent_v1'
EVAL = ROOT / 'training_data' / 'semantic_intent_v1_eval.jsonl'
REPORT = ROOT / 'training_output' / 'semantic_intent_v1_eval_report.md'
BASE = '/home/luxy/models/Qwen2.5-7B-Instruct'
REQUIRED = {'intent_type', 'goal', 'constraints', 'targets', 'missing', 'should_ask', 'plan'}


def post(path, body, timeout=180):
    r = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                               headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read().decode())


def get(path, timeout=30):
    with urllib.request.urlopen(API + path, timeout=timeout) as f:
        return json.loads(f.read().decode())


def register_adapter():
    """把训练产物登记成 LoRA（不重训 ✓ 只复制 + 写 registry ✓）。"""
    dst = LORAS / NAME
    if not (dst / 'adapter_model.safetensors').exists():
        dst.mkdir(parents=True, exist_ok=True)
        for f in SRC.glob('*'):
            if f.is_file():
                shutil.copy2(f, dst / f.name)
        print(f'  ✓ 产物复制到 {dst}')
    else:
        print(f'  ✓ 产物已在 {dst}')
    reg_p = LORAS / 'registry.json'
    reg = json.loads(reg_p.read_text(encoding='utf-8')) if reg_p.exists() else {}
    if NAME not in reg:
        reg[NAME] = {'base_model': BASE, 'hidden_size': 3584, 'created_at': '2026-10-15',
                     'purpose': '用户语义理解（意图/约束/目标/计划/该不该追问）',
                     'dataset': 'semantic_intent_v1.jsonl', 'data_count': 1322,
                     'note': '训练 loss 0.0003（疑过拟合），先评测再决定是否采用'}
        reg_p.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'  ✓ registry.json 已登记 {NAME}')
    else:
        print(f'  （registry 已有 {NAME}）')
    return dst


def load_lora():
    try:
        d = post('/loras/load', {'lora_name': NAME}, timeout=180)
        print(f'  ✓ 已挂载: {json.dumps(d, ensure_ascii=False)[:160]}')
        return True
    except urllib.error.HTTPError as e:
        print(f'  ✗ 挂载被拒（{e.code}）: {e.read().decode("utf-8", errors="replace")[:200]}')
        return False
    except Exception as e:
        print(f'  ✗ 挂载异常: {e}')
        return False


def gen(system, human):
    d = post('/chat/completions', {
        'provider': 'vllm', 'lora': NAME,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': human}],
        'max_tokens': 700, 'temperature': 0.0,
    }, timeout=300)
    c = d.get('content')
    if isinstance(c, str) and c.strip():
        return c.strip()
    for ch in (d.get('choices') or []):
        v = str((ch.get('message') or {}).get('content') or '').strip()
        if v:
            return v
    return ''


def parse_json(txt):
    t = (txt or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[-1].rsplit('```', 1)[0]
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            return None
    return None


def main():
    limit = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else 0
    rows = [json.loads(l) for l in EVAL.read_text(encoding='utf-8').splitlines() if l.strip()]
    if limit:
        rows = rows[:limit]
    print(f'=== 留出集评测 {len(rows)} 条 ===')

    register_adapter()
    if not load_lora():
        print('  ⇒ 无法挂载，评测中止（多半要先把底座切到 FP16 7B）'); return 1

    tools = {t.get('name') for t in json.loads((ROOT / 'agent_kb' / '_tools.json').read_text(encoding='utf-8'))} \
        if isinstance(json.loads((ROOT / 'agent_kb' / '_tools.json').read_text(encoding='utf-8')), list) else set()
    if not tools:
        import re
        txt = (ROOT / 'webui' / 'index3.html').read_text(encoding='utf-8')
        tools = set(re.findall(r'AGENT_TOOLS\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{', txt))
    print(f'  真实工具集: {len(tools)} 个')

    res = []
    for i, r in enumerate(rows, 1):
        conv = r['conversations']
        system, human, want = conv[0]['value'], conv[1]['value'], json.loads(conv[2]['value'])
        out = gen(system, human)
        got = parse_json(out)
        schema_ok = bool(got) and REQUIRED.issubset(set(got.keys()))
        tool_ok = bool(got) and all((st.get('tool') in tools) for st in (got.get('plan') or []) if st)
        want_ask = bool(want.get('should_ask'))
        got_ask = bool(got.get('should_ask')) if got else False
        ask_ok = (want_ask == got_ask)
        multi_ok = None
        if want.get('intent_type') == 'multi_request':
            need = len(want.get('plan') or [])
            have = len((got or {}).get('plan') or [])
            multi_ok = have >= max(2, need - 1)
        res.append({'i': i, 'human': human, 'want_type': want.get('intent_type'),
                    'got_type': (got or {}).get('intent_type'), 'schema_ok': schema_ok,
                    'tool_ok': tool_ok, 'want_ask': want_ask, 'got_ask': got_ask,
                    'ask_ok': ask_ok, 'multi_ok': multi_ok, 'raw': out[:300]})
        flag = '✓' if (schema_ok and tool_ok and ask_ok) else '✗'
        print(f'  [{i:>2}/{len(rows)}] {flag} 类型 {res[-1]["got_type"]} '
              f'(期望 {res[-1]["want_type"]}) ask={got_ask}/{want_ask} {human[:34]}')

    n = len(res)
    schema = sum(1 for x in res if x['schema_ok'])
    toolr = sum(1 for x in res if x['tool_ok'])
    askr = sum(1 for x in res if x['ask_ok'])
    traps = [x for x in res if x['want_ask']]
    trap_ok = sum(1 for x in traps if x['got_ask'])
    mults = [x for x in res if x['multi_ok'] is not None]
    multi_ok = sum(1 for x in mults if x['multi_ok'])
    typ = sum(1 for x in res if x['got_type'] == x['want_type'])
    print()
    print('=' * 62)
    print(f'  schema 合法率   {schema}/{n} = {100*schema//max(1,n)}%（阈值 95%）')
    print(f'  工具名正确率    {toolr}/{n} = {100*toolr//max(1,n)}%（阈值 90%）')
    print(f'  ★ 该追问命中率  {trap_ok}/{len(traps)} = {100*trap_ok//max(1,len(traps))}%（阈值 80%）')
    print(f'  多要求覆盖率    {multi_ok}/{len(mults)} = {100*multi_ok//max(1,len(mults))}%（阈值 80%）')
    print(f'  intent_type 命中 {typ}/{n} = {100*typ//max(1,n)}%')
    print(f'  ask 判定一致率  {askr}/{n} = {100*askr//max(1,n)}%')
    print('  预测类型分布:', dict(Counter(x['got_type'] for x in res).most_common(6)))

    L = ['# 语义理解 LoRA · 留出集评测', '',
         f'- 模型: `{NAME}`（base {BASE}）· 留出集 {n} 条 · temperature=0',
         f'- schema 合法率: **{schema}/{n} = {100*schema//max(1,n)}%**（阈值 95%）',
         f'- 工具名正确率: **{toolr}/{n} = {100*toolr//max(1,n)}%**（阈值 90%）',
         f'- ★ 该追问命中率: **{trap_ok}/{len(traps)} = {100*trap_ok//max(1,len(traps))}%**（阈值 80%）',
         f'- 多要求覆盖率: **{multi_ok}/{len(mults)} = {100*multi_ok//max(1,len(mults))}%**（阈值 80%）',
         f'- intent_type 命中率: {typ}/{n} = {100*typ//max(1,n)}%',
         '', '## 逐条', '']
    for x in res:
        L.append(f"- [{ '✓' if (x['schema_ok'] and x['tool_ok'] and x['ask_ok']) else '✗' }] "
                 f"`{x['want_type']}` → `{x['got_type']}` · ask {x['got_ask']}/{x['want_ask']} · {x['human'][:50]}")
    REPORT.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f'\n  明细 → {REPORT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
