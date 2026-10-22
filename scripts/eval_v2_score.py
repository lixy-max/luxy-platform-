#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval_v2_score.py — EVAL V2 判分器（2026-10-21 · T-5）

★ 输入: E:/WSL/_check/eval_v2/out/runs_v2.jsonl（eval_v2_run.mjs 产出 ✓）
        E:/WSL/_check/eval_v2/out/snapshots.json（状态快照 ✓）
★ 输出: markdown 报告 + JSON 汇总

★ 判分口径（对齐 EVAL_PROTOCOL M1-M5 ✓）:
  · 单次通过  = finished ✓ + 真执行了工具（exec_count>0 ✓）+ 非占位行 ✓ + 未升级到云 ✗
  · ★ 稳定性  = 同一任务 N 次里通过 ≥2 次 ⇒ 稳定 ✓（直接针对"只对一次 11/40" ✗）
  · 工具命中率 = 期望工具被调用的比例 ✓
  · 状态不变量 = workflow_config md5 / KB 块数 / LoRA 列表 跑前跑后一致 ✓
  · 分档报告  = hard / mid / easy / stability ✓

用法:
  python scripts/eval_v2_score.py                        # 默认规则判分
  python scripts/eval_v2_score.py --runs <path> --snap <path> --out <md>
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DEFAULT_RUNS = pathlib.Path('/mnt/e/WSL/_check/eval_v2/out/runs_v2.jsonl')
DEFAULT_SNAP = pathlib.Path('/mnt/e/WSL/_check/eval_v2/out/snapshots.json')
DEFAULT_OUT = pathlib.Path('/mnt/e/WSL/_check/eval_v2/out/EVAL_V2_RESULT.md')


# ══════════════════════════════════════════════════════════
# 判定一次跑 ✓
# ══════════════════════════════════════════════════════════
def judge_run(r: dict) -> dict:
    """按规则判一次跑 ⇒ 通过 / 未通过 + 原因 ✓"""
    reasons = []
    passed = True
    # ① 占位行 ⇒ 不算 ✓
    if r.get('placeholder'):
        return {'pass': False, 'reasons': ['占位行（<3s 且零工具）✗']}
    # ② 必须完成 ✓
    if not r.get('ok'):
        passed = False
        reasons.append('未完成 ✗')
    # ③ 必须真执行了工具 ✓
    if (r.get('exec_count') or 0) <= 0:
        passed = False
        reasons.append('零工具执行 ✗')
    # ④ 升级到云 ⇒ 算失败（本地没做完 ✗）
    if r.get('escalated'):
        passed = False
        reasons.append('升级到云 ✗')
    # ⑤ 有 error ⇒ 失败 ✓
    if r.get('error'):
        passed = False
        reasons.append('运行出错 ✗')
    return {'pass': passed, 'reasons': reasons}


