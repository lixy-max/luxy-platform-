#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""★ 逐条评估【本地模型自己答的】是否满足要求（用户要求的核心 ✓）。

产出:
  ① 有结果的条数 ✓
  ② ★符合要求的条数 ✓✓
  ③ 不符合的【按缺什么归类】✓
  ④ ★【needs_cloud 列表】= 该让云重做一遍、把更好的答案学下来的样本 ✓✓
     （这些就是"本地能力之外、够得着"的东西 → 入库才有意义 ✗ 否则就是过拟合 ✓）
"""
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = 'http://127.0.0.1:8001'
SRC = Path('/home/luxy/paper_lora_project/data/learning/batch_chat.jsonl')
OUT = Path('/home/luxy/paper_lora_project/data/learning/batch_chat_judged.json')

PROMPT = """你在验收一个【本地小模型】对任务的回答。

【任务】
{task}

【本地模型的回答】
{answer}

请判定，只输出 JSON：
1. meets_requirement: 是否【满足了任务的全部硬要求】
   —— 逐项对照（"分三点""每点有小标题""XX字以内""列出5个""做对比表""至少4个维度""按可能性排序"等），
      少一项、数量不够、字数明显超出、格式没照做，都算 false。
2. score: 0~5（5 完全达标且质量好；3 基本达标有小瑕疵；1 只沾边；0 没答/答非所问）
3. missing: 缺什么（一句话；没有就空串）
4. reason: 一句话理由
5. needs_cloud: 是否值得【让云端大模型重做一遍并把答案学下来】（true/false）

严格 JSON：
{{"meets_requirement": true, "score": 5, "missing": "", "reason": "...", "needs_cloud": false}}
"""


def llm(prompt, max_tokens=400):
    body = {'provider': 'openai', 'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.2, 'max_tokens': max_tokens}
    r = urllib.request.Request(B + '/chat/completions', data=json.dumps(body).encode(),
                               headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(r, timeout=180) as f:
        d = json.loads(f.read().decode())
    c = d.get('content')
    if isinstance(c, str) and c.strip():
        return c.strip()
    for ch in (d.get('choices') or []):
        v = str((ch.get('message') or {}).get('content') or '').strip()
        if v:
            return v
    return ''


def parse(txt):
    if not txt:
        return {}
    t = txt.strip()
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            pass
    return {}


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SRC
    rows = [json.loads(l) for l in src.read_text(encoding='utf-8').splitlines() if l.strip()]
    print(f'=== 评估 {len(rows)} 条（{src.name}）===\n')

    res = []
    for i, r in enumerate(rows, 1):
        task = str(r.get('task') or '')
        answer = str(r.get('text') or '')
        if not answer:
            v = {'meets_requirement': False, 'score': 0, 'missing': '没有回答',
                 'reason': '空产出', 'needs_cloud': True}
        else:
            try:
                v = parse(llm(PROMPT.format(task=task[:800], answer=answer[:2600])))
            except Exception as e:
                v = {'error': f'{type(e).__name__}: {e}'}
        rec = {'i': i, 'task': task, 'chars': len(answer), 'sec': r.get('sec'),
               'local_only': r.get('local_only'), **v}
        res.append(rec)
        flag = '✓' if v.get('meets_requirement') else '✗'
        print(f"  [{i:>2}/{len(rows)}] {flag} {v.get('score','?')}分 {len(answer):>4}字 "
              f"{'（云接手）' if not r.get('local_only') else '（本地）'} {task[:40]}")
        if not v.get('meets_requirement'):
            print(f"        缺: {str(v.get('missing'))[:88]}")

    n = len(res)
    ok = sum(1 for x in res if x.get('meets_requirement'))
    has = sum(1 for x in res if x.get('chars'))
    avg = sum(float(x.get('score') or 0) for x in res) / max(1, n)
    nc = [x for x in res if x.get('needs_cloud')]
    print()
    print('=' * 66)
    print(f'  【有结果】{has}/{n}')
    print(f'  ★【符合要求】{ok}/{n}（{100*ok//max(1,n)}%）')
    print(f'  平均分 {avg:.2f}/5')
    print(f'  分数分布: ' + ' · '.join(f'{k}分:{v}' for k, v in sorted(Counter(int(x.get('score') or 0) for x in res).items())))
    print()
    print(f'  ★【建议让云重做（needs_cloud）】{len(nc)} 条 ← 这些学下来才有意义 ✓')
    print()
    print('  --- 不符合要求的按"缺什么"归类 ---')
    miss = Counter()
    for x in res:
        if not x.get('meets_requirement'):
            miss[str(x.get('missing') or x.get('reason') or '未说明')[:42]] += 1
    for k, v in miss.most_common(12):
        print(f'    {v:>3}  {k}')

    OUT.write_text(json.dumps({'source': str(src), 'n': n, 'has_result': has,
                               'meets_requirement': ok, 'avg_score': avg,
                               'needs_cloud': [x['i'] for x in nc],
                               'results': res}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n  明细 → {OUT}')


if __name__ == '__main__':
    main()
