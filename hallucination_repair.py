#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""幻觉修复引擎（2026-10-22）

设计目标（用户要求）:
  · **可发现** —— 复用 hallucination_guard 的 7 类检测（领域无关）
  · **可定位** —— 把 issue 定位到原文 span（哪句话、哪个数字）
  · **可修复** —— issue_type → 修复策略 映射表 + 确定性执行器
  · **可验证** —— 修复后重新检测，risk 必须下降，否则回滚
  · **可扩展** —— 领域验证器可插拔（今天物理、明天生物、后天法律）

架构:
  L1 发现   hallucination_guard.analyze()
  L2 定位   locate_issues()  ← 本文件
  L3 修复   REPAIR_STRATEGIES + apply_repairs()
  L4 验证   verify_repair()
  L5 领域验证器  register_validator() + 通用「用户文档当权威源」
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# ══════════════════════════════════════════════════════════════
# L5 领域验证器注册表（可插拔 —— 换领域只需注册一个函数）
# ══════════════════════════════════════════════════════════════
_VALIDATORS = {}


def register_validator(domain, fn, desc=''):
    """注册一个领域验证器

    fn(claim) -> dict
        claim: {'subject': 主体, 'value': 值, 'unit': 单位, 'text': 原文}
        返回: {'ok': True/False/None,  # None = 无法判定
               'correct': 正确值（可选）,
               'source': 依据来源（可选）,
               'note': 说明}
    """
    _VALIDATORS[str(domain)] = {'fn': fn, 'desc': desc}
    return True


def list_validators():
    return [{'domain': k, 'desc': v['desc']} for k, v in _VALIDATORS.items()]


def validate_claim(claim):
    """依次调用所有验证器，返回第一个给出明确判定的结果"""
    out = []
    for dom, v in _VALIDATORS.items():
        try:
            r = v['fn'](claim)
        except Exception as e:
            out.append({'domain': dom, 'ok': None, 'note': '验证器异常: %s' % e})
            continue
        if r is None:
            continue
        r = dict(r or {})
        r['domain'] = dom
        out.append(r)
        if r.get('ok') is not None:
            return r, out
    return (out[0] if out else {'ok': None, 'note': '无可用验证器'}), out


# ── 通用验证器：把「用户自己上传的文档」当权威源 ────────────────
#   这是跨领域最通用的一条：生物、法律、公司制度…都能用，
#   前提是这些文档已经在知识库里（用户自己的权威源）
def _validator_from_kb(claim, kb_query_fn=None, kb_names=None):
    try:
        if kb_query_fn is None:
            import kb as _kb
            kb_query_fn = _kb.query_kb_detailed
        names = kb_names
        if not names:
            try:
                import lora_manager as _lm
                names = list((_lm.load_settings() or {}).get('kb_mounted') or [])
            except Exception:
                names = []
        subject = str(claim.get('subject') or '').strip()
        val = claim.get('value')
        if not subject or val is None or not names:
            return {'ok': None, 'note': '缺少主体或无挂载知识库'}
        q = subject
        chunks = []
        for nm in names[:4]:
            try:
                rs, _dg = kb_query_fn(nm, q, 3)
                for r in (rs or []):
                    t = str(r.get('text') or r.get('content') or '')
                    if t:
                        chunks.append((nm, t))
            except Exception:
                continue
        if not chunks:
            return {'ok': None, 'note': '知识库无相关片段'}
        vs = str(val).strip()
        for nm, t in chunks:
            if vs in t:
                return {'ok': True, 'source': 'KB:%s' % nm, 'note': '知识库中出现同值'}
        # 找同主体的其它数值（判断是否矛盾）
        nums = re.findall(r'(\d+(?:\.\d+)?)\s*(%|％|天|日|小时|年|月|公斤|kg|克|g|元|米|m|头|次|倍|mg|ppm|升|L|ml|℃|度)', ' '.join(t for _n, t in chunks))
        if nums:
            return {'ok': False,
                    'source': 'KB:%s' % chunks[0][0],
                    'correct': None,
                    'note': '知识库中该主题出现的值是: ' + ', '.join('%s%s' % (a, b) for a, b in nums[:4]) + '（与回答中的 %s 不一致）' % vs}
        return {'ok': None, 'note': '知识库有片段但未找到可比值'}
    except Exception as e:
        return {'ok': None, 'note': 'KB 验证器异常: %s' % e}


