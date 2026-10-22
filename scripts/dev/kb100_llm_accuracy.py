#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P-74 步骤 3：LLM 答题准确率（2026-10-21）

## 目标
  不只测"检索命中" ✗ 让 LLM 用 top3 检索结果回答 → 判对错 ✓
  两臂: **清洗后 KB** vs **原始 KB** ✓ 算准确率 + 置信区间 ✓

## 方法
  ① 100 题（docs/EVAL_SET_KB100.json ✓）
  ② 每题 × 2 臂: query_kb(arm, question, top_k=3) ✓
  ③ 组装 prompt: 检索结果 + 问题 ✓ → 本地 vLLM 7B ✓
  ④ 判分: **关键词匹配** ✓（客观 ✓ 零成本 ✓）+ LLM judge（可选 ⚠）
  ⑤ 输出: 准确率 + Bootstrap CI ✓

## ⚠ 诚实
  · 关键词匹配是**保守判定** ✓ 可能低估（同义表述不算对 ✗）
  · 本地 7B 远弱于云端 ✗ ⇒ 准确率会偏低 ✓ 但**可迁移** ✓
"""
import argparse
import json
import pathlib
import random
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import kb as kbmod                                    # noqa: E402

VLLM = 'http://127.0.0.1:8000/v1/chat/completions'
MODEL = '/home/luxy/models/Qwen2.5-7B-Instruct-AWQ'

ARMS = {}
_DEFAULT_ARMS = {'clean': 'ab_clean_20261021', 'raw': 'ab_raw_20261021'}


def chat(messages, max_tokens=300, temperature=0.0, timeout=120):
    import urllib.request
    body = json.dumps({
        'model': MODEL,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
    }).encode('utf-8')
    req = urllib.request.Request(
        VLLM, data=body,
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8'))
        return (d['choices'][0]['message'].get('content') or '').strip()
    except Exception as e:
        return '__ERROR__: %s' % e


def retrieve(arm: str, question: str, top_k: int = 3):
    try:
        results, diag = kbmod.query_kb_detailed(arm, question, top_k=top_k)
        return [r.get('text') or '' for r in (results or [])]
    except Exception as e:
        return []


def build_prompt(question: str, chunks: list) -> list:
    ctx = '\n\n'.join('【片段 %d】%s' % (i + 1, c[:900]) for i, c in enumerate(chunks))
    if not ctx:
        ctx = '（无检索结果）'
    return [
        {'role': 'system', 'content': '你是严谨的助手。只根据给定材料回答，材料里没有就说"材料未提及"。回答简洁。'},
        {'role': 'user', 'content': '材料：\n%s\n\n问题：%s\n\n请回答：' % (ctx, question)},
    ]


def judge(answer: str, q: dict) -> bool:
    """关键词匹配判定 ✓（保守 ✓）"""
    if not answer or answer.startswith('__ERROR__'):
        return False
    a = answer.lower()
    # ① 答案提示直接命中 ✓
    hint = str(q.get('answer_hint') or '').strip().lower()
    if hint and len(hint) <= 40 and hint in a:
        return True
    # ② 关键词命中 ≥50% ✓
    kws = [str(k).lower() for k in (q.get('keywords') or []) if k]
    if not kws:
        return bool(hint) and hint in a
    hit = sum(1 for k in kws if k in a)
    return hit >= max(1, int(len(kws) * 0.5))


def answer_one(arm: str, q: dict) -> dict:
    t0 = time.time()
    chunks = retrieve(ARMS[arm], q['question'], top_k=3)
    ans = chat(build_prompt(q['question'], chunks))
    ok = judge(ans, q)
    return {
        'arm': arm,
        'qid': q['id'],
        'type': q.get('type'),
        'pdf': q.get('pdf'),
        'correct': bool(ok),
        'n_chunks': len(chunks),
        'answer': ans[:300],
        'elapsed_s': round(time.time() - t0, 2),
    }


def bootstrap_ci(vals, n=10000, seed=42):
    random.seed(seed)
    N = len(vals)
    if N == 0:
        return 0.0, 0.0, 0.0
    point = sum(vals) / N
    boots = []
    for _ in range(n):
        s = sum(vals[random.randrange(N)] for _ in range(N))
        boots.append(s / N)
    boots.sort()
    return point, boots[int(n * 0.025)], boots[int(n * 0.975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0, help='0=全部')
    ap.add_argument('--questions', default='docs/EVAL_SET_KB100.json')
    ap.add_argument('--clean-kb', default='ab_clean_20261021')
    ap.add_argument('--raw-kb', default='ab_raw_20261021')
    ap.add_argument('--out', default='')
    args = ap.parse_args()

    ARMS.clear()
    ARMS['clean'] = args.clean_kb
    ARMS['raw'] = args.raw_kb
    qp = ROOT / args.questions if not str(args.questions).startswith('/') else pathlib.Path(args.questions)
    data = json.loads(qp.read_text(encoding='utf-8'))
    qs = data['questions']
    if args.limit:
        qs = qs[:args.limit]
    print('  题目: %d · 臂: %s' % (len(qs), list(ARMS)))

    # 检查服务 ✓
    print('  检查 vLLM ...', end=' ', flush=True)
    test = chat([{'role': 'user', 'content': '答一个字:ok'}], max_tokens=10, timeout=60)
    print('✓' if not test.startswith('__ERROR__') else ('✗ %s' % test[:100]))
    if test.startswith('__ERROR__'):
        print('  ✗ vLLM 不可用，退出')
        return 1

    rows = []
    t0 = time.time()
    for i, q in enumerate(qs):
        for arm in ARMS:
            r = answer_one(arm, q)
            rows.append(r)
        if (i + 1) % 5 == 0 or i + 1 == len(qs):
            n_ok = sum(1 for x in rows if x['correct'])
            print('    %d/%d 题 · 跑次 %d · 正确 %d (%.1f%%) · %.0fs' % (
                i + 1, len(qs), len(rows), n_ok,
                n_ok / max(len(rows), 1) * 100, time.time() - t0))

    # 汇总 ✓
    print('')
    print('=' * 78)
    print('  结果')
    print('=' * 78)
    summary = {}
    for arm in ARMS:
        sub = [x for x in rows if x['arm'] == arm]
        vals = [1.0 if x['correct'] else 0.0 for x in sub]
        point, lo, hi = bootstrap_ci(vals)
        summary[arm] = {
            'n': len(vals), 'acc': round(point, 4),
            'ci': [round(lo, 4), round(hi, 4)],
            'chunks_avg': round(sum(x['n_chunks'] for x in sub) / max(len(sub), 1), 2),
            'elapsed_avg': round(sum(x['elapsed_s'] for x in sub) / max(len(sub), 1), 1),
        }
        print('  %-6s n=%-4d 准确率 **%.1f%%** [%.1f%%, %.1f%%] · 块均 %.1f · 耗时均 %.1fs' % (
            arm, len(vals), point * 100, lo * 100, hi * 100,
            summary[arm]['chunks_avg'], summary[arm]['elapsed_avg']))

    # 配对差 ✓
    cmap = {(x['qid'], x['arm']): x['correct'] for x in rows}
    paired_a, paired_b = [], []
    for q in qs:
        k = q['id']
        if (k, 'clean') in cmap and (k, 'raw') in cmap:
            paired_a.append(1.0 if cmap[(k, 'clean')] else 0.0)
            paired_b.append(1.0 if cmap[(k, 'raw')] else 0.0)
    if paired_a:
        # 配对 bootstrap ✓
        random.seed(42)
        N = len(paired_a)
        diffs = []
        for _ in range(10000):
            idx = [random.randrange(N) for _ in range(N)]
            diffs.append(sum(paired_a[i] - paired_b[i] for i in idx) / N)
        diffs.sort()
        point = sum(paired_a) / N - sum(paired_b) / N
        lo, hi = diffs[int(10000 * 0.025)], diffs[int(10000 * 0.975)]
        cnt = sum(1 for d in diffs if d <= 0) if point > 0 else sum(1 for d in diffs if d >= 0)
        p = min(2 * cnt / 10000, 1.0)
        print('')
        print('  配对差 (clean - raw): **%+.1fpp** CI [%+.1f%%, %+.1f%%] p=%.4f %s' % (
            point * 100, lo * 100, hi * 100, p,
            '**显著** ✓' if p < 0.05 else '不显著 ✗'))
        summary['paired'] = {
            'n': N, 'diff_pp': round(point * 100, 2),
            'ci': [round(lo * 100, 2), round(hi * 100, 2)],
            'p': round(p, 4),
            'significant': p < 0.05,
        }

    # 按类型分 ✓
    print('')
    print('  按题型:')
    for typ in sorted(set(x['type'] for x in rows if x['type'])):
        line = '    %-12s' % typ
        for arm in ARMS:
            sub = [x for x in rows if x['arm'] == arm and x['type'] == typ]
            if sub:
                acc = sum(1 for x in sub if x['correct']) / len(sub)
                line += ' %s %.1f%%(n=%d)' % (arm, acc * 100, len(sub))
        print(line)

    # 落盘 ✓
    out = pathlib.Path(args.out or (ROOT / 'docs' / 'KB100_LLM_ACCURACY.json'))
    out.write_text(json.dumps({
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'questions': len(qs),
        'model': 'local 7B (Qwen2.5-7B-Instruct-AWQ)',
        'judge': 'keyword match (conservative)',
        'arms': ARMS,
        'summary': summary,
        'rows': rows,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print('')
    print('  ✅ 已写出: %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
