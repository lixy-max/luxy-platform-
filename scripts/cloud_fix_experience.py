#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""★ 给云答案文档补一节【可检索的通用经验】（治"入库了但查不到 ✗"）。

实测: 95_cloud_answers.md 入库后（12 条目/90 块 ✓）但检索"字数限制 怎么满足"查不到它 ✗
   → 因为文档是【答案集 ✗】而检索问的是【怎么做 ✗】→ 语义不匹配 ✓
   而 agent_use 里的经验型文档（30_pitfalls / 50_decision_rules）就很容易被查到 ✓
修法: 加一节【通用经验】✓ 短句 ✓ 可照做 ✓ 放在文档最前面 ✓✓
"""
import json
import sys
import urllib.request
import pathlib
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DOC = ROOT / 'agent_kb' / '95_cloud_answers.md'
KB = 'agent_use'
B = 'http://127.0.0.1:8001'

EXPERIENCE = '''# 答题硬约束经验（本地答不达标 → 云重做后提炼）

> 这份来自【让本地模型先答、判官逐条核对、不达标的再让云重做】的实测对照 ✓
> 用途: 遇到带【字数/条目数/格式/结构】硬要求的任务时，照下面做 ✓
> 本地模型最常见的 4 类不达标原因都在这（实测 20 条里 3 条不合格 ✓）

## 一、有字数上限时（最高频的坑 ✗）
- 任务写"300 字以内"就【必须 ≤300 字】✗ —— 本地模型实测会写到 400~600 字 ✗
- 做法：先列要点，再按要点铺陈；【输出前自己数一遍字数】✓ 超了就删到限内 ✓
- 任务写"200 字以内"同理 ✓ 不要"差不多就行"✓

## 二、有明确条目数时
- "分三点"就是【正好三点】✗ 不要两点也不要四点 ✓ 每点要有小标题 ✓
- "列出 5 个"就是【5 个】✓ "至少 4 个维度"就是【表格 4 列以上】✓
- 做法：写完逐条数一遍 ✓

## 三、要求"不要用术语"时
- 不是"少用"✗ 是【不要出现】✓（实测本地还在说"LoRA 微调""LoRA 技术"✗）
- 做法：把技术词换成生活比喻 ✓ 连概念名本身也尽量少提 ✓

## 四、要求"对比表"时
- 必须是【真表格（Markdown 表）】✗ 不是分点罗列 ✓
- 维度数要够 ✓ 最好每行一个维度、每列一个对象 ✓

## 五、要求"说清 A 与 B"时
- 必须【分成两块写】✗ 一块 A 一块 B ✓
- 不能混在一段里让人自己分辨 ✓

## 六、知识性内容（准确性 ✗ 最容易出错）
- 不确定的机制【宁可不写】✗ 不要编 ✗
- 实测本地模型把"LoRA 核心原理"讲错了（说成"参数精简"✗ 漏了低秩分解 A/B 矩阵、
  冻结原权重、只训练新增低秩矩阵这些关键点 ✓）
- 正确表述要点：在原始权重旁【并联一对低秩矩阵 A/B】✓ 前向为 W + BA ✓
  训练时【冻结 W ✗ 只更新 A/B】✓ 因此可训练参数极少、显存与存储开销小 ✓
  推理时可把 BA 合并回 W ✓ 不增加推理延迟 ✓；它【不改变模型能力上限】✗
  主要解决【微调成本】问题 ✓ 对小数据量、新领域适配最有效 ✓

---

'''

def main():
    md = DOC.read_text(encoding='utf-8')
    if md.startswith('# 答题硬约束经验'):
        print('  （已加过 ✓）')
    else:
        DOC.write_text(EXPERIENCE + md, encoding='utf-8')
        print(f'  ✓ 已在最前面加【通用经验】一节（现 {len(DOC.read_text(encoding="utf-8"))} 字符）')

    import kb as kbmod
    info = kbmod.add_documents_to_kb(KB, [str(DOC)])
    print(f'  ✓ 重新入库: {json.dumps(info, ensure_ascii=False)[:180]}')

    with urllib.request.urlopen(B + f'/kb/{KB}', timeout=20) as f:
        d = json.loads(f.read().decode())
    print(f'  {KB}: {len(d.get("documents") or [])} 条目 / {d.get("total_chunks")} 块')
    for x in (d.get('documents') or []):
        if '95_' in str(x.get('name')):
            print(f'    {x.get("name")}: {x.get("chunks")} 块 · {x.get("chars")} 字符')

    print()
    print('  --- ★ 再验检索（这次应该能查到经验节了 ✓）---')
    for q in ('字数上限 怎么满足', '不要用术语 怎么做', 'LoRA 原理 准确说法',
              '对比表 怎么做', '分三点 条目数'):
        r = urllib.request.Request(B + f'/kb/{KB}/query',
            data=json.dumps({'query': q, 'top_k': 3}).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(r, timeout=120) as f:
                dd = json.loads(f.read().decode())
            rs = dd.get('results') or []
            hit = [y for y in rs if '95_' in str(y.get('source'))]
            print(f'    {"✓" if hit else "✗"} {q}')
            for y in rs[:2]:
                print(f'        [{float(y.get("score") or 0):.3f}] {y.get("source")} :: '
                      f'{str(y.get("text"))[:66].replace(chr(10)," ")}')
        except Exception as e:
            print(f'    ✗ {q}: {str(e)[:60]}')


if __name__ == '__main__':
    main()
