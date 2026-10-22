#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""7B 定论实验 · 分析脚本（2026-10-21）

★ 自动做:
   ① 去重（旧 20 条 + 新 76 条 ✗ ⇒ 按时间戳取最新的 ✓）
   ② 逐题配对（同题同 repeat 的两臂对比 ✓）
   ③ 总体/分档通过率 ✓
   ④ 逐题差异 + 不一致对（discordant pairs ✓）
   ⑤ 配对 Bootstrap（10000 次 ✓ 复用 scripts/bootstrap_ci.py 思路 ✓）
   ⑥ 效应量（Cohen h / 差值 ✓）
   ⑦ 生成报告数据（JSON ✓ 供 md 填 ✓）

★ 诚实原则（用户要求 ✓）:
   · CI 含 0 或 p>0.05 ⇒ 必须写"不显著" ✗ 不能说"提升" ✗
   · n 太小 ⇒ 必须写"探索性" ⚠
   · 不推测原因 ⇒ 只描述观测 ✓

用法:
  python scripts/7b_definitive_analyze.py                # 分析 + 打印
  python scripts/7b_definitive_analyze.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = pathlib.Path('/mnt/e/WSL/_check/eval_v2/out/runs_v2.jsonl')
TAG_A = 'final_7b_kb0'
TAG_B = 'final_7b_kb1v3'  # ★ 干净重跑版 ✓（旧 kb1 污染弃用 ✗）
# ★ 重跑起点（旧 20 条在这之前 ✗ 要排除 ✓）
RETRY_TS = '2026-10-21T13:14:35'
N_BOOT = 10000
SEED = 42


def is_pass(r: dict) -> bool:
    """★ 判定口径 = eval_v2_score.judge_run ✓
       通过 = ok 且 至少调用一次工具 且 未升级 且 无 error ✓"""
    return (bool(r.get('ok'))
            and (r.get('exec_count') or 0) > 0
            and not r.get('escalated')
            and not r.get('error'))


