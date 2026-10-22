#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootstrap_ci.py — Bootstrap 置信区间（补 V3.0 缺口 · 2026-10-21）

★ 为什么需要: EVAL_PROTOCOL M5 要求"报均分+差+t+p+n" ✓ 但缺**置信区间** ✗
   t 检验给 p 值 ✓ 但没说"效应有多大、区间多宽" ✗
   Bootstrap 给 **效应量 + 95% CI** ✓ 更适合小样本（n=15~50 ✓）

★ 方法: 配对 Bootstrap（percentile method ✓ 10000 次重采样 ✓ 固定种子可复现 ✓）

用法:
  # 从两个 JSON 数组（配对）
  python scripts/bootstrap_ci.py --a a.json --b b.json
  # 或命令行直接给
  python scripts/bootstrap_ci.py --a "0.6,0.8,0.4" --b "0.8,0.9,0.6"
  # 从 eval_v2 判分结果（自动按 tag 配对）
  python scripts/bootstrap_ci.py --runs E:/WSL/_check/eval_v2/out/runs_v2.jsonl --tag-a 7b_kb0 --tag-b 7b_kb1
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# ══════════════════════════════════════════════════════════
# 核心：配对 Bootstrap ✓
# ══════════════════════════════════════════════════════════
def paired_bootstrap(a: list[float], b: list[float], n_boot: int = 10000,
                     ci: float = 0.95, seed: int = 42) -> dict:
    """配对 Bootstrap：估计 mean(b−a) 的置信区间 ✓

    Args:
        a, b: 配对样本（等长 ✓）
        n_boot: 重采样次数 ✓
        ci: 置信水平 ✓
        seed: 固定种子（可复现 ✓）

    Returns:
        {diff, ci_lo, ci_hi, p_two_sided, significant, ...}
    """
    assert len(a) == len(b), '配对样本必须等长 ✗'
    n = len(a)
    if n < 2:
        return {'error': '样本太少（n<2）✗', 'n': n}
    diffs = [b[i] - a[i] for i in range(n)]
    obs = sum(diffs) / n
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        s = 0.0
        for _i in range(n):
            s += diffs[rng.randrange(n)]
        boots.append(s / n)
    boots.sort()
    alpha = (1 - ci) / 2
    lo = boots[int(alpha * n_boot)]
    hi = boots[int((1 - alpha) * n_boot) - 1]
    # 双侧 p（Bootstrap 版）：零假设 diff=0 ⇒ 看 0 落在分布哪一侧 ✓
    # ★ 修（2026-10-21）: 全零差时 n_neg=n_boot ⇒ p=0 ✗ 应为 1.0 ✓（零假设完全成立）
    if all(abs(d) < 1e-12 for d in diffs):
        p = 1.0
    else:
        n_neg = sum(1 for x in boots if x <= 0)
        n_pos = n_boot - n_neg
        p = 2 * min(n_neg, n_pos) / n_boot
        # 加上观测值本身的修正（避免 p=0 的绝对化 ✗）
        p = max(p, 1.0 / n_boot)
        p = min(p, 1.0)
    # 效应量（Cohen's d，配对）✓
    import math
    mean_d = obs
    var = sum((d - mean_d) ** 2 for d in diffs) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    d = mean_d / sd if sd > 0 else 0.0
    return {
        'n': n,
        'mean_a': sum(a) / n,
        'mean_b': sum(b) / n,
        'diff': obs,
        'ci_lo': lo,
        'ci_hi': hi,
        'ci_level': ci,
        'p_bootstrap': p,
        'cohen_d': d,
        'significant': lo > 0 or hi < 0,          # CI 不含 0 ⇒ 显著 ✓
        'n_boot': n_boot,
        'seed': seed,
        'diffs': diffs,
    }


