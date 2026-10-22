#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演练重复性验证（M-1 · 2026-10-19）

## 为什么需要（方法论漏洞 ✓）
实测 449 条记录里，同一 (tag, 任务) 重复跑 = **0 组** ✗
而换说法实验已证明单次波动 stdev ≈ 20 分 ✗
⇒ 之前"闸门⑤ +6.45 分"的结论【没有置信区间】✗ 可能一部分是运气 ✓

## 这个脚本做什么
1. 对同一配置的 N 次重复跑（tag 列表 ✓）逐任务打分 ✓
2. 计算:
   · 每次跑的整体均值/合规率 ✓
   · **跨次均值 ± 标准差**（答案的"噪音"✓）
   · **逐任务跨次 stdev**（哪些任务波动大 ✓）
   · 与基线（单次）的**配对差 + t 统计量** ✓
3. 输出判定: 提升是否大于噪音 ✓（|差| > 2×SEM ⇒ 认为显著 ✓）

## 用法
  python scripts/drill_repeat.py --tags p1gate5,p1gate5_r2,p1gate5_r3 --baseline p1full \
      --tasks docs/DRILL_TASKS_PART1.md --out docs/EVAL_REPEATABILITY.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import drill_score as D  # noqa: E402

COMPLIANT = 42


def load_runs(jsonl_path: Path) -> dict:
    """tag → {task_id: run}（同 tag 多条取最近 ✓）"""
    out = {}
    if not jsonl_path.exists():
        return out
    for line in jsonl_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        tag = r.get('tag') or 'none'
        tid = r.get('id')
        bucket = out.setdefault(tag, {})
        # ★ P-60 去重（2026-10-19）: 同一任务可能有多条（重跑/补跑 ✓）
        #   规则: 有【有效】记录就保留最后一条有效 ✓ 没有才留最后一条任意记录 ✓
        prev = bucket.get(tid)
        if prev is None or is_valid_record(r) or not is_valid_record(prev):
            bucket[tid] = r
    return out


def is_valid_record(run: dict) -> bool:
    """★ P-60（2026-10-19 子代理发现）: 瞬时占位/失败行不算有效成绩 ✗
    特征: msgs_len==0（零对话）或 elapsed_s 为 null 或 ok==false 且耗时 <3s
    """
    try:
        msgs = run.get('msgs')
        if msgs is not None and len(str(msgs)) == 0:
            return False
        if run.get('msgs_len') is not None and int(run.get('msgs_len') or 0) == 0:
            return False
        el = run.get('elapsed_s')
        if el is None and not (run.get('api_tools') or []):
            return False
        if el is not None and float(el) < 3 and not (run.get('api_tools') or []):
            return False
        return True
    except Exception:
        return True


