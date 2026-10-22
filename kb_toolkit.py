#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_toolkit.py — 知识库数据清洗/排序唯一真相（P-74 / K0）

★ 依据（可行性实测 ✓ 2026-10-20）:
  · T1 噪声识别 LLM 只有 67% ✗ ⇒ **规则优先** ✓（页码/页眉 pattern 秒级 ✓）
  · T3 元数据 / T4 问题生成 LLM 优秀 ✓ ⇒ **可选增强** ✓
  · 全量 LLM 18 分钟/书 ✗ vs 规则秒级 ✓ ⇒ **分层混合** ✓

★ 公开 API:
    clean_text(text)                    规则清洗（页码/页眉/乱码 ✓）
    split_structure_aware(text, ...)    结构感知切块（Markdown 标题 ✓ 不跨章节 ✓）
    dedup_chunks(chunks)                精确去重 ✓
    filter_chunks(chunks, ...)          空/超短过滤 ✓
    analyze_docs(paths)                 入库前检测 ✓ 报告（只读 ✓）
    clean_docs(paths, opts)             一键清洗 ✓ 返回 chunks + stats
    quality_score(chunk)                规则质量分 ✓（用于排序 ✓）
    sort_chunks(chunks, by)             排序 ✓（source/section/quality ✓）
    enrich_metadata(chunks, llm_fn)     本地模型增强（T3 元数据 ✓ 可选 ✓）
    gen_questions(chunks, llm_fn, n)    本地模型出题（T4 ✓ 评测集用 ✓）
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from collections import Counter

# ══════════════════════════════════════════════════════════
# 一、质量规范（B1-B10 · 来自 docs/KB_EVAL_PLAN.md ✓）
# ══════════════════════════════════════════════════════════
KB_SPEC = {
    "chunk_size": 1200,         # ★ 修 P-74（2026-10-21）: 500→1200 ✓ 太碎损害检索 ✗
    "overlap": 200,             # ★ 修 P-74: 50→200 ✓ 跨块连续 ✓
    "min_chars": 40,            # B3: <20 字丢弃 ✓
    "max_chars": 1200,          # 防超长 ✓
    "dedup_exact": True,        # B4 ✓
    "dedup_near": False,        # B5 默认关 ✓（误伤风险 ⚠）
    "near_threshold": 0.9,
    "clean_pdf_noise": True,    # B6 ✓
    "structure_aware": True,    # B7 ✓
    "meta_required": ["source"],  # B8 ✓
    "language": "auto",         # B9 只提示 ✓
}

