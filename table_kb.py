# -*- coding: utf-8 -*-
"""table_kb.py —— S1：表格类文档（xls/xlsx）→ 知识库 的标准链路

用途：把"2025区角玩具清单.xls"这类采购/装备清单转成可检索知识库。
链路（对齐架构 v2 的 KB 构建标准）：
  parse_xls → 结构化记录（自动识别表头行/列）
  → 生成 Markdown 文档（概览 + 每 N 行一批表格，保留行号）
  → kb.ingest_folder 入库
  → 检索验收（编号/名称/价格/安装培训等字段）

输出文档带元数据头（来源/行数/批次），便于后续质量门与追溯。
"""
import json
import os
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DOCS_DIR = PROJECT_DIR / "data" / "uploads" / "table_docs"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def parse_table(path, max_rows=None):
    """解析 xls/xlsx → {sheets:[{name, headers, rows}], file, total_rows}"""
    path = str(path)
    ext = Path(path).suffix.lower()
    sheets = []
    if ext == ".xls":
        import xlrd
        wb = xlrd.open_workbook(path, on_demand=True)
        for sh in wb.sheets():
            rows = []
            for r in range(sh.nrows):
                rows.append([str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)])
            sheets.append({"name": sh.name, "rows": rows})
    elif ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append([("" if v is None else str(v).strip()) for v in row])
            sheets.append({"name": ws.title, "rows": rows})
    else:
        raise ValueError("仅支持 xls/xlsx: %s" % path)

    out = []
    total = 0
    for sh in sheets:
        raw = sh["rows"]
        # 找表头行：第一个"非空单元格 >= 3 且下一行更长/数值化"的行
        header_idx = 0
        for i, row in enumerate(raw[:10]):
            nonempty = [c for c in row if c]
            if len(nonempty) >= 3:
                header_idx = i
                break
        headers = [c or ("列%d" % (j + 1)) for j, c in enumerate(raw[header_idx])] if raw else []
        data = [r for r in raw[header_idx + 1:] if any(str(c).strip() for c in r)]
        if max_rows:
            data = data[:int(max_rows)]
        total += len(data)
        out.append({"name": sh["name"], "headers": headers, "rows": data})
    return {"file": Path(path).name, "path": path, "sheets": out, "total_rows": total}


