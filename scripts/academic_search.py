#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""academic_search.py — 优质论文检索（Grounding-first · 2026-10-21）

★ 目的（用户想法）: **模型遇到问题优先检索优质论文网站，而不是幻觉推理** ✓
★ 设计原则: **API 优先 ✓ 浏览器兜底 ✓**（API 结构化/快/稳 ✓ 浏览器慢且易坏 ⚠）
★ 实测依据: arXiv 1086ms ✓ / CrossRef 1672ms ✓ / Semantic Scholar 429 限流（需退避 ⚠）

★ 为什么这能治幻觉（我们自己的数据 ✓）:
   ✗ KB 被动注入 = 系统塞片段 ⇒ 4B 分不清"参考"与"任务" ✗（实测 −29.5pp ✗）
   ✓ 主动检索   = 模型自己决定"我要查" ⇒ 拿到的是**结构化事实 + 可验证 URL** ✓
   ⇒ 关键区别: **主动 vs 被动** ✓ 且**可引用性**（每个事实能追到 URL ✓）

用法:
  python scripts/academic_search.py "query"                  # 三源合并检索
  python scripts/academic_search.py "query" --source arxiv    # 指定源
  python scripts/academic_search.py "query" --json            # JSON 输出
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

UA = {'User-Agent': 'luxy-academic-search/1.0 (research use)'}


