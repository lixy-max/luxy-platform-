# -*- coding: utf-8 -*-
"""多 sheet 表格结构化存储 v2：每行带 sheet，支持跨表检索"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TABLE_STORE_DB", str(PROJECT_DIR / "data" / "table_store.db")))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tables_meta (
  kb_name TEXT PRIMARY KEY,
  file TEXT, sheet TEXT, headers TEXT, sheets TEXT, n_rows INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS rows (
  kb_name TEXT NOT NULL, sheet TEXT NOT NULL DEFAULT '', seq INTEGER NOT NULL, cells TEXT,
  PRIMARY KEY (kb_name, sheet, seq)
);
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    cols = {r["name"] for r in c.execute("PRAGMA table_info(rows)").fetchall()}
    if cols and "sheet" not in cols:
        # v1 → v2 迁移：旧表无 sheet 列，直接重建（需重新入库）
        c.executescript("DROP TABLE IF EXISTS rows; DROP TABLE IF EXISTS tables_meta;")
    c.executescript(_SCHEMA)
    try:
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(tables_meta)").fetchall()}
        if "sheets" not in mcols:
            c.execute("ALTER TABLE tables_meta ADD COLUMN sheets TEXT DEFAULT '[]'")
    except Exception:
        pass
    c.commit()
    return c


def register_table(kb_name, parsed):
    """注册全部 sheet（v2）：每行带 sheet 名"""
    kb_name = str(kb_name)
    sheets = parsed.get("sheets") or []
    if not sheets:
        return {"kb_name": kb_name, "rows": 0, "sheets": []}
    total = 0
    sheets_meta = []
    with _conn() as c:
        c.execute("DELETE FROM rows WHERE kb_name=?", (kb_name,))
        c.execute("DELETE FROM tables_meta WHERE kb_name=?", (kb_name,))
        for sh in sheets:
            headers = sh.get("headers") or []
            rows = sh.get("rows") or []
            name = str(sh.get("name") or "")
            sheets_meta.append({"name": name, "headers": headers, "rows": len(rows)})
            for i, row in enumerate(rows, 1):
                c.execute("INSERT INTO rows(kb_name, sheet, seq, cells) VALUES (?,?,?,?)",
                          (kb_name, name, i, json.dumps(row, ensure_ascii=False)))
            total += len(rows)
        first = sheets[0]
        c.execute("INSERT INTO tables_meta(kb_name, file, sheet, headers, sheets, n_rows, created_at) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (kb_name, parsed.get("file"), first.get("name"),
                   json.dumps(first.get("headers") or [], ensure_ascii=False),
                   json.dumps(sheets_meta, ensure_ascii=False), total, _now()))
    return {"kb_name": kb_name, "rows": total, "sheets": sheets_meta}


def has_table(kb_name):
    try:
        with _conn() as c:
            r = c.execute("SELECT 1 FROM tables_meta WHERE kb_name=?", (str(kb_name),)).fetchone()
        return bool(r)
    except Exception:
        return False


def get_meta(kb_name):
    with _conn() as c:
        r = c.execute("SELECT * FROM tables_meta WHERE kb_name=?", (str(kb_name),)).fetchone()
        if not r:
            return None
        try:
            sheets = json.loads(r["sheets"] or "[]")
        except Exception:
            sheets = []
        return {"kb_name": r["kb_name"], "file": r["file"], "sheet": r["sheet"],
                "headers": json.loads(r["headers"] or "[]"), "sheets": sheets,
                "n_rows": r["n_rows"], "created_at": r["created_at"]}


def get_rows(kb_name):
    meta = get_meta(kb_name)
    if not meta:
        return []
    with _conn() as c:
        rs = c.execute("SELECT sheet, seq, cells FROM rows WHERE kb_name=? ORDER BY sheet, seq",
                       (str(kb_name),)).fetchall()
    return [{"sheet": r["sheet"], "seq": r["seq"], "cells": json.loads(r["cells"] or "[]")}
            for r in rs]


def rows_with_headers(kb_name):
    """[(sheet, headers, row)]：多 sheet 下每行用自己 sheet 的表头"""
    meta = get_meta(kb_name)
    if not meta:
        return []
    hmap = {s.get("name"): (s.get("headers") or []) for s in (meta.get("sheets") or [])}
    out = []
    for r in get_rows(kb_name):
        out.append((r["sheet"], hmap.get(r["sheet"]) or meta.get("headers") or [], r))
    return out


def list_tables():
    with _conn() as c:
        rs = c.execute("SELECT * FROM tables_meta ORDER BY created_at DESC").fetchall()
    out = []
    for r in rs:
        try:
            sheets = json.loads(r["sheets"] or "[]")
        except Exception:
            sheets = []
        out.append({"kb_name": r["kb_name"], "file": r["file"], "sheet": r["sheet"],
                    "n_rows": r["n_rows"], "sheets": [s.get("name") for s in sheets],
                    "created_at": r["created_at"]})
    return out


# ───────────────────── 字段工具 ─────────────────────
def find_col(headers, *keywords):
    for i, h in enumerate(headers or []):
        hs = str(h or "")
        for kw in keywords:
            if kw and kw in hs:
                return i
    return None


def rows_for_col(rows_hdr, *keywords):
    """在 rows_with_headers 结果里找有该列的行：[(sheet, idx, row)]"""
    out = []
    for sheet, headers, row in rows_hdr:
        idx = find_col(headers, *keywords)
        if idx is not None:
            out.append((sheet, idx, row))
    return out


def cell(row, idx):
    if idx is None or idx < 0 or idx >= len(row["cells"]):
        return ""
    return str(row["cells"][idx]).strip()


_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~～至]\s*(\d+(?:\.\d+)?)")
_WAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万|w|W)")


def num(v):
    """单元格 → 数字（v3）：
    · 普通数值（去 ¥/￥/,/元/空格）
    · 区间 "1000-2000" → 取中位数
    · 带万 "1.5万" → 15000
    · 非数值（待定/－/面议）→ None
    """
    t = str(v or "").strip()
    if not t:
        return None
    if t in ("－", "-", "—", "/", "待定", "面议", "无", "N/A", "n/a"):
        return None
    m = _RANGE_RE.search(t)
    if m:
        try:
            return (float(m.group(1)) + float(m.group(2))) / 2
        except Exception:
            pass
    mw = _WAN_RE.search(t)
    if mw:
        try:
            return float(mw.group(1)) * 10000
        except Exception:
            pass
    t2 = re.sub(r"[¥￥,，\s]|元|块|元/.*$|/.*$", "", t)
    m2 = re.search(r"\d+(?:\.\d+)?", t2)
    if not m2:
        return None
    try:
        return float(m2.group(0))
    except Exception:
        return None


def row_field_map(headers, row):
    out = {}
    for i, h in enumerate(headers or []):
        v = cell(row, i)
        if v:
            out[str(h)] = v
    return out
