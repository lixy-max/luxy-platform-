# -*- coding: utf-8 -*-
"""table_store.py —— 表格类 KB 的结构化存储（S3 表格答案模式）

为什么：RAG 把表格切成 markdown 片段后，14B 容易"跨行串位"（把 A 行价格安到 B 行）。
解决：入库时同时保留**结构化行**（列名 + 行号 + 逐行数据），问答时先走确定性规则，
      答案带"数据第 N 行"引用；LLM 兜底时再做数字交叉校验。

数据：data/table_store.db
  tables_meta(kb_name PRIMARY KEY, file, sheet, headers JSON, n_rows, created_at)
  rows(kb_name, seq, cells JSON)
"""
import json
import os
import sqlite3
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TABLE_STORE_DB", str(PROJECT_DIR / "data" / "table_store.db")))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tables_meta (
  kb_name TEXT PRIMARY KEY,
  file TEXT, sheet TEXT, headers TEXT, n_rows INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS rows (
  kb_name TEXT NOT NULL, seq INTEGER NOT NULL, cells TEXT,
  PRIMARY KEY (kb_name, seq)
);
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def register_table(kb_name, parsed):
    """把 parse_table() 结果注册为结构化表（同库先清空再写）"""
    kb_name = str(kb_name)
    sheets = parsed.get("sheets") or []
    if not sheets:
        return {"kb_name": kb_name, "rows": 0}
    sh = sheets[0]
    headers = sh.get("headers") or []
    rows = sh.get("rows") or []
    with _conn() as c:
        c.execute("DELETE FROM rows WHERE kb_name=?", (kb_name,))
        c.execute("DELETE FROM tables_meta WHERE kb_name=?", (kb_name,))
        c.execute("INSERT INTO tables_meta(kb_name, file, sheet, headers, n_rows, created_at) "
                  "VALUES (?,?,?,?,?,?)",
                  (kb_name, parsed.get("file"), sh.get("name"),
                   json.dumps(headers, ensure_ascii=False), len(rows), _now()))
        for i, row in enumerate(rows, 1):
            c.execute("INSERT INTO rows(kb_name, seq, cells) VALUES (?,?,?)",
                      (kb_name, i, json.dumps(row, ensure_ascii=False)))
    return {"kb_name": kb_name, "sheet": sh.get("name"), "headers": headers, "rows": len(rows)}


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
        return {"kb_name": r["kb_name"], "file": r["file"], "sheet": r["sheet"],
                "headers": json.loads(r["headers"] or "[]"), "n_rows": r["n_rows"],
                "created_at": r["created_at"]}


def get_rows(kb_name):
    meta = get_meta(kb_name)
    if not meta:
        return []
    with _conn() as c:
        rs = c.execute("SELECT seq, cells FROM rows WHERE kb_name=? ORDER BY seq", (str(kb_name),)).fetchall()
    return [{"seq": r["seq"], "cells": json.loads(r["cells"] or "[]")} for r in rs]


def list_tables():
    with _conn() as c:
        rs = c.execute("SELECT * FROM tables_meta ORDER BY created_at DESC").fetchall()
    return [{"kb_name": r["kb_name"], "file": r["file"], "sheet": r["sheet"],
             "n_rows": r["n_rows"], "created_at": r["created_at"]} for r in rs]


# ───────────────────── 字段工具 ─────────────────────
def find_col(headers, *keywords):
    for i, h in enumerate(headers or []):
        hs = str(h or "")
        for kw in keywords:
            if kw and kw in hs:
                return i
    return None


def cell(row, idx):
    if idx is None or idx < 0 or idx >= len(row["cells"]):
        return ""
    return str(row["cells"][idx]).strip()


def num(v):
    """单元格 → 数字（支持 ¥/￥/,/元/空格；失败 None）"""
    import re
    t = str(v or "").strip()
    if not t:
        return None
    t = re.sub(r"[¥￥,，\s]|元", "", t)
    try:
        return float(t)
    except Exception:
        return None


def row_field_map(meta, row):
    """行 → {列名: 值}（只保留非空列）"""
    out = {}
    for i, h in enumerate(meta.get("headers") or []):
        v = cell(row, i)
        if v:
            out[str(h)] = v
    return out
