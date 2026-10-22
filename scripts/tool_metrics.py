#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具调用指标 v1（T3 · 2026-10-19）

不改造旧判分器（drill_score.py 517 行 v2 已经很完善 ✓ 原则二：先找不训 ✓）
只新增 5 个【工具调用维度】的指标 ✓

指标:
  1. tool_choice    工具选择命中率（有没有调对期望的工具 ✓）
  2. param_valid    参数有效率（exec_ok / (exec_ok + exec_err) ✓ 代理参数正确率 ✓）
  3. first_ok       首次调用成功率（第一个工具调用就成功 ✓）
  4. repair_rate    报错修复率（报错后有没有再试成功 ✓）
  5. rounds         调用轮数（越少越好 ✓）

用法:
  python scripts/tool_metrics.py --runs /mnt/e/WSL/_check/drill/out/runs.jsonl --tag h322_on
  python scripts/tool_metrics.py --runs ... --compare h322_off h322_on
  python scripts/tool_metrics.py --runs ... --json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

TASKS_P1 = ROOT / 'docs' / 'DRILL_TASKS_PART1.md'
TASKS_P2 = ROOT / 'docs' / 'DRILL_TASKS_PART2.md'


def load_tasks():
    """复用 drill_score 的任务解析 ✓"""
    try:
        import drill_score as ds
    except Exception as e:
        print('✗ 无法导入 drill_score:', e)
        return {}
    tasks = {}
    for p in (TASKS_P1, TASKS_P2):
        if p.exists():
            for t in ds.parse_tasks(p):
                tasks[t['id']] = t
    # P2 缺类目时从 P1 继承 ✓（drill_score 里有现成函数 ✓）
    if TASKS_P1.exists() and TASKS_P2.exists():
        try:
            ds.inherit_pairs(list(tasks.values()), TASKS_P1)
        except Exception:
            pass
    return tasks


def _ordered_calls(run):
    """按顺序取工具调用（优先 ui_cards ✓ 有顺序+状态 ✓ 否则 exec_tools ✓）"""
    cards = run.get('ui_cards') or []
    if cards and all(isinstance(c, dict) and c.get('name') for c in cards):
        return [{'name': (c.get('name') or ''), 'status': (c.get('status') or '')} for c in cards]
    ok = set(run.get('exec_ok_tools') or [])
    err = set(run.get('exec_err_tools') or [])
    out = []
    for name in (run.get('exec_tools') or []):
        st = 'ok' if name in ok else ('error' if name in err else 'unknown')
        out.append({'name': name, 'status': st})
    return out


def metrics_for_run(task, run):
    calls = _ordered_calls(run)
    names = [c['name'] for c in calls]
    ok_names = {c['name'] for c in calls if c['status'] == 'ok'}
    err_names = {c['name'] for c in calls if c['status'] == 'error'}
    expected = set(task.get('tools') or []) if task else set()

    # 1 工具选择命中（期望集合任一被调用即算 ✓ "或"语义 ✓ 和 drill_score 一致 ✓）
    choice = None
    if expected and not task.get('is_impossible'):
        choice = 1.0 if (expected & set(names)) else 0.0

    # 2 参数有效率
    n_ok, n_err = len(run.get('exec_ok_tools') or []), len(run.get('exec_err_tools') or [])
    param = (n_ok / (n_ok + n_err)) if (n_ok + n_err) > 0 else None

    # 3 首次调用成功
    first = calls[0] if calls else None
    first_ok = None
    if first:
        first_ok = 1.0 if first['status'] == 'ok' else 0.0

    # 4 报错修复：出现 error 之后（按顺序 ✓ 有 ok ✓）
    repair = None
    if any(c['status'] == 'error' for c in calls):
        idx_err = next(i for i, c in enumerate(calls) if c['status'] == 'error')
        repair = 1.0 if any(c['status'] == 'ok' for c in calls[idx_err + 1:]) else 0.0

    return {
        'id': run.get('id'),
        'tag': run.get('tag'),
        'tool_choice': choice,
        'param_valid': param,
        'first_ok': first_ok,
        'repair_rate': repair,
        'rounds': len(calls),
        'ok': 1.0 if run.get('ok') else 0.0,
        'elapsed_s': run.get('elapsed_s'),
        'model_value': run.get('model_value'),
    }


METRIC_KEYS = ['tool_choice', 'param_valid', 'first_ok', 'repair_rate', 'rounds', 'ok', 'elapsed_s']
METRIC_NAMES = {
    'tool_choice': '工具选择命中率', 'param_valid': '参数有效率', 'first_ok': '首次调用成功率',
    'repair_rate': '报错修复率', 'rounds': '调用轮数', 'ok': '任务ok率', 'elapsed_s': '耗时(s)',
}


