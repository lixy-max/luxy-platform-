#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按"学习增强"链路批量跑复杂任务（走对话端点 ✓ 不挂知识库 ✓）。

链路: 本地模型先答 → 超时/失败 → 大模型接手 → 采轨迹 → 清洗 → 提炼 → 入库
      最终看【能不能洗出一个知识库】✓

与 reflect_run 的区别（两条路互补 ✓ 别混）:
  · reflect_run: 走 DSH headless（云端带 53 个工具 ✓）→ 产出【工具调用】轨迹
  · 本脚本:      走工作台对话端点（无工具 ✗）→ 产出【问答质量】轨迹
    ⇒ 用户要的"打开学习增强跑 50 条"就是后者 ✓
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = 'http://127.0.0.1:8001'
OUT = Path('/home/luxy/paper_lora_project/data/learning')
OUT.mkdir(parents=True, exist_ok=True)

# 50 条复杂任务（纯生成/分析类 ✓ 不依赖工具 ✗ 因为对话端点没有工具 ✓）
# 覆盖 4 类: 长文写作 / 结构化分析 / 多步推理 / 领域知识整理
TASKS = []
TOPICS = ['向量检索', 'LoRA 微调', '注意力机制', 'RAG 系统', '知识蒸馏', '模型量化',
          '提示词工程', '数据标注', '评测集设计', '长上下文处理']
# ★ 10 主题 × 10 种"用户会提的活" = 100 条
#   （都是【本地模型应该能做但未必做得好】的生成/分析任务 ✓
#    差异度大 ✓ 好用来判断"哪些本地能做、哪些要云接手"✓）
REQS = [
    (400, '写一段 {n} 字左右的说明，介绍{t}的核心原理，要求分三点、每点有小标题。'),
    (0,   '针对"{t}"列出 5 个常见误解，每条给出正确说法的对照。'),
    (300, '假设要向非技术的产品经理解释"{t}"，写一份 {n} 字以内的说明，不要用术语。'),
    (0,   '分析"{t}"在工程落地时最容易踩的 4 个坑，每条给出规避办法。'),
    (0,   '给"{t}"设计一个 5 步的上手学习路径，每步说明产出物是什么。'),
    (0,   '把"{t}"和它最容易混淆的那个概念做一张对比表，至少 4 个维度。'),
    (200, '用一段 {n} 字以内的话，说清"{t}"到底解决了什么问题、没解决什么问题。'),
    (0,   '给"{t}"写一份 6 条的自查清单（检查一个方案做得对不对）。'),
    (0,   '假设{t}的方案上线后效果不好，列出 5 个最可能的排查方向，按可能性排序。'),
    (0,   '给{t}写 3 道能考出真理解的问答题，并各附一段标准答案要点。'),
]
for t in TOPICS:
    for n, tpl in REQS:
        TASKS.append(tpl.format(t=t, n=n))


def run_one(task: str, timeout_sec: int, kb_name: str, kb_mode: str = 'new') -> dict:
    body = {
        'provider': 'vllm',
        'messages': [{'role': 'user', 'content': task}],
        'max_tokens': 900, 'temperature': 0.7,
        'kb': None,                       # ★ 不挂任何知识库 ✓（用户明确要求）
        'learn': {
            'enabled': True,
            'timeout_sec': timeout_sec,
            'escalate_provider': 'openai',
            'build_kb': True,
            'kb_mode': kb_mode,
            'kb_name': kb_name,
        },
    }
    r = urllib.request.Request(B + '/chat/completions/stream',
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    t0 = time.time()
    res = {'task': task, 'handoff': None, 'stats': None, 'chars': 0, 'error': None,
           'sec': 0, 'local_only': True,
           'text': '',            # ★ 回答正文（本地或云 ✓ 评估"是否符合要求"必须用 ✓）
           'local_text': '',      # ★ 本地那一段的正文（云接手时用来对比 ✓）
           'cloud_text': ''}
    try:
        with urllib.request.urlopen(r, timeout=timeout_sec + 300) as f:
            for raw in f:
                line = raw.decode('utf-8', errors='replace').strip()
                if not line.startswith('data: '):
                    continue
                try:
                    d = json.loads(line[6:])
                except Exception:
                    continue
                if 'delta' in d:
                    _piece = str(d['delta'])
                    res['chars'] += len(_piece)
                    res['text'] += _piece
                    if res['handoff'] is None:
                        res['local_text'] += _piece     # ★ 云接手前 = 本地产出 ✓
                    else:
                        res['cloud_text'] += _piece     # ★ 接手之后 = 云产出 ✓
                if 'handoff' in d:
                    res['handoff'] = d['handoff']
                    res['local_only'] = False
                if 'learn_stats' in d:
                    res['stats'] = d['learn_stats']
                if 'error' in d:
                    res['error'] = d['error']
    except Exception as e:
        res['error'] = f'{type(e).__name__}: {e}'
    res['sec'] = round(time.time() - t0, 1)
    return res


def main():
    kb_name = sys.argv[1] if len(sys.argv) > 1 else 'learned_chat'
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    timeout_sec = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    # ★ 第 4 个参数: kb_mode（existing = 追加进已有库 ✓ 默认就是它 ✗ 别新建 ✓）
    kb_mode = sys.argv[4] if len(sys.argv) > 4 else 'existing' 
    tasks = TASKS[:limit]
    print(f'  任务 {len(tasks)} 条 · 目标知识库 {kb_name}（mode={kb_mode}）· 本地超时 {timeout_sec} 秒 · 不挂知识库 ✓')
    log = OUT / 'batch_chat.jsonl'
    n_ho = n_ok = 0
    t0 = time.time()
    with log.open('a', encoding='utf-8') as fh:
        for i, task in enumerate(tasks, 1):
            r = run_one(task, timeout_sec, kb_name)
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
            n_ho += 0 if r['local_only'] else 1
            if r['stats'] and r['stats'].get('kept'):
                n_ok += 1
            flag = '接手' if not r['local_only'] else '本地'
            st = (r['stats'] or {})
            print(f"  [{i:>2}/{len(tasks)}] {flag} {r['sec']:>5.1f}s {r['chars']:>4}字 "
                  f"清洗keep={st.get('kept')} 入KB={st.get('kb')} "
                  f"{('丢弃: ' + ','.join(st.get('dropped') or [])) if st.get('dropped') else ''}"
                  f"{'  err=' + str(r['error'])[:40] if r['error'] else ''}")
    print()
    print(f'  合计: {len(tasks)} 条 · 接手 {n_ho} 条 · 清洗后入库 {n_ok} 条 · 用时 {(time.time()-t0)/60:.1f} 分钟')
    print(f'  ★ 结果: {"✓ 洗出了知识库条目 ✓" if n_ok else "✗ 一条都没入库 ✗（看上面丢弃原因）"}')


if __name__ == '__main__':
    main()
