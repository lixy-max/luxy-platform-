# -*- coding: utf-8 -*-
"""table_qa.py —— 表格答案模式 v2（多 sheet / 数字解析增强 / 实体校验）

支持意图：计数、极值、TopN、精确查找（编号/名称）、列表、条件筛选、关键词搜索。
答案带"数据第 N 行（sheet）"引用；LLM 兜底做数字 + 实体交叉校验。
"""
import re

import table_store as ts

COL_KEYS = {
    "code": ("产品编号", "编号", "料号", "sku", "货号"),
    "name": ("产品名称", "名称", "品名"),
    "price": ("市场价", "价格", "单价", "金额", "报价", "价"),
    "training": ("安装培训", "培训"),
    "weight": ("重量",),
}


def _norm(t):
    t = str(t or "").lower()
    return re.sub(r"[\s，,。？?！!：:；;\"'“”‘’（）()【】\[\]]+", "", t)


def _num_from_text(t):
    m = re.search(r"(\d+)", str(t or ""))
    return int(m.group(1)) if m else None


def _fmt_row(sheet, headers, row, multi=False):
    idx = {str(h): i for i, h in enumerate(headers or [])}
    parts = []
    for key in ("code", "name", "price", "training", "weight"):
        for k, i in idx.items():
            if any(w in k for w in COL_KEYS[key]):
                v = ts.cell(row, i)
                if v:
                    label = {"code": "编号", "name": "名称", "price": "市场价",
                             "training": "安装培训", "weight": "重量"}[key]
                    parts.append("%s：%s" % (label, v))
                break
    if not parts:
        fm = ts.row_field_map(headers, row)
        parts = ["%s：%s" % (k, v) for k, v in list(fm.items())[:4]]
    loc = "数据第 %d 行" % row["seq"]
    if multi:
        loc += "，sheet=%s" % sheet
    return "；".join(parts) + "（%s）" % loc


def _meta_headers(meta):
    return meta.get("headers") or []


