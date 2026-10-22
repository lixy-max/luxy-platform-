#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具秘书训练集 v3（① 只教"该调哪个工具" + A 合成 + C 已有数据）

## 目标（用户确认 ①）
训一个秘书：输入用户话 ⇒ 输出**该调哪个工具** ✓
不教参数填法（合成难 ✗ 留 V2）✓ 不教多步序列（留 V2）✓
⇒ 正好对应今天发现的失败模式: 7B 全部 14 个失败都是 `exec_count=0` ✗

## 数据（用户确认 A+C）
A. 从 92 个工具文档**模板合成** ✓（零 GPU ✓ 无 LLM 调用 ✓）
C. 435 条 exercise_book_v3_3_0_merged ✓（格式已验证 ✓ P-73 证明有效 ✓）
+ 少量反样本（不该调工具 ✓ 教"别乱调"✓）

## 依据
· arXiv:2401.07324 Small LLMs Are Weak Tool Learners ⇒ 小模型工具决策弱 ⇒ 需专门训 ✓
· 今天实测: has_tool 是置信度最强信号 ✓ ⇒ 工具决策是关键 ✓
"""
import json
import pathlib
import random
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / 'agent_kb/_tool_docs.json'
EB = ROOT / 'training_data/exercise_book_v3_3_0_merged.jsonl'
OUT = ROOT / 'data/flywheel/tool_secretary_v3.jsonl'
SYSTEM = '你是工具调用秘书。判断该不该调工具；该调就输出工具调用，不该调就说明不需要。'

# ★ 中文模板（按工具名前缀 ✓ 每个 5-6 种说法 ✓）
TPL = {
    'list':   ['列出{cn}', '现在有哪些{cn}？', '帮我看一下{cn}', '把{cn}列出来', '{cn}都有什么？'],
    'get':    ['查一下{cn}', '看看{cn}是什么', '{cn}现在怎么样？', '帮我获取{cn}', '{cn}的状态如何'],
    'read':   ['读一下{cn}', '打开{cn}看看', '{cn}里面是什么？', '帮我看看{cn}内容'],
    'write':  ['写一个{cn}', '帮我生成{cn}', '创建{cn}', '把内容写成{cn}'],
    'create': ['新建{cn}', '帮我建一个{cn}', '创建{cn}', '生成一个{cn}'],
    'update': ['更新{cn}', '改一下{cn}', '把{cn}改成新的'],
    'delete': ['删掉{cn}', '把{cn}删除', '移除{cn}'],
    'remove': ['删掉{cn}', '把{cn}移除'],
    'run':    ['跑一下{cn}', '执行{cn}', '帮我运行{cn}', '启动{cn}'],
    'start':  ['启动{cn}', '开始{cn}', '帮我起{cn}'],
    'stop':   ['停掉{cn}', '停止{cn}', '把{cn}关掉'],
    'query':  ['查一下{cn}', '在{cn}里搜', '{cn}里有什么？', '帮我查{cn}'],
    'search': ['搜一下{cn}', '找一下{cn}', '{cn}搜索结果给我'],
    'validate': ['校验{cn}', '检查{cn}对不对', '{cn}有没有问题？'],
    'replace': ['在{cn}里替换', '把{cn}里的内容改掉'],
    'set_lines': ['改{cn}的指定行', '在{cn}里改几行'],
    'add':    ['给{cn}加内容', '在{cn}里补充'],
    'condense': ['精简{cn}', '把{cn}压缩一下'],
    'polish': ['润色{cn}', '把{cn}改通顺'],
    'translate': ['翻译{cn}', '把{cn}翻成英文'],
    'proofread': ['校对{cn}', '查{cn}的错别字'],
}
# 反样本（不该调工具 ✓）
NEG = [
    '你好', '谢谢', '在吗', '嗯', '好的', '这是什么意思？',
    '帮我解释一下什么是 LoRA', '介绍一下 QLoRA 和 LoRA 的区别',
    '1+1 等于几？', '现在几点了？', '今天星期几？',
    '你能做什么？', '介绍一下你自己', '什么是知识库？',
    '为什么要做量化？', '谈谈你对 AI 的看法', '给我讲个笑话',
]


def cn_from(name: str, desc: str) -> str:
    """从工具名/描述造中文说明 ✓"""
    d = re.sub(r'^[（(]?[a-z_]+[）)]?\s*', '', desc or '')
    d = d.split('·')[0].split('（')[0].strip()
    if len(d) >= 3:
        return d[:12]
    return name.replace('_', ' ')


def main() -> int:
    print('=' * 74)
    print('  工具秘书训练集 v3（① 只教该调哪个工具 ✓ A+C ✓）')
    print('=' * 74)
    docs = json.loads(DOCS.read_text(encoding='utf-8')).get('tools') or {}
    print('  工具: %d 个 ✓' % len(docs))

    # ── A. 合成 ✓ ──
    syn = []
    matched = 0
    for name, meta in docs.items():
        desc = str(meta.get('desc') or '')
        cn = cn_from(name, desc)
        # 找前缀 ✓
        pre = None
        for k in TPL:
            if name.startswith(k + '_') or name == k:
                pre = k
                break
        if pre is None:
            if name.startswith(('browser_', 'agent_')):
                pre = 'run'
            else:
                continue
        matched += 1
        for t in TPL[pre]:
            task = t.format(cn=cn)
            syn.append({'task': task, 'tool': name})
        # 也加一条英文式 ✓
        syn.append({'task': '请调用 %s' % name, 'tool': name})

    print('  ✅ A 合成: **%d 条** ✓（覆盖 %d 个工具 ✓）' % (len(syn), matched))

    # ── 反样本 ✓ ──
    negs = [{'task': t, 'tool': None} for t in NEG]
    print('  ✅ 反样本: %d 条 ✓' % len(negs))

    # ── C. exercise_book ✓ ──
    eb = []
    if EB.exists():
        for line in EB.open(encoding='utf-8', errors='replace'):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get('tools') and r.get('conversations'):
                eb.append(r)
    print('  ✅ C exercise_book: **%d 条** ✓' % len(eb))

    # ── 生成 sharegpt ✓ ──
    def tools_field(names):
        specs = []
        for nm in names:
            m = docs.get(nm) or {}
            props = {}
            for prm in (m.get('params') or []):
                if isinstance(prm, dict) and prm.get('name'):
                    props[prm['name']] = {'type': str(prm.get('type') or 'string'),
                                          'description': str(prm.get('desc') or '')[:100]}
            specs.append({'type': 'function', 'function': {
                'name': nm, 'description': str(m.get('desc') or nm)[:180],
                'parameters': {'type': 'object', 'properties': props}}})
        return json.dumps(specs, ensure_ascii=False)

    out = []
    for s in syn:
        nm = s['tool']
        out.append({
            'system': SYSTEM,
            'conversations': [
                {'from': 'human', 'value': s['task']},
                {'from': 'function_call',
                 'value': json.dumps({'name': nm, 'arguments': {}}, ensure_ascii=False)},
            ],
            'tools': tools_field([nm]),
            '_src': 'synthetic',
        })
    for n in negs:
        out.append({
            'system': SYSTEM,
            'conversations': [
                {'from': 'human', 'value': n['task']},
                {'from': 'gpt', 'value': '（不需要调用工具，直接回答即可）'},
            ],
            'tools': json.dumps([], ensure_ascii=False),
            '_src': 'negative',
        })
    for r in eb:
        out.append({'system': r.get('system') or SYSTEM,
                    'conversations': r.get('conversations'),
                    'tools': r.get('tools'),
                    '_src': 'exercise_book'})

    random.Random(42).shuffle(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w', encoding='utf-8') as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    print('')
    print('=' * 74)
    print('  产物: %s' % OUT)
    print('  总计: **%d 条** ✓' % len(out))
    for s, n in Counter(r['_src'] for r in out).items():
        print('    %-16s %4d 条（%.0f%%）' % (s, n, n / len(out) * 100))
    print('')
    print('  ✅ 目标 ① 已满足: 教"该调哪个工具" ✓')
    print('  ⚠ 局限: 参数用 {} 空占位 ✗ 不教参数填法 ✗（留 V2）')
    print('  ⚠ 局限: 合成任务的措辞是模板 ⚠ 不如真实用户话自然 ✗')
    print('  ⚠ 局限: exercise_book 那 435 条含多步序列 ✓ 与 ① 目标略超范围 ⚠')
    return 0


if __name__ == '__main__':
    sys.exit(main())