def _validator_from_sim(claim):
    """计算类验证器：交给 sim_nodes（物理/数学）—— 已有能力，接进来"""
    try:
        node = str(claim.get('node') or '').strip()
        if not node:
            return None
        import sim_nodes as _sn
        fn = getattr(_sn, 'run', None) or getattr(_sn, 'compute', None)
        if not callable(fn):
            return None
        r = fn(node, claim.get('args') or {})
        if isinstance(r, dict) and r.get('ok'):
            return {'ok': True, 'correct': (r.get('result') or {}).get('value'),
                    'source': 'sim:%s' % node}
    except Exception:
        pass
    return None


register_validator('kb_user_docs', _validator_from_kb, '用户自己的文档（知识库）当权威源 —— 跨领域通用')
register_validator('sim_compute', _validator_from_sim, '仿真节点确定性计算（物理/数学）')


# ══════════════════════════════════════════════════════════════
# L2 定位：把 issue 定位到原文 span
# ══════════════════════════════════════════════════════════════
def locate_issues(answer_text, issues):
    """给每个 issue 补 span（起止位置 + 原文片段）

    hallucination_guard 的 issue 里 samples 是出错片段（如 '46%'、假 URL）
    ⇒ 在原文里找它的位置。找不到就退化为"整段标记"
    """
    t = str(answer_text or '')
    out = []
    for it in (issues or []):
        it2 = dict(it)
        spans = []
        samples = []
        # 从 detail 里提取样例（guard 的 detail 形如「样例: 46%, 100公斤」）
        d = str(it.get('detail') or '')
        m = re.search(r'样例[:：]\s*(.+)$', d)
        if m:
            samples = [x.strip() for x in re.split(r'[,，、]', m.group(1)) if x.strip()]
        # 另外直接找 URL / 数值模式
        if it.get('type') == 'unreachable_url':
            samples += re.findall(r'https?://[^\s)）\]>"\']+', t)
        if it.get('type') == 'fabricated_image':
            samples += re.findall(r'!\[[^\]]*\]\([^)]+\)', t)
        # ★ 修（实测）: upload/file 类 issue 的 detail 里没有"样例:"，
        #   但错误本身就是"整句声称" ⇒ 直接按句式在原文里找整句
        if it.get('type') in ('fabricated_upload_claim', 'fabricated_file'):
            for m2 in re.finditer(r'[^。！？\n]*?(上传|收到了|已收到|文件内容|附件)[^。！？\n]*[。！？]?', t):
                snip = m2.group(0).strip()
                if len(snip) >= 6:
                    samples.append(snip)
        # 数值类兜底：detail 里没样例时，抓原文所有「数字+单位」
        if it.get('type') == 'unsourced_number' and not samples:
            samples += [m3.group(0) for m3 in
                        re.finditer(r'\d+(?:\.\d+)?(?:\s*[~\-–—]\s*\d+(?:\.\d+)?)?\s*(?:%|％|天|日|小时|年|月|公斤|kg|克|g|元|米|m|头|次|倍|mg|ppm|升|L|ml|℃|度)', t)][:8]
        for s in samples[:8]:
            pos = t.find(s)
            if pos >= 0:
                spans.append({'start': pos, 'end': pos + len(s), 'text': s})
        # 时间/日期类兜底
        it2['spans'] = spans
        it2['fixable'] = bool(spans) and it.get('type') in REPAIR_STRATEGIES
        out.append(it2)
    return out


# ══════════════════════════════════════════════════════════════
# L3 修复策略（issue_type → 动作）
#    原则: 确定性修复优先（能规则改就规则改），改不了才建议重生
# ══════════════════════════════════════════════════════════════
def _fix_remove_image(text, spans, ctx):
    """编造图片 ⇒ 整段删除 markdown 图片引用"""
    n = 0
    for sp in sorted(spans, key=lambda x: -x['start']):
        text = text[:sp['start']] + text[sp['end']:]
        n += 1
    return text, ('删除 %d 处编造图片引用' % n if n else '')


