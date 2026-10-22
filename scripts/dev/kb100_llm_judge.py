#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P-74 步骤 3 增强：LLM judge 判分（2026-10-21）

## 问题
  关键词匹配太严 ✗ ⇒ clean 17% / raw 9% 都太低 ✗
  main_idea 全 0% ✗（"概述主题"没有唯一关键词 ✗）

## 修法
  用 **LLM 判分** ✓ 判断"答案是否正确回答了问题" ✓
  · 输入: 问题 + 标准答案提示 + 模型的答案 ✓
  · 输出: 0=错 / 1=对 ✓

## ⚠ 诚实标注
  · 用**本地 7B 自己判自己** ⇒ 有自我偏好偏差 ✗
  · 缓解: 严格 prompt + 分档判分（0/0.5/1）✓
  · 真正的独立判分应换模型 ✗（如 deepseek 云端 ⚠）
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
VLLM = 'http://127.0.0.1:8000/v1/chat/completions'
MODEL = '/home/luxy/models/Qwen2.5-7B-Instruct-AWQ'


def chat(messages, max_tokens=200, temperature=0.0, timeout=90):
    body = json.dumps({
        'model': MODEL, 'messages': messages,
        'max_tokens': max_tokens, 'temperature': temperature,
    }).encode('utf-8')
    req = urllib.request.Request(VLLM, data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8'))
        return (d['choices'][0]['message'].get('content') or '').strip()
    except Exception as e:
        return '__ERROR__: %s' % e


JUDGE_SYS = '''你是严格、公正的阅卷员。判断【学生答案】是否正确回答了【问题】。

判定标准：
- 正确（1）：答案抓住了核心要点，与【参考要点】实质一致（措辞不同也算对）
- 部分正确（0.5）：答对一部分但遗漏关键信息
- 错误（0）：答非所问、明显错误、或说"材料未提及"但材料其实有

只输出一个数字：1 / 0.5 / 0，不要任何解释。'''


def judge_one(q: dict, answer: str) -> dict:
    """LLM 判分 ✓"""
    if not answer or answer.startswith('__ERROR__'):
        return {'score': 0.0, 'raw': '(空/错误)'}
    # 答案太短 ⇒ 直接 0 ✓
    if len(answer.strip()) < 8:
        return {'score': 0.0, 'raw': '(过短)'}
    user = ('【问题】%s\n\n【参考要点】%s\n\n【学生答案】%s\n\n请判分（只输出 1/0.5/0）：' % (
        q['question'],
        (q.get('answer_hint') or '')[:400],
        answer[:600],
    ))
    txt = chat([{'role': 'system', 'content': JUDGE_SYS},
                {'role': 'user', 'content': user}], max_tokens=20)
    if txt.startswith('__ERROR__'):
        return {'score': 0.0, 'raw': txt[:80]}
    # 解析 ✓
    import re
    m = re.search(r'(0\.5|0|1)', txt)
    if not m:
        return {'score': 0.0, 'raw': txt[:60]}
    v = float(m.group(1))
    return {'score': v, 'raw': txt[:40]}


def bootstrap_ci(vals, n=10000, seed=42):
    import random
    random.seed(seed)
    N = len(vals)
    if not N:
        return 0.0, 0.0, 0.0
    point = sum(vals) / N
    boots = []
    for _ in range(n):
        boots.append(sum(vals[random.randrange(N)] for _ in range(N)) / N)
    boots.sort()
    return point, boots[int(n * 0.025)], boots[int(n * 0.975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--questions', default='docs/EVAL_SET_KB100.json')
    ap.add_argument('--answers', default='docs/KB100_LLM_ACCURACY.json')
    ap.add_argument('--out', default=str(ROOT / 'docs' / 'KB100_LLM_JUDGE.json'))
    args = ap.parse_args()

    # 读已有答案 ✓
    src = ROOT / args.answers if not str(args.answers).startswith('/') else pathlib.Path(args.answers)
    data = json.loads(src.read_text(encoding='utf-8'))
    rows = data['rows']
    _qp = ROOT / args.questions if not str(args.questions).startswith('/') else pathlib.Path(args.questions)
    qs = {q['id']: q for q in json.loads(_qp.read_text(encoding='utf-8'))['questions']}

    if args.limit:
        rows = rows[:args.limit * 2]
    print('  待判: %d 条答案（%d 题 × 2 臂）' % (len(rows), len(rows) // 2))

    # 检查 vLLM ✓
    print('  检查 vLLM ...', end=' ', flush=True)
    t = chat([{'role': 'user', 'content': 'ok'}], max_tokens=5, timeout=60)
    print('✓' if not t.startswith('__ERROR__') else '✗ %s' % t[:80])
    if t.startswith('__ERROR__'):
        return 1

    t0 = time.time()
    for i, r in enumerate(rows):
        q = qs.get(r['qid'])
        if not q:
            r['llm_score'] = 0.0
            continue
        jr = judge_one(q, r.get('answer') or '')
        r['llm_score'] = jr['score']
        r['llm_raw'] = jr['raw']
        # 二元化 ✓（0.5 → 不算对 ✓ 保守 ✓）
        r['llm_correct'] = jr['score'] >= 0.5
        if (i + 1) % 20 == 0:
            print('    %d/%d · %.0fs' % (i + 1, len(rows), time.time() - t0))

    # 汇总 ✓
    print('')
    print('=' * 78)
    print('  结果（LLM judge ✓）')
    print('=' * 78)
    summary = {}
    compare = {}
    for arm in ('clean', 'raw'):
        sub = [r for r in rows if r['arm'] == arm]
        kw = [1.0 if r['correct'] else 0.0 for r in sub]
        llm = [1.0 if r['llm_correct'] else 0.0 for r in sub]
        p_kw, lo_kw, hi_kw = bootstrap_ci(kw)
        p_llm, lo_llm, hi_llm = bootstrap_ci(llm)
        # 部分分均值 ✓
        avg_score = sum(r['llm_score'] for r in sub) / max(len(sub), 1)
        summary[arm] = {
            'n': len(sub),
            'kw_acc': round(p_kw, 4), 'kw_ci': [round(lo_kw, 4), round(hi_kw, 4)],
            'llm_acc': round(p_llm, 4), 'llm_ci': [round(lo_llm, 4), round(hi_llm, 4)],
            'llm_avg_score': round(avg_score, 4),
        }
        compare[arm] = {'kw': kw, 'llm': llm}
        print('  %-6s n=%-4d' % (arm, len(sub)))
        print('    关键词判定: %.1f%% [%.1f%%, %.1f%%]' % (
            p_kw * 100, lo_kw * 100, hi_kw * 100))
        print('    **LLM 判定**: %.1f%% [%.1f%%, %.1f%%]  平均分 %.3f' % (
            p_llm * 100, lo_llm * 100, hi_llm * 100, avg_score))

    # 配对差（两种判分 ✓）
    print('')
    for mode in ('kw', 'llm'):
        a, b = [], []
        for r in rows:
            if r['arm'] != 'clean':
                continue
            k = r['qid']
            for r2 in rows:
                if r2['arm'] == 'raw' and r2['qid'] == k:
                    key = 'correct' if mode == 'kw' else 'llm_correct'
                    a.append(1.0 if r[key] else 0.0)
                    b.append(1.0 if r2[key] else 0.0)
                    break
        if not a:
            continue
        import random
        random.seed(42)
        N = len(a)
        diffs = []
        for _ in range(10000):
            idx = [random.randrange(N) for _ in range(N)]
            diffs.append(sum(a[i] - b[i] for i in idx) / N)
        diffs.sort()
        point = sum(a) / N - sum(b) / N
        lo, hi = diffs[250], diffs[9750]
        cnt = sum(1 for d in diffs if d <= 0) if point > 0 else sum(1 for d in diffs if d >= 0)
        p = min(2 * cnt / 10000, 1.0)
        label = '关键词' if mode == 'kw' else '**LLM**'
        print('  配对差 %s: %+.1fpp CI [%+.1f%%, %+.1f%%] p=%.4f %s' % (
            label, point * 100, lo * 100, hi * 100, p,
            '**显著** ✓' if p < 0.05 else '不显著 ✗'))
        summary['paired_' + mode] = {
            'diff_pp': round(point * 100, 2),
            'ci': [round(lo * 100, 2), round(hi * 100, 2)],
            'p': round(p, 4), 'significant': p < 0.05,
        }

    # 按题型 ✓
    print('')
    print('  LLM 判定 · 按题型:')
    for typ in sorted(set(r.get('type') for r in rows if r.get('type'))):
        line = '    %-12s' % typ
        for arm in ('clean', 'raw'):
            sub = [r for r in rows if r['arm'] == arm and r.get('type') == typ]
            if sub:
                acc = sum(1 for r in sub if r['llm_correct']) / len(sub)
                line += ' %s %.1f%%(n=%d)' % (arm, acc * 100, len(sub))
        print(line)

    out = pathlib.Path(args.out)
    out.write_text(json.dumps({
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'judge': 'LLM (local 7B) ⚠ 自我偏好偏差',
        'rows': rows,
        'summary': summary,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print('')
    print('  ✅ 已写出: %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