# ══════════════════════════════════════════════════════════
# 从 eval_v2 runs 提取每题的"通过与否" ✓
# ══════════════════════════════════════════════════════════
def load_runs_by_tag(path: pathlib.Path, tag: str) -> dict:
    """从 runs_v2.jsonl 取某 tag 的逐题结果 ✓"""
    out = {}
    if not path.exists():
        return out
    for line in path.open(encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('tag') != tag:
            continue
        if r.get('placeholder'):
            continue
        tid = r.get('id')
        if not tid:
            continue
        # 判定通过 ✓（同 eval_v2_score.py 口径 ✓）
        ok = bool(r.get('ok')) and (r.get('exec_count') or 0) > 0 \
             and not r.get('escalated') and not r.get('error')
        out.setdefault(tid, []).append(1.0 if ok else 0.0)
    return {k: sum(v) / len(v) for k, v in out.items()}   # 每题通过率 ✓


def main() -> int:
    ap = argparse.ArgumentParser(description='配对 Bootstrap 置信区间')
    ap.add_argument('--a', default='', help='A 组（JSON 数组文件 或 逗号分隔数字）')
    ap.add_argument('--b', default='', help='B 组')
    ap.add_argument('--runs', default='', help='eval_v2 runs_v2.jsonl 路径')
    ap.add_argument('--tag-a', default='', help='A 臂 tag')
    ap.add_argument('--tag-b', default='', help='B 臂 tag')
    ap.add_argument('--n-boot', type=int, default=10000)
    ap.add_argument('--ci', type=float, default=0.95)
    ap.add_argument('--seed', type=int, default=42)
    a = ap.parse_args()

    A, B, label_a, label_b = None, None, 'A', 'B'

    if a.runs and a.tag_a and a.tag_b:
        # ★ 从跑批结果配对 ✓
        p = pathlib.Path(a.runs)
        ra = load_runs_by_tag(p, a.tag_a)
        rb = load_runs_by_tag(p, a.tag_b)
        common = sorted(set(ra) & set(rb))
        if not common:
            print('  ✗ 没有共同任务（tag 不对？）')
            print('    %s: %d 题 · %s: %d 题' % (a.tag_a, len(ra), a.tag_b, len(rb)))
            return 1
        A = [ra[t] for t in common]
        B = [rb[t] for t in common]
        label_a, label_b = a.tag_a, a.tag_b
        print('  配对任务: %d 条（%s vs %s）' % (len(common), a.tag_a, a.tag_b))
        print('  任务: %s' % ', '.join(common[:12]))
    elif a.a and a.b:
        def parse(x):
            pp = pathlib.Path(x)
            if pp.exists():
                d = json.loads(pp.read_text(encoding='utf-8'))
                return [float(v) for v in (d if isinstance(d, list) else d.get('values'))]
            return [float(v) for v in x.split(',') if v.strip()]
        A, B = parse(a.a), parse(a.b)
    else:
        print('  用法见 --help')
        return 1

    if len(A) != len(B):
        print('  ✗ 两组不等长（%d vs %d）✗ 配对 Bootstrap 需要等长' % (len(A), len(B)))
        return 1

    res = paired_bootstrap(A, B, a.n_boot, a.ci, a.seed)
    if 'error' in res:
        print('  ✗ %s' % res['error'])
        return 1

    print('')
    print('=' * 66)
    print('  配对 Bootstrap（%s vs %s）' % (label_a, label_b))
    print('=' * 66)
    print('  n            = %d 对' % res['n'])
    print('  %-12s = %.4f' % (label_a, res['mean_a']))
    print('  %-12s = %.4f' % (label_b, res['mean_b']))
    print('  ★ 差值       = **%+.4f**' % res['diff'])
    print('  ★ %.0f%% CI    = [%+.4f, %+.4f]' % (res['ci_level'] * 100, res['ci_lo'], res['ci_hi']))
    print('  Bootstrap p  = %.4f' % res['p_bootstrap'])
    print('  Cohen d      = %.3f' % res['cohen_d'])
    print('  ★ 显著？     = %s' % ('✅ **是**（CI 不含 0 ✓）' if res['significant'] else '❌ **否**（CI 含 0 ✗）'))
    print('  重采样       = %d 次 · seed=%d（可复现 ✓）' % (res['n_boot'], res['seed']))
    print('')
    print('  ★ 结论: %s' % (
        'B 显著优于 A ✓（效应 %.2f 个标准差）' % res['cohen_d'] if res['significant'] and res['diff'] > 0 else
        'B 显著差于 A ✗（效应 %.2f 个标准差）' % res['cohen_d'] if res['significant'] else
        '**无显著差异** ✗（CI 含 0 · 不能声称有提升 ✓ 也不能声称无效 ⚠ 需更大样本）'))
    out = pathlib.Path('/mnt/e/WSL/_check/eval_v2/out')
    out.mkdir(parents=True, exist_ok=True)
    (out / ('bootstrap_%s_vs_%s.json' % (label_a, label_b))).write_text(
        json.dumps({**res, 'label_a': label_a, 'label_b': label_b}, ensure_ascii=False, indent=2),
        encoding='utf-8')
    print('  已存: bootstrap_%s_vs_%s.json' % (label_a, label_b))
    return 0


if __name__ == '__main__':
    sys.exit(main())
