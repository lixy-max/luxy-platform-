#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""50×2 学习能力对照分析（含旧基线作参考组 ✓）。"""
import json, statistics, sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
D = Path('/home/luxy/paper_lora_project/data/trajectories')
pairs = json.loads((D / 'ab50_pairs.json').read_text(encoding='utf-8'))
TPL = ('【第一步必须先查知识库】调用 paper_lora_kb_query，在 {kb} 知识库里检索与下面这个任务相关的经验'
       '（重点看工具可靠性与已知坑），看完再规划怎么做。\n任务:')
def load_arm(arm):
    f = D / f'arm_{arm}' / 'trajectories.jsonl'
    out = {}
    if not f.exists(): return out
    for l in f.read_text(encoding='utf-8').splitlines():
        if not l.strip(): continue
        r = json.loads(l)
        t = str(r.get('task') or '')
        for kb in ('agent_use_ctrl', 'agent_use'):
            pre = TPL.replace('{kb}', kb)
            if t.startswith(pre):
                t = t[len(pre):]
        out.setdefault(t.strip(), r)
    return out
def m(rows, k):
    v = [r[k] for r in rows]
    return (sum(v), statistics.median(v) if v else 0)
ctrl, learn = load_arm('ctrl'), load_arm('learn')
print(f'  样本: 对照库 {len(ctrl)} 条 · 经验库 {len(learn)} 条 · 基线配对 {len(pairs)} 条')
def summarize(name, arm):
    rs = [arm[p['task'].strip()] for p in pairs if p['task'].strip() in arm]
    if not rs: return None
    e = [len(r.get('tool_errors') or []) for r in rs]
    s = [len(r.get('tools_used') or []) for r in rs]
    t = [int((r.get('usage') or {}).get('inputTokens') or 0) for r in rs]
    ok = sum(1 for r in rs if r.get('reason') == 'completed' or r.get('final'))
    return {'n': len(rs), 'errors': sum(e), 'err_med': statistics.median(e),
            'steps': sum(s), 'tok': sum(t), 'ok': ok}
S = {'基线(无指令)': None, '对照库(无经验)': summarize('ctrl', ctrl), '经验库(有蒸馏)': summarize('learn', learn)}
rs = [p['baseline'] for p in pairs]
S['基线(无指令)'] = {'n': len(rs), 'errors': sum(r['errors'] for r in rs),
                  'err_med': statistics.median([r['errors'] for r in rs]) if rs else 0,
                  'steps': sum(r['steps'] for r in rs), 'tok': sum(r['tokens'] for r in rs),
                  'ok': sum(1 for r in rs if r.get('reason') == 'completed')}
print()
print(f"  {'组':<16}{'n':>4}{'报错合计':>10}{'报错中位':>10}{'步数合计':>10}{'token合计':>14}")
for k, v in S.items():
    if not v: print(f'  {k:<16}  (无数据)'); continue
    print(f"  {k:<16}{v['n']:>4}{v['errors']:>10}{v['err_med']:>10.1f}{v['steps']:>10}{v['tok']:>14,}")
c, l = S['对照库(无经验)'], S['经验库(有蒸馏)']
if c and l and c['n'] and l['n']:
    print()
    print('=== 结论（同一句指令 ✓ 唯一差异 = 库里有没有蒸馏经验 ✓）===')
    de = l['errors'] - c['errors']
    dt = l['tok'] - c['tok']
    print(f"  工具报错: 对照库 {c['errors']} → 经验库 {l['errors']}  （{'少' if de<0 else '多'}{abs(de)}）")
    print(f"  token:    对照库 {c['tok']:,} → 经验库 {l['tok']:,}  （{'省' if dt<0 else '多'}{abs(dt):,}，{abs(dt)/max(1,c['tok'])*100:.0f}%）")
    if c['errors'] and l['errors'] < c['errors'] * 0.8:
        print('  ★ 蒸馏经验【确实有效】✓（报错显著下降，且两组指令相同 → 排除了"先查"这个混杂 ✓）')
    elif abs(de) <= max(1, c['errors'] * 0.2):
        print('  ⚠ 两组接近 → 起效的可能是"先查知识库"这个动作，而非蒸馏内容本身 ✗（如实报告 ✓）')
    else:
        print('  ✗ 经验库反而更差 → 如实报告 ✓ 需要检查蒸馏内容是否误导 ✓')
elif not l or not l['n']:
    print('  （经验库还没数据 ✓ 稍后再看）')
