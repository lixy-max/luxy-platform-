#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A/B 混跑分析（闸门⑤ 因果效应 · 2026-10-19）

设计：同一次跑批内，每个任务连续跑两臂（相邻几分钟）
  臂 OFF: localStorage.ds_gate5='off' ⇒ 闸门⑤ 关闭
  臂 ON : 默认 ⇒ 闸门⑤ 开启
⇒ run-level 漂移对两臂同向影响 ⇒ 配对差里抵消 ✓

判定（事先定好）：
  |t| > 2 且 配对差均值 > 0 ⇒ 闸门⑤ 有净效应 ✓
  |t| ≤ 2                  ⇒ 无显著净效应 ✗
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drill_score as D          # noqa: E402
import drill_repeat as R         # noqa: E402


def analyze(tasks: dict, off: dict, on: dict, exclude: set) -> dict:
    common = sorted(set(off) & set(on) - exclude)
    rows = []
    for tid in common:
        t = tasks.get(tid)
        if not t:
            continue
        ro, rn = off[tid], on[tid]
        if not (R.is_valid_record(ro) and R.is_valid_record(rn)):
            continue
        try:
            so = D.score_machine(t, ro)['machine_score']
            sn = D.score_machine(t, rn)['machine_score']
        except Exception:
            continue
        rows.append({
            'id': tid, 'kind': t.get('kind', ''),
            'off': so, 'on': sn, 'diff': sn - so,
            'off_s': ro.get('elapsed_s'), 'on_s': rn.get('elapsed_s'),
            'off_tools': len(ro.get('exec_tools') or []),
            'on_tools': len(rn.get('exec_tools') or []),
        })
    diffs = [r['diff'] for r in rows]
    if not diffs:
        return {'n': 0, 'rows': []}
    n = len(diffs)
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    return {
        'n': n, 'rows': rows,
        'mean_off': statistics.mean([r['off'] for r in rows]),
        'mean_on': statistics.mean([r['on'] for r in rows]),
        'mean_diff': mean, 'sd_diff': sd,
        'sem': sd / math.sqrt(n) if n else 0,
        't': mean / (sd / math.sqrt(n)) if sd else 0.0,
        'up': len([d for d in diffs if d > 0]),
        'down': len([d for d in diffs if d < 0]),
        'same': len([d for d in diffs if d == 0]),
        'dt_off': statistics.mean([r['off_s'] for r in rows if r['off_s'] is not None] or [0]),
        'dt_on': statistics.mean([r['on_s'] for r in rows if r['on_s'] is not None] or [0]),
        'tools_off': statistics.mean([r['off_tools'] for r in rows]),
        'tools_on': statistics.mean([r['on_tools'] for r in rows]),
    }


def verdict(a: dict) -> str:
    if not a or not a.get('n'):
        return '数据不足 ✗'
    t = a['t']
    if abs(t) > 2 and a['mean_diff'] > 0:
        return '✅ 闸门⑤ 有正向净效应（t 显著 ✓）'
    if abs(t) > 2 and a['mean_diff'] < 0:
        return '⚠ 闸门⑤ 有负向净效应（t 显著但方向反 ✗）'
    return '❌ 无显著净效应（|t| ≤ 2 ✗）'