def ttest_paired(a, b):
    """配对 t 检验（n≥2 ✓）返回 (mean_a, mean_b, diff, t, p_approx)"""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n < 2:
        return None
    diffs = [y - x for x, y in pairs]
    mean_d = sum(diffs) / n
    var_d = sum((d - mean_d) ** 2 for d in diffs) / (n - 1)
    if var_d <= 0:
        return {'n': n, 'mean_a': sum(x for x, _ in pairs) / n, 'mean_b': sum(y for _, y in pairs) / n,
                'diff': mean_d, 't': None, 'p': None}
    se = math.sqrt(var_d / n)
    t = mean_d / se
    # 双尾 p 近似（t 分布 → 用正态近似，n≥20 足够 ✓ 小 n 标注 ⚠）
    p = 2 * (1 - _norm_cdf(abs(t)))
    return {'n': n, 'mean_a': sum(x for x, _ in pairs) / n, 'mean_b': sum(y for _, y in pairs) / n,
            'diff': mean_d, 't': t, 'p': p, 'se': se}


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def group_by_tag(runs, tasks):
    groups = {}
    for r in runs:
        tag = r.get('tag')
        if not tag:
            continue
        tid = r.get('id')
        task = tasks.get(tid)
        m = metrics_for_run(task, r)
        groups.setdefault(tag, {})[tid] = m
    return groups


def aggregate(group):
    out = {}
    for k in METRIC_KEYS:
        vals = [m[k] for m in group.values() if m.get(k) is not None]
        out[k] = (sum(vals) / len(vals)) if vals else None
        out[k + '_n'] = len(vals)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', required=True)
    ap.add_argument('--tag', help='只看某个 tag')
    ap.add_argument('--compare', nargs=2, metavar=('A', 'B'), help='配对比较两个 tag')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--min-n', type=int, default=30, help='样本量下限（默认 30 ✓ 原则三 M2）')
    ap.add_argument('--eval-set', help='评测集文件（如 docs/EVAL_SET_32.json ✓ 只算这些任务 ✓）')
    args = ap.parse_args()

    runs = [json.loads(l) for l in open(args.runs, encoding='utf-8') if l.strip()]
    tasks = load_tasks()
    if args.eval_set:
        es = Path(args.eval_set)
        if not es.is_absolute():
            es = ROOT / es
        try:
            esd = json.loads(es.read_text(encoding='utf-8'))
            allowed = {t['id'] for t in esd.get('tasks', [])}
            before = len(runs)
            runs = [r for r in runs if r.get('id') in allowed]
            print(f'  评测集过滤: {before} → {len(runs)} 条（{es.name} n={len(allowed)} ✓）')
        except Exception as e:
            print('  ⚠ 评测集读取失败:', e)
    groups = group_by_tag(runs, tasks)

    if args.tag:
        if args.tag not in groups:
            print('✗ 没找到 tag:', args.tag, '（可用:', ', '.join(groups.keys())[:200], '）')
            return 1
        g = groups[args.tag]
        agg = aggregate(g)
        print(f'\n=== tag={args.tag} · 任务数={len(g)} ===')
        for k in METRIC_KEYS:
            v = agg[k]
            print(f'  {METRIC_NAMES[k]:<16} {("—" if v is None else f"{v:.3f}") if k != "rounds" else ("—" if v is None else f"{v:.2f}"):>10}  (n={agg[k+"_n"]})')
        if len(g) < args.min_n:
            print(f'  ⚠ 样本量 {len(g)} < {args.min_n}（原则三 M2 ✗ t 值不可信 ⚠）')
        if args.json:
            print(json.dumps(agg, ensure_ascii=False, indent=1))
        return 0

    if args.compare:
        a_tag, b_tag = args.compare
        for t in (a_tag, b_tag):
            if t not in groups:
                print(f'✗ 没找到 tag: {t}（可用:', ', '.join(groups.keys()), '）')
                return 1
        ids = sorted(set(groups[a_tag]) & set(groups[b_tag]))
        print(f'\n=== 配对比较: {a_tag}(A) vs {b_tag}(B) · 共同任务 {len(ids)} 条 ===')
        if len(ids) < args.min_n:
            print(f'  ⚠ n={len(ids)} < {args.min_n} ⇒ t 值不可信 ⚠（原则三 M2）')
        rows = []
        print(f'  {"指标":<16}{"A":>10}{"B":>10}{"Δ(B-A)":>10}{"t":>9}{"p":>9}{"n":>5}')
        print(f'  {"-"*16}{"-"*10}{"-"*10}{"-"*10}{"-"*9}{"-"*9}{"-"*5}')
        for k in METRIC_KEYS:
            a = [groups[a_tag][i].get(k) for i in ids]
            b = [groups[b_tag][i].get(k) for i in ids]
            r = ttest_paired(a, b)
            if not r:
                continue
            t_s = ('—' if r['t'] is None else f"{r['t']:.2f}")
            p_s = ('—' if r['p'] is None else f"{r['p']:.3f}")
            print(f'  {METRIC_NAMES[k]:<16}{r["mean_a"]:>10.3f}{r["mean_b"]:>10.3f}{r["diff"]:>+10.3f}{t_s:>9}{p_s:>9}{r["n"]:>5}')
            rows.append({'metric': k, 'name': METRIC_NAMES[k], **r})
        print()
        print('  ⚠ |t| > 2 才算显著 ✓（原则三 M5）')
        print('  ⚠ n < 30 ⇒ 只报差，不下显著结论 ⚠')
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    print('✗ 需要 --tag 或 --compare')
    print('  可用 tag:', ', '.join(sorted(groups.keys())))
    return 1


if __name__ == '__main__':
    sys.exit(main())
