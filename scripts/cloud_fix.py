#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""★ A v2: 把"本地不达标 → 云重做"的成果【汇总成一份 Q&A 对照文档】再入库。

v1 的两个问题（实测 ✓）:
  ① 每条各调一次 sink → 都写 learned_agent_use.md → 【同名替换 ✗ 只留最后一条 ✗】
  ② rd.distill() 提炼的是【工具调用骨架 ✗】→ 对"答案质量"这种学习没用 ✓
v2 做法:
  · 把 N 条（任务 + 本地问题 + 云端正确答案）汇总成【一份 Markdown】✓
  · 交给 add_documents_to_kb(agent_use, [md]) ✓（幂等 ✓ 同名替换这一份 ✓）
  · 文档形态 = Q&A 对照 ✓ → agent 检索到就学到"这类任务该怎么答"✓✓
"""
import json
import sys
import time
import urllib.request
import pathlib
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
B = 'http://127.0.0.1:8001'
JUDGED = ROOT / 'data' / 'learning' / 'batch_chat_judged.json'
DOC = ROOT / 'agent_kb' / '95_cloud_answers.md'      # ★ 新增的一份文档（不动原有 10 份 ✓）
KB = 'agent_use'


def post(path, body, timeout=600):
    r = urllib.request.Request(B + path, data=json.dumps(body).encode(),
                               headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read().decode())


def ask_cloud(task, extra=''):
    prompt = task if not extra else (
        f'{task}\n\n【上一版回答的问题（必须避免）】{extra}\n\n'
        f'请严格满足上面任务的全部硬要求（字数、条目数、格式、结构都要照做）。')
    d = post('/chat/completions', {'provider': 'openai',
                                   'messages': [{'role': 'user', 'content': prompt}],
                                   'temperature': 0.5, 'max_tokens': 1800})
    c = d.get('content')
    if isinstance(c, str) and c.strip():
        return c.strip()
    for ch in (d.get('choices') or []):
        v = str((ch.get('message') or {}).get('content') or '').strip()
        if v:
            return v
    return ''


def kb_stat():
    with urllib.request.urlopen(B + f'/kb/{KB}', timeout=20) as f:
        d = json.loads(f.read().decode())
    return {'docs': len(d.get('documents') or []), 'chunks': d.get('total_chunks'),
            'names': [x.get('name') for x in (d.get('documents') or [])]}


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    judged = json.loads(JUDGED.read_text(encoding='utf-8'))
    need = [x for x in (judged.get('results') or [])
            if (not x.get('meets_requirement')) or x.get('needs_cloud')][:limit]
    print(f'=== 处理 {len(need)} 条（本地不达标/建议走云 ✓）===')

    before = kb_stat()
    print(f'  入库前 {KB}: {before["docs"]} 条目 / {before["chunks"]} 块')

    L = ['# 云端参考答案（本地答不达标 → 云重做）',
         '',
         '> 来源: `scripts/cloud_fix.py` —— 让【本地模型】先答，用判官逐条核对任务硬要求；',
         '> 不达标的再让【云端大模型】重做一遍，把正确答案沉淀在这里 ✓',
         '> 用途: 遇到同类任务时，按这里的做法答（尤其是字数/条目数/格式这类硬约束 ✓）',
         f'> 生成时间: {time.strftime("%Y-%m-%d %H:%M")}',
         '']
    ok = 0
    for i, x in enumerate(need, 1):
        task = str(x.get('task') or '')
        miss = str(x.get('missing') or x.get('reason') or '')
        print(f'  [{i}/{len(need)}] {task[:56]}')
        try:
            cloud = ask_cloud(task, miss)
        except Exception as e:
            print(f'      ✗ 云调用失败: {type(e).__name__}: {e}')
            continue
        if not cloud:
            print('      ✗ 云没给回答'); continue
        ok += 1
        print(f'      ✓ 云端 {len(cloud)} 字（本地问题: {miss[:44]}）')
        L += ['---', '', f'## 任务 {i}：{task}', '',
              f'**本地模型出的问题**：{miss or "（判官建议让云重做）"}', '',
              '**正确做法（云端参考答案）**：', '', cloud, '']

    if ok == 0:
        print('  ✗ 一条都没成功'); return
    DOC.write_text('\n'.join(L) + '\n', encoding='utf-8')
    print(f'\n  ✓ 汇总文档 {DOC.name}（{len("".join(L))} 字符 / {len(L)} 行 · {ok} 条对照）')

    import kb as kbmod
    info = kbmod.add_documents_to_kb(KB, [str(DOC)])
    print(f'  ✓ 入库: {json.dumps(info, ensure_ascii=False)[:200]}')

    after = kb_stat()
    print()
    print('=' * 66)
    print(f'  {KB}: {before["docs"]} 条目/{before["chunks"]} 块 → '
          f'{after["docs"]} 条目/{after["chunks"]} 块')
    print(f'  新增文档: {[n for n in after["names"] if n not in before["names"]]}')

    # 检索验证（真能查到吗 ✓）
    print()
    print('  --- 检索验证（新沉淀的内容能查到吗 ✓）---')
    for q in ('LoRA 核心原理 怎么答', '字数限制 怎么满足', '对比表 怎么做'):
        r = urllib.request.Request(B + f'/kb/{KB}/query',
            data=json.dumps({'query': q, 'top_k': 2}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(r, timeout=120) as f:
                d = json.loads(f.read().decode())
            rs = d.get('results') or []
            print(f'    ★ {q}')
            for y in rs:
                print(f'      [{float(y.get("score") or 0):.3f}] {y.get("source")} :: '
                      f'{str(y.get("text"))[:76].replace(chr(10)," ")}')
        except Exception as e:
            print(f'    ✗ {q}: {str(e)[:60]}')


if __name__ == '__main__':
    main()