def try_answer(kb_name, query):
    """确定性回答；处理不了返回 None"""
    if not ts.has_table(kb_name):
        return None
    meta = ts.get_meta(kb_name)
    rows_hdr = ts.rows_with_headers(kb_name)
    if not meta or not rows_hdr:
        return None
    q = str(query or "")
    nq = _norm(q)
    headers = _meta_headers(meta)
    multi = len(meta.get("sheets") or []) > 1
    name_i = ts.find_col(headers, *COL_KEYS["name"])
    price_i = ts.find_col(headers, *COL_KEYS["price"])

    # 1) 计数
    if re.search(r"(多少|几|共|总数|数量)", nq) and re.search(r"(产品|行|条|个)", nq):
        return {"mode": "structured", "confidence": 0.9, "verified": True,
                "answer": "表格共 **%d 行数据**（%d 个 sheet；文件=%s）。" % (
                    meta["n_rows"], len(meta.get("sheets") or []), meta.get("file")),
                "evidence": {"count": meta["n_rows"]}}

    # 2/3) 极值 / TopN（按含价格列的行做，多 sheet 合并）
    priced = []
    for sheet, idx, row in ts.rows_for_col(rows_hdr, *COL_KEYS["price"]):
        p = ts.num(ts.cell(row, idx))
        if p is not None:
            priced.append((p, sheet, row, ts.find_col(dict((i, h) for i, h in enumerate(
                next((h for s2, h, r2 in rows_hdr if r2 is row), headers))), *COL_KEYS["name"])))
    if re.search(r"(最高|最大|最贵|最低|最小|最便宜|top|前\d+)", nq) and priced:
        n = _num_from_text(q) or (3 if ("top" in nq or "前" in nq) else 1)
        n = max(1, min(n, 20))
        asc = bool(re.search(r"(最低|最小|最便宜)", nq))
        priced.sort(key=lambda x: x[0], reverse=not asc)
        top = priced[:n]
        lines = ["市场价格%s %d 个：" % ("最低" if asc else "最高", len(top))]
        for i, (p, sheet, row, _ni) in enumerate(top, 1):
            nm = ""
            for sheet2, hs, r2 in rows_hdr:
                if r2 is row:
                    ni = ts.find_col(hs, *COL_KEYS["name"])
                    nm = ts.cell(row, ni) if ni is not None else ""
                    break
            lines.append("%d) %s —— 市场价 %g 元（%s）" % (
                i, nm or "(无名称)", p, "数据第 %d 行，sheet=%s" % (row["seq"], sheet) if multi
                else "数据第 %d 行" % row["seq"]))
        return {"mode": "structured", "confidence": 0.88, "verified": True,
                "answer": "\n".join(lines),
                "evidence": {"rows": [r["seq"] for _, _, r, _ in top]}}

    # 3.5) 列表
    _fields = sum(1 for k in ("编号", "名称", "价格", "市场价", "安装培训") if k in q)
    _list_intent = bool(re.search(r"(列出|列举|给我|显示|前\d+个|几个|看看)", nq))
    _wants_products = bool(re.search(r"(产品|行|条|项|清单)", nq))
    if _list_intent and (_fields >= 2 or (_fields >= 1 and _wants_products)
                         or (_wants_products and re.search(r"\d+", q))):
        n = _num_from_text(q) or 5
        n = max(1, min(n, 50))
        lines = ["表格前 %d 行（共 %d 行数据）：" % (n, meta["n_rows"])]
        for i, (sheet, hs, row) in enumerate(rows_hdr[:n]):
            lines.append("- " + _fmt_row(sheet, hs, row, multi=multi))
        return {"mode": "structured", "confidence": 0.85, "verified": True,
                "answer": "\n".join(lines),
                "evidence": {"rows": [r["seq"] for _, _, r in rows_hdr[:n]]}}

    # 4) 精确查找（编号/名称命中）
    qn = _norm(q)
    hits = []
    for sheet, hs, row in rows_hdr:
        code_i = ts.find_col(hs, *COL_KEYS["code"])
        name_i2 = ts.find_col(hs, *COL_KEYS["name"])
        code_v = _norm(ts.cell(row, code_i)) if code_i is not None else ""
        name_v = _norm(ts.cell(row, name_i2)) if name_i2 is not None else ""
        if code_v and len(code_v) >= 3 and code_v in qn:
            hits.append((sheet, hs, row))
        elif name_v and len(name_v) >= 2 and name_v in qn:
            hits.append((sheet, hs, row))
    if hits:
        lines = ["找到 %d 条匹配：" % len(hits)]
        for sheet, hs, row in hits[:5]:
            lines.append("- " + _fmt_row(sheet, hs, row, multi=multi))
        if len(hits) > 5:
            lines.append("（仅显示前 5 条，共 %d 条）" % len(hits))
        return {"mode": "structured", "confidence": 0.95, "verified": True,
                "answer": "\n".join(lines),
                "evidence": {"rows": [r["seq"] for _, _, r in hits[:5]]}}

    # 5) 条件筛选
    for key in ("training", "weight"):
        if not any(COL_KEYS[key][0] in q for kk in ("training", "weight")) and COL_KEYS[key][0] not in q:
            if COL_KEYS[key][0] not in q:
                continue
        neg = bool(re.search(r"(为空|没有|无|不包含|不需要)", nq))
        sel = []
        for sheet, idx, row in ts.rows_for_col(rows_hdr, *COL_KEYS[key]):
            v = ts.cell(row, idx).strip()
            has = bool(v) and v not in ("－", "-", "—", "/", "无")
            if has != neg:
                sel.append((sheet, row))
        if sel:
            lines = ["%s%s的产品（共 %d 项）：" % ("不包含" if neg else "包含", COL_KEYS[key][0], len(sel))]
            for sheet, row in sel[:10]:
                hs = next((h for s2, h, r2 in rows_hdr if r2 is row and s2 == sheet), headers)
                lines.append("- " + _fmt_row(sheet, hs, row, multi=multi))
            if len(sel) > 10:
                lines.append("（仅显示前 10 条，共 %d 项）" % len(sel))
            return {"mode": "structured", "confidence": 0.85, "verified": True,
                    "answer": "\n".join(lines),
                    "evidence": {"rows": [r["seq"] for _, r in sel[:10]]}}

    # 6) 关键词搜索
    toks = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]{2,}", q) if len(t) >= 2]
    scored = []
    for sheet, hs, row in rows_hdr:
        txt = " ".join(str(c) for c in row["cells"]).lower()
        sc = sum(1 for t in toks if t.lower() in txt)
        if sc:
            scored.append((sc, sheet, hs, row))
    if scored:
        scored.sort(key=lambda x: -x[0])
        top = scored[:8]
        lines = ["匹配「%s」的行（按相关度，共 %d 条）：" % (q[:30], len(scored))]
        for _, sheet, hs, row in top:
            lines.append("- " + _fmt_row(sheet, hs, row, multi=multi))
        return {"mode": "structured", "confidence": 0.6, "verified": True,
                "answer": "\n".join(lines),
                "evidence": {"rows": [r["seq"] for _, _, _, r in top]}}
    return None


