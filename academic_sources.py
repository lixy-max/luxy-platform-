"""接入 OpenAlex + 中国科技论文在线 作为 academic_search 的来源（2026-10-22）

实测可达性（WSL）:
  api.openalex.org        200 ✓  ← 2.5 亿开放文献，无需 key
  www.paper.edu.cn        200 ✓  ← 中国科技论文在线（开放获取）
  arxiv.org               000 ✗  ← 被墙（原实现主源，所以一直返回 0 条）
  api.semanticscholar.org 429 ✗  ← 限流
"""
import json
import urllib.request
import urllib.parse
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
UA = {'User-Agent': 'LuxyPlatform/1.0 (research; mailto:luxy@example.com)'}


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as x:
        return x.read().decode('utf-8', 'replace')


def openalex_search(query, limit=8):
    """OpenAlex: 全球开放学术数据库，无需 API key ✓"""
    url = ('https://api.openalex.org/works?search=' + urllib.parse.quote(query)
           + '&per-page=' + str(max(1, min(25, int(limit))))
           + '&sort=relevance_score:desc')
    d = json.loads(_get(url))
    out = []
    for w in (d.get('results') or []):
        title = (w.get('title') or '').strip()
        if not title:
            continue
        year = w.get('publication_year')
        doi = (w.get('doi') or '').replace('https://doi.org/', '')
        # 开放获取 PDF 链接
        oa = (w.get('best_oa_location') or w.get('primary_location') or {}) or {}
        pdf = oa.get('pdf_url') or ''
        landing = oa.get('landing_page_url') or (w.get('doi') or '')
        authors = [a.get('author', {}).get('display_name') for a in (w.get('authorships') or [])[:4]]
        out.append({
            'title': title[:200],
            'year': year,
            'doi': doi,
            'authors': [a for a in authors if a],
            'url': landing or '',
            'pdf_url': pdf or '',
            'venue': ((w.get('primary_location') or {}).get('source') or {}).get('display_name') or '',
            'cited_by': w.get('cited_by_count') or 0,
            'source': 'openalex',
        })
    return out


def paper_edu_search(query, limit=5):
    """中国科技论文在线（开放获取中文论文）—— 兜底用"""
    import html
    url = 'https://www.paper.edu.cn/releasepaper/search?q=' + urllib.parse.quote(query)
    try:
        txt = _get(url, timeout=20)
    except Exception:
        return []
    out = []
    for m in re.finditer(r'href="(/releasepaper/content/\d+)"[^>]*>(.*?)</a>', txt, re.S):
        path, name = m.group(1), re.sub(r'<[^>]+>', '', m.group(2))
        name = html.unescape(name).strip()
        if not name or len(name) < 6:
            continue
        out.append({
            'title': name[:200], 'year': None, 'doi': '', 'authors': [],
            'url': 'https://www.paper.edu.cn' + path, 'pdf_url': '',
            'venue': '中国科技论文在线', 'cited_by': 0, 'source': 'paper.edu.cn',
        })
        if len(out) >= limit:
            break
    return out


def search(query, limit=8):
    """统一入口：OpenAlex 优先 → 中国科技论文在线兜底"""
    res = []
    try:
        res = openalex_search(query, limit)
    except Exception as e:
        print('[academic] openalex 失败: %s' % e, flush=True)
    if len(res) < 3:
        try:
            res += paper_edu_search(query, limit - len(res))
        except Exception as e:
            print('[academic] paper.edu.cn 失败: %s' % e, flush=True)
    return res


if __name__ == '__main__':
    q = sys.argv[1] if len(sys.argv) > 1 else 'artificial general intelligence'
    r = search(q, 6)
    print('query:', q, '→', len(r), 'hits')
    for i, x in enumerate(r):
        print('[%d] %s' % (i, x['title'][:80]))
        print('    year=%s cited=%s src=%s' % (x['year'], x['cited_by'], x['source']))
        print('    pdf=%s' % (x['pdf_url'] or '(none)'))