def main(argv=None):
    ap = argparse.ArgumentParser(description='A/B 混跑分析')
    ap.add_argument('--runs', default='/mnt/e/WSL/_check/drill/out/runs.jsonl')
    ap.add_argument('--off-tag', default='ab_off')
    ap.add_argument('--on-tag', default='ab_on')
    ap.add_argument('--tasks', default='docs/DRILL_TASKS_PART1.md')
    ap.add_argument('--exclude', default='P1-01,P1-02,P1-03,P1-04,P1-05')
    ap.add_argument('--out', default='docs/AB_RESULT.md')
    a = ap.parse_args(argv)

    tasks = {t['id']: t for t in D.parse_tasks(ROOT / a.tasks)}
    all_runs = R.load_runs(Path(a.runs))
    off = all_runs.get(a.off_tag, {})
    on = all_runs.get(a.on_tag, {})
    exclude = {s.strip() for s in a.exclude.split(',') if s.strip()}

    full = analyze(tasks, off, on, set())
    clean = analyze(tasks, off, on, exclude)

    L = ['# A/B 混跑结果（闸门⑤ 因果效应 · 2026-10-19）', '',
         f'> 臂 OFF = `{a.off_tag}`（`ds_gate5=off`）· 臂 ON = `{a.on_tag}`（默认开启）',
         '> 每任务两臂相邻执行 ⇒ run-level 漂移抵消 ✓',
         '> 判定标准（事先定 ✓）：|t| > 2 ⇒ 有净效应；|t| ≤ 2 ⇒ 无显著净效应', '']

    for name, res in ((f'干净数据（剔除 {len(exclude)} 个并行污染任务）', clean),
                      ('全量（含被污染任务 ⚠）', full)):
        L += [f'## {name}', '',
              '| 指标 | 数值 |', '|---|---|',
              f'| 配对任务数 | {res.get("n", 0)} |',
              f'| 均分 OFF → ON | {res.get("mean_off", 0):.2f} → {res.get("mean_on", 0):.2f} |',
              f'| **配对差均值（ON−OFF）** | **{res.get("mean_diff", 0):+.2f}** |',
              f'| 配对差 stdev | {res.get("sd_diff", 0):.2f} |',
              f'| SEM | {res.get("sem", 0):.2f} |',
              f'| **t 统计量** | **{res.get("t", 0):.2f}** |',
              f'| 提升 / 下降 / 持平 | {res.get("up", 0)} / {res.get("down", 0)} / {res.get("same", 0)} |',
              f'| 平均耗时 OFF → ON | {res.get("dt_off", 0):.1f}s → {res.get("dt_on", 0):.1f}s |',
              f'| 平均工具数 OFF → ON | {res.get("tools_off", 0):.1f} → {res.get("tools_on", 0):.1f} |',
              '', f'**判定: {verdict(res)}**', '']
        if res.get('rows'):
            L += ['<details><summary>逐任务明细（点开）</summary>', '',
                  '| 任务 | 类目 | OFF | ON | Δ | 耗时 OFF→ON | 工具 OFF→ON |',
                  '|---|---|---|---|---|---|---|']
            for r in sorted(res['rows'], key=lambda x: -x['diff']):
                L.append(f'| {r["id"]} | {r["kind"]} | {r["off"]:.0f} | {r["on"]:.0f} | '
                         f'{r["diff"]:+.0f} | {r["off_s"]}s→{r["on_s"]}s | {r["off_tools"]}→{r["on_tools"]} |')
            L += ['', '</details>', '']

    L += ['## 结论', '', f'- 干净数据：{verdict(clean)}（t={clean.get("t", 0):.2f}，Δ={clean.get("mean_diff", 0):+.2f}）',
          f'- 全量数据：{verdict(full)}（t={full.get("t", 0):.2f}，Δ={full.get("mean_diff", 0):+.2f}）']
    if clean.get('mean_diff') and abs(clean.get('t', 0)) > 2:
        L += ['', f'- 效应：均分 {clean["mean_diff"]:+.2f} 分 · 耗时 {clean.get("dt_off", 0):.1f}s → '
              f'{clean.get("dt_on", 0):.1f}s · 工具数 {clean.get("tools_off", 0):.1f} → {clean.get("tools_on", 0):.1f}']
    else:
        L += ['', '- 无显著净效应 ⇒ 只保留"定向修好几个具体案例"的说法 ✓ 不宣称整体提升 ✗']

    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f'✓ {out}')
    print(f'  干净: n={clean.get("n")} Δ={clean.get("mean_diff", 0):+.2f} t={clean.get("t", 0):.2f} ⇒ {verdict(clean)}')
    print(f'  全量: n={full.get("n")} Δ={full.get("mean_diff", 0):+.2f} t={full.get("t", 0):.2f} ⇒ {verdict(full)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