def _http(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')


# ══════════════════════════════════════════════════════════
# ① arXiv（免费 ✓ 最稳 ✓ 实测 1086ms ✓）
# ══════════════════════════════════════════════════════════
def search_arxiv(q: str, limit: int = 5) -> list[dict]:
    url = ('http://export.arxiv.org/api/query?search_query=all:%s'
           '&start=0&max_results=%d&sortBy=relevance' % (urllib.parse.quote(q), limit))
    try:
        raw = _http(url, 20)
    except Exception as e:
        print('    arXiv ✗ %s' % str(e)[:70], file=sys.stderr)
        return []
    out = []
    for e in re.findall(r'<entry>(.*?)</entry>', raw, re.S)[:limit]:
        def g(tag, dotall=False):
            m = re.search(r'<%s>(.*?)</%s>' % (tag, tag), e, re.S if dotall else 0)
            return m.group(1).strip() if m else ''
        out.append({
            'source': 'arxiv',
            'title': re.sub(r'\s+', ' ', g('title'))[:300],
            'url': g('id'),
            'date': g('published')[:10],
            'abstract': re.sub(r'\s+', ' ', g('summary', True))[:600],
            'authors': re.findall(r'<name>(.*?)</name>', e)[:6],
            'verified': True,
        })
    return out


# ══════════════════════════════════════════════════════════
# ② CrossRef（免费 ✓ DOI 可校验 ✓ 实测 1672ms ✓）
# ══════════════════════════════════════════════════════════
def search_crossref(q: str, limit: int = 5) -> list[dict]:
    url = ('https://api.crossref.org/works?query=%s&rows=%d'
           '&select=title,author,DOI,issued,container-title,abstract'
           % (urllib.parse.quote(q), limit))
    try:
        d = json.loads(_http(url, 20))
    except Exception as e:
        print('    CrossRef ✗ %s' % str(e)[:70], file=sys.stderr)
        return []
    out = []
    for it in ((d.get('message') or {}).get('items') or [])[:limit]:
        t = (it.get('title') or [''])[0]
        doi = it.get('DOI') or ''
        yr = ((it.get('issued') or {}).get('date-parts') or [[None]])[0][0]
        authors = []
        for a in (it.get('author') or [])[:6]:
            n = ' '.join(x for x in [a.get('given'), a.get('family')] if x)
            if n:
                authors.append(n)
        out.append({
            'source': 'crossref',
            'title': re.sub(r'\s+', ' ', str(t))[:300],
            'url': ('https://doi.org/' + doi) if doi else '',
            'doi': doi,
            'date': str(yr or ''),
            'abstract': re.sub(r'<[^>]+>', '', str(it.get('abstract') or ''))[:600],
            'authors': authors,
            'venue': (it.get('container-title') or [''])[0][:120],
            'verified': bool(doi),
        })
    return out


# ══════════════════════════════════════════════════════════
# ③ Semantic Scholar（免费 ✓ 但限流 ⚠ 需退避 ✓）
# ══════════════════════════════════════════════════════════
def search_s2(q: str, limit: int = 5, retries: int = 3) -> list[dict]:
    url = ('https://api.semanticscholar.org/graph/v1/paper/search'
           '?query=%s&limit=%d&fields=title,year,authors,abstract,url,citationCount,venue'
           % (urllib.parse.quote(q), limit))
    for attempt in range(retries):
        try:
            d = json.loads(_http(url, 20))
            out = []
            for p in (d.get('data') or [])[:limit]:
                out.append({
                    'source': 'semantic_scholar',
                    'title': re.sub(r'\s+', ' ', str(p.get('title') or ''))[:300],
                    'url': p.get('url') or '',
                    'date': str(p.get('year') or ''),
                    'abstract': re.sub(r'\s+', ' ', str(p.get('abstract') or ''))[:600],
                    'authors': [a.get('name', '') for a in (p.get('authors') or [])[:6]],
                    'venue': str(p.get('venue') or '')[:120],
                    'citations': p.get('citationCount'),
                    'verified': True,
                })
            return out
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 3 * (attempt + 1)          # ★ 退避 3s → 6s → 9s ✓
                print('    S2 限流(429) ⇒ 等 %ds 重试 ⚠' % wait, file=sys.stderr)
                time.sleep(wait)
                continue
            print('    S2 ✗ HTTP %s' % e.code, file=sys.stderr)
            return []
        except Exception as e:
            print('    S2 ✗ %s' % str(e)[:70], file=sys.stderr)
            return []
    return []


# ══════════════════════════════════════════════════════════
# 合并 + 去重 + 排序 ✓
# ══════════════════════════════════════════════════════════
def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        # 标题归一化做去重键 ✓
        k = re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', (it.get('title') or '').lower())[:80]
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def rank(items: list[dict], q: str) -> list[dict]:
    """打分: 标题命中 > 摘要命中 > 有 DOI/URL > 有引用数 ✓"""
    q_words = [w.lower() for w in re.findall(r'[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}', q)]
    for it in items:
        s = 0.0
        t = (it.get('title') or '').lower()
        a = (it.get('abstract') or '').lower()
        for w in q_words:
            if w in t:
                s += 3.0
            if w in a:
                s += 1.0
        if it.get('verified'):
            s += 2.0
        if it.get('url'):
            s += 1.0
        if it.get('citations'):
            s += min(3.0, it['citations'] / 100.0)
        it['score'] = round(s, 2)
    return sorted(items, key=lambda x: -x.get('score', 0))


# ══════════════════════════════════════════════════════════
# 主流程 ✓
# ══════════════════════════════════════════════════════════
def search(q: str, sources=('arxiv', 'crossref', 's2'), limit: int = 5,
           quiet: bool = False) -> list[dict]:
    """检索 ✓ quiet=True 时不打印人类可读输出（--json 用 ✓）"""
    def p(*a, **kw):
        if not quiet:
            print(*a, **kw)

    p('=' * 76)
    p('  优质论文检索 · query=%r' % q)
    p('=' * 76)
    all_items = []
    if 'arxiv' in sources:
        t0 = time.time()
        r = search_arxiv(q, limit)
        p('  arXiv:    %d 条（%.0f ms）' % (len(r), (time.time() - t0) * 1000))
        all_items += r
    if 'crossref' in sources:
        t0 = time.time()
        r = search_crossref(q, limit)
        p('  CrossRef: %d 条（%.0f ms）' % (len(r), (time.time() - t0) * 1000))
        all_items += r
    if 's2' in sources:
        t0 = time.time()
        r = search_s2(q, limit)
        p('  S2:       %d 条（%.0f ms）' % (len(r), (time.time() - t0) * 1000))
        all_items += r

    d = dedupe(all_items)
    d = rank(d, q)
    p('')
    p('  去重后: %d 条 · 按相关度排序 ✓' % len(d))
    p('')
    for i, it in enumerate(d[:10], 1):
        p('  [%d] %.1f 分 · %s' % (i, it.get('score', 0), (it.get('title') or '')[:66]))
        p('      %s · %s' % (it.get('date') or '—', (it.get('venue') or it.get('source'))[:50]))
        p('      %s' % (it.get('url') or '（无 URL ⚠）'))
        ab = it.get('abstract') or ''
        if ab:
            p('      %s...' % ab[:130])
        p('')
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description='优质论文检索（Grounding-first）')
    ap.add_argument('query', help='检索词')
    ap.add_argument('--source', default='all', choices=['all', 'arxiv', 'crossref', 's2'])
    ap.add_argument('--limit', type=int, default=5)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    src = ('arxiv', 'crossref', 's2') if a.source == 'all' else (a.source,)
    items = search(a.query, src, a.limit, quiet=a.json)
    if a.json:
        # ★ --json 只输出 JSON ✓ 不混其他文本 ✓
        sys.stdout.write(json.dumps(items, ensure_ascii=False, indent=2) + '\n')
    return 0 if items else 1


if __name__ == '__main__':
    sys.exit(main())
