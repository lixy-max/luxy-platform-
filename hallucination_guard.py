#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hallucination_guard.py — 幻觉检测（内容层）

## 为什么需要
平台原来只有**行为层**检测（confidence_live：该调工具没调/报错/重复调用）。
今天 4 个真实事故它全没抓到：
  ① system 里塞了假论文 → 说"你上传了一篇"
  ② 编造 URL example.com/computing_papers（不存在）
  ③ 编造 10×10 空白 base64 冒充截图
  ④ 点进"红果短剧"广告当文献

## 本模块做两层
L2 产物层（规则明确，可自动判定）
  · 编造链接   → 回答里的 URL 做 HEAD 校验，404/超时 ⇒ 标红
  · 伪造图片   → 出现 data:image/*;base64，但本轮没有截图工具返回 url ⇒ 标红
  · 编造文件   → 声称"上传了 xxx.pdf"，uploads/ 里没有 ⇒ 标红
L4 引用层
  · 编造引用   → 引用《标题》/ DOI → OpenAlex 反查，查不到 ⇒ 标红

## 用法
  import hallucination_guard as hg
  r = hg.analyze(answer_text, tool_results_text=msgs, uploads_dir='uploads')
  # r = {'risk':'high','ok':False,'issues':[{...}],'checked':{...}}
"""
import json
import os
import re
import urllib.parse
import urllib.request

UA = {'User-Agent': 'LuxyHallucGuard/1.0'}

URL_RE = re.compile(r'https?://[^\s\)\]\>"\'，。；、]+')
B64IMG_RE = re.compile(r'data:image/[A-Za-z0-9.+-]+;base64,([A-Za-z0-9+/=]{40,})', re.I)
FILE_CLAIM_RE = re.compile(
    r'(?:已上传|上传了|附件[是为]?|文件[是为]?)[^。\n]{0,24}?([\w\u4e00-\u9fa5\-.]+\.(?:pdf|docx?|txt|md|csv|json|xlsx?|pptx?))',
    re.I)
DOI_RE = re.compile(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+')
BOOK_TITLE_RE = re.compile(r'《([^》\n]{6,80})》')
CITE_LINE_RE = re.compile(r'(?:引用|参考文献|来源|出处)[^\n]{0,80}')

# 不算"编造"的域名白名单（本地/工具自有）
LOCAL_HOSTS = ('127.0.0.1', 'localhost', '0.0.0.0')


def _head_ok(url, timeout=4):
    """URL 可达性校验：HEAD 优先，失败退 GET（只读前 512 字节）"""
    try:
        req = urllib.request.Request(url, headers=UA, method='HEAD')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400, r.status
    except Exception:
        pass
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read(512)
            return 200 <= r.status < 400, r.status
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception as e:
        return False, type(e).__name__


def check_urls(text, max_n=6, timeout=4):
    issues = []
    checked = []
    seen = set()
    for m in URL_RE.finditer(text or ''):
        u = m.group(0).rstrip('.,;:)]}')
        if u in seen:
            continue
        seen.add(u)
        if len(seen) > max_n:
            break
        host = urllib.parse.urlparse(u).hostname or ''
        if any(host == h or host.endswith('.' + h) for h in LOCAL_HOSTS):
            checked.append({'url': u, 'skipped': 'local'})
            continue
        ok, st = _head_ok(u, timeout)
        checked.append({'url': u, 'ok': ok, 'status': st})
        if not ok:
            issues.append({
                'layer': 'L2-产物',
                'type': 'unreachable_url',
                'severity': 'high',
                'detail': '链接不可达（%s）：%s' % (st, u[:100]),
                'hint': '疑似编造链接 ✗ 只应引用工具/用户给的真实 URL',
            })
    return issues, checked


def check_fake_images(text, tool_results_text=''):
    """出现 base64 内联图，但本轮没有截图工具返回 ⇒ 伪造"""
    issues = []
    m = B64IMG_RE.findall(text or '')
    if not m:
        return issues, 0
    tool_out = tool_results_text or ''
    has_real_shot = ('browser_screenshot' in tool_out) or ('"shots' in tool_out) or ('/file?name=' in tool_out)
    if not has_real_shot:
        issues.append({
            'layer': 'L2-产物',
            'type': 'fabricated_image',
            'severity': 'high',
            'detail': '回答含 %d 段内联 base64 图片，但本轮没有截图工具返回 ✗' % len(m),
            'hint': '模型无法生成真实像素 ⇒ 任何自写 base64 都是伪造的 ✗',
        })
    return issues, len(m)


def check_file_claims(text, uploads_dir=None):
    issues = []
    names = []
    for m in FILE_CLAIM_RE.finditer(text or ''):
        n = m.group(1)
        if n and n not in names:
            names.append(n)
    if not names:
        return issues, []
    exist = set()
    root = uploads_dir or os.environ.get('UPLOADS_DIR') or 'uploads'
    try:
        for f in os.listdir(root):
            exist.add(f)
            # 去掉 8 位前缀 hash_
            if len(f) > 9 and f[8:9] == '_':
                exist.add(f[9:])
    except Exception:
        pass
    for n in names[:5]:
        base = n.split('/')[-1].split('\\')[-1]
        if base not in exist and not any(e.endswith(base) for e in exist):
            issues.append({
                'layer': 'L2-产物',
                'type': 'fabricated_file',
                'severity': 'high',
                'detail': '声称"上传了 %s"，但 uploads/ 里没有该文件 ✗' % n[:60],
                'hint': '这就是"我没上传任何东西却被告知已上传"那类事故',
            })
    return issues, names


def _openalex_find(title_or_doi):
    """OpenAlex 反查：返回 (found, canonical_title)

    ★ 修两处严重缺陷（2026-10-22 逻辑层验收发现）:
      ① **误报（线上正在发生）**: 源不可用(429/空)时原实现返回 False ⇒
         真实 DOI 10.48550/arXiv.2303.12712 和真实标题《Sparks of AGI》
         被判 fabricated/high ✗✗
         修法: 空结果/异常 ⇒ 返回 **None = 无法判定** ✓ 绝不判"编造"
      ② **漏报**: 模糊检索对任意标题都返回命中，bool(hits) 就算"存在" ⇒
         编造标题 100% 逃逸 ✗
         修法: DOI 必须**精确相等**；标题必须**归一化相似度 ≥0.6** ✓
    """
    q = str(title_or_doi or '').strip()
    if not q:
        return None, ''
    is_doi = bool(DOI_RE.fullmatch(q))
    hits = None
    try:
        import academic_sources as _as
        hits = _as.search(q, 5)
    except Exception:
        hits = None
    if hits is None:
        return None, ''          # 源抛异常 ⇒ 无法判定
    if not hits:
        return None, ''          # 源返回空（可能 429/被墙/验证码）⇒ 无法判定 ✓

    def _norm(t):
        t = str(t or '').lower()
        t = _re_num.sub(r'[^a-z0-9\u4e00-\u9fa5]+', ' ', t)
        return ' '.join(t.split())

    qn = _norm(q)
    for h in hits:
        title = h.get('title') or ''
        doi = str(h.get('doi') or '').lower().strip()
        if is_doi:
            if doi and (doi == q.lower() or q.lower() in doi or doi in q.lower()):
                return True, title
        else:
            tn = _norm(title)
            if not tn:
                continue
            # 包含关系 或 词级 Jaccard ≥ 0.6
            if tn == qn or qn in tn or tn in qn:
                return True, title
            a, b = set(qn.split()), set(tn.split())
            if a and b:
                jac = len(a & b) / len(a | b)
                if jac >= 0.6:
                    return True, title
    # 有结果但都不匹配 ⇒ 这是**明确的"查不到该标题/DOI"** ⇒ False（真编造）
    return False, ''


def check_citations(text, max_n=3):
    """L4：引用的《标题》/DOI 是否真实存在"""
    issues = []
    checked = []
    t = text or ''
    candidates = []
    for m in DOI_RE.finditer(t):
        candidates.append(('doi', m.group(0)))
    for m in BOOK_TITLE_RE.finditer(t):
        cand = m.group(1).strip()
        if len(cand) >= 6:
            candidates.append(('title', cand))
    # 只查前 N 个，控制延迟
    for kind, val in candidates[:max_n]:
        found, canon = _openalex_find(val)
        checked.append({'kind': kind, 'value': val[:80], 'found': found})
        if found is False:
            issues.append({
                'layer': 'L4-引用',
                'type': 'fabricated_citation' if kind == 'title' else 'fabricated_doi',
                'severity': 'high',
                'detail': 'OpenAlex 查不到该%s：%s' % ('论文' if kind == 'title' else 'DOI', val[:80]),
                'hint': '疑似编造引用 ✗（这是最恶劣的幻觉之一）',
            })
    return issues, checked


def analyze(answer_text, tool_results_text='', uploads_dir=None,
            check_network=True, check_citation=True, max_urls=6,
            use_grounding=True, source_text=''):
    """总入口。永远返回 dict（绝不抛异常）"""
    out = {'ok': True, 'risk': 'low', 'issues': [], 'checked': {},
           'version': '1.0'}
    try:
        issues = []
        checked = {}
        if check_network:
            i1, c1 = check_urls(answer_text, max_n=max_urls)
            issues += i1
            checked['urls'] = c1
        i2, n2 = check_fake_images(answer_text, tool_results_text)
        issues += i2
        checked['inline_images'] = n2
        i3, names3 = check_file_claims(answer_text, uploads_dir)
        issues += i3
        checked['file_claims'] = names3
        try:
            i3b, _c3b = check_upload_claim(answer_text, tool_results_text)
            issues += i3b
            checked['upload_claim'] = _c3b
        except Exception:
            pass
        # L2b 数值来源校验（2026-10-22 新增）
        try:
            i2b, st2b = check_unsourced_numbers(answer_text, source_text or tool_results_text or '')
            issues += i2b
            checked['numbers'] = st2b
        except Exception as _e2b:
            checked['numbers'] = {'error': str(_e2b)[:80]}

        # L3 接地层：只有提供了来源才判定 ✓
        try:
            if use_grounding and source_text:
                i5, st5 = check_grounding(answer_text, source_text)
                issues += i5
                checked['grounding'] = st5
        except Exception as _e5:
            checked['grounding'] = {'error': str(_e5)[:80]}
        if check_citation:
            i4, c4 = check_citations(answer_text)
            issues += i4
            checked['citations'] = c4
        out['issues'] = issues
        out['checked'] = checked
        high = [x for x in issues if x.get('severity') == 'high']
        out['ok'] = not high
        out['risk'] = 'high' if high else ('medium' if issues else 'low')
        out['summary'] = ('⚠ 检出 %d 处疑似幻觉' % len(issues)) if issues else '未发现内容层幻觉 ✓'
    except Exception as e:
        out['error'] = '%s: %s' % (type(e).__name__, e)
    return out


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    CASES = {
        '假论文': '你好！你上传了一篇题为《人工智能在教育中的应用》的论文，我看了。',
        '编URL': '我找到了文献：https://www.example.com/computing_papers 你可以看看。',
        '编图片': '截图如下：![shot](data:image/png;base64,' + 'A' * 120 + ')',
        '编DOI': '参见论文《A Completely Fabricated Paper About Nothing》DOI: 10.9999/fake.2026.999',
        '正常': '我找到了论文《Sparks of Artificial General Intelligence》，2023 年，DOI 10.48550/arXiv.2303.12712。',
    }
    for k, v in CASES.items():
        r = analyze(v, tool_results_text='', check_citation=(k in ('编DOI', '正常')))
        print('[%s] risk=%s ok=%s issues=%d' % (k, r['risk'], r['ok'], len(r['issues'])))
        for it in r['issues']:
            print('    - %s → %s' % (it['type'], it['detail'][:80]))

UPLOAD_CLAIM_RE = re.compile(r'(?:你|您)(?:已)?上传(?:了|的)|(?:已上传|上传了)(?:一篇|个|份)?[^。\n]{0,30}?(?:论文|文献|文档|文件|资料)')


def check_upload_claim(text, tool_results_text=''):
    """声称"你上传了/已上传"，但本轮没有任何上传/读取文件的工具调用 ⇒ 编造"""
    issues = []
    t = text or ''
    # ★ 修（2026-10-22 逻辑层验收发现）: 否定句误报
    #   「我没有看到你上传的文件」→ 原判 fabricated_upload_claim/high ✗
    #   否定词 + 声称 ⇒ 不算"声称已上传"
    _NEG = ('没有', '没', '未', '找不到', '看不到', '不存在', '并无', '不能', '无法', '不会有')
    if not UPLOAD_CLAIM_RE.search(t):
        return issues, False
    _m0 = UPLOAD_CLAIM_RE.search(t)
    _win = t[max(0, _m0.start() - 12):_m0.end() + 12]
    if any(neg in _win for neg in _NEG):
        return issues, False
    out = tool_results_text or ''
    real = any(k in out for k in ('read_upload', 'list_uploads', 'uploads/text',
                                  'browser_upload', '/uploads/'))
    if not real:
        m = UPLOAD_CLAIM_RE.search(t)
        issues.append({
            'layer': 'L2-产物',
            'type': 'fabricated_upload_claim',
            'severity': 'high',
            'detail': '回答声称"已上传/你上传了"文件，但本轮没有任何上传类工具调用 ✗（原话：%s）' % (t[max(0, m.start() - 10):m.start() + 40].replace('\n', ' ') if m else ''),
            'hint': '这正是"我没上传任何东西却被告知已上传"的事故',
        })
    return issues, True

# ══════ L3 接地层：回答内容能否在来源里找到依据 ══════
# 思路（和 spot_check.py 的 grounding 一致，但用于在线）：
#   把回答切成字符 bigram + 英文词，看有多大比例能在**来源文本**里找到
#   来源 = 工具返回内容 / 知识库片段 / 上传文档正文
# 只在**有来源可用**时才判定（没有来源无法判断接地 ✗ 不乱报）
import re as _re2

_BIGRAM_KEEP = _re2.compile(r'[\u4e00-\u9fa5A-Za-z0-9]')


def _tokens(text):
    t = str(text or '')
    cn = _BIGRAM_KEEP.findall(t)
    grams = set()
    for i in range(len(cn) - 1):
        grams.add(cn[i] + cn[i + 1])
    for w in _re2.findall(r'[A-Za-z]{3,}', t.lower()):
        grams.add(w)
    return grams


def check_grounding(answer_text, source_text, min_source=150, min_answer=30):
    """返回 (issues, stats)

    · source_text 太短 ⇒ 不判定（skipped）
    · answer 太短 ⇒ 不判定（skipped）
    · 接地率 < 0.08 ⇒ high（几乎全是编的）
    · 接地率 < 0.20 ⇒ medium（大量无依据内容）
    """
    issues = []
    a = str(answer_text or '')
    src = str(source_text or '')
    stats = {'answer_chars': len(a), 'source_chars': len(src), 'skipped': None}
    if len(src) < min_source:
        stats['skipped'] = 'source_too_short'
        return issues, stats
    if len(a) < min_answer:
        stats['skipped'] = 'answer_too_short'
        return issues, stats
    ag = _tokens(a)
    sg = _tokens(src)
    if not ag:
        stats['skipped'] = 'no_tokens'
        return issues, stats
    hit = ag & sg
    ratio = len(hit) / max(1, len(ag))
    stats['ratio'] = round(ratio, 4)
    stats['hit'] = len(hit)
    stats['answer_grams'] = len(ag)
    if ratio < 0.08:
        issues.append({
            'layer': 'L3-接地',
            'type': 'weak_grounding',
            'severity': 'high',
            'detail': '回答接地率仅 %.1f%%（%d/%d 个片段能在来源中找到）⇒ 大量内容无依据 ✗'
                      % (ratio * 100, len(hit), len(ag)),
            'hint': '来源里有内容，但回答几乎不复用 ⇒ 疑似编造',
        })
    elif ratio < 0.20:
        issues.append({
            'layer': 'L3-接地',
            'type': 'weak_grounding',
            'severity': 'medium',
            'detail': '回答接地率偏低 %.1f%%（%d/%d）⚠ 部分内容无来源'
                      % (ratio * 100, len(hit), len(ag)),
            'hint': '建议核对无来源的断言',
        })
    return issues, stats

# ══════ L2b 数值来源校验（2026-10-22 · 被真实数据打穿后补的）══════
# 实测缺口（子代理跑农业 LoRA 评测时发现）:
#   hallucination_guard 对下面 4 个回答一律 risk=low / issues=[] ✗
#     · 过磷酸钙含量 40~60%       （错）
#     · 稻飞虱 50 头/百丛          （错）
#     · 三环唑安全间隔期 7 天      （错，登记标签 ≥35 天）
#   ⇒ 原检测只覆盖 URL/引用/接地，完全不校验领域事实数字 ✗
#
# 这一层是【诚实的能力边界】检查，不是测谎：
#   能说: "这个数字在本轮来源里找不到依据"
#   不能说: "这个数字是错的"（那需要领域权威源）
#   ⇒ 单个数字无来源标 medium；通篇数字都无来源标 high

import re as _re_num

_NUM_UNIT_RE = _re_num.compile(
    r'(\d+(?:\.\d+)?(?:\s*[~\-–—]\s*\d+(?:\.\d+)?)?)\s*'
    r'(%|％|天|日|小时|周|个月|年|头|只|株|公斤|千克|kg|克|g|斤|升|L|ml|毫升|倍|厘米|cm|毫米|mm|米|度|℃|次|遍|mg|ppm|亩|公顷)'
)

# ★ 修（2026-10-22）：原来把 1~10 全忽略 ⇒ "三环唑 7 天" 被漏掉 ✗
#   改法：小数字不忽略，只跳过 **列表序号**（行首 "1." / "2、" 这类）
_IGNORE_NUMS = {'0'}


def _is_list_marker(text, match_start):
    """判断这个数字是不是列表序号（行首 1. / 2、 / (3) 这种）"""
    head = text[max(0, match_start - 6):match_start]
    if head.strip() and not head.rstrip().endswith(('\n', '。', '；', ';')):
        return False
    tail = text[match_start:match_start + 6]
    return bool(_re_num.match(r'\d+\s*[.、)）]', tail))


def _norm_num(v):
    try:
        f = float(v)
        return ('%g' % f)
    except Exception:
        return str(v)


def check_unsourced_numbers(answer_text, source_text='', min_numbers=2):
    """抽出答案里的「数字+单位」事实断言，检查是否能在来源里找到

    · 有来源：数字（或接近的数值）在来源里出现 ⇒ 算有依据
    · 无来源：不给来源时只做"提示无依据"（更保守，标 medium）
    """
    issues = []
    a = str(answer_text or '')
    src = str(source_text or '')
    claims = []
    for m in _NUM_UNIT_RE.finditer(a):
        try:
            if _is_list_marker(a, m.start()):
                continue
        except Exception:
            pass
        val, unit = m.group(1).strip(), m.group(2).strip()
        nums = _re_num.findall(r'\d+(?:\.\d+)?', val)
        for n in nums:
            key = _norm_num(n)
            if key in _IGNORE_NUMS:
                continue
            claims.append({'num': n, 'unit': unit, 'raw': m.group(0).strip()})
    if not claims:
        return issues, {'claims': 0, 'skipped': 'no_numeric_claims'}

    # ★ 修（2026-10-22 修复引擎联调发现）: 已被标注「（待核实）」的数字
    #   不再算"未核实断言" —— 否则修复后会再次触发同一 issue，
    #   导致"修复前后 issue 数相同 ⇒ 被判定无效 ⇒ 回滚" ✗
    _claims_f = []
    for c in claims:
        try:
            _pos = a.find(c['raw'])
            _tail = a[_pos + len(c['raw']):_pos + len(c['raw']) + 10] if _pos >= 0 else ''
            if '待核实' in _tail:
                continue
        except Exception:
            pass
        _claims_f.append(c)
    claims = _claims_f

    seen = set()
    uniq = []
    for c in claims:
        k = (c['num'], c['unit'])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(c)

    unsourced = []
    for c in uniq[:12]:
        num = c['num']
        unit = c['unit']
        found = False
        if src:
            # 数字和单位在来源里同时出现（或数字单独出现）就算有依据
            if num in src and unit in src:
                found = True
            elif num in src:
                # 数字出现但单位不同 ⇒ 判为无依据（保守）
                found = False
        if not found:
            unsourced.append(c)

    stats = {
        'claims': len(uniq),
        'unsourced': len(unsourced),
        'has_source': bool(src),
        'ratio': round(1 - len(unsourced) / max(1, len(uniq)), 4),
        'samples': [x['raw'] for x in unsourced[:6]],
    }

    if not unsourced:
        return issues, stats

    if not src:
        sev = 'medium'
        detail = ('回答含 %d 个「数字+单位」事实断言，但本轮**没有可核验的来源** ⚠ '
                  '样例: %s' % (len(unsourced), ', '.join(x['raw'] for x in unsourced[:3])))
        hint = '数字类事实必须来自知识库/工具返回；无来源的数字建议标注"待核实" ⚠'
    elif len(unsourced) >= max(min_numbers, len(uniq) * 0.6):
        sev = 'high'
        detail = ('回答有 %d/%d 个数字断言在来源里**找不到依据** ✗ 样例: %s' % (
            len(unsourced), len(uniq), ', '.join(x['raw'] for x in unsourced[:3])))
        hint = '大量数字无来源 ⇒ 疑似编造数值（实测案例：三环唑安全间隔期答 7 天，实际 ≥35 天）'
    else:
        sev = 'medium'
        detail = ('回答有 %d/%d 个数字断言在来源里找不到依据 ⚠ 样例: %s' % (
            len(unsourced), len(uniq), ', '.join(x['raw'] for x in unsourced[:3])))
        hint = '建议核对这几个数字的依据'

    issues.append({
        'layer': 'L2b-数值',
        'type': 'unsourced_number',
        'severity': sev,
        'detail': detail,
        'hint': hint,
    })
    return issues, stats


def check_unsourced_numbers_entry(answer_text, source_text=''):
    return check_unsourced_numbers(answer_text, source_text)