# ══════════════════════════════════════════════════════════
# 二、规则清洗（B6 ✓ 秒级 ✓）
# ══════════════════════════════════════════════════════════
# 页码 / 页眉 / 页脚 的典型 pattern ✓
NOISE_PATTERNS = [
    re.compile(r'^\s*第\s*\d+\s*页\s*(/\s*共?\s*\d+\s*页?)?\s*$', re.M),          # 第 3 页 / 共 28 页
    re.compile(r'^\s*Page\s*\d+\s*(of\s*\d+)?\s*$', re.I | re.M),                 # Page 3 of 28
    re.compile(r'^\s*-+\s*\d+\s*-+\s*$', re.M),                                    # --- 3 ---
    re.compile(r'^\s*\d+\s*/\s*\d+\s*$', re.M),                                    # 3/28
    re.compile(r'^\s*共\s*\d+\s*页\s*$', re.M),                                    # 共 28 页
    re.compile(r'^\s*目\s*录\s*$', re.M),                                          # 目录（单独行）
    # ★ 2026-10-20 修: 带装饰前缀的页眉/页码（实测漏判 ✗）
    re.compile(r'^\s*[【\[（(].*?第\s*\d+\s*页.*?[】\]）)]\s*.*$', re.M),           # 【内部资料 · 第 1 页】…
    re.compile(r'^\s*[【\[（(].*?请勿外传.*?[】\]）)]?\s*$', re.M),                    # 【…请勿外传…】
    re.compile(r'^\s*[-—=*~]{2,}\s*第?\s*\d+\s*页?\s*[-—=*~]{2,}\s*$', re.M),        # --- 1 ---,
    # ══════════════════════════════════════════════════════════
    # ★ 2026-10-21 补: 实测「任意脏文档」漏清的 4 类噪声 ✗
    #   实测: 660 字符脏文档 → 只清掉页码 ✗
    #         「机密文件 · 请勿外传」「版权所有 © 2026 某某公司」全残留 ✗
    #   修: 补 水印 / 版权 / 机密 / 公司全称 / 页码变体 ✓
    # ══════════════════════════════════════════════════════════
    # 水印（含分隔符 ✓ 如「机密文件 · 请勿外传」）
    re.compile(r'^\s*(?:绝密|机密|秘密|内部|仅供内部|请勿外传|禁止外传|confidential|internal\s+use\s+only)'
               r'[\s·•\-—_/|、,，:：]*(?:请勿外传|禁止外传|文件|资料)?[\s·•\-—_/|、,，:：]*$', re.I | re.M),
    # 版权行（含 © / (c) / Copyright ✓）
    re.compile(r'^\s*(?:版权|著作权|copyright|©|\(c\)|版权所有)\s*[:：]?.*$', re.I | re.M),
    # 页码变体（单独行的数字 ✓ 或「- 3 -」「P3」✓）
    re.compile(r'^\s*(?:[Pp]\.?\s*\d+|[-—]\s*\d+\s*[-—]|\d+\s*页)\s*$', re.M),
    # 公司/机构行（「XXX公司」「XXX有限公司」全称 ✓ 单独行 ✓）
    re.compile(r'^\s*[\u4e00-\u9fff]{2,20}(?:有限公司|股份有限公司|集团|研究院|研究所|大学|学院|中心)\s*$', re.M),
    # 页眉横线 + 短标题（如「某某公司内部技术规范 V2.1」✓ 含版本号 ✓）
    re.compile(r'^\s*[\u4e00-\u9fff]{4,30}\s*[Vv]?\d+\.\d+\s*$', re.M),
    # 「本文档由...批准发布」「禁止复制/转载」✓
    re.compile(r'^\s*(?:本文档|本文件|本手册|本规范)\s*(?:由|经).{0,30}(?:批准|发布|审核|制定).{0,10}$', re.M),
    re.compile(r'^\s*(?:禁止|严禁)\s*(?:复制|转载|传播|外传|翻印).{0,20}$', re.M),
    # ══════════════════════════════════════════════════════════
    # ★ 2026-10-21 补第二波: 实测「英文学术 PDF」漏清 9 类 ✗
    #   实测: 831 字符学术文档 → 只清 9% ✗ 移 2 行 ✗
    # ══════════════════════════════════════════════════════════
    re.compile(r'^\s*(?:Downloaded\s+from|This\s+article\s+is\s+available\s+at|'
               r'Available\s+(?:online|at)|Preprint\s+submitted\s+to|'
               r'arXiv:\d{4}\.\d{4,5}|https?://(?:www\.)?doi\.org/\S*)\s*.*$', re.I | re.M),
    re.compile(r'^\s*(?:Received|Revised|Accepted|Published|Submitted)\s*[:：].{0,200}$', re.I | re.M),
    # ★ 2026-10-21 精修: 实测 "Corresponding author. Email: a@b.com" 这种一行带两种信息 ✗
    #   原 pattern 要求紧接 : 或 空 ⇒ 匹配不到 ✗ ⇒ 改为「行首含 Corresponding author / Email」即删 ✓
    re.compile(r'^\s*(?:\*?\s*)?Corresponding\s+author\b.*$', re.I | re.M),
    re.compile(r'^\s*(?:E-?mail|电子邮箱|电子信箱)\s*[:：].*$', re.I | re.M),
    re.compile(r'^\s*\S+@\S+\.\S+\s*$', re.M),          # 纯邮箱行 ✓
    re.compile(r'^\s*(?:通讯作者|作者简介|通信作者)\b.*$', re.M),
    re.compile(r'^\s*(?:Figure|Fig\.?|Table|图|表)\s*\d+\s*[.．:：]?\s*.*$', re.I | re.M),
    re.compile(r'^\s*(?:\[\d{1,3}\]|\d{1,3}\.)\s+[A-Z][A-Za-z\-\']+[,.]?\s.*$', re.M),
    # ★ 2026-10-21 精修: 实测 "## References"（markdown 标题 ✗）匹配不到 ✗ ⇒ 允许 # 前缀 ✓
    re.compile(r'^\s*(?:#{1,6}\s*)?(?:References|Bibliography|Acknowledg(?:e)?ments?|'
               r'参考文献|致谢|附录)\s*$', re.I | re.M),
    re.compile(r'^\s*(?:Fund(?:ed|ing)\s+by|Grant\s+(?:No\.?|Number)|'
               r'基金项目|资助项目)\s*[:：]?.{0,120}$', re.I | re.M),
    re.compile(r'^\s*(?:©|\u00a9|Copyright).{0,120}$', re.I | re.M),
    re.compile(r'^\s*.*(?:Creative\s+Commons|CC\s+BY|Licensee\s+MDPI|'
               r'Springer\s+Nature|Elsevier\s+B\.V\.|All\s+rights\s+reserved).*$', re.I | re.M),
    # ══════════════════════════════════════════════════════════
    # ★ 2026-10-21 第三波: 实测 3 篇真实 arXiv PDF，清洗只 -1.7% ✗
    #   残留: Preprint 41次 · 孤立页码 53行 · ≤3字碎片 215行 · 纯URL 46次
    #   ★ 边界: 只删【从正文分离出来的孤立噪声行】✓
    #          绝不删正文内的 Figure/Table/et al.（那是内容 ✗ 删了毁语义 ✗）
    # ══════════════════════════════════════════════════════════
    # ① 页眉水印: 单独一行的 Preprint / Under review / Draft / Accepted 等 ✓
    re.compile(r'^\s*(?:Preprint|Pre-?print|Under\s+review|Submitted\s+to|'
               r'To\s+appear\s+in|Draft|Working\s+paper|'
               r'Camera[- ]ready|Accepted\s+(?:at|to)|'
               r'arXiv\s+preprint)\s*(?:arXiv:\S+)?\s*$', re.I | re.M),
    # ② arXiv 编号单独行 ✓（arXiv:2410.12345v1 / arXiv:2410.12345 [cs.CL]）
    re.compile(r'^\s*arXiv:\d{4}\.\d{4,5}(?:v\d+)?(?:\s*\[[\w.\-]+\])?\s*$', re.I | re.M),
    # ③ 纯 URL 单独行 ✓（http/https/ftp/www 开头且整行就是一个链接）
    re.compile(r'^\s*(?:https?://|ftp://|www\.)\S+\s*$', re.I | re.M),
    # ④ 纯 DOI 单独行 ✓
    re.compile(r'^\s*(?:doi|DOI)\s*[:：]?\s*10\.\d{4,9}/\S+\s*$', re.M),
    # ⑤ 孤立页码行（加强 ✓ 实测残留 53 行 ✗）
    re.compile(r'^\s*[-–—\s]*\d{1,4}[-–—\s]*$', re.M),
    # ⑥ 孤立罗马数字页码 ✓（i, ii, iii ... xiv）
    re.compile(r'^\s*(?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii|xiii|xiv|xv)\s*$', re.M),
    # ⑦ 分隔线/装饰行 ✓
    re.compile(r'^\s*[\-=_~*•·●○◆◇■□▲△▼▽→←↑↓|/\\]{3,}\s*$', re.M),
    # ⑧ 出版社/会议 单独行（整行只有机构名 ✓）
    re.compile(r'^\s*(?:IEEE|ACM|Springer|Elsevier|MDPI|AAAI|IJCAI|NeurIPS|ICML|ICLR|'
               r'CVPR|ICCV|ECCV|ACL|EMNLP|NAACL|KDD|SIGIR|WWW)\s*'
               r'(?:\d{4})?\s*$', re.I | re.M),
    # ⑨ 版权 单独行 ✓
    re.compile(r'^\s*(?:©|\u00a9|Copyright)\s*[\d\s\-–—,.]*'
               r'(?:\w[\w\s.,]*)?(?:All\s+rights\s+reserved\.?)?\s*$', re.I | re.M),
    # ⑩ 页码格式 "Page 3" / "p. 3" / "3 of 10" ✓
    re.compile(r'^\s*(?:Page|page|p\.|pp\.)\s*\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$', re.M)
]

