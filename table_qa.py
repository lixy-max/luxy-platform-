# -*- coding: utf-8 -*-
"""table_qa.py —— S3 表格答案模式：结构化问答 + 数字交叉校验

定位：表格类 KB 的查询优先走**确定性规则**（不靠 14B 自由发挥），答案带"数据第 N 行"引用；
      规则覆盖不了时才回退 LLM，并用 check_numbers() 交叉校验数字是否真的在表里。

支持意图：
  1. 计数     「多少产品/共几行/数量」
  2. 极值     「市场价最高/最低」「最贵/最便宜」（自动找价格列）
  3. TopN     「最高 3 个」「前 5 名」
  4. 精确查找 「BFB899a 的价格」「编号 … 的名称」→ 命中的整行字段
  5. 条件筛选 「包含安装培训」「重量为空」「需要培训」
  6. 关键词搜索 「跑酷」「木工坊」
"""
import re

import table_store as ts

CHINESE_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 常见列语义
COL_KEYS = {
    "code": ("产品编号", "编号", "料号", "sku", "货号"),
    "name": ("产品名称", "名称", "品名"),
    "price": ("市场价", "价格", "单价", "金额", "报价", "价"),
    "training": ("安装培训", "培训"),
    "weight": ("重量",),
    "spec": ("产品参数", "参数", "规格", "说明"),
}


def _norm(t):
    t = str(t or "").lower()
    t = re.sub(r"[\s，,。？?！!：:；;\"'“”‘’（）()【】\[\]]+", "", t)
    return t


def _num_from_text(t):
    m = re.search(r"(\d+)", str(t or ""))
    if not m:
        return None
    return int(m.group(1))


def _fmt_row(meta, row, fields=None):
    """行 → "编号：X；名称：Y；市场价：Z 元（数据第 N 行）" """
    idx = {str(h): i for i, h in enumerate(meta.get("headers") or [])}
    parts = []
    for key in ("code", "name", "price", "training", "weight"):
        want = COL_KEYS[key]
        for k, i in idx.items():
            if any(w in k for w in want):
                v = ts.cell(row, i)
                if v:
                    label = {"code": "编号", "name": "名称", "price": "市场价", "training": "安装培训", "weight": "重量"}[key]
                    parts.append("%s：%s" % (label, v))
                break
    if not parts:
        fm = ts.row_field_map(meta, row)
        parts = ["%s：%s" % (k, v) for k, v in list(fm.items())[:4]]
    return "；".join(parts) + "（数据第 %d 行）" % row["seq"]


def _col_idx(meta, key):
    return ts.find_col(meta.get("headers") or [], *COL_KEYS[key])