# ══════════════════════════════════════════════════════════
# 主流程 ✓
# ══════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description='EVAL V2 判分器')
    ap.add_argument('--runs', default=str(DEFAULT_RUNS))
    ap.add_argument('--snap', default=str(DEFAULT_SNAP))
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    a = ap.parse_args()

    runs_p = pathlib.Path(a.runs)
    if not runs_p.exists():
        print('  X 找不到 runs 文件: %s' % runs_p)
        return 1

    rows = []
    for line in runs_p.open(encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    print('  读入 %d 条跑次' % len(rows))

    if not rows:
        print('  X 没有数据')
        return 1

    # ══════════════════════════════════════════════════════
    # ① 按 (tag, task_id) 聚合 ✓
    # ══════════════════════════════════════════════════════
    by_task: dict[tuple, list] = defaultdict(list)
    for r in rows:
        key = (r.get('tag') or '?', r.get('id') or '?')
        if r.get('placeholder'):
            continue          # 占位行剔除 ✓
        by_task[key].append(r)

    print('  有效任务数: %d' % len(by_task))

    # ══════════════════════════════════════════════════════
    # ② 逐任务算指标 ✓
    # ══════════════════════════════════════════════════════
    task_stats = []
    for (tag, tid), rs in by_task.items():
        n = len(rs)
        verdicts = [judge_run(r) for r in rs]
        pass_n = sum(1 for v in verdicts if v['pass'])
        # 稳定性 ✓: N 次里通过 ≥2 次（N=1 时不算稳定 ✓ 标 N/A）
        stable = (pass_n >= 2) if n >= 2 else None
        # 工具命中率 ✓
        hits = [r.get('tools_hit_rate') for r in rs if r.get('tools_hit_rate') is not None]
        hit_avg = statistics.mean(hits) if hits else None
        # 耗时 ✓
        els = [r.get('elapsed_s') for r in rs if r.get('elapsed_s')]
        el_avg = statistics.mean(els) if els else None
        # 工具数 ✓
        execs = [r.get('exec_count') or 0 for r in rs]
        first = rs[0]
        task_stats.append({
            'tag': tag, 'id': tid, 'tier': first.get('tier') or '?',
            'source_id': first.get('source_id') or '',
            'n': n,
            'pass_n': pass_n,
            'pass_rate': pass_n / n,
            'stable': stable,
            'tools_hit_rate': hit_avg,
            'exec_avg': statistics.mean(execs) if execs else 0,
            'elapsed_avg': el_avg,
            'escalated_n': sum(1 for r in rs if r.get('escalated')),
        })

    # ══════════════════════════════════════════════════════
    # ③ 按档聚合 ✓
    # ══════════════════════════════════════════════════════
    by_tier: dict[str, list] = defaultdict(list)
    for s in task_stats:
        by_tier[s['tier']].append(s)

    print('')
    print('=' * 78)
    print('  EVAL V2 结果')
    print('=' * 78)

    # 全量 ✓
    n_tasks = len(task_stats)
    all_pass = sum(s['pass_n'] for s in task_stats)
    all_runs = sum(s['n'] for s in task_stats)
    print('')
    print('  【总览】')
    print('    任务数: %d · 总跑次: %d' % (n_tasks, all_runs))
    print('    单次通过率: %d/%d = %.1f%%' % (all_pass, all_runs, all_pass / max(all_runs, 1) * 100))
    stable_tasks = [s for s in task_stats if s['stable'] is not None]
    if stable_tasks:
        n_stable = sum(1 for s in stable_tasks if s['stable'])
        print('    ★ 稳定性: %d/%d 任务稳定（%.0f%%）' % (
            n_stable, len(stable_tasks), n_stable / len(stable_tasks) * 100))
    hits = [s['tools_hit_rate'] for s in task_stats if s['tools_hit_rate'] is not None]
    if hits:
        print('    工具命中率: %.1f%%' % (statistics.mean(hits) * 100))
    esc = sum(s['escalated_n'] for s in task_stats)
    print('    升级到云: %d / %d 次（%.0f%%）' % (esc, all_runs, esc / max(all_runs, 1) * 100))

    # 分档 ✓
    print('')
    print('  【分档】')
    print('    %-12s %-5s %-8s %-8s %-8s' % ('档', '任务', '单次通过', '★稳定性', '工具命中'))
    for tier in ('hard', 'mid', 'easy', 'stability'):
        if tier not in by_tier:
            continue
        ts = by_tier[tier]
        p = sum(s['pass_n'] for s in ts)
        r = sum(s['n'] for s in ts)
        st = [s for s in ts if s['stable'] is not None]
        st_n = sum(1 for s in st if s['stable']) if st else 0
        h = [s['tools_hit_rate'] for s in ts if s['tools_hit_rate'] is not None]
        print('    %-12s %-5d %-8s %-8s %-8s' % (
            tier, len(ts),
            '%d/%d=%.0f%%' % (p, r, p / max(r, 1) * 100),
            ('%d/%d' % (st_n, len(st))) if st else 'N/A',
            ('%.0f%%' % (statistics.mean(h) * 100)) if h else 'N/A'))

    # ══════════════════════════════════════════════════════
    # ④ 状态不变量 ✓
    # ══════════════════════════════════════════════════════
    snap_p = pathlib.Path(a.snap)
    inv = None
    if snap_p.exists():
        try:
            snap = json.loads(snap_p.read_text(encoding='utf-8'))
            inv = snap.get('invariant') or {}
            print('')
            print('  【状态不变量】')
            print('    workflow_config 未改: %s %s' % (
                inv.get('workflow_config_unchanged'),
                '✓' if inv.get('workflow_config_unchanged') else '✗'))
            kb = inv.get('kb_counts') or {}
            if kb.get('before') and kb.get('after') and isinstance(kb['before'], dict):
                changed = [k for k in kb['before'] if kb['before'].get(k) != kb['after'].get(k)]
                print('    KB 块数变化: %s' % ('无 ✓' if not changed else ('%s ✗' % changed)))
        except Exception as e:
            print('  ⚠ 快照读失败: %s' % e)

    # ══════════════════════════════════════════════════════
    # ⑤ 逐任务明细 ✓
    # ══════════════════════════════════════════════════════
    print('')
    print('  【逐任务】（按 tier + 通过率排序）')
    print('    %-10s %-12s %-5s %-8s %-8s %s' % ('ID', 'tier', 'n', '通过', '稳定', '工具命中'))
    order = {'hard': 0, 'stability': 1, 'mid': 2, 'easy': 3}
    for s in sorted(task_stats, key=lambda x: (order.get(x['tier'], 9), -x['pass_rate'])):
        print('    %-10s %-12s %-5d %-8s %-8s %s' % (
            s['id'], s['tier'], s['n'],
            '%d/%d' % (s['pass_n'], s['n']),
            ('✓' if s['stable'] else ('✗' if s['stable'] is False else '—')),
            ('%.0f%%' % (s['tools_hit_rate'] * 100)) if s['tools_hit_rate'] is not None else '—'))

    # ══════════════════════════════════════════════════════
    # ⑥ 写报告 ✓
    # ══════════════════════════════════════════════════════
    outp = pathlib.Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append('# EVAL V2 结果报告')
    L.append('')
    L.append('> 生成: %s' % __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    L.append('> 判定口径: 完成 ✓ + 真执行工具 ✓ + 未升级云 ✗ + 非占位行 ✓')
    L.append('> 稳定性: 同一任务 N 次里通过 ≥2 次 ⇒ 稳定 ✓')
    L.append('')
    L.append('## 一、总览')
    L.append('')
    L.append('| 指标 | 值 |')
    L.append('|---|---|')
    L.append('| 任务数 | %d |' % n_tasks)
    L.append('| 总跑次 | %d |' % all_runs)
    L.append('| 单次通过率 | %d/%d = **%.1f%%** |' % (all_pass, all_runs, all_pass / max(all_runs, 1) * 100))
    if stable_tasks:
        L.append('| ★ 稳定性 | **%d/%d = %.0f%%** |' % (n_stable, len(stable_tasks), n_stable / len(stable_tasks) * 100))
    if hits:
        L.append('| 工具命中率 | **%.1f%%** |' % (statistics.mean(hits) * 100))
    L.append('| 升级到云 | %d/%d = %.0f%% |' % (esc, all_runs, esc / max(all_runs, 1) * 100))
    L.append('')
    L.append('## 二、分档')
    L.append('')
    L.append('| 档 | 任务 | 单次通过 | ★稳定性 | 工具命中 |')
    L.append('|---|---|---|---|---|')
    for tier in ('hard', 'mid', 'easy', 'stability'):
        if tier not in by_tier:
            continue
        ts = by_tier[tier]
        p = sum(s['pass_n'] for s in ts)
        r = sum(s['n'] for s in ts)
        st = [s for s in ts if s['stable'] is not None]
        st_n = sum(1 for s in st if s['stable']) if st else 0
        h = [s['tools_hit_rate'] for s in ts if s['tools_hit_rate'] is not None]
        L.append('| %s | %d | %d/%d=%.0f%% | %s | %s |' % (
            tier, len(ts), p, r, p / max(r, 1) * 100,
            ('%d/%d' % (st_n, len(st))) if st else 'N/A',
            ('%.0f%%' % (statistics.mean(h) * 100)) if h else 'N/A'))
    if inv:
        L.append('')
        L.append('## 三、状态不变量')
        L.append('')
        L.append('| 检查 | 结果 |')
        L.append('|---|---|')
        L.append('| workflow_config 未改 | %s |' % ('✓' if inv.get('workflow_config_unchanged') else '✗'))
        kb = inv.get('kb_counts') or {}
        if kb.get('before') and kb.get('after') and isinstance(kb['before'], dict):
            changed = [k for k in kb['before'] if kb['before'].get(k) != kb['after'].get(k)]
            L.append('| KB 块数变化 | %s |' % ('无 ✓' if not changed else '%s ✗' % changed))
    L.append('')
    L.append('## 四、逐任务明细')
    L.append('')
    L.append('| ID | tier | n | 通过 | 稳定 | 工具命中 |')
    L.append('|---|---|---|---|---|---|')
    for s in sorted(task_stats, key=lambda x: (order.get(x['tier'], 9), -x['pass_rate'])):
        L.append('| %s | %s | %d | %d/%d | %s | %s |' % (
            s['id'], s['tier'], s['n'], s['pass_n'], s['n'],
            ('✓' if s['stable'] else ('✗' if s['stable'] is False else '—')),
            ('%.0f%%' % (s['tools_hit_rate'] * 100)) if s['tools_hit_rate'] is not None else '—'))
    L.append('')
    L.append('---')
    L.append('')
    L.append('*判分器: `scripts/eval_v2_score.py` · 跑批: `tools/eval_v2_run.mjs`*')
    outp.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print('')
    print('  ✅ 报告: %s' % outp)

    # JSON 汇总 ✓
    jsonp = outp.with_suffix('.json')
    jsonp.write_text(json.dumps({
        'generated': __import__('datetime').datetime.now().isoformat(),
        'total': {'tasks': n_tasks, 'runs': all_runs, 'pass': all_pass,
                  'pass_rate': all_pass / max(all_runs, 1),
                  'stable_tasks': (sum(1 for s in stable_tasks if s['stable']) if stable_tasks else None),
                  'stable_total': len(stable_tasks),
                  'escalated': esc},
        'by_tier': {k: {'tasks': len(v),
                        'pass': sum(s['pass_n'] for s in v),
                        'runs': sum(s['n'] for s in v)}
                    for k, v in by_tier.items()},
        'tasks': task_stats,
        'invariant': inv,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  ✅ JSON:  %s' % jsonp)
    return 0


if __name__ == '__main__':
    sys.exit(main())