# 页眉页脚：短行且重复出现 ✓（启发式 ✓）
def _find_repeated_short_lines(lines: list[str], min_ratio: float = 0.10,
                              min_count: int = 5) -> set[str]:
    # ★ 2026-10-21 加 min_count: 实测 "Preprint" 出现 41 次但只占 2%
    #   ⇒ 比例法（10%）抓不到 ✗ ⇒ 加「绝对次数 ≥5 且短行」兜底 ✓
    # ★ 2026-10-21 修: min_ratio 0.5 太严 ✗ 实测「机密文件 · 请勿外传」出现 3/25 行=12%
    #   ⇒ 抓不到 ✗ ⇒ 降到 0.10 ✓ 并加「绝对次数 ≥2 且短行」的兜底 ✓
    """找重复出现的短行（页眉页脚候选 ✓）"""
    cnt = Counter()
    for ln in lines:
        s = ln.strip()
        if 3 <= len(s) <= 40:   # 短行 ✓
            cnt[s] += 1
    # 出现在 ≥min_ratio 行里的短行 ⇒ 页眉页脚 ✓
    non_empty = len([l for l in lines if l.strip()])
    threshold = max(3, int(non_empty * min_ratio))
    out = {s for s, c in cnt.items() if c >= threshold}
    # ★ 2026-10-21 加: 绝对次数兜底 ✓
    #   实测 "Preprint" 出现 41 次但只占 2% ⇒ 比例法抓不到 ✗
    #   保守条件: ≥min_count 次 + 无句末标点 + 短 ✓
    #   ⇒ 不会误伤章节标题（Abstract/Results 一般只出现 1-2 次 ✓）
    for s, c in cnt.items():
        if c >= min_count and not re.search(r'[.。!?！?,，;；:：]', s):
            out.add(s)
    return out


