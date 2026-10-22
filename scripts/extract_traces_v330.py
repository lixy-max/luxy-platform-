#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T-1/T-2：从新 schema 轨迹提取训练数据（2026-10-19）

数据源（新 schema ✓ 91 工具）:
  t4v    32 条（T4 验证 · 完整 ✓）
  t4fix  4 条（修复验证 ✓）
  coll_r2 / coll_r3 （T-1 采集 ✓）

产出: training_data/exercise_book_v3_3_0.jsonl（新 ✓）
      + 合并旧的 278 条（v3-2-2 ✓）⇒ 目标 ≥ 800 条 ✓

核心改进（vs v3-2-2）:
  · 工具集更大（91 ✓）⇒ 真实轨迹里的工具名更丰富 ✓
  · 数据量更多（32×3 轮 + 4 ≈ 100 条轨迹 ✓）
  · 全部是【真实观测 + 真实错误】✓
"""
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNS = '/mnt/e/WSL/_check/drill/out/runs.jsonl'
# ★ 2026-10-19 修正：采集实际用的是 c12b_r01..r12（28 安全任务 ✓）
#   旧的 c12_r01/r02 也有效（已验证 32/32 + 21/21 全 ok ✓ 53 条 ✓ 别浪费 ✓）
#   t4v/t4fix 是 T4 验证的真实轨迹 ✓ 也算 ✓
TAGS = ['t4v', 't4fix',
        'c12_r01', 'c12_r02',
        'c12b_r01', 'c12b_r02', 'c12b_r03', 'c12b_r04', 'c12b_r05', 'c12b_r06',
        'c12b_r07', 'c12b_r08', 'c12b_r09', 'c12b_r10', 'c12b_r11', 'c12b_r12']
OUT = ROOT / 'training_data/exercise_book_v3_3_0.jsonl'
OUT_EVAL = ROOT / 'training_data/exercise_book_v3_3_0_eval.jsonl'

TOOLS = json.loads((ROOT / 'agent_kb/_tools.json').read_text(encoding='utf-8'))
DOCS = json.loads((ROOT / 'agent_kb/_tool_docs.json').read_text(encoding='utf-8'))['tools']
TOOL_NAMES = set(TOOLS) | set(DOCS)
SKIP_TOOLS = {'parallel_notice'}
SYSTEM = '你是 LoRA 工作台 Agent。需要本机信息先调工具；改动用写类工具；多件事逐件做完；失败要说明。'


TOOL_NAME_RE = re.compile(r'Agent\n([✅❌])\n([a-z_][a-z0-9_]*)\n([\d.]+s)\n参数\n')


def __jump_json(lines, start):
    """从 start 行开始大括号匹配（跳过字符串 ✓）"""
    buf, depth, started, in_str, esc, i, n = [], 0, False, False, False, start, len(lines)
    while i < n:
        ln = lines[i]
        buf.append(ln)
        for ch in ln:
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == '{':
                depth += 1
                started = True
            elif ch == '}':
                depth -= 1
        i += 1
        if started and depth <= 0 and not in_str:
            break
        if len(buf) > 500:
            break
    raw = '\n'.join(buf).strip()
    try:
        return json.loads(raw), i
    except Exception:
        return None, i


def parse_msgs(msgs, phrase='', tool_names=None):
    """返回 (user_text, pairs[(name,args,result)], final_text) 或 []"""
    if not msgs:
        return []
    lines = msgs.split('\n')
    n = len(lines)

    # ① 找所有工具块起点
    starts = []
    i = 0
    while i < n - 5:
        if lines[i].strip() == 'Agent' and i + 4 < n and lines[i + 1].strip() in ('✅', '❌') \
                and lines[i + 4].strip() == '参数':
            starts.append(i)
            i += 1
            continue
        i += 1

    if not starts:
        # 无工具块 ⇒ 无训练价值（gpt 只说不做 ✗）
        user = '\n'.join(lines[:]).strip()
        return []

    # ② 解析每个工具块
    pairs, seg_texts, ends = [], [], []
    for k, s in enumerate(starts):
        name = lines[s + 2].strip()
        try:
            args, j = _jump_json(lines, s + 5)
        except Exception:
            args, j = None, s + 5
        if args is None:
            args = {}
        result = {}
        if j < n and lines[j].strip() == '结果':
            result, j = _jump_json(lines, j + 1)
            if result is None:
                result = {'_raw': 'parse_fail'}
        ends.append(j)
        # 块后到下一个块前的文本 = 助手文本 ✓
        nxt = starts[k + 1] if k + 1 < len(starts) else n
        seg = '\n'.join(lines[j:nxt]).strip()
        seg_texts.append(seg)
        if tool_names is None or name in tool_names:
            if isinstance(args, dict) and isinstance(result, dict):
                pairs.append((name, args, result))

    # ③ 用户文本 = 开头到第一个工具块前（去掉末尾 你 ✓ 和助手开场白 ✓）
    head = '\n'.join(lines[:starts[0]]).strip()
    # 去掉 👤 行和结尾的 你
    head_lines = [l for l in head.split('\n') if l.strip() not in ('👤',)]
    # 用户话 = 第一个空行前的部分（助手开场白通常在之后 ⚠）
    # 旧格式有 '你' 分隔符 ✓ 新格式也有 '你' ✓
    idx = None
    for k, l in enumerate(head_lines):
        if l.strip() == '你':
            idx = k
            break
    if idx is not None:
        user_text = '\n'.join(head_lines[:idx]).strip()
    else:
        user_text = head_lines[0].strip() if head_lines else ''
    if not user_text:
        user_text = (phrase or '').strip()

    # ④ 最终文本 = 最后一个块后的文本（去掉 🤖 标记行 ✓）
    final_text = seg_texts[-1] if seg_texts else ''
    final_lines = [l for l in final_text.split('\n') if l.strip() not in ('🤖',)]
    final_text = '\n'.join(final_lines).strip()

    if not user_text or not pairs or not final_text:
        return []
    return user_text, pairs, final_text


def main():
    rows = [json.loads(l) for l in open(RUNS, encoding='utf-8') if l.strip()]
    mine = [r for r in rows if r.get('tag') in TAGS]
    print('总记录: %d · 目标 tag 记录: %d' % (len(rows), len(mine)))
    for t in TAGS:
        n = sum(1 for r in mine if r.get('tag') == t)
        print('  %-10s %d' % (t, n))

    samples, seen, stats = [], {}, {'parsed': 0, 'no_pairs': 0, 'dup': 0}
    for r in mine:
        out = parse_msgs(r.get('msgs'), r.get('phrase') or r.get('title') or '', TOOL_NAMES)
        if not out:
            stats['no_pairs'] += 1
            continue
        user_text, pairs, final_text = out
        if not pairs or len(final_text) < 30:
            stats['no_pairs'] += 1
            continue
        pairs = pairs[:4]
        # 过滤 _raw 观测（不干净的不要 ✓）
        clean = []
        for name, args, result in pairs:
            if isinstance(result, dict) and '_raw' in result and len(result) == 1:
                break
            clean.append((name, args, result))
        if not clean:
            stats['no_pairs'] += 1
            continue
        pairs = clean
        key = (r.get('id'), tuple(p[0] for p in pairs))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 8:
            stats['dup'] += 1
            continue

        convs = [{'from': 'human', 'value': user_text}]
        for name, args, result in pairs:
            convs.append({'from': 'function_call',
                          'value': json.dumps({'name': name, 'arguments': args}, ensure_ascii=False)})
            convs.append({'from': 'observation', 'value': json.dumps(result, ensure_ascii=False)[:1500]})
        convs.append({'from': 'gpt', 'value': final_text[:1200]})

        # tools 字段：用到的工具 + 少量干扰 ✓（不要放 91 个 ✗ 会爆 token ✗）
        used = list(dict.fromkeys([p[0] for p in pairs]))
        pool = used + [t for t in ('list_knowledge_bases', 'list_loras', 'list_history',
                                   'list_uploads', 'get_settings', 'list_running_tasks')
                       if t not in used][:3]
        tools_json = json.dumps([{'type': 'function', 'function': {
            'name': tn,
            'description': (DOCS.get(tn, {}).get('desc') or tn)[:60],
            'parameters': {'type': 'object', 'properties': {}}}}
            for tn in pool], ensure_ascii=False)

        samples.append({'system': SYSTEM, 'conversations': convs, 'tools': tools_json,
                        '_id': r.get('id'), '_tag': r.get('tag'), '_pairs': len(pairs)})
        stats['parsed'] += 1

    print('')
    print('提取: %s' % stats)
    print('样本: %d' % len(samples))
    from collections import Counter
    ids = Counter(s['_id'] for s in samples)
    print('不同任务: %d 个' % len(ids))
    print('任务分布 Top5: %s' % dict(ids.most_common(5)))
    # 新增工具覆盖率 ✓
    all_tools = Counter()
    for s in samples:
        for c in s['conversations']:
            if c['from'] == 'function_call':
                try:
                    all_tools[json.loads(c['value'])['name']] += 1
                except Exception:
                    pass
    print('涉及工具: %d 种' % len(all_tools))
    print('Top10: %s' % dict(all_tools.most_common(10)))

    # 切分
    import random
    random.seed(20261019)
    random.shuffle(samples)
    n_eval = max(10, int(len(samples) * 0.1))
    ev, tr = samples[:n_eval], samples[n_eval:]
    for path, data in ((OUT, tr), (OUT_EVAL, ev)):
        with open(path, 'w', encoding='utf-8') as f:
            for s in data:
                f.write(json.dumps(s, ensure_ascii=False) + '\n')
    print('')
    print('✓ 训练: %s（%d 条）' % (OUT.name, len(tr)))
    print('✓ 验证: %s（%d 条）' % (OUT_EVAL.name, len(ev)))
    print('')
    print('⇒ 加上旧的 278 条（v3-2-2）⇒ 合计 %d 条' % (len(samples) + 278))


if __name__ == '__main__':
    main()