def _fix_strip_url(text, spans, ctx):
    """不可达 URL ⇒ 删链接地址，保留链接文字"""
    n = 0
    for sp in sorted(spans, key=lambda x: -x['start']):
        raw = sp['text']
        # markdown [文字](url) → 文字
        m = re.fullmatch(r'\[([^\]]*)\]\(([^)]+)\)', raw)
        rep = m.group(1) if m else ''
        text = text[:sp['start']] + rep + text[sp['end']:]
        n += 1
    return text, ('移除 %d 个失效链接地址（保留文字）' % n if n else '')


def _fix_drop_claim_sentence(text, spans, ctx):
    """编造上传声明 / 编造文件 ⇒ 删除整句"""
    n = 0
    for sp in sorted(spans, key=lambda x: -x['start']):
        # 扩到整句
        st, en = sp['start'], sp['end']
        while st > 0 and text[st - 1] not in '。！？\n；;':
            st -= 1
        while en < len(text) and text[en] not in '。！？\n；;':
            en += 1
        if en < len(text):
            en += 1
        text = text[:st] + text[en:]
        n += 1
    return text, ('删除 %d 句无依据的声称' % n if n else '')


def _fix_citation(text, spans, ctx):
    """编造引用 ⇒ 删除引用标记；如检索到真实文献则替换"""
    n = 0
    for sp in sorted(spans, key=lambda x: -x['start']):
        text = text[:sp['start']] + text[sp['end']:]
        n += 1
    return text, ('移除 %d 处无法核实的引用' % n if n else '')


def _fix_unsourced_number(text, spans, ctx):
    """无来源数字 ⇒ ①领域验证器 ②有正确值则替换 ③否则标注待核实"""
    verified, changed = [], 0
    source_text = ctx.get('source_text') or ''
    for sp in sorted(spans, key=lambda x: -x['start']):
        raw = sp['text']
        m = re.match(r'(\d+(?:\.\d+)?(?:\s*[~\-–—]\s*\d+(?:\.\d+)?)?)\s*(.+)$', raw)
        val = m.group(1) if m else raw
        unit = m.group(2).strip() if m else ''
        # 找主体（前 20 字）
        pre = text[max(0, sp['start'] - 24):sp['start']]
        claim = {'subject': pre.strip(), 'value': val, 'unit': unit, 'text': raw}
        r, _all = validate_claim(claim)
        if r.get('ok') is False and r.get('correct') not in (None, ''):
            newv = '%s%s' % (r.get('correct'), unit)
            text = text[:sp['start']] + newv + text[sp['end']:]
            verified.append('  %s → %s（依据 %s）' % (raw, newv, r.get('source')))
            changed += 1
        else:
            # ★ 修（实测）: 原来只在有 source_text 时才标注 ⇒ 无来源时啥也不做 ✗
            #   现在: 验证器没给出正确值 ⇒ 一律标注"待核实"（不删、不编 ✓）
            text = text[:sp['start']] + raw + '（待核实）' + text[sp['end']:]
            verified.append('  %s → 标注（待核实）%s' % (
                raw, ('（%s）' % r.get('note')) if r.get('note') else ''))
            changed += 1
    msg = '处理 %d 处无依据数字' % changed if changed else ''
    if verified:
        msg += '\n' + '\n'.join(verified[:6])
    return text, msg


def _fix_weak_grounding(text, spans, ctx):
    """接地率低 ⇒ 确定性修不了，只能建议重生（不静默乱改）"""
    return text, '接地率偏低——需带更强来源约束重新生成（本引擎不擅自改写）'


REPAIR_STRATEGIES = {
    'fabricated_image':        {'fn': _fix_remove_image,        'auto': True,  'desc': '删除编造图片引用'},
    'unreachable_url':         {'fn': _fix_strip_url,           'auto': True,  'desc': '删失效链接保文字'},
    'fabricated_upload_claim': {'fn': _fix_drop_claim_sentence, 'auto': True,  'desc': '删无依据的上传声称'},
    'fabricated_file':         {'fn': _fix_drop_claim_sentence, 'auto': True,  'desc': '删编造文件声称'},
    'fabricated_citation':     {'fn': _fix_citation,            'auto': True,  'desc': '移除无法核实的引用'},
    'unsourced_number':        {'fn': _fix_unsourced_number,    'auto': True,  'desc': '校验数值：有正确值则替换，否则标待核实'},
    'weak_grounding':          {'fn': _fix_weak_grounding,      'auto': False, 'desc': '需重新生成（不自动改写）'},
}