def _merge_hyphen_breaks(text: str) -> tuple[str, int]:
    """合并连字符断行 ✓（2026-10-21 新增）

    实测: 3 篇真实 arXiv PDF 里跨行连字符 **111 处** ✗ 是最大噪声源 ✗
      "exam-\nple" 应为 "example" ✓
    保守策略: 只在前后都是**英文字母**时拼接 ✓（避免误伤破折号 ✓）
    """
    import re as _re
    n = [0]

    def _merge(m):
        n[0] += 1
        return m.group(1) + m.group(2)

    # 跨行连字符 ✓
    text = _re.sub(r'([A-Za-z])-[ \t]*\n[ \t]*([a-z])', _merge, text)
    return text, n[0]


def clean_text(text: str, *, remove_noise: bool = True) -> tuple[str, dict]:
    """规则清洗 ✓（页码/页眉/页脚/乱码 ✓）

    Returns:
        (cleaned_text, stats)
    """
    stats = {"input_chars": len(text or ""), "removed_lines": 0, "patterns_hit": {}}
    # ★ 新增（2026-10-21）: **先合并连字符断行** ✓（实测最大噪声源 111 处 ✗）
    if remove_noise and text:
        _orig_len = len(text)
        text, _n_hyphen = _merge_hyphen_breaks(text)
        if _n_hyphen:
            stats["patterns_hit"]["hyphen_line_breaks"] = _n_hyphen
    if not text:
        return "", stats

    # ① 统一换行/去 BOM ✓
    t = str(text).replace('\r\n', '\n').replace('\r', '\n').replace('\ufeff', '')

    # ② 去页码/页眉/页脚 pattern ✓
    if remove_noise:
        for pat in NOISE_PATTERNS:
            hits = len(pat.findall(t))
            if hits:
                t = pat.sub('', t)
                stats["removed_lines"] += hits
                stats["patterns_hit"][pat.pattern[:40]] = hits

        # ③ 重复短行（页眉页脚 ✓）
        lines = t.split('\n')
        repeated = _find_repeated_short_lines(lines)
        if repeated:
            kept = []
            for ln in lines:
                if ln.strip() in repeated:
                    stats["removed_lines"] += 1
                else:
                    kept.append(ln)
            t = '\n'.join(kept)
            stats["patterns_hit"]["repeated_short_lines"] = len(repeated)

    # ④ 去乱码（连续非打印字符 ✓）/ 多余空白 ✓
    t = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', t)
    t = re.sub(r'[ \t]+\n', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)

    stats["output_chars"] = len(t)
    return t, stats