def try_answer(kb_name, query):
    """确定性回答；处理不了返回 None（交由 LLM 兜底）"""
    if not ts.has_table(kb_name):
        return None
    meta = ts.get_meta(kb_name)
    rows = ts.get_rows(kb_name)
    if not meta or not rows:
        return None
    q = str(query or "")
    nq = _norm(q)
    headers = meta.get("headers") or []
    name_i = _col_idx(meta, "name")
    code_i = _col_idx(meta, "code")
    price_i = _col_idx(meta, "price")
    train_i = _col_idx(meta, "training")
    weight_i = _col_idx(meta, "weight")

    # ── 1) 计数 ──
    if re.search(r"(多少|几|共|总数|数量)", nq) and re.search(r"(产品|行|条|个)", nq):
        return {"mode": "structured", "confidence": 0.9, "verified": True,
                "answer": "表格共 **%d 行数据**（不含表头；sheet=%s，文件=%s）。" % (
                    meta["n_rows"], meta.get("sheet"), meta.get("file")),
                "evidence": {"count": meta["n_rows"]}}

    # ── 2/3) 极值 / TopN ──
    want_extreme = re.search(r"(最高|最大|最贵|最低|最小|最便宜|top|前\d+)", nq)
    if want_extreme and price_i is not None:
        n = _num_from_text(q) or (3 if "top" in nq or "前" in nq else 1)
        n = max(1, min(n, 20))
        asc = bool(re.search(r"(最低|最小|最便宜)", nq))
        priced = [(ts.num(ts.cell(r, price_i)), r) for r in rows]
        priced = [(p, r) for p, r in priced if p is not None]
        if priced:
            priced.sort(key=lambda x: x[0], reverse=not asc)
            top = priced[:n]
            lines = ["市场价格%s %d 个：" % ("最低" if asc else "最高", len(top))]
            for i, (p, r) in enumerate(top, 1):
                nm = ts.cell(r, name_i) if name_i is not None else ""
                lines.append("%d) %s —— 市场价 %g 元（数据第 %d 行）" % (i, nm or "(无名称)", p, r["seq"]))
            return {"mode": "structured", "confidence": 0.88, "verified": True,
                    "answer": "\n".join(lines),
                    "evidence": {"column": headers[price_i], "rows": [r["seq"] for _, r in top]}}

    # ── 4) 精确查找：编号/名称命中查询 ──
    qn = _norm(q)
    hits = []
    for r in rows:
        code_v = _norm(ts.cell(r, code_i)) if code_i is not None else ""
        name_v = _norm(ts.cell(r, name_i)) if name_i is not None else ""
        if code_v and len(code_v) >= 3 and code_v in qn:
            hits.append(r)
        elif name_v and len(name_v) >= 2 and name_v in qn:
            hits.append(r)
    if hits:
        lines = ["找到 %d 条匹配：" % len(hits)]
        for r in hits[:5]:
            lines.append("- " + _fmt_row(meta, r))
        if len(hits) > 5:
            lines.append("（仅显示前 5 条，共 %d 条）" % len(hits))
        return {"mode": "structured", "confidence": 0.95, "verified": True,
                "answer": "\n".join(lines), "evidence": {"rows": [r["seq"] for r in hits[:5]]}}

    # ── 5) 条件筛选（列值非空/为空/包含）──
    for key, ci in (("training", train_i), ("weight", weight_i)):
        if ci is None:
            continue
        if COL_KEYS[key][0] in q or any(k in qn for k in COL_KEYS[key]):
            neg = bool(re.search(r"(为空|没有|无|不包含|不需要)", nq))
            sel = []
            for r in rows:
                v = ts.cell(r, ci).strip()
                has = bool(v) and v not in ("－", "-", "—", "/", "无")
                if has != neg:
                    sel.append(r)
            if sel:
                head = ("不包含" if neg else "包含") + COL_KEYS[key][0]
                lines = ["%s的产品（共 %d 项）：" % (head, len(sel))]
                for r in sel[:10]:
                    lines.append("- " + _fmt_row(meta, r))
                if len(sel) > 10:
                    lines.append("（仅显示前 10 条，共 %d 项）" % len(sel))
                return {"mode": "structured", "confidence": 0.85, "verified": True,
                        "answer": "\n".join(lines), "evidence": {"rows": [r["seq"] for r in sel[:10]]}}

    # ── 6) 关键词搜索 ──
    toks = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]{2,}", q) if len(t) >= 2]
    scored = []
    for r in rows:
        txt = " ".join(str(c) for c in r["cells"]).lower()
        sc = sum(1 for t in toks if t.lower() in txt)
        if sc:
            scored.append((sc, r))
    if scored:
        scored.sort(key=lambda x: -x[0])
        top = [r for _, r in scored[:8]]
        lines = ["匹配「%s」的行（按相关度，共 %d 条）：" % (q[:30], len(scored))]
        for r in top:
            lines.append("- " + _fmt_row(meta, r))
        return {"mode": "structured", "confidence": 0.6, "verified": True,
                "answer": "\n".join(lines), "evidence": {"rows": [r["seq"] for r in top]}}
    return None


def search_rows_text(kb_name, query, limit=8):
    """给 LLM 兜底用的结构化行上下文（带行号）"""
    meta = ts.get_meta(kb_name)
    if not meta:
        return ""
    rows = ts.get_rows(kb_name)
    q = _norm(query)
    toks = [t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9_]{2,}", str(query))]
    scored = []
    for r in rows:
        txt = _norm(" ".join(str(c) for c in r["cells"]))
        sc = sum(1 for t in toks if _norm(t) in txt)
        if sc:
            scored.append((sc, r))
    scored.sort(key=lambda x: -x[0])
    pick = [r for _, r in scored[:limit]] or rows[:limit]
    lines = ["列：%s" % " | ".join(str(h) for h in meta.get("headers") or []), ""]
    for r in pick:
        lines.append("数据第 %d 行：%s" % (r["seq"], " | ".join(str(c) for c in r["cells"])))
    return "\n".join(lines)


def check_numbers(answer, kb_name, ignore_small=True):
    """交叉校验：答案里出现的数字是否都能在表格中找到（价格/数量类）。
    返回 {ok, missing, checked}。"""
    if not ts.has_table(kb_name):
        return {"ok": True, "missing": [], "checked": 0, "skipped": "非表格 KB"}
    rows = ts.get_rows(kb_name)
    table_nums = set()
    for r in rows:
        for c in r["cells"]:
            n = ts.num(c)
            if n is not None:
                table_nums.add(round(n, 2))
    found = set()
    # 只取"独立数字"（排除产品编号里的数字，如 BFB899a 的 899）
    for m in re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])", str(answer or "")):
        try:
            n = round(float(m), 2)
        except Exception:
            continue
        found.add(n)
    missing = []
    for n in found:
        if ignore_small and abs(n) <= 12:
            continue
        if n not in table_nums:
            missing.append(n)
    return {"ok": not missing, "missing": missing[:10], "checked": len(found)}
