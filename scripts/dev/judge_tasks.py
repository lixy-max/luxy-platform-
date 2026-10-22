#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""★ 逐条评估: 任务是否【有结果】且【符合要求】（用户明确要求 ✓）。

不用"有没有 final"这种粗判 ✗ —— 那只能看出"有没有输出"✗
这里用【LLM 判官】逐条判: 把【任务 + 工具调用摘要 + 最终回答】给云端大模型 ✓
  输出结构化结论: has_result · meets_requirement · score · missing · reason

用法:
  python scripts/judge_tasks.py <trajectories.jsonl> [--limit N] [--out 路径]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = 'http://127.0.0.1:8001'

JUDGE_PROMPT = """你在验收一个 AI agent 的任务完成情况。

【用户给的任务】
{task}

【agent 实际调用的工具（按顺序）】
{tools}

【agent 的最终回答】
{final}

请判定两件事，只输出 JSON：
1. has_result: agent 是否给出了【实质性的最终回答】（空、只有"我做不到"而无解释、或明显中断都算 false）
2. meets_requirement: 这个回答是否【真的满足了用户的全部要求】
   —— 逐项对照任务里的要求（例如"列出前三名""说明各自适合训什么""给出依据"），
      少一项就不算满足。
3. score: 0~5（5=完全满足且质量好；3=基本满足但有欠缺；1=只沾边；0=没做/答非所问）
4. missing: 缺了什么（一句话；没有就空字符串）
5. reason: 一句话理由

输出格式（严格 JSON，不要解释、不要代码块）：
{{"has_result": true, "meets_requirement": true, "score": 5, "missing": "", "reason": "..."}}
"""


def llm(prompt, max_tokens=400, temperature=0.2):
    body = {'provider': 'openai', 'messages': [{'role': 'user', 'content': prompt}],
            'temperature': temperature, 'max_tokens': max_tokens}
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
    if t.startswith('```'):
        t = t.split('\n', 1)[-1]
        t = t.rsplit('```', 1)[0]
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            pass
    return {}


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        '/home/luxy/paper_lora_project/data/trajectories/trajectories.jsonl')
    limit = 0
    out_path = None
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])
    if '--out' in sys.argv:
        out_path = Path(sys.argv[sys.argv.index('--out') + 1])

    rows = [json.loads(l) for l in src.read_text(encoding='utf-8').splitlines() if l.strip()]
    if limit:
        rows = rows[:limit]
    print(f'=== 评估 {len(rows)} 条 · 来源 {src.name} ===')

    results = []
    for i, r in enumerate(rows, 1):
        task = str(r.get('task') or '')
        final = str(r.get('final') or '').strip()
        cs = [c for s in (r.get('steps') or []) for c in (s.get('tool_calls') or [])]
        tools = ' → '.join(str(c.get('tool') or c.get('name')) for c in cs)[:600] or '（没有调用任何工具）'
        # 客观项
        obj = {'calls': len(cs),
               'has_final': bool(final),
               'final_len': len(final)}
        # LLM 判官（空回答就不用问了 ✓ 直接判 ✗）
        if not final:
            verdict = {'has_result': False, 'meets_requirement': False, 'score': 0,
                       'missing': '没有任何最终回答', 'reason': '空产出'}
        else:
            try:
                verdict = parse(llm(JUDGE_PROMPT.format(task=task[:900], tools=tools,
                                                        final=final[:2500])))
            except Exception as e:
                verdict = {'error': f'{type(e).__name__}: {e}'}
        rec = {'i': i, 'task': task, **obj, **verdict}
        results.append(rec)
        flag = '✓' if verdict.get('meets_requirement') else (
            '△' if verdict.get('has_result') else '✗')
        print(f'  [{i:>3}/{len(rows)}] {flag} score={verdict.get("score","?")} '
              f'调用{obj["calls"]:>2} {task[:44]}')
        if not verdict.get('meets_requirement'):
            print(f'          缺: {str(verdict.get("missing"))[:90]}')

    # ---------- 汇总
    n = len(results)
    has = sum(1 for x in results if x.get('has_result'))
    meet = sum(1 for x in results if x.get('meets_requirement'))
    avg = sum(float(x.get('score') or 0) for x in results) / max(1, n)
    print()
    print('=' * 66)
    print(f'  【有结果】{has}/{n}（{100*has//max(1,n)}%）')
    print(f'  【符合要求】{meet}/{n}（{100*meet//max(1,n)}%）  ← 这是真正的验收标准')
    print(f'  平均分 {avg:.2f} / 5')
    from collections import Counter
    sc = Counter(int(x.get('score') or 0) for x in results)
    print(f'  分数分布: ' + ' · '.join(f'{k}分:{v}' for k, v in sorted(sc.items())))
    print()
    print('  --- 不符合要求的（按缺什么归类）---')
    miss = Counter()
    for x in results:
        if not x.get('meets_requirement'):
            m = str(x.get('missing') or x.get('reason') or '未说明')[:40]
            miss[m] += 1
    for k, v in miss.most_common(12):
        print(f'    {v:>3}  {k}')
    print()
    print('  --- 调用超预算（>12 次）的条数 ---')
    over = [x for x in results if x['calls'] > 12]
    print(f'    {len(over)} 条' + (f'：{[x["i"] for x in over[:15]]}' if over else ' ✓'))

    op = out_path or (src.parent / (src.stem + '_judged.json'))
    op.write_text(json.dumps({'source': str(src), 'n': n, 'has_result': has,
                              'meets_requirement': meet, 'avg_score': avg,
                              'results': results}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n  明细 → {op}')


if __name__ == '__main__':
    main()
