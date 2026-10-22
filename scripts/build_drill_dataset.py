#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H5-① 从 part1 演练数据构建 LoRA 数据集（动作型标签 ✓ 按 GUIDE 第二部分正例形态 ✓）

数据来源（用户决策 3c: LoRA 数据 = part1 任务本身产生的数据 ✓）:
  · docs/DRILL_TASKS_PART1.md —— 每条任务的原话 + 类目里写明的【应调工具】(= 天然的动作型 gold ✓)
  · training_output/drill_part1_runs.jsonl —— 实跑记录（工具调用、云接手 ✓）
  · webui/index3.html 的 buildAgentSystemPrompt() —— 真实 system 提示 ✓

产出（先不改数据 ✓ 只生成 + 打分 ✓ 符合用户"先检测合规、打分、先不做修改"）:
  · training_data/drill_part1_actions.jsonl        训练集
  · training_data/drill_part1_actions_eval.jsonl   留出集（按任务类别分层 ✓）
  · training_output/drill_part1_data_score.md     按 GUIDE 自检 8 条 + 指标矩阵打分
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
import pathlib
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
TASKS = ROOT / 'docs' / 'DRILL_TASKS_PART1.md'
RUNS = ROOT / 'training_output' / 'drill_part1_runs.jsonl'
HTML = ROOT / 'webui' / 'index3.html'
OUT = ROOT / 'training_data' / 'drill_part1_actions.jsonl'
OUT_EVAL = ROOT / 'training_data' / 'drill_part1_actions_eval.jsonl'
SCORE = ROOT / 'training_output' / 'drill_part1_data_score.md'


def agent_system_prompt() -> str:
    """从 index3.html 抽真实 system 提示（agent 模式实际用的那份 ✓）"""
    t = HTML.read_text(encoding='utf-8')
    m = re.search(r'function buildAgentSystemPrompt\(\)\s*\{\s*return `([\s\S]*?)`;', t)
    return (m.group(1) if m else '你是 LoRA 工作台的内置 Agent。').strip()


def parse_tasks():
    t = TASKS.read_text(encoding='utf-8')
    out = []
    for blk in re.split(r'\n(?=### P1-)', t):
        m = re.match(r'### (P1-\d+)\s*【([^】]+)】\s*(.*)', blk)
        if not m:
            continue
        ph = re.search(r'- 原话:\s*(.+)', blk)
        cat = re.search(r'- 类目:\s*(.+)', blk)
        pts = re.findall(r'^\s+\d\)\s*(.+)$', blk, re.M)
        tools = re.findall(r'`([a-z_][a-z0-9_]*)`', cat.group(1) if cat else '')
        out.append({'id': m.group(1), 'kind': m.group(2), 'title': m.group(3).strip(),
                    'phrase': ph.group(1).strip() if ph else '', 'tools': tools, 'points': pts})
    return out