# ══════════════════════════════════════════════════════════════
# L3+L4 主流程：检测 → 定位 → 修复 → 再检测验证（不达标回滚）
# ══════════════════════════════════════════════════════════════
def repair(answer_text, source_text='', max_rounds=2, check_network=True):
    """修复幻觉并验证

    返回:
      {ok, changed, text, before, after, repairs[], verified, rolled_back, rounds}
    """
    import hallucination_guard as hg
    text = str(answer_text or '')
    before = hg.analyze(text, source_text=source_text,
                        check_network=bool(check_network), check_citation=False)
    init_risk = before.get('risk')
    init_n = len(before.get('issues') or [])
    state = {'text': text, 'risk': init_risk, 'n': init_n,
             'issues': before.get('issues') or []}
    all_repairs = []
    rounds = 0
    for _r in range(max(1, int(max_rounds))):
        rounds += 1
        issues = locate_issues(state['text'], state['issues'])
        todo = [i for i in issues if i.get('fixable') and i.get('spans')]
        if not todo:
            break
        new_text = state['text']
        # 按 span 从后往前改（避免位置漂移）：逐 issue 处理
        for it in todo:
            fn = (REPAIR_STRATEGIES.get(it['type']) or {}).get('fn')
            if not fn:
                continue
            try:
                new_text, msg = fn(new_text, it['spans'], {'source_text': source_text})
            except Exception as e:
                msg = '修复异常: %s' % e
            if msg:
                all_repairs.append({'type': it['type'], 'desc': msg,
                                    'severity': it.get('severity')})
        if new_text == state['text']:
            break
        after = hg.analyze(new_text, source_text=source_text,
                           check_network=False, check_citation=False)
        state = {'text': new_text, 'risk': after.get('risk'),
                 'n': len(after.get('issues') or []), 'issues': after.get('issues') or []}
        # 提前成功
        if state['n'] == 0:
            break

    # L4 验证：risk 必须下降（或 issue 数下降），否则回滚
    rank = {'low': 0, 'medium': 1, 'high': 2, None: 0}
    improved = (rank.get(state['risk'], 0) < rank.get(init_risk, 0)) or (state['n'] < init_n)
    rolled_back = False
    if not improved and state['text'] != text:
        rolled_back = True
        state = {'text': text, 'risk': init_risk, 'n': init_n, 'issues': before.get('issues') or []}

    return {
        'ok': True,
        'changed': state['text'] != text,
        'text': state['text'],
        'before': {'risk': init_risk, 'issues': init_n},
        'after': {'risk': state['risk'], 'issues': state['n']},
        'repairs': all_repairs,
        'verified': bool(improved),
        'rolled_back': rolled_back,
        'rounds': rounds,
        'strategies': {k: {'auto': v['auto'], 'desc': v['desc']} for k, v in REPAIR_STRATEGIES.items()},
        'validators': list_validators(),
    }


# ══════════════════════════════════════════════════════════════
# 自测
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    CASES = [
        ('编造图片',
         '看这张图：![](data:image/png;base64,' + 'A' * 400 + ')\n这就是结果。'),
        ('失效链接',
         '详见 [论文](https://www.example.com/computing_papers) 里的说明。'),
        ('编造上传声明',
         '你上传了一篇题为《人工智能在教育中的应用》的论文，我总结如下：……'),
        ('无来源数字',
         '过磷酸钙含磷量约 46%，每亩施用 100 公斤。'),
        ('正常回答（不该被改）',
         '今天天气不错，适合出去散步。'),
    ]
    for name, txt in CASES:
        r = repair(txt, source_text='')
        print('=' * 70)
        print('%s' % name)
        print('  before: risk=%s issues=%s' % (r['before']['risk'], r['before']['issues']))
        print('  after : risk=%s issues=%s' % (r['after']['risk'], r['after']['issues']))
        print('  changed=%s verified=%s rolled_back=%s rounds=%s' % (
            r['changed'], r['verified'], r['rolled_back'], r['rounds']))
        for x in r['repairs']:
            print('    · [%s] %s' % (x['type'], str(x['desc']).replace('\n', ' / ')[:120]))
        if r['changed']:
            print('  修复后文本: %s' % r['text'][:150].replace('\n', ' '))
    print('=' * 70)
    print('已注册验证器:', list_validators())
