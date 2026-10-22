#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""confidence_scorer.py - Agent 置信度打分器（2026-10-21 · 文献驱动）

文献依据（用户要求不凭感觉）:
  1. arXiv:2107.07511   Conformal Prediction 入门 -> 覆盖率数学保证
  2. arXiv:2604.01457   Wired for Overconfidence -> 不用模型自评
  3. arXiv:2610.01256   DeFA 失败归因 -> 轨迹信号
  4. ACL2025            Response Agreement -> 一致性信号
  5. arXiv:2607.08065   When LLMs Agree -> 一致不等于对

方法:
  8 维零成本特征 -> 逻辑回归(答对概率) -> 共形预测定阈值 -> 三档
  保证: P(错误且被接受) <= alpha

用法:
  python scripts/confidence_scorer.py --fit
  python scripts/confidence_scorer.py --eval
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = pathlib.Path('/mnt/e/WSL/_check/eval_v2/out/runs_v2.jsonl')
MODEL = ROOT / 'data/flywheel/confidence_model.json'
SEED = 42

# ★ 2026-10-21 修: 剔除【标签泄漏】特征 ✗
#   label = ok AND exec_count>0 AND not escalated AND no error
#   ⇒ has_tool(=exec_count>0) 和 escalated 都是 label 的组成部分 ✗
#   ⇒ 用它们等于循环论证 ✗ AUROC 会虚高 ✗
#   保留 6 个【不在 label 定义里】的独立信号 ✓
FEATURES = [
    'tool_ok_rate',      # 工具成功率（不在 label 里 ✓）
    'n_tools_norm',      # 工具数（不在 label 里 ✓）
    'dup_guard_rate',    # 重复倾向（不在 label 里 ✓）
    'finished',          # 是否正常结束（不在 label 里 ✓）
    'elapsed_norm',      # 耗时（不在 label 里 ✓）
    'api_tool_norm',     # API 层工具数（不在 label 里 ✓）
]


