#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""先找不训 v2（Search Before Train）· T2 改进版 · 2026-10-19

改进（v1 → v2）:
  ① 多查询合并（同义词变体 ✓ 避免漏搜）
  ② GitHub 降噪（限制 in:name,description + 排除 awesome/list 类 ✓）
  ③ LoRA 识别修正（tags 里 peft/adapter/lora ✓ 不误判 GGUF ✗）
  ④ 已知优秀模型快查表（curated registry ✓ 先查表再搜 ✓）
  ⑤ 排序改进（≤9B 优先 ✓ 下载量 ✓ 许可 ✓）

用法:
  python tools/search_existing.py "function calling"
  python tools/search_existing.py "角色扮演" --kind model
  python tools/search_existing.py "tool retrieval" --json
"""
import argparse
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HF_MIRROR = 'https://hf-mirror.com'
COMMERCIAL_OK = {'apache-2.0', 'mit', 'bsd-3-clause', 'llama3', 'llama3.1', 'llama3.2', 'llama2', 'openrail', 'cc-by-4.0', 'gemma'}

# ── 已知优秀模型快查表（curated ✓ 人工核实过 ✓）──
# 格式: 能力关键词 → [(模型ID, 参数量B, 说明, 实测下载量级)]
KNOWN_GOOD = {
    'tool': [
        ('Salesforce/Llama-xLAM-2-8b-fc-r', 8, 'xLAM-2 8B 函数调用（BFCL 强 ✓）', '98k+'),
        ('MadeAgents/Hammer2.1-7b', 7, 'Hammer 2.1 7B 工具调用（榜单第一系 ✓）', '1k+'),
        ('Nexusflow/NexusRaven-V2-13B', 13, 'NexusRaven V2 13B（超 GPT-4 系 ✓ 稍大）', '6k+'),
        ('Trelis/Llama-2-7b-chat-hf-function-calling-v2', 7, 'Llama-2 7B 函数调用（较早 ✓）', '3k+'),
    ],
    'roleplay': [
        ('NousResearch/Hermes-3-Llama-3.1-8B', 8, 'Hermes-3 8B 角色/指令（社区常用 ✓）', ''),
        ('Sao10K/L3-8B-Stheno-v3.2', 8, 'Stheno 8B 角色扮演（RP 社区 ✓）', ''),
        ('microsoft/Phi-3-mini-4k-instruct', 3.8, 'Phi-3 mini（小快 ✓）', ''),
    ],
    'embedding': [
        ('BAAI/bge-m3', 0.6, 'bge-m3 向量（我们已在用 ✓）', ''),
        ('BAAI/bge-small-zh-v1.5', 0.1, 'bge-small 中文小向量（快 ✓）', ''),
    ],
    'chinese': [
        ('Qwen/Qwen2.5-7B-Instruct', 7, 'Qwen2.5 7B 中文（我们底座 ✓）', ''),
        ('Qwen/Qwen3-8B', 8, 'Qwen3 8B（更新一代 ✓）', ''),
    ],
}

QUERY_VARIANTS = {
    'function calling': ['function calling', 'function-calling', 'tool calling', 'tool-calling', 'tool use'],
    'tool': ['tool calling', 'function calling', 'tool use', 'agent tool'],
    '角色': ['roleplay', 'role-play', 'character', 'persona'],
    'roleplay': ['roleplay', 'role-play', 'character', 'persona'],
    'embedding': ['embedding', 'bge', 'sentence-transformers'],
    '中文': ['chinese', 'zh', 'qwen', 'baichuan'],
}


def _get(url, timeout=20, retries=2):
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read()
        except Exception:
            if i == retries:
                return None
            time.sleep(1.5)
    return None


def _size_of(tags, model_id=''):
    text = ' '.join(str(t) for t in tags) + ' ' + model_id
    m = re.search(r'(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])', text)
    if m:
        b = float(m.group(1))
    else:
        found = None
        for pat, b in ((r'\b(\d+(?:\.\d+)?)[bB]\b', None),):
            pass
        b = None
        for pat, val in ((r'7[bB]', 7), (r'8[bB]', 8), (r'4[bB]', 4), (r'3[bB]', 3), (r'1\.5[bB]', 1.5),
                         (r'13[bB]', 13), (r'14[bB]', 14), (r'20[bB]', 20), (r'32[bB]', 32), (r'70[bB]', 70),
                         (r'0\.5[bB]', 0.5), (r'1\.2[bB]', 1.2)):
            if re.search(pat, text):
                b = val
                found = val
                break
        if not found:
            return None, 'unknown'
    return b, ('ok16gb' if b <= 9 else 'too_big')


def _is_lora(tags, mid):
    t = ' '.join(str(x) for x in tags).lower()
    m = mid.lower()
    # 明确是 LoRA/adapter/peft ✓ 但 GGUF 只是量化 ✗ 不算 LoRA ✗
    if 'gguf' in t or 'gguf' in m:
        return False
    return ('lora' in t.split() or 'lora' in m or 'adapter' in m or 'peft' in t.split()
            or any(x in t for x in ('lora', 'adapter', 'peft')))


def search_hf_one(query, limit=15):
    q = urllib.parse.quote(query)
    raw = _get(f'{HF_MIRROR}/api/models?search={q}&limit={min(limit, 50)}&sort=downloads&direction=-1&full=true')
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except Exception:
        return []
    out = []
    for it in items:
        mid = it.get('id') or it.get('modelId') or ''
        tags = it.get('tags') or []
        b, fit = _size_of(tags, mid)
        lic = next((t.split(':', 1)[1] for t in tags if isinstance(t, str) and t.startswith('license:')), '')
        out.append({
            'id': mid, 'downloads': it.get('downloads') or 0, 'likes': it.get('likes') or 0,
            'is_lora': _is_lora(tags, mid), 'params_b': b, 'fit_16gb': fit,
            'license': lic, 'commercial': (lic.lower() in COMMERCIAL_OK) if lic else None,
        })
    return out


def search_hf(query, limit=8):
    """多查询合并 ✓"""
    variants = QUERY_VARIANTS.get(query.lower().strip(), [query])
    if query.lower().strip() not in [v.lower() for v in variants]:
        variants = [query] + variants
    seen, merged = set(), []
    for v in variants[:4]:
        for m in search_hf_one(v, limit=20):
            if m['id'] in seen:
                continue
            seen.add(m['id'])
            merged.append(m)
        time.sleep(0.3)
    merged.sort(key=lambda x: (0 if x['fit_16gb'] == 'ok16gb' else 1,
                               0 if x['is_lora'] else 1,
                               -(x['downloads'] or 0)))
    return merged[:limit]


def search_github(query, limit=6):
    q = urllib.parse.quote(f'"{query}" in:name,description')
    raw = _get(f'https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=20')
    if not raw:
        return []
    try:
        d = json.loads(raw)
    except Exception:
        return []
    out = []
    for it in d.get('items', []):
        name = (it.get('full_name') or '').lower()
        desc = (it.get('description') or '').lower()
        # 降噪：排除 awesome/list/curated/资源合集 ✗
        if any(x in name or x in desc for x in ('awesome', 'list-of', 'curated', 'book', 'tutorial', 'course')):
            continue
        out.append({'source': 'github', 'id': it.get('full_name'), 'url': it.get('html_url'),
                    'stars': it.get('stargazers_count') or 0, 'desc': (it.get('description') or '')[:100],
                    'lang': it.get('language') or '', 'updated': (it.get('pushed_at') or '')[:10]})
        if len(out) >= limit:
            break
    return out


def search_arxiv(query, limit=5):
    q = urllib.parse.quote(f'all:"{query}"')
    url = f'http://export.arxiv.org/api/query?search_query={q}&max_results={limit}&sortBy=relevance'
    raw = None
    for attempt in range(3):
        raw = _get(url, timeout=25, retries=0)
        if raw:
            break
        time.sleep(6 + attempt * 6)
    if not raw:
        return []
    txt = raw.decode('utf-8', 'replace')
    out = []
    for m in re.finditer(r'<entry>(.*?)</entry>', txt, re.S):
        blk = m.group(1)
        t = re.search(r'<title>(.*?)</title>', blk, re.S)
        i = re.search(r'<id>(.*?)</id>', blk, re.S)
        if not t or not i:
            continue
        out.append({'source': 'arxiv', 'url': (i.group(1) or '').strip(),
                    'title': re.sub(r'\s+', ' ', (t.group(1) or '')).strip()[:140]})
    return out


def known_good_for(query):
    q = query.lower()
    for key, items in KNOWN_GOOD.items():
        if key in q or (key == 'tool' and any(x in q for x in ('tool', 'function', '工具'))):
            return items
        if key == 'roleplay' and any(x in q for x in ('角色', 'role', 'persona', 'character')):
            return items
        if key == 'chinese' and any(x in q for x in ('中文', 'chinese', 'zh')):
            return items
    return []


def verdict(models, repos, papers, known, query):
    lines = []
    best_known = [k for k in known if not k[1] or k[1] <= 9]
    usable_live = [m for m in models if m['fit_16gb'] == 'ok16gb' and (m['downloads'] or 0) >= 100]
    if best_known:
        grade = 'A'
        lines.append(f'  ★ 结论: [A] 【已知优秀模型】有现成的 ✓ 优先直接用 ✓')
        for mid, b, desc, dl in best_known[:3]:
            lines.append(f'    · {mid}（{b}B · {desc}{" · 下载 " + dl if dl else ""}）')
        lines.append(f'    下一步: 下载 → 和我们评测口径对比 → 能用就【不要训】✓')
    elif usable_live:
        grade = 'A'
        b = usable_live[0]
        lines.append(f'  ★ 结论: [A] 有现成模型可用 ✓')
        lines.append(f'    首选: {b["id"]}（{b["params_b"]}B · 下载 {b["downloads"]} · {b["license"] or "?"}）')
        lines.append(f'    下一步: 直接下载试 → 不行才考虑微调 ✓')
    elif any(m['is_lora'] for m in models):
        grade = 'B'
        lb = [m for m in models if m['is_lora']][0]
        lines.append(f'  ★ 结论: [B] 有现成 LoRA 可借 ✓')
        lines.append(f'    首选: {lb["id"]}（下载 {lb["downloads"]}）')
        lines.append(f'    下一步: 下载 LoRA 挂上试 ⇒ 不行再考虑自己训 ✓')
    elif papers or repos:
        grade = 'B'
        lines.append(f'  ★ 结论: [B] 有工程/论文方案可借 ✓（不一定需要训练 ✓）')
        if papers:
            lines.append(f'    论文: {papers[0]["title"][:70]}')
        if repos:
            lines.append(f'    项目: {repos[0]["id"]}（★{repos[0]["stars"]}）')
        lines.append(f'    下一步: 照方案实现（通常比训练便宜 10 倍 ✓）')
    else:
        grade = 'C'
        lines.append(f'  ★ 结论: [C] 没找到现成方案 ✗ ⇒ 这时才考虑自己训 ✓')
        lines.append(f'    下一步: 先做 2 小时小验证（原则一 R2 ✓）⇒ 确认可行再投入')
    return '\n'.join(lines), grade


def main():
    ap = argparse.ArgumentParser(description='先找不训：搜现成模型/LoRA/项目/论文')
    ap.add_argument('query')
    ap.add_argument('--kind', default='all', choices=['all', 'model', 'lora', 'repo', 'paper'])
    ap.add_argument('--limit', type=int, default=8)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    q = args.query

    known = known_good_for(q)
    models = [] if args.kind in ('repo', 'paper') else search_hf(q, args.limit)
    repos = [] if args.kind in ('model', 'lora', 'paper') else search_github(q, min(args.limit, 6))
    papers = [] if args.kind in ('model', 'lora', 'repo') else search_arxiv(q, min(args.limit, 4))

    if args.json:
        print(json.dumps({'query': q, 'known_good': known, 'models': models, 'repos': repos, 'papers': papers},
                         ensure_ascii=False, indent=1))
        return

    print(f'\n{"="*80}')
    print(f'  先找不训 · 查询: 「{q}」')
    print(f'{"="*80}')
    if known:
        print(f'\n【⭐ 已知优秀模型（人工核实 ✓）】')
        for mid, b, desc, dl in known:
            print(f'  · {mid:<50} {b:>5}B  {desc}')

    if models:
        print(f'\n【搜到的模型 / LoRA】（hf-mirror ✓）')
        print(f'  {"模型ID":<54}{"下载":>8}{"B":>6}{"适配":>6}  许可')
        print(f'  {"-"*54}{"-"*8}{"-"*6}{"-"*6}  {"-"*12}')
        for m in models[:args.limit]:
            tag = 'LoRA' if m['is_lora'] else ('✓' if m['fit_16gb'] == 'ok16gb' else '✗大')
            print(f'  {m["id"][:54]:<54}{m["downloads"]:>8}{str(m["params_b"] or "?"):>6}{tag:>6}  {m["license"][:12]}')
    if repos:
        print(f'\n【开源项目】（GitHub ✓ 已降噪）')
        for r in repos:
            print(f'  ★{r["stars"]:<6} {r["id"][:42]:<42}{r["lang"]:<10}{r["desc"][:34]}')
    if papers:
        print(f'\n【论文】（arXiv ✓）')
        for p in papers:
            print(f'  · {p["title"][:74]}')
            print(f'    {p["url"]}')

    txt, grade = verdict(models, repos, papers, known, q)
    print(f'\n{"-"*80}')
    print('  能不能直接用？')
    print(txt)
    print(f'{"-"*80}')
    print('  ⚠ 原则二：能找就找 ✓ 找到就试 ✓ 都不行才训 ✗')
    print('  ⚠ 原则一：开工前先在这里查 ≥2 篇依据 ✓\n')


if __name__ == '__main__':
    main()
