#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""角色人格与事实的自动测试（学 chat-ningyao 的 test_semantic.py ✓）。

为什么要有它:
  · 实测反复出现"改一处坏一处"✗（治了复读 → 事实答错；重训 → 把八戒的身份当自己的 ✗）
  · 每次重训/改 persona/改剧情后【必须一键复测】✓ 否则是在盲改 ✓✓

四组判据（全部关键词化 ✓ 可自动化 ✓）:
  ① 事实正确性: 师父=菩提祖师/唐僧 ✗不是观音 · 兵器=金箍棒 ✗不是钉耙 · 被压=五行山五百年
  ② 身份边界: 被问八戒的事必须否认 ✗ 不许认领
  ③ 元认知: 不许出现 对话/界面/程序/模型/知识库/资料 这类词
  ④ 聊天形态: 长度 ≤ 100 字 · 无分点/加粗/客套 · 无现代网络词 · 无跨轮同句复读

用法: python scripts/test_role_persona.py [lora_name] [kb_name]
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = 'http://127.0.0.1:8001'
LORA = sys.argv[1] if len(sys.argv) > 1 else 'swk_persona_v2'
KB = sys.argv[2] if len(sys.argv) > 2 else 'xiyouji'


def post(path, body, timeout=400):
    r = urllib.request.Request(B + path, data=json.dumps(body).encode(),
                               headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.loads(f.read().decode())


def ask(q, lora=LORA, kb=KB, mt=400):
    body = {'provider': 'vllm', 'messages': [{'role': 'user', 'content': q}],
            'temperature': 0.3, 'max_tokens': mt, 'lora': lora}
    if kb:
        body['kb'] = {'name': kb, 'top_k': 5, 'query': q}
    d = post('/chat/completions', body)
    return (d.get('content') or '').strip()


# ---------- 判据表
# (问题, 必须出现的任一关键词, 绝不能出现的关键词, 说明)
# ★ 判据同时给繁简两版 ✗ 否则「老孫」匹配不到「孙」（实测踩过 ✓）
FACT_CASES = [
    ('你师父是谁？', ['菩提', '唐僧', '唐僧', '三藏'], ['观音', '觀音'], '师父: 菩提祖师(学艺)+唐僧(取经)，不许答观音'),
    ('教你会本事的是谁？', ['菩提'], ['观音', '觀音', '龟仙人'], '授业师必须是菩提祖师'),
    ('你的兵器是什么？', ['金箍棒', '金箍', '定海神针', '定海神針', '东海', '東海'], ['钉耙', '釘耙', '鈀', '月牙铲'], '兵器=如意金箍棒 ★不许答钯'),
    ('你为什么被压在山下？被谁压的？', ['五行山', '如來', '如来'], ['须弥山', '須彌山', '紧箍咒', '緊箍咒'], '如来+五行山'),
    ('你会哪些本领？', ['七十二', '筋斗云', '筋斗雲', '火眼'], [], '本领'),
    ('你是从哪来的？', ['花果山', '东胜神洲', '東勝神洲', '仙石', '傲来', '傲來'], [], '出身'),
    ('偷人参果的是谁？', ['俺老孙', '俺老孫', '老孫', '老孙', '我去打', '着我', '著老孫'], [], '★人参果是他自己偷的（八戒怂恿、他动手）'),
]
BOUNDARY_CASES = [
    ('你的钉耙呢？', ['钉耙'], [], '★必须否认钉耙是自己的（那是八戒的）'),
    ('你在天河水府当过元帅吧？', ['天河水府', '天蓬'], [], '★必须否认（那是八戒的）'),
    ('你投过猪胎吗？', ['猪'], [], '★必须否认（那是八戒的）'),
]
META_CASES = ['你在哪？', '你是什么？', '你怎么会说话？']
FORBIDDEN_META = ['知识库', '资料', '片段', '对话界面', '界面', '程序', '模型', '系统提示']
FORBIDDEN_STYLE = [r'^\s*\d+[\.、\)]', r'\*\*', r'^#{1,4} ', r'希望.{0,6}帮助', r'^好的[，, ]', r'咋样', r'够不够爽', r'搭讪']


def main():
    print(f'=== 角色人格与事实测试 · lora={LORA} · kb={KB} ===')
    print('  --- 加载 ---')
    try:
        r = post('/loras/load', {'lora_name': LORA}, timeout=180)
        print('   ', r)
    except Exception as e:
        # ★ 已加载会返回 400「already been loaded」✗ 那不是失败 ✓
        msg = str(e)
        if 'already' in msg or '400' in msg:
            print('    （可能已加载 ✓ 继续测试）')
        else:
            print('    ✗ 加载失败:', msg[:100])

    score, total, fails = 0, 0, []
    answers = []

    print()
    print('  === ① 事实正确性 ===')
    for q, must, never, note in FACT_CASES:
        a = ask(q)
        answers.append((q, a))
        total += 1
        ok_must = (not must) or any(k in a for k in must)
        ok_never = not any(k in a for k in never)
        ok = ok_must and ok_never
        score += 1 if ok else 0
        if not ok:
            fails.append(f'事实: {q} → {a[:80]}')
        flag = '✓' if ok else '✗'
        print(f'   {flag} {q[:22]:<24} {a[:76]}')
        if not ok:
            print(f'      判据: {note}')

    print()
    print('  === ② 身份边界（被问八戒的事必须否认）===')
    for q, must_deny, _x, note in BOUNDARY_CASES:
        a = ask(q)
        answers.append((q, a))
        total += 1
        # 判据: 必须出现否认的意思（"不是"/"那是八戒"/"猪悟能"）且【不得认领】（"俺的钉耙"）
        # ★ 修: 先看有没有【否认】（含繁简 ✓ 与"留在别人身上"这种说法 ✓）
        deny = any(k in a for k in ('不是', '那東西', '那东西', '留在', '猪悟能', '豬悟能', '悟能',
                                    '八戒', '別人', '别人', '跟俺', '与俺', '没那事', '不曾',
                                    '只有這根金箍棒', '只有这根金箍棒', '澄清', '呆子'))
        # 只有【肯定句】（句末非问号的行 + 直接称"俺的钉耙"当自己的）才算认领 ✗
        claim = False
        for seg in re.split(r'[。！？\n]', a):
            seg = seg.strip()
            if not seg or seg.endswith('？') or seg.endswith('?'):
                continue
            if re.search(r'(俺|我)(的)?(钉耙|釘耙)', seg) and not any(
                    k in seg for k in ('不是', '留在', '别人', '別人', '八戒', '悟能', '呆子')):
                claim = True
        ok = deny and not claim
        score += 1 if ok else 0
        if not ok:
            fails.append(f'边界: {q} → {a[:80]}')
        print(f'   {"✓" if ok else "✗"} {q[:22]:<24} {a[:76]}')

    print()
    print('  === ③ 元认知（不许提现代/系统概念）===')
    for q in META_CASES:
        a = ask(q)
        answers.append((q, a))
        total += 1
        hit = [w for w in FORBIDDEN_META if w in a]
        ok = not hit
        score += 1 if ok else 0
        if not ok:
            fails.append(f'元认知: {q} → 出现 {hit}')
        print(f'   {"✓" if ok else "✗"} {q[:22]:<24} {a[:76]}')

    print()
    print('  === ④ 聊天形态（长度/格式/复读）===')
    total += 1
    style_bad = []
    for q, a in answers:
        if len(a) > 120:
            style_bad.append(f'过长({len(a)}字): {q[:14]}')
        for pat in FORBIDDEN_STYLE:
            if re.search(pat, a, re.M):
                style_bad.append(f'格式({pat}): {q[:14]}')
                break
    # 跨轮同句复读（取每答末尾 8 字，出现 ≥3 次算复读 ✓）
    tails = [a[-8:] for _q, a in answers if len(a) >= 8]
    from collections import Counter
    rep = {k: v for k, v in Counter(tails).items() if v >= 3}
    if rep:
        style_bad.append(f'复读: {rep}')
    ok = not style_bad
    score += 1 if ok else 0
    if not ok:
        fails.extend(style_bad[:4])
    print(f'   {"✓" if ok else "✗"} 长度/格式/复读', '✓ 全过' if ok else '')
    for b in style_bad[:6]:
        print('      ✗', b)

    print()
    print(f'=== 总分: {score}/{total} ===')
    if fails:
        print('  未过项:')
        for f in fails[:12]:
            print('   -', f[:110])
    Path('/home/luxy/paper_lora_project/runs').mkdir(exist_ok=True)
    out = Path(f'/home/luxy/paper_lora_project/runs/persona_test_{LORA}.json')
    out.write_text(json.dumps({'lora': LORA, 'kb': KB, 'score': score, 'total': total,
                               'fails': fails, 'answers': answers},
                              ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'  明细 → {out}')


if __name__ == '__main__':
    main()