def search_rows_text(kb_name, query, limit=8):
    """给 LLM 兜底的结构化行上下文（带 sheet/行号）"""
    meta = ts.get_meta(kb_name)
    if not meta:
        return ""
    rows_hdr = ts.rows_with_headers(kb_name)
    toks = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]{2,}", str(query))]
    scored = []
    for sheet, hs, row in rows_hdr:
        txt = _norm(" ".join(str(c) for c in row["cells"]))
        sc = sum(1 for t in toks if _norm(t) in txt)
        if sc:
            scored.append((sc, sheet, hs, row))
    scored.sort(key=lambda x: -x[0])
    pick = [(s, h, r) for _sc, s, h, r in scored[:limit]] or list(rows_hdr[:limit])
    lines = []
    for sheet, hs, row in pick:
        cells = " | ".join(str(c) for c in row["cells"])
        lines.append("数据第 %d 行（sheet=%s）：%s" % (row["seq"], sheet, cells))
    return "\n".join(lines)


def _table_nums(kb_name):
    nums = set()
    for r in ts.get_rows(kb_name):
        for c in r["cells"]:
            n = ts.num(c)
            if n is not None:
                nums.add(round(n, 2))
    return nums


def check_numbers(answer, kb_name, ignore_small=True):
    """数字交叉校验（排除编号内数字）"""
    if not ts.has_table(kb_name):
        return {"ok": True, "missing": [], "checked": 0, "skipped": "非表格 KB"}
    table_nums = _table_nums(kb_name)
    found = set()
    for m in re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])", str(answer or "")):
        try:
            found.add(round(float(m), 2))
        except Exception:
            continue
    missing = [n for n in found if not (ignore_small and abs(n) <= 12) and n not in table_nums]
    return {"ok": not missing, "missing": missing[:10], "checked": len(found)}


def check_entities(answer, kb_name):
    """实体交叉校验：答案里的编号/类似编号 token 是否在表中（B003 部分修复）"""
    if not ts.has_table(kb_name):
        return {"ok": True, "missing": [], "checked": 0, "skipped": "非表格 KB"}
    rows = ts.get_rows(kb_name)
    pool = " ".join(" ".join(str(c) for c in r["cells"]) for r in rows).upper()
    ans = str(answer or "").upper()
    cands = set(re.findall(r"\b[A-Z]{1,4}\d{2,}[A-Z]?\b", ans))
    cands |= set(re.findall(r"\b[A-Z]{2,}[A-Z0-9]*\d[A-Z0-9]*\b", ans))
    missing = sorted(c for c in cands if c not in pool)
    return {"ok": not missing, "missing": missing[:10], "checked": len(cands)}