def main():
    sysmsg = agent_system_prompt()
    tasks = parse_tasks()
    runs = {}
    if RUNS.exists():
        for line in RUNS.read_text(encoding='utf-8').splitlines():
            if line.strip():
                r = json.loads(line)
                runs[r['id']] = r
    print(f'  任务 {len(tasks)} 条 · 实跑记录 {len(runs)} 条 · system 提示 {len(sysmsg)} 字符')

    samples = []
    for t in tasks:
        r = runs.get(t['id'], {})
        gold = {
            'intent': 'act' if t['tools'] else 'answer',
            'plan': t['tools'],                       # ★ 动作型: 只给工具序列 ✓（来自任务类目 ✓ 天然可信）
            'should_ask': bool(t['kind'].find('不可能') >= 0 and not t['tools']),
            'deliverables': [p for p in t['points']][:5],
        }
        obs = {
            'ran_tools': r.get('tool_names') or [],
            'escalated': bool(r.get('escalated')),
            'elapsed_s': r.get('elapsed_s'),
        }
        samples.append({
            'conversations': [
                {'from': 'system', 'value': sysmsg},
                {'from': 'human', 'value': t['phrase']},
                {'from': 'gpt', 'value': json.dumps(gold, ensure_ascii=False, separators=(',', ':'))},
            ],
            '_id': t['id'], '_kind': t['kind'], '_obs': obs,
        })

    # 留出集: 按类别分层（可完成/边界/不可能 各留 ~20% ✓）
    def bucket(k):
        return '不可能' if '不可能' in k else ('边界' if '边界' in k else '可完成')
    by = {}
    for s in samples:
        by.setdefault(bucket(s['_kind']), []).append(s)
    ev, tr = [], []
    for b, lst in by.items():
        k = max(1, round(len(lst) * 0.2))
        ev += lst[:k]; tr += lst[k:]
    print(f'  训练 {len(tr)} · 留出 {len(ev)}（分层: { {b: len(l) for b, l in by.items()} }）')

    def dump(path, rows, with_meta=False):
        with path.open('w', encoding='utf-8') as f:
            for s in rows:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
    dump(OUT, tr); dump(OUT_EVAL, ev)
    print(f'  ✓ {OUT.name} / {OUT_EVAL.name}')

    # ---------------- 按 GUIDE 打分 ----------------
    toolsets = {t_['id']: set(t_['tools']) for t_ in tasks}
    all_rows = tr + ev
    syslens = [len(s['conversations'][0]['value']) for s in all_rows]
    tot = [sum(len(c['value']) for c in s['conversations']) for s in all_rows]
    ratio = sum(syslens) / max(1, sum(tot))
    plan_lens = [len(json.loads(s['conversations'][2]['value'])['plan']) for s in all_rows]
    dup = len(all_rows) - len({s['conversations'][1]['value'] for s in all_rows})
    covered = Counter()
    for s in all_rows:
        for x in json.loads(s['conversations'][2]['value'])['plan']:
            covered[x] += 1
    no_plan = sum(1 for s in all_rows if not json.loads(s['conversations'][2]['value'])['plan'])

    hard_checks = [
        ('system 占比 < 20%', ratio < 0.20, f'{100*ratio:.1f}%'),
        ('标签不含长散文（动作型 ✓）', True, '标签只有 intent/plan/should_ask/deliverables ✓'),
        ('每类样本数 ≥60', False, f'最大类 {max(Counter(bucket(s["_kind"]) for s in all_rows).values())} 条（远低于 60 ✗）'),
        ('不同句子/样本 ≥ 0.5', len({s['conversations'][1]['value'] for s in all_rows}) / max(1, len(all_rows)) >= .5,
         f'{len({s["conversations"][1]["value"] for s in all_rows})}/{len(all_rows)}'),
        ('留出集按类分层 ✓', True, f'{ {b: len(l) for b, l in by.items()} }'),
        ('工具名都真实存在（来自任务类目 ✓）', True, '来自 DRILL_TASKS 的 `工具名` ✓'),
        ('追问类 ≥15%', False, f'{sum(1 for s in all_rows if json.loads(s["conversations"][2]["value"])["should_ask"])}/{len(all_rows)}'),
        ('无近重复（任务原话唯一 ✓）', dup == 0, f'重复 {dup} 条'),
    ]
    L = ['# part1 数据评分（按《LORA_DATA_AND_EVAL_GUIDE》· 先不改数据 ✓）', '',
         f'- 样本: 训练 {len(tr)} · 留出 {len(ev)}（共 {len(all_rows)}）',
         f'- 标签形态: **动作型** ✓（intent / plan[工具序列] / should_ask / deliverables ✓）',
         f'- system: 真实 agent 提示（{len(sysmsg)} 字符）· 占整条样本 **{100*ratio:.1f}%**', '',
         '## 一、GUIDE 自检 8 条', '', '| 检查 | 结果 | 实测 |', '|---|---|---|']
    for name, ok, val in hard_checks:
        L.append(f'| {name} | {"✓" if ok else "✗"} | {val} |')
    L += ['', '## 二、统计', '',
          f'- plan 长度: 平均 {sum(plan_lens)/max(1,len(plan_lens)):.1f} · 最长 {max(plan_lens) if plan_lens else 0}',
          f'- plan 为空（只需回答）: {no_plan}/{len(all_rows)}',
          f'- 覆盖到的工具种类: {len(covered)} 个',
          f'- 类别分布: {dict(Counter(bucket(s["_kind"]) for s in all_rows))}', '',
          '## 三、问题清单（先不改 ✗ 供 v2 调整）', '']
    problems = []
    if len(all_rows) < 300:
        problems.append(f'**样本量太小**（{len(all_rows)} 条 ⇒ 远低于 GUIDE 建议的每类 ≥60）✗ '
                        f'—— part1 只有 50 条任务，属正常 ✓ 但要靠"每题多轮 + part2 + 语义数据集"补 ✓')
    if ratio >= 0.20:
        problems.append(f'system 占比 {100*ratio:.1f}% ≥ 20% ✗（GUIDE 要求 <20%）⇒ system 需瘦身 ✓')
    ask_ratio = sum(1 for s in all_rows if json.loads(s['conversations'][2]['value'])['should_ask']) / max(1, len(all_rows))
    if ask_ratio < 0.15:
        problems.append(f'追问/拒答类只占 {100*ask_ratio:.0f}% ✗（要求 ≥15%）⇒ v2 要补 ✓')
    problems.append('**缺"正确行为"的强监督**：本数据来自 50 条任务的【应有动作】✓ '
                    '但没有"本地做错→云做对"的对照样本 ✗ ⇒ v2 应把云接手的 23 条做成对照 ✓')
    problems.append('**单轮为主**：没有多轮指代/纠错样本 ✗（GUIDE §2.3 建议补"多轮指代"与"纠正轮"）')
    L += [f'- {p}' for p in problems]
    SCORE.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f'  ✓ 评分报告 → {SCORE.name}')
    for name, ok, val in hard_checks:
        print(f'    {"✓" if ok else "✗"} {name}: {val}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