def score_tag(tasks: dict, runs: dict) -> dict:
    """→ {task_id: machine_score} + 整体统计"""
    per = {}
    skipped = 0
    for tid, run in runs.items():
        t = tasks.get(tid)
        if not t:
            continue
        if not is_valid_record(run):      # ★ P-60: 占位行 → 不算成绩 ✗
            skipped += 1
            continue
        try:
            m = D.score_machine(t, run)
            per[tid] = m['machine_score']
        except Exception:
            continue
    vals = list(per.values())
    ok = [v for v in vals if v >= COMPLIANT]
    return {
        'per_task': per,
        'n': len(vals),
        'skipped_invalid': skipped,
        'mean': round(statistics.mean(vals), 2) if vals else None,
        'stdev': round(statistics.stdev(vals), 2) if len(vals) > 1 else None,
        'compliance': round(100 * len(ok) / len(vals), 1) if vals else None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description='演练重复性验证')
    ap.add_argument('--runs', default='/mnt/e/WSL/_check/drill/out/runs.jsonl')
    ap.add_argument('--tags', default='p1gate5,p1gate5_r2,p1gate5_r3', help='重复跑的 tag 列表 ✓')
    ap.add_argument('--baseline', default='p1full', help='基线 tag（单次参考 ✓）')
    ap.add_argument('--tasks', default='docs/DRILL_TASKS_PART1.md')
    ap.add_argument('--out', default='docs/EVAL_REPEATABILITY.md')
    a = ap.parse_args(argv)

    task_list = D.parse_tasks(ROOT / a.tasks)
    tasks = {t['id']: t for t in task_list}
    all_runs = load_runs(Path(a.runs))

    tags = [t.strip() for t in a.tags.split(',') if t.strip()]
    arms = {}
    for tag in tags:
        if tag not in all_runs:
            print(f'  ⚠ tag {tag} 没有数据（可能还在跑 ✓）')
            continue
        arms[tag] = score_tag(tasks, all_runs[tag])

    base = score_tag(tasks, all_runs.get(a.baseline, {})) if a.baseline in all_runs else None

    L = ['# 演练重复性验证（M-1 · 2026-10-19）', '',
         f'> 目的：量化"闸门⑤ +6.45 分"这个提升**是否大于噪音** ✓',
         f'> 方法：同配置重复跑 {len(arms)} 次 + 逐任务配对 + 跨次 stdev + t 统计量', '']

    # ---- 一、各次跑的总体 ----
    L += ['## 一、每次跑的总体（同 50 条任务 ✓）', '',
          '| 次数 | tag | 条数 | 均分 | 单次内 stdev | 合规率(≥42) |', '|---|---|---|---|---|---|']
    if base:
        L.append(f'| 基线 | `{a.baseline}` | {base["n"]} | **{base["mean"]}** | '
                 f'{base["stdev"]} | {base["compliance"]}% |')
    for tag, s in arms.items():
        _skip = ('（跳过无效 %d 条）' % s.get('skipped_invalid', 0)) if s.get('skipped_invalid') else ''
        L.append(f'| {tag} | `{tag}` | {s["n"]} | **{s["mean"]}** | {s["stdev"]} | {s["compliance"]}% |' + _skip)

    # ---- 二、跨次统计（核心 ✓）----
    means = [s['mean'] for s in arms.values() if s['mean'] is not None]
    comps = [s['compliance'] for s in arms.values() if s['compliance'] is not None]
    L += ['', '## 二、跨次统计（★ 核心：答案的"噪音"有多大）', '']
    if len(means) > 1:
        m_mean = statistics.mean(means)
        m_sd = statistics.stdev(means)
        sem = m_sd / math.sqrt(len(means))
        c_mean = statistics.mean(comps)
        c_sd = statistics.stdev(comps)
        L += ['| 指标 | 均值 | 跨次 stdev | SEM（标准误） | 95% 区间 |', '|---|---|---|---|---|']
        L.append(f'| 均分 | **{m_mean:.2f}** | {m_sd:.2f} | {sem:.2f} | '
                 f'{m_mean - 1.96 * sem:.2f} ~ {m_mean + 1.96 * sem:.2f} |')
        L.append(f'| 合规率 | **{c_mean:.1f}%** | {c_sd:.1f}pp | {c_sd / math.sqrt(len(comps)):.1f}pp | — |')
    else:
        m_mean = means[0] if means else None
        m_sd = None
        L.append('（只有 1 次数据 ⇒ 无法算跨次 stdev ✗ 等重复跑完 ✓）')

    # ---- 三、与基线配对检验 ----
    L += ['', '## 三、与基线的配对检验（★ 提升是否大于噪音）', '']
    if base and arms:
        # 用所有重复跑的逐任务均值 vs 基线 ✓
        common = set(base['per_task'])
        for s in arms.values():
            common &= set(s['per_task'])
        common = sorted(common)
        if common:
            base_vals = [base['per_task'][t] for t in common]
            rep_vals = [statistics.mean([s['per_task'][t] for s in arms.values()]) for t in common]
            diffs = [r - b for r, b in zip(rep_vals, base_vals)]
            n = len(diffs)
            d_mean = statistics.mean(diffs)
            d_sd = statistics.stdev(diffs) if n > 1 else 0.0
            t = d_mean / (d_sd / math.sqrt(n)) if d_sd else 0.0
            L += [f'- 配对任务数: **{n}**',
                  f'- 平均提升: **{d_mean:+.2f} 分**（逐任务配对 ✓）',
                  f'- 逐任务差 stdev: **{d_sd:.2f}**',
                  f'- **t 统计量: {t:.2f}**（|t| > 2 ⇒ 在 95% 水平上显著 ✓）',
                  f'- 提升/下降任务数: 提升 {len([d for d in diffs if d > 0])} · 下降 {len([d for d in diffs if d < 0])} · 持平 {len([d for d in diffs if d == 0])}']
            # 判定
            if m_sd is not None and m_sd > abs(d_mean):
                L += ['', '### ⚠ 判定: **提升小于跨次波动** ⇒ 不能声称显著 ✗']
            elif abs(t) > 2:
                L += ['', '### ✅ 判定: **提升在 95% 水平显著**（|t| > 2 ✓）']
            else:
                L += ['', '### ⚠ 判定: **提升不显著**（|t| ≤ 2 ✗）需要更多重复 ✓']
            # 波动最大的任务
            per_task_sd = []
            for t_id in common:
                vals = [s['per_task'][t_id] for s in arms.values() if t_id in s['per_task']]
                if len(vals) > 1:
                    per_task_sd.append((t_id, statistics.stdev(vals), statistics.mean(vals)))
            per_task_sd.sort(key=lambda x: -x[1])
            L += ['', '### 跨次波动最大的任务（说明判分对这些任务不稳 ✓）', '',
                  '| 任务 | 跨次 stdev | 均值 |', '|---|---|---|']
            for tid, sd, mn in per_task_sd[:10]:
                L.append(f'| {tid} | {sd:.1f} | {mn:.1f} |')
    L += ['', '## 四、诚实标注', '',
          '- 重复性验证的样本量仍小（3 次 ✓ 95% 区间较宽 ⚠）',
          '- 单次内 stdev 反映任务难度差异 ✓ 跨次 stdev 才是"噪音" ✓',
          '- 基线只有 1 次 ✗ ⇒ 配对检验用"重复跑的均值 vs 基线单次" ✓ 保守 ✓'
          if base else '- 基线缺失 ⚠']

    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f'✓ {out}')
    if len(means) > 1:
        print(f'  跨次: 均分 {m_mean:.2f} ± {m_sd:.2f}')
    for tag, s in arms.items():
        print(f'  {tag}: {s["mean"]} / {s["compliance"]}%')
    return 0


if __name__ == '__main__':
    sys.exit(main())