def to_markdown_docs(parsed, batch_size=10, source_note=""):
    """结构化记录 → markdown 文档列表（概览 + 分批表格）"""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(parsed["file"]).stem
    docs = []
    for sh in parsed["sheets"]:
        headers = sh["headers"]
        rows = sh["rows"]
        n = len(rows)
        col_names = [h for h in headers if h and not h.startswith("列")]
        overview = [
            "# %s · %s" % (stem, sh["name"]),
            "",
            "- 来源文件：%s" % parsed["file"],
            "- 工作表：%s" % sh["name"],
            "- 数据行数：%d" % n,
            "- 列：%s" % "、".join(headers[:12]),
            "- 生成时间：%s" % _now(),
            ("- 备注：%s" % source_note) if source_note else "",
            "",
            "## 概览",
            "",
        ]
        # 关键列概览：名称列前 30 个
        name_col = None
        for i, h in enumerate(headers):
            if h and ("名称" in h or "产品" in h and "名称" in h):
                name_col = i
                break
        if name_col is not None:
            names = [r[name_col] for r in rows if len(r) > name_col and r[name_col]][:40]
            overview.append("- 产品名称（前 40 个）：" + "、".join(names))
        overview.append("")
        docs.append({"name": "%s_%s_00_概览.md" % (stem, sh["name"]), "text": "\n".join(overview)})

        # ── 派生索引文档（提升聚合类查询的召回）──
        idx = {str(h).strip(): i for i, h in enumerate(headers) if str(h).strip()}
        def _col(*keys):
            for k, i in idx.items():
                if any(x in k for x in keys):
                    return i
            return None
        name_i = _col("产品名称", "名称")
        code_i = _col("产品编号", "编号")
        train_i = _col("安装培训")
        price_i = _col("市场价", "价格")
        if code_i is not None and name_i is not None:
            lines = ["# %s · 产品索引（编号/名称）" % stem, "",
                     "- 来源：%s / %s" % (parsed["file"], sh["name"]), ""]
            for r in rows:
                if len(r) > max(code_i, name_i) and r[name_i]:
                    lines.append("- %s | %s" % (r[code_i], r[name_i]))
            docs.append({"name": "%s_%s_01_产品索引.md" % (stem, sh["name"]), "text": "\n".join(lines)})
        if train_i is not None and name_i is not None:
            items = []
            for r in rows:
                if len(r) > max(train_i, name_i):
                    v = str(r[train_i]).strip()
                    if v and v not in ("－", "-", "—", "/", "无"):
                        items.append("- %s | 安装培训：%s" % (r[name_i], v))
            lines = ["# %s · 安装培训清单（%d 项）" % (stem, len(items)), "", "以下产品包含安装培训要求：", ""] + items
            docs.append({"name": "%s_%s_02_安装培训清单.md" % (stem, sh["name"]), "text": "\n".join(lines)})
        if price_i is not None and name_i is not None:
            priced = []
            for r in rows:
                if len(r) <= max(price_i, name_i):
                    continue
                try:
                    pv = float(str(r[price_i]).replace(",", "").replace("元", "").strip())
                except Exception:
                    continue
                priced.append((pv, r[name_i]))
            priced.sort(reverse=True)
            lines = ["# %s · 市场价格排序（共 %d 项有价格）" % (stem, len(priced)), ""]
            for pv, nm in priced:
                lines.append("- %s | 市场价：%g 元" % (nm, pv))
            docs.append({"name": "%s_%s_03_价格排序.md" % (stem, sh["name"]), "text": "\n".join(lines)})

        for b in range(0, n, batch_size):
            batch = rows[b:b + batch_size]
            lines = ["# %s · %s（第 %d–%d 行）" % (stem, sh["name"], b + 1, min(b + batch_size, n)),
                     "", "- 来源：%s / %s" % (parsed["file"], sh["name"]), ""]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "|".join(["---"] * len(headers)) + "|")
            for row in batch:
                cells = [str(c).replace("\n", " ").replace("|", "/")[:200] for c in row]
                cells += [""] * (len(headers) - len(cells))
                lines.append("| " + " | ".join(cells) + " |")
            docs.append({"name": "%s_%s_%02d.md" % (stem, sh["name"], b // batch_size + 1),
                         "text": "\n".join(lines)})
    return docs


def ingest_table(path, kb_name, batch_size=10, description="", source_note=""):
    """完整链路：解析 → 生成 docs → 写盘 → kb.ingest_folder → 返回统计"""
    import kb
    parsed = parse_table(path)
    docs = to_markdown_docs(parsed, batch_size=batch_size, source_note=source_note)
    out_dir = DOCS_DIR / ("%s_%s" % (Path(parsed["file"]).stem, time.strftime("%Y%m%d")))
    out_dir.mkdir(parents=True, exist_ok=True)
    for d in docs:
        (out_dir / d["name"]).write_text(d["text"], encoding="utf-8")
    try:
        kb.create_kb(kb_name, description=description or ("表格知识库：%s" % parsed["file"]))
    except Exception:
        pass
    info = kb.ingest_folder(kb_name, str(out_dir), extensions=[".md"])
    # ★ S3 表格答案模式：同时注册结构化行（供确定性问答/数字校验）
    _store = {}
    try:
        import table_store
        _store = table_store.register_table(kb_name, parsed)
    except Exception as _e:
        _store = {"error": str(_e)[:200]}
    return {"file": parsed["file"], "sheets": [s["name"] for s in parsed["sheets"]],
            "total_rows": parsed["total_rows"], "docs_dir": str(out_dir),
            "docs": len(docs), "ingest": info, "kb": kb_name, "table_store": _store}


def table_stats(path):
    """仅解析统计（不落库）"""
    parsed = parse_table(path)
    return {"file": parsed["file"], "total_rows": parsed["total_rows"],
            "sheets": [{"name": s["name"], "rows": len(s["rows"]),
                        "cols": len(s["headers"]), "headers": s["headers"][:12]}
                       for s in parsed["sheets"]]}