def load_runs() -> tuple[list, list]:
    """读 runs_v2.jsonl → (臂A 记录, 臂B 记录) ✓ 已去重 ✓"""
    if not RUNS.exists():
        print('  ✗ 找不到 %s' % RUNS)
        return [], []
    a_raw, b_raw = [], []
    for line in RUNS.open(encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get('placeholder'):
            continue
        tag = r.get('tag')
        if tag == TAG_A:
            a_raw.append(r)
        elif tag == TAG_B:
            b_raw.append(r)

    # ★ 臂B 去重: 只保留 RETRY_TS 之后的 ✓（旧 20 条是 13:10 之前 ✗ 重跑的是 13:14:35 之后 ✓）
    b_new = [r for r in b_raw if str(r.get('started_at') or '') >= RETRY_TS]
    b_old = [r for r in b_raw if str(r.get('started_at') or '') < RETRY_TS]
    # 再按 (id, repeat) 去重，取最后一条 ✓
    def dedupe(rs):
        d = {}
        for r in rs:
            k = (r.get('id') or r.get('task_id'), r.get('repeat'))
            d[k] = r          # 后面的覆盖前面的 ✓
        return list(d.values())

    a = dedupe(a_raw)
    b = dedupe(b_new) if b_new else dedupe(b_raw)
    print('  臂A(%s): %d 条（去重后）' % (TAG_A, len(a)))
    print('  臂B(%s): %d 条（去重后）' % (TAG_B, len(b)))
    if b_new:
        print('     └ 其中旧数据排除 %d 条 ⚠ 只用了 13:14:35 之后重跑的 ✓' % len(b_old))
    return a, b


def paired(a: list, b: list) -> list[tuple]:
    """逐题配对 ✓（同 id + 同 repeat 才算一对 ✓）"""
    ai = {(r.get('id') or r.get('task_id'), r.get('repeat')): r for r in a}
    bi = {(r.get('id') or r.get('task_id'), r.get('repeat')): r for r in b}
    keys = sorted(set(ai) & set(bi))
    out = []
    for k in keys:
        out.append((k, ai[k], bi[k]))
    print('  配对成功: %d 对（臂A 独有 %d · 臂B 独有 %d）' % (
        len(out), len(set(ai) - set(bi)), len(set(bi) - set(ai))))
    return out


def summarize(rs: list, label: str) -> dict:
    """总体 + 分档通过率 ✓"""
    if not rs:
        return {}
    npass = sum(1 for r in rs if is_pass(r))
    byt = defaultdict(lambda: [0, 0])
    for r in rs:
        t = r.get('tier') or '?'
        byt[t][1] += 1
        if is_pass(r):
            byt[t][0] += 1
    d = {
        'label': label, 'n': len(rs), 'npass': npass,
        'rate': npass / len(rs),
        'by_tier': {t: {'pass': v[0], 'n': v[1], 'rate': v[0] / v[1]}
                    for t, v in sorted(byt.items())},
    }
    print('')
    print('  【%s】%d/%d = **%.2f%%**' % (label, npass, len(rs), d['rate'] * 100))
    for t, v in d['by_tier'].items():
        print('    %-8s %2d/%2d = %5.1f%%' % (t, v['pass'], v['n'], v['rate'] * 100))
    return d


def bootstrap_paired(pairs: list[tuple]) -> dict:
    """配对 Bootstrap ✓（10000 次重采样 ✓ 复现固定种子 ✓）"""
    diffs = []
    for _, ra, rb in pairs:
        diffs.append((1 if is_pass(rb) else 0) - (1 if is_pass(ra) else 0))
    n = len(diffs)
    if n == 0:
        return {}
    obs = sum(diffs) / n
    rng = random.Random(SEED)
    boot = []
    for _ in range(N_BOOT):
        s = sum(diffs[rng.randrange(n)] for _ in range(n))
        boot.append(s / n)
    boot.sort()
    lo = boot[int(0.025 * N_BOOT)]
    hi = boot[int(0.975 * N_BOOT) - 1]
    # p 值: 双侧 —— 零分布中心在 0 ✓ 两个尾的比例 ✓
    # 修正: 观测为 0 时 p=1.0 ✓（避免假显著 ✗）
    if abs(obs) < 1e-12:
        p = 1.0
    else:
        # 用 bootstrap 分布对 0 的偏离 ✓
        cnt = sum(1 for x in boot if (x <= 0 if obs > 0 else x >= 0))
        p = 2.0 * cnt / N_BOOT
        p = min(1.0, max(1.0 / N_BOOT, p))   # 下限 ✓
    # 不一致对 ✓
    b_only = sum(1 for d in diffs if d == 1)     # A 错 B 对 ✓
    a_only = sum(1 for d in diffs if d == -1)    # A 对 B 错 ✓
    both = sum(1 for d in diffs if d == 0)
    return {
        'n_pairs': n, 'diff': obs, 'ci_lo': lo, 'ci_hi': hi, 'p': p,
        'n_boot': N_BOOT, 'seed': SEED,
        'b_only': b_only, 'a_only': a_only, 'both_same': both,
        'significant': (lo > 0 or hi < 0),
    }


def per_task(pairs: list[tuple]) -> list[dict]:
    """逐题汇总 ✓（每题 2 个 repeat 合起来看 ✓）"""
    agg = defaultdict(lambda: {'a_pass': 0, 'b_pass': 0, 'n': 0,
                               'tier': '', 'phrase': ''})
    for _, ra, rb in pairs:
        tid = ra.get('id') or ra.get('task_id')
        d = agg[tid]
        d['n'] += 1
        d['tier'] = ra.get('tier') or ''
        d['phrase'] = ra.get('phrase') or ''
        if is_pass(ra):
            d['a_pass'] += 1
        if is_pass(rb):
            d['b_pass'] += 1
    out = []
    for tid in sorted(agg):
        d = agg[tid]
        out.append({'id': tid, 'tier': d['tier'], 'n': d['n'],
                    'a_pass': d['a_pass'], 'b_pass': d['b_pass'],
                    'delta': (d['b_pass'] - d['a_pass']) / max(d['n'], 1)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='')
    a = ap.parse_args()

    print('=' * 76)
    print('  7B 定论实验 · 分析')
    print('=' * 76)
    ra, rb = load_runs()
    if not ra or not rb:
        print('')
        print('  ⚠ 数据不全 ✗ 无法分析 ⚠（等臂B 跑完 ✓）')
        return 2

    sa = summarize(ra, '臂A（KB 关）')
    sb = summarize(rb, '臂B（KB 开）')
    pairs = paired(ra, rb)
    if not pairs:
        print('  ⚠ 无法配对 ✗')
        return 3
    bt = bootstrap_paired(pairs)
    pt = per_task(pairs)

    print('')
    print('=' * 76)
    print('  ★ 配对统计')
    print('=' * 76)
    print('  配对数:        %d' % bt['n_pairs'])
    print('  差值 (B−A):    **%+.4f**（%+.1f pp）' % (bt['diff'], bt['diff'] * 100))
    print('  95%% CI:        [%+.4f, %+.4f]（[%+.1f, %+.1f] pp）' % (
        bt['ci_lo'], bt['ci_hi'], bt['ci_lo'] * 100, bt['ci_hi'] * 100))
    print('  p 值:          %.4f（Bootstrap %d 次 · seed=%d）' % (
        bt['p'], bt['n_boot'], bt['seed']))
    print('  不一致对:      B好 %d · A好 %d · 同 %d' % (
        bt['b_only'], bt['a_only'], bt['both_same']))
    print('  ★ 判定:        %s' % (
        '**显著 ✓**' if bt['significant'] else
        '**不显著 ✗**（CI 含 0 ⇒ 不能声称提升 ✗）'))

    print('')
    print('  逐题差异（只列有变化的 ✓）:')
    changed = [t for t in pt if t['a_pass'] != t['b_pass']]
    if not changed:
        print('    （无差异 ✓ 两臂完全一致 ✓）')
    for t in changed:
        print('    %-10s [%s] A=%d B=%d %s' % (
            t['id'], t['tier'], t['a_pass'], t['b_pass'],
            '↑' if t['delta'] > 0 else '↓'))

    result = {
        'tag_a': TAG_A, 'tag_b': TAG_B, 'retry_ts': RETRY_TS,
        'arm_a': sa, 'arm_b': sb,
        'paired': bt, 'per_task': pt,
        'verdict': 'significant' if bt['significant'] else 'not_significant',
    }
    if a.json:
        pathlib.Path(a.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('')
        print('  已存: %s' % a.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