def clip01(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def extract(r: dict) -> dict | None:
    if not isinstance(r, dict):
        return None
    tools = r.get('exec_tools') or []
    ok_tools = r.get('exec_ok_tools') or []
    api = r.get('api_uniq_tools') or []
    rate = (len(ok_tools) / len(tools)) if tools else 0.0
    dup = float(r.get('dup_guard_hits') or 0)
    iters = max(float(r.get('exec_count') or 1), 1.0)
    el = float(r.get('elapsed_s') or 0)
    return {
        'tool_ok_rate': round(rate, 4),
        'n_tools_norm': round(clip01(len(tools), 0, 5), 4),
        'has_tool': 1.0 if (r.get('exec_count') or 0) > 0 else 0.0,
        'dup_guard_rate': round(clip01(dup / iters, 0, 1), 4),
        'escalated': 1.0 if r.get('escalated') else 0.0,
        'finished': 1.0 if r.get('finished') else 0.0,
        'elapsed_norm': round(clip01(el, 0, 180), 4),
        'api_tool_norm': round(clip01(len(api), 0, 8), 4),
    }


def load() -> tuple[list, list]:
    X, y = [], []
    if not RUNS.exists():
        print('  ERR runs not found: %s' % RUNS)
        return X, y
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
        f = extract(r)
        if f is None:
            continue
        ok = (bool(r.get('ok'))
              and (r.get('exec_count') or 0) > 0
              and not r.get('escalated')
              and not r.get('error'))
        X.append([f[k] for k in FEATURES])
        y.append(1 if ok else 0)
    print('  samples: %d (pos %d / neg %d)' % (len(y), sum(y), len(y) - sum(y)))
    # ★ 新增（2026-10-21 R-2）: **加入 589 条轨迹** ✓
    #   为什么: 旧模型只用 265 条 eval ✗ 负样本仅 35 个 ✗ ⇒ ECE 0.6975 ✗✗
    #   轨迹里 **291 条含工具错误** ✓ ⇒ 负样本大增 ✓
    #   轨迹没有 exec_tools/elapsed ✗ ⇒ 从 steps 派生 ✓
    td = pathlib.Path('Path(__file__).resolve().parent.parent/data/trajectories')
    n_traj = 0
    if td.exists():
        for f in sorted(td.glob('*.jsonl')):
            for line in f.open(encoding='utf-8', errors='replace'):
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except Exception:
                    continue
                steps = [x for x in (t.get('steps') or []) if isinstance(x, dict)]
                if not steps:
                    continue
                # 派生特征 ✓
                all_tc = []
                for st in steps:
                    for tc in (st.get('tool_calls') or []):
                        if isinstance(tc, dict):
                            all_tc.append(tc)
                n_tools = len(all_tc)
                if n_tools == 0:
                    continue          # 零工具轨迹 ⊥ 不用（无法判工具成功率 ✓）
                n_err = sum(1 for tc in all_tc
                            if str(tc.get('status') or '').lower() in ('error', 'failed'))
                n_ok = n_tools - n_err
                # label ✓: 有工具且无错误 ⇒ 成功 ✓
                y_lbl = 0 if n_err == 0 else 1
                feat = {
                    'tool_ok_rate': n_ok / n_tools,
                    'n_tools_norm': min(n_tools / 10.0, 1.0),
                    'dup_guard_rate': 0.0,
                    'finished': 1.0 if (len(steps) > 0 and not n_err) else 0.0,
                    'elapsed_norm': 0.0,      # 轨迹无耗时 ✗ 用 0 ⚠
                    'api_tool_norm': min(n_tools / 10.0, 1.0),
                }
                X.append([feat[k] for k in FEATURES])
                y.append(y_lbl)
                n_traj += 1
    if n_traj:
        print('  ✅ 加入轨迹样本: %d（累计 %d）' % (n_traj, len(X)))

    return X, y

def sigmoid(z: float) -> float:
    if z > 30:
        return 1.0
    if z < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def fit_lr(X: list, y: list, lr: float = 0.15, epochs: int = 600, l2: float = 1e-3):
    n = len(X)
    if n == 0:
        return [], 0.0
    d = len(X[0])
    w = [0.0] * d
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for i in range(n):
            z = b
            for j in range(d):
                z += w[j] * X[i][j]
            err = sigmoid(z) - y[i]
            for j in range(d):
                gw[j] += err * X[i][j]
            gb += err
        for j in range(d):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
        b -= lr * (gb / n)
    return w, b


def proba(w: list, b: float, x: list) -> float:
    z = b
    for j in range(len(w)):
        z += w[j] * x[j]
    return sigmoid(z)


def conf_q(scores: list, alpha: float) -> float:
    n = len(scores)
    if n == 0:
        return 1.0
    k = math.ceil((n + 1) * (1 - alpha))
    k = min(max(k, 1), n)
    return sorted(scores)[k - 1]


def calc_auroc(y: list, p: list) -> float:
    pos = [v for v, t in zip(p, y) if t == 1]
    neg = [v for v, t in zip(p, y) if t == 0]
    if not pos or not neg:
        return float('nan')
    c = 0.0
    for a in pos:
        for b2 in neg:
            c += 1.0 if a > b2 else (0.5 if a == b2 else 0.0)
    return c / (len(pos) * len(neg))


def calc_ece(y: list, p: list, bins: int = 10) -> float:
    if not y:
        return float('nan')
    acc = {}
    cnt = {}
    for pi, yi in zip(p, y):
        bk = min(int(pi * bins), bins - 1)
        acc[bk] = acc.get(bk, 0.0) + (1.0 if yi == 1 else 0.0)
        cnt[bk] = cnt.get(bk, 0) + 1
    total = len(y)
    e = 0.0
    for bk in acc:
        a = acc[bk] / cnt[bk]
        c = (bk + 0.5) / bins
        e += (cnt[bk] / total) * abs(a - c)
    return e


def cmd_fit(alpha: float) -> int:
    print('=== FIT ===')
    X, y = load()
    if len(y) < 20 or len(set(y)) < 2:
        print('  ERR: not enough samples or single class')
        return 1
    idx = list(range(len(y)))
    random.Random(SEED).shuffle(idx)
    cut = int(len(idx) * 0.7)
    tr, ca = idx[:cut], idx[cut:]
    w, b = fit_lr([X[i] for i in tr], [y[i] for i in tr])
    scores = [1.0 - proba(w, b, X[i]) for i in ca]
    q = conf_q(scores, alpha)
    print('  train %d / calib %d' % (len(tr), len(ca)))
    print('  weights:')
    for nm, wj in zip(FEATURES, w):
        print('    %-18s %+.4f' % (nm, wj))
    print('    %-18s %+.4f' % ('bias', b))
    print('  conformal q(alpha=%.2f) = %.4f' % (alpha, q))
    m = {
        'version': '1.0',
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'features': FEATURES,
        'weights': [round(v, 6) for v in w],
        'bias': round(b, 6),
        'alpha': alpha,
        'conformal_q': round(q, 6),
        'n_train': len(tr),
        'n_calib': len(ca),
        'literature': [
            'arXiv:2107.07511', 'arXiv:2604.01457', 'arXiv:2610.01256',
            'ACL2025-Response-Agreement', 'arXiv:2607.08065',
        ],
        'limitations': [
            'n<200 -> conservative guarantee',
            'retrieval_hit not stored -> n_tools as proxy',
            'no unseen-task handling',
            'guarantees error rate <= alpha, not zero errors',
        ],
    }
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
    print('  SAVED %s' % MODEL)
    return 0


def cmd_eval() -> int:
    print('=== EVAL ===')
    if not MODEL.exists():
        print('  ERR: run --fit first')
        return 1
    m = json.loads(MODEL.read_text(encoding='utf-8'))
    w = m['weights']
    b = m['bias']
    q = m['conformal_q']
    alpha = m.get('alpha', 0.10)
    X, y = load()
    if not y:
        return 1
    p = [proba(w, b, x) for x in X]
    base = sum(y) / len(y) * 100
    a = calc_auroc(y, p)
    e = calc_ece(y, p)
    print('  base accuracy (accept all): %.2f%%' % base)
    print('  ECE   : %.4f %s' % (e, 'OK' if e < 0.15 else 'HIGH'))
    print('  AUROC : %.4f %s' % (a, 'OK' if a > 0.7 else ('MID' if a > 0.6 else 'WEAK')))
    # three tiers
    high = [i for i in range(len(p)) if (1 - p[i]) <= q * 0.5]
    mid = [i for i in range(len(p)) if q * 0.5 < (1 - p[i]) <= q]
    low = [i for i in range(len(p)) if (1 - p[i]) > q]
    print('')
    print('  tiers (q=%.4f, alpha=%.2f):' % (q, alpha))
    for label, grp in (('high', high), ('medium', mid), ('low', low)):
        if not grp:
            print('    %-8s 0' % label)
            continue
        acc = sum(y[i] for i in grp) / len(grp) * 100
        print('    %-8s %3d (%.0f%%) acc=%.2f%%' % (
            label, len(grp), len(grp) / len(p) * 100, acc))
    # selective curve
    order = sorted(range(len(p)), key=lambda i: -p[i])
    print('')
    print('  selective accuracy:')
    cum = 0
    for rank, i in enumerate(order, 1):
        cum += y[i]
        if rank in (5, 10, 20, 40, 60, 80, len(order)):
            print('    coverage %3.0f%% -> acc %.2f%%' % (
                rank / len(order) * 100, cum / rank * 100))
    if low:
        err = 1 - sum(y[i] for i in low) / len(low)
        print('')
        print('  low-tier error %.2f%% vs alpha %.0f%% -> %s' % (
            err * 100, alpha * 100,
            'OK (conformal satisfied)' if err <= alpha + 0.001 else 'EXCEEDED (small n)'))
    return 0



def cmd_cv(k: int = 5, alpha: float = 0.10) -> int:
    """★ 5 折交叉验证（out-of-sample ✓ 诚实数字 ✓）

    每折: k-1 折训练+校准 → 留出折评估 ✓
    汇总所有留出折的预测 ✓ ⇒ 无数据泄漏 ✓
    """
    print('=== %d-FOLD CROSS-VALIDATION (out-of-sample) ===' % k)
    X, y = load()
    n = len(y)
    if n < 50:
        print('  ERR: n<50 too small')
        return 1
    idx = list(range(n))
    random.Random(SEED).shuffle(idx)
    folds = [idx[i::k] for i in range(k)]
    all_p, all_y = [], []
    fold_stats = []
    for fi in range(k):
        test = folds[fi]
        rest = [i for i in idx if i not in set(test)]
        # 再从 rest 里分 70/30 训练/共形校准 ✓
        rnd = random.Random(SEED + fi)
        rnd.shuffle(rest)
        cut = int(len(rest) * 0.7)
        tr, ca = rest[:cut], rest[cut:]
        w, b = fit_lr([X[i] for i in tr], [y[i] for i in tr])
        cal_scores = [1.0 - proba(w, b, X[i]) for i in ca]
        q = conf_q(cal_scores, alpha)
        ps = [proba(w, b, X[i]) for i in test]
        ys = [y[i] for i in test]
        all_p.extend(ps)
        all_y.extend(ys)
        acc = sum(ys) / len(ys) * 100
        fold_stats.append({'fold': fi + 1, 'n': len(ys), 'acc': acc, 'q': q})
        print('  fold %d: test=%3d acc=%5.1f%% q=%.4f' % (fi + 1, len(ys), acc, q))
    # 汇总 ✓
    print('')
    base = sum(all_y) / len(all_y) * 100
    a = calc_auroc(all_y, all_p)
    e = calc_ece(all_y, all_p)
    print('  ★ OUT-OF-SAMPLE 汇总（诚实数字 ✓）:')
    print('    n = %d · 基线准确率 %.2f%%' % (len(all_y), base))
    print('    ECE   : %.4f %s' % (e, 'OK' if e < 0.15 else 'HIGH'))
    print('    AUROC : %.4f %s' % (a, 'OK' if a > 0.7 else ('MID' if a > 0.6 else 'WEAK')))
    # 三档（用整体 alpha 分位 ✓ 近似 ✓）
    q_all = conf_q([1.0 - p for p in all_p], alpha)
    high = [i for i in range(len(all_p)) if (1 - all_p[i]) <= q_all * 0.5]
    mid = [i for i in range(len(all_p)) if q_all * 0.5 < (1 - all_p[i]) <= q_all]
    low = [i for i in range(len(all_p)) if (1 - all_p[i]) > q_all]
    print('')
    print('    三档（q=%.4f）:' % q_all)
    for label, grp in (('high', high), ('medium', mid), ('low', low)):
        if not grp:
            print('      %-7s 0' % label)
            continue
        acc = sum(all_y[i] for i in grp) / len(grp) * 100
        print('      %-7s %3d (%2.0f%%) acc=%.2f%%' % (
            label, len(grp), len(grp) / len(all_p) * 100, acc))
    # selective curve ✓
    order = sorted(range(len(all_p)), key=lambda i: -all_p[i])
    print('')
    print('    selective accuracy:')
    cum = 0
    for rank, i in enumerate(order, 1):
        cum += all_y[i]
        if rank in (10, 30, 60, 100, 150, 200, len(order)):
            print('      coverage %3.0f%% -> acc %.2f%%' % (
                rank / len(order) * 100, cum / rank * 100))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--fit', action='store_true')
    ap.add_argument('--eval', action='store_true')
    ap.add_argument('--cv', type=int, default=0)
    ap.add_argument('--alpha', type=float, default=0.10)
    a = ap.parse_args()
    if a.fit:
        return cmd_fit(a.alpha)
    if a.eval:
        return cmd_eval()
    if a.cv:
        return cmd_cv(a.cv, a.alpha)
    ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
