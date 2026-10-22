#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T-3：合并新旧数据 + 校验（2026-10-19）

旧: v3-2-2（278 条 · 真实轨迹 · 已证明 +8.0 ✓）
新: v3-3-0（新 schema 轨迹 ✓ 工具更丰富 ✓）
合并: 去重 + 切分 + 全量校验（DATA_QUALITY_SPEC ✓）
"""
import json
import pathlib
import random
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
TD = ROOT / 'training_data'

OLD = [TD / 'exercise_book_v3_2_2.jsonl', TD / 'exercise_book_v3_2_2_eval.jsonl']
NEW = [TD / 'exercise_book_v3_3_0.jsonl', TD / 'exercise_book_v3_3_0_eval.jsonl']
OUT = TD / 'exercise_book_v3_3_0_merged.jsonl'
OUT_EVAL = TD / 'exercise_book_v3_3_0_merged_eval.jsonl'

TOOLS = json.loads((ROOT / 'agent_kb/_tools.json').read_text(encoding='utf-8'))
DOCS = json.loads((ROOT / 'agent_kb/_tool_docs.json').read_text(encoding='utf-8'))['tools']
TOOL_NAMES = set(TOOLS) | set(DOCS)


def load(paths):
    out = []
    for p in paths:
        if p.exists():
            for line in open(p, encoding='utf-8'):
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    return out


old = load(OLD)
new = load(NEW)
print('旧 v3-2-2: %d 条' % len(old))
print('新 v3-3-0: %d 条' % len(new))

# 去重（system+conversations 完全相同的 ✓）
seen = set()
merged = []
for r in old + new:
    key = (r.get('system'), json.dumps(r.get('conversations'), ensure_ascii=False, sort_keys=True))
    if key in seen:
        continue
    seen.add(key)
    merged.append(r)
print('去重后合计: %d 条' % len(merged))

# ── 校验 ──
fails = Counter()
ratios, syslens = [], []
for r in merged:
    conv = r.get('conversations') or []
    if not conv or conv[0].get('from') != 'human':
        fails['F1 首条非 human'] += 1
    if any(c.get('from') == 'system' for c in conv):
        fails['F5 system 混入'] += 1
    if not r.get('system'):
        fails['F5b 缺 system'] += 1
    if not isinstance(r.get('tools'), str):
        fails['F6 tools 非字符串'] += 1
    if conv and conv[-1].get('from') != 'gpt':
        fails['F4 末条非 gpt'] += 1
    ok_pos = True
    for i, c in enumerate(conv):
        if i == len(conv) - 1:
            if c.get('from') != 'gpt':
                ok_pos = False
        else:
            want = 'human' if i == 0 else ('function_call' if i % 2 == 1 else 'observation')
            if c.get('from') != want:
                ok_pos = False
    if not ok_pos:
        fails['F2/F3 位置错'] += 1
    for c in conv:
        if c.get('from') == 'function_call':
            try:
                fc = json.loads(c['value'])
                if fc.get('name') not in TOOL_NAMES:
                    fails['F7 工具名不在白名单'] += 1
                if not isinstance(fc.get('arguments'), dict):
                    fails['F8 args 非 dict'] += 1
            except Exception:
                fails['F8 fc 非法 JSON'] += 1
    system = r.get('system') or ''
    total = sum(len(c.get('value') or '') for c in conv) + len(system)
    if total:
        ratios.append(len(system) / total)
        syslens.append(len(system))

voices = [next((c.get('value') for c in (r.get('conversations') or []) if c.get('from') == 'human'), '') for r in merged]
norm = [re.sub(r'[\s，。！？、,.!?]', '', v) for v in voices]
q3 = len(set(norm)) / len(norm) if norm else 0

# 工具覆盖 ✓
tool_cov = Counter()
obs_total = 0
obs_err = 0
pair_cnt = Counter()
for r in merged:
    n = 0
    for c in (r.get('conversations') or []):
        if c.get('from') == 'function_call':
            n += 1
            try:
                tool_cov[json.loads(c['value'])['name']] += 1
            except Exception:
                pass
        if c.get('from') == 'observation':
            obs_total += 1
            try:
                o = json.loads(c['value'])
                if isinstance(o, dict) and 'error' in o:
                    obs_err += 1
            except Exception:
                pass
    pair_cnt[n] += 1

print('')
print('════════ 格式校验 ════════')
if fails:
    for k, v in fails.items():
        print('  ✗ %-24s %d' % (k, v))
else:
    print('  ✅ F1-F8 全过 ✓')

print('')
print('════════ 质量 ════════')
print('  Q1 system 占比 平均 %.1f%% %s' % (sum(ratios) / len(ratios) * 100,
                                          '✅' if sum(ratios) / len(ratios) < 0.2 else '✗'))
print('  Q2 system 字符 %d %s' % (syslens[0], '✅' if syslens[0] <= 400 else '✗'))
print('  Q3 句子多样性 %.0f%% %s' % (q3 * 100, '✅' if q3 >= 0.5 else '⚠'))
print('  Q5 完全重复 0（已去重 ✓）')

print('')
print('════════ 数量 ════════')
print('  总样本: %d（目标 ≥800 %s）' % (len(merged), '✅' if len(merged) >= 800 else '⚠ 还差 %d' % (800 - len(merged))))
print('  涉及工具: %d 种' % len(tool_cov))
print('  工具对数分布: %s' % dict(sorted(pair_cnt.items())))
print('  真实观测 %d 个（其中真实错误 %d 个 = %.0f%% ✓）' % (obs_total, obs_err,
                                                          100 * obs_err / obs_total if obs_total else 0))

# 切分
random.seed(20261019)
random.shuffle(merged)
n_eval = max(20, int(len(merged) * 0.1))
ev, tr = merged[:n_eval], merged[n_eval:]
for path, data in ((OUT, tr), (OUT_EVAL, ev)):
    with open(path, 'w', encoding='utf-8') as f:
        for s in data:
            f.write(json.dumps(s, ensure_ascii=False) + '\n')
print('')
print('✓ 训练: %s（%d 条）' % (OUT.name, len(tr)))
print('✓ 验证: %s（%d 条）' % (OUT_EVAL.name, len(ev)))

print('')
print('════════ Top 15 工具覆盖 ════════')
for t, n in tool_cov.most_common(15):
    print('  %-28s %d' % (t, n))