# ══════════════════════════════════════════════════════════
# 三、结构感知切块（B7 ✓ 关键改进 ✓）
# ══════════════════════════════════════════════════════════
_MD_HEADING = re.compile(r'^(#{1,6})\s+(.+?)\s*$')


def _split_sentences(text: str) -> list[str]:
    """按中文/英文句末切分 ✓"""
    parts = re.split(r'(?<=[。！？!?；;])\s*', text)
    return [p for p in parts if p.strip()]


def split_structure_aware(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[dict]:
    """结构感知切块 ✓（★ 核心改进 ✓）

    策略:
      ① 先按 Markdown 标题分段（# / ## / ### ✓）→ 每段带 section ✓
      ② 段内按段落→句子→硬切（复用 kb.py 思路 ✓）
      ③ **不跨章节** ✓（每个 chunk 只属于一个 section ✓）
      ④ 每块带元数据: source / section / chars ✓（B8 ✓）

    Returns:
        [{"text": ..., "section": ..., "chars": ...}, ...]
    """
    t = text or ""
    chunks: list[dict] = []

    # ① 按标题切段 ✓
    lines = t.split('\n')
    sections: list[tuple[str, str]] = []   # (section_title, body)
    cur_title = ""
    cur_body: list[str] = []
    for ln in lines:
        m = _MD_HEADING.match(ln.strip())
        if m:
            # 收上一段 ✓
            if cur_body or cur_title:
                sections.append((cur_title, '\n'.join(cur_body).strip()))
            cur_title = m.group(2).strip()[:60]
            cur_body = []
        else:
            cur_body.append(ln)
    if cur_body or cur_title:
        sections.append((cur_title, '\n'.join(cur_body).strip()))

    # 没标题 ⇒ 全文一段 ✓
    if not sections:
        sections = [("", t)]

    # ② 每段内切块 ✓（不跨章节 ✓）
    for title, body in sections:
        if not body.strip():
            continue
        for ch in _split_by_para_sentence(body, chunk_size, overlap):
            ch = ch.strip()
            if len(ch) < KB_SPEC["min_chars"]:
                continue
            chunks.append({
                "text": ch,
                "section": title,
                "chars": len(ch),
            })
    return chunks


def _split_by_para_sentence(text: str, chunk_size: int, overlap: int) -> list[str]:
    """段落优先 → 句子 → 硬切 ✓（同 kb.py 思路 ✓）"""
    paras = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if not paras:
        return []
    out: list[str] = []
    buf = ""
    for p in paras:
        if len(p) > chunk_size:
            # 长段落 ⇒ 句子切 ✓
            if buf:
                out.append(buf)
                buf = ""
            for sent in _split_sentences(p):
                # ★ 2026-10-21 修: 单句就可能 > chunk_size ✗
                #   真实 PDF: 表格/公式/参考文献常一大段无句末标点 ✗
                #   实测: 5373 字巨块 ✗（目标 500 ✗）
                #   ⇒ 超长句先硬切成 chunk_size 大小的片段 ✓
                if len(sent) > chunk_size:
                    if buf:
                        out.append(buf)
                        buf = ""
                    # 硬切 ✓（不破坏顺序 ✓）
                    for _k in range(0, len(sent), chunk_size):
                        piece = sent[_k:_k + chunk_size]
                        if piece.strip():
                            out.append(piece.strip())
                    continue
                if len(buf) + len(sent) + 1 <= chunk_size:
                    buf = (buf + sent).strip()
                else:
                    if buf:
                        out.append(buf)
                    buf = sent
            continue
        if len(buf) + len(p) + 1 <= chunk_size:
            buf = (buf + '\n' + p).strip() if buf else p
        else:
            if buf:
                out.append(buf)
            buf = p
    if buf:
        out.append(buf)

    # 加重叠 ✓（只在剩余空间允许时 ✓）
    if overlap > 0 and len(out) > 1:
        merged = []
        for i, c in enumerate(out):
            if i == 0:
                merged.append(c)
                continue
            room = max(0, chunk_size - len(c))
            if room >= 10:
                tail = out[i - 1][-min(overlap, room):]
                merged.append((tail + c).strip())
            else:
                merged.append(c)
        out = merged
    return out


# ══════════════════════════════════════════════════════════
# 四、去重 / 过滤 / 排序
# ══════════════════════════════════════════════════════════
def _chunk_fp(text: str) -> str:
    return hashlib.sha256(re.sub(r'\s+', '', text or "").encode('utf-8')).hexdigest()


def dedup_chunks(chunks: list[dict]) -> tuple[list[dict], int]:
    """精确去重 ✓（B4 ✓）"""
    seen = set()
    out = []
    dropped = 0
    for c in chunks:
        fp = _chunk_fp(c.get("text", ""))
        if fp in seen:
            dropped += 1
            continue
        seen.add(fp)
        out.append(c)
    return out, dropped


def filter_chunks(chunks: list[dict], *, min_chars: int = None, max_chars: int = None) -> tuple[list[dict], dict]:
    """过滤空/超短/超长 ✓（B3 ✓）"""
    mn = min_chars if min_chars is not None else KB_SPEC["min_chars"]
    mx = max_chars if max_chars is not None else KB_SPEC["max_chars"]
    out = []
    stats = {"empty": 0, "short": 0, "long": 0}
    for c in chunks:
        t = (c.get("text") or "").strip()
        if not t:
            stats["empty"] += 1
            continue
        if len(t) < mn:
            stats["short"] += 1
            continue
        if len(t) > mx:
            stats["long"] += 1
            continue
        out.append(c)
    return out, stats


def quality_score(chunk: dict) -> int:
    """规则质量分 ✓（零成本 ✓ 用于排序 ✓ 不做删除 ✓）

    依据可行性实验 T2 ✓: LLM 评分有区分度但不可硬过滤 ✗
    ⇒ **规则分 + LLM 分（可选）组合** ✓
    """
    t = chunk.get("text") or ""
    s = 0
    n = len(t)
    # 长度适中 ✓
    if 200 <= n <= 900:
        s += 3
    elif 100 <= n <= 1200:
        s += 1
    # 有标点（完整句子 ✓）
    if re.search(r'[。！？.!?]', t):
        s += 2
    # 有标题/section ✓
    if chunk.get("section"):
        s += 1
    # 有代码/数字（技术内容 ✓）
    if re.search(r'\d', t):
        s += 1
    # 纯符号/纯数字 ⇒ 扣分 ✓
    alpha = len(re.findall(r'[\u4e00-\u9fffA-Za-z]', t))
    if n > 0 and alpha / n < 0.3:
        s -= 3
    # 重复标点（乱码 ✓）
    if re.search(r'([!?。])\1{3,}', t):
        s -= 2
    return s


def sort_chunks(chunks: list[dict], by: str = "source") -> list[dict]:
    """排序 ✓

    by: 'source'（按来源 ✓ 默认）/ 'section'（按章节 ✓）/ 'quality'（质量分 ✓）/ 'length'（长度 ✓）
    """
    if by == "quality":
        return sorted(chunks, key=lambda c: (-quality_score(c), c.get("source", "")))
    if by == "length":
        return sorted(chunks, key=lambda c: len(c.get("text") or ""))
    if by == "section":
        return sorted(chunks, key=lambda c: (c.get("source", ""), c.get("section", "")))
    return sorted(chunks, key=lambda c: (c.get("source", ""), c.get("section", "")))


# ══════════════════════════════════════════════════════════
# 五、文档级检测 / 清洗（K0 主入口 ✓）
# ══════════════════════════════════════════════════════════
def _read_doc_text(path: pathlib.Path) -> str:
    """读文档文本 ✓（复用 kb.py 的提取链 ✓）"""
    try:
        import kb as _kb
        return _kb._extract_file_text(str(path)) or ""
    except Exception:
        # 兜底: 直接读 ✓
        try:
            return path.read_text(encoding='utf-8', errors='replace')
        except Exception:
            return ""


def analyze_docs(paths: list[str | pathlib.Path], *, chunk_size: int = None,
                 overlap: int = None) -> dict:
    """入库前检测 ✓（只读 ✓ 不写盘 ✓）"""
    cs = chunk_size or KB_SPEC["chunk_size"]
    ov = overlap if overlap is not None else KB_SPEC["overlap"]
    docs = []
    total_chars = 0
    total_raw_chunks = 0
    total_clean_chunks = 0
    all_problems = Counter()
    for p in paths:
        pp = pathlib.Path(p)
        if not pp.exists():
            docs.append({"file": pp.name, "error": "文件不存在"})
            continue
        try:
            raw = _read_doc_text(pp)
            total_chars += len(raw)
            cleaned, cstats = clean_text(raw)
            # 原始切块（默认方式 ✓ 对照组 ✓）
            try:
                import kb as _kb
                raw_chunks = _kb.split_text(raw, chunk_size=cs, overlap=ov)
            except Exception:
                raw_chunks = [{'text': x} for x in _split_by_para_sentence(raw, cs, ov)]
            # 清洗后切块（结构感知 ✓ 实验组 ✓）
            clean_chunks = split_structure_aware(cleaned, cs, ov)
            clean_chunks, dedup_n = dedup_chunks(clean_chunks)
            clean_chunks, fstats = filter_chunks(clean_chunks)
            total_raw_chunks += len(raw_chunks)
            total_clean_chunks += len(clean_chunks)
            if cstats.get("removed_lines"):
                all_problems["noise_lines_removed"] += cstats["removed_lines"]
            if dedup_n:
                all_problems["duplicates_removed"] += dedup_n
            for k, v in (fstats or {}).items():
                if v:
                    all_problems["filtered_" + k] += v
            docs.append({
                "file": pp.name,
                "chars": len(raw),
                "raw_chunks": len(raw_chunks),
                "clean_chunks": len(clean_chunks),
                "noise_removed": cstats.get("removed_lines", 0),
                "dedup_removed": dedup_n,
            })
        except Exception as e:
            docs.append({"file": pp.name, "error": str(e)[:100]})
    return {
        "docs": docs,
        "doc_count": len(docs),
        "total_chars": total_chars,
        "raw_chunks": total_raw_chunks,
        "clean_chunks": total_clean_chunks,
        "problems": dict(all_problems),
        "params": {"chunk_size": cs, "overlap": ov},
    }


def clean_docs(paths: list[str | pathlib.Path], opts: dict | None = None) -> tuple[list[dict], dict]:
    """一键清洗 ✓（返回 chunks ✓ 带完整元数据 ✓）

    Returns:
        (chunks, stats)   chunks = [{"text","source","section","chars","quality"}...]
    """
    o = dict(KB_SPEC)
    if opts:
        o.update(opts)
    cs = o["chunk_size"]
    ov = o["overlap"]
    all_chunks: list[dict] = []
    stats = {
        "docs": 0, "chars_in": 0, "chars_out": 0,
        "noise_removed": 0, "dedup_removed": 0, "filtered": {},
        "chunks_before_filter": 0, "chunks": 0,
    }
    for p in paths:
        pp = pathlib.Path(p)
        if not pp.exists():
            continue
        try:
            raw = _read_doc_text(pp)
            stats["docs"] += 1
            stats["chars_in"] += len(raw)
            cleaned, cstats = clean_text(raw, remove_noise=o.get("clean_pdf_noise", True))
            stats["chars_out"] += len(cleaned)
            stats["noise_removed"] += cstats.get("removed_lines", 0)
            chunks = split_structure_aware(cleaned, cs, ov)
            stats["chunks_before_filter"] += len(chunks)
            for ch in chunks:
                ch["source"] = pp.name
                ch["quality"] = quality_score(ch)
            all_chunks.extend(chunks)
        except Exception:
            continue

    # 去重 ✓
    if o.get("dedup_exact", True):
        all_chunks, dn = dedup_chunks(all_chunks)
        stats["dedup_removed"] = dn
    # 过滤 ✓
    all_chunks, fstats = filter_chunks(all_chunks, min_chars=o["min_chars"], max_chars=o["max_chars"])
    stats["filtered"] = fstats
    stats["chunks"] = len(all_chunks)
    return all_chunks, stats


# ══════════════════════════════════════════════════════════
# 六、本地模型增强（可选 ✓ T3/T4 实测优秀 ✓）
# ══════════════════════════════════════════════════════════
def enrich_metadata(chunks: list[dict], llm_fn, *, limit: int = 0) -> list[dict]:
    """本地模型抽元数据 ✓（T3 ✓ 实测优秀 ✓）

    Args:
        llm_fn: (system, user) -> str
        limit: 0 = 全部 ✓，>0 只处理前 N 块 ✓
    """
    sys_p = ("你是知识库元数据抽取器。只输出 JSON："
             '{"topic":"主题≤10字","section":"章节≤15字（无则空）",'
             '"keywords":["关键词1","关键词2","关键词3"]}。不要输出其他内容。')
    targets = chunks[:limit] if limit > 0 else chunks
    for c in targets:
        try:
            out = llm_fn(sys_p, (c.get("text") or "")[:600])
            d = json.loads(out[out.find('{'):out.rfind('}') + 1]) if '{' in (out or '') else {}
            if isinstance(d, dict):
                c["topic"] = str(d.get("topic", ""))[:20]
                if d.get("section") and not c.get("section"):
                    c["section"] = str(d.get("section"))[:30]
                c["keywords"] = d.get("keywords") or []
        except Exception:
            continue
    return chunks


def gen_questions(chunks: list[dict], llm_fn, *, n_chunks: int = 20, per_chunk: int = 2) -> list[dict]:
    """本地模型出题 ✓（T4 ✓ 实测优秀 ✓ 用于检索评测集 ✓）

    ★ 只抽样 n_chunks 块 ✓（可行性实验: 评测集只需 20-30 条 ✓ 省成本 ✓）

    Returns:
        [{"question": ..., "chunk_id": i, "source": ..., "section": ...}]
    """
    sys_p = ("你是检索评测集生成器。为给定文本块生成 %d 个【只有这段才能回答】的问题。"
             "只输出 JSON：{\"questions\":[\"问题1\",\"问题2\"]}。不要输出其他内容。" % per_chunk)
    import random
    picks = list(range(len(chunks)))
    random.Random(42).shuffle(picks)
    picks = picks[:max(1, n_chunks)]
    out = []
    for i in picks:
        c = chunks[i]
        try:
            txt = llm_fn(sys_p, (c.get("text") or "")[:600])
            d = json.loads(txt[txt.find('{'):txt.rfind('}') + 1]) if '{' in (txt or '') else {}
            qs = d.get("questions") or []
            for q in qs[:per_chunk]:
                if isinstance(q, str) and q.strip():
                    out.append({
                        "question": q.strip(),
                        "chunk_id": i,
                        "source": c.get("source", ""),
                        "section": c.get("section", ""),
                    })
        except Exception:
            continue
    return out


# ══════════════════════════════════════════════════════════
# 七、自测
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print('=' * 70)
    print('  kb_toolkit 自测')
    print('=' * 70)

    # ① 噪声清洗 ✓
    noisy = """第 3 页 / 共 28 页
2026 年第三季度技术报告 · 内部资料 · 请勿外传

## 3.2 分块策略

结构感知切块（structure-aware chunking）是指按文档的标题层级切分，而不是按固定字符数硬切。
优点是每个块语义完整、不跨章节；缺点是需要文档有清晰结构（Markdown / HTML）。

另外需要注意的是，在实际部署时还要考虑到并发和延迟的问题。

第 4 页 / 共 28 页
2026 年第三季度技术报告 · 内部资料 · 请勿外传
"""
    cleaned, st = clean_text(noisy)
    print('\n  [噪声清洗]')
    print('    移除行: %d · 命中: %s' % (st['removed_lines'], st['patterns_hit']))
    print('    清洗后前 80 字: %r' % cleaned[:80])

    # ② 结构感知切块 ✓
    md = """## 1. 概述

这是第一章的内容。它介绍了一些基础知识。

## 2. 方法

这是第二章的内容。它描述了具体方法。

### 2.1 子方法

这是子节的内容，应该和 2.1 一起。
"""
    chunks = split_structure_aware(md, chunk_size=100, overlap=10)
    print('\n  [结构感知切块] %d 块:' % len(chunks))
    for c in chunks:
        print('    section=%-8r chars=%3d %r' % (c['section'], c['chars'], c['text'][:40]))

    # ③ 去重 ✓
    cs2 = [{'text': 'abc'}, {'text': 'abc'}, {'text': 'def'}]
    dd, dn = dedup_chunks(cs2)
    print('\n  [去重] %d → %d（删 %d）' % (len(cs2), len(dd), dn))

    # ④ 质量分 ✓
    print('\n  [质量分]')
    for c in chunks:
        print('    %-30r score=%d' % (c['text'][:28], quality_score(c)))

    print('\n  OK 自测完成')
