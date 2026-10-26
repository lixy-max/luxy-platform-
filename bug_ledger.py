# -*- coding: utf-8 -*-
"""bug_ledger.py —— Bug 台账（挂起，最后统一修）

用户约定（2026-10-26）：后续发现 bug **只登记挂起**，不打断主线；最后集中修复。
用法：
  add(title, area, detail, status="parked", severity="P2")
  list(status=None) / update(bug_id, status=...)
  stats()
数据：data/bug_ledger.jsonl（append-only，最新状态按 id 合并）
文档：docs/BUG_LEDGER.md（由本模块生成/刷新）
"""
import json
import os
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
LEDGER = Path(os.environ.get("BUG_LEDGER", str(PROJECT_DIR / "data" / "bug_ledger.jsonl")))
DOC = PROJECT_DIR / "docs" / "BUG_LEDGER.md"

STATUSES = ("parked", "in_progress", "fixed", "wontfix", "duplicate")
SEVERITIES = ("P0", "P1", "P2", "P3")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read():
    if not LEDGER.exists():
        return []
    rows = []
    for ln in LEDGER.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    return rows


def _append(rec):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def add(title, area="general", detail="", severity="P2", status="parked", found_in=""):
    rows = _read()
    seq = len({r.get("id") for r in rows}) + 1
    bid = "B%03d" % seq
    rec = {"id": bid, "title": str(title)[:120], "area": str(area)[:40],
           "detail": str(detail)[:600], "severity": severity if severity in SEVERITIES else "P2",
           "status": status if status in STATUSES else "parked", "found_in": str(found_in)[:60],
           "created_at": _now(), "updated_at": _now()}
    _append(rec)
    return rec


def update(bug_id, **kw):
    rows = _read()
    cur = {r.get("id"): r for r in rows}
    base = dict(cur.get(bug_id) or {})
    if not base:
        raise ValueError("bug 不存在: %s" % bug_id)
    base.update({k: v for k, v in kw.items() if k in ("title", "area", "detail", "severity",
                                                      "status", "found_in", "note")})
    base["updated_at"] = _now()
    _append(base)
    return base


def list_bugs(status=None, severity=None):
    latest = {}
    for r in _read():
        latest[r.get("id")] = r
    rows = list(latest.values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if severity:
        rows = [r for r in rows if r.get("severity") == severity]
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    rows.sort(key=lambda r: (order.get(r.get("severity"), 9), r.get("id", "")))
    return rows


def stats():
    rows = list_bugs()
    by = {}
    for r in rows:
        by.setdefault(r.get("status"), 0)
        by[r.get("status")] += 1
    return {"total": len(rows), "by_status": by,
            "parked": len([r for r in rows if r.get("status") == "parked"])}


def render_doc():
    rows = list_bugs()
    lines = ["# Bug 台账（挂起，最后集中修复）", "",
             "- 约定：后续发现 bug **只登记挂起**，不打断主线；最后统一修复。",
             "- 数据：`data/bug_ledger.jsonl`；本文件由 `bug_ledger.render_doc()` 生成。",
             "- 更新：`POST /bugs` 新增，`POST /bugs/{id}/status` 改状态。", "",
             "| ID | 严重度 | 状态 | 区域 | 标题 | 发现于 |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            r.get("id"), r.get("severity"), r.get("status"), r.get("area"),
            str(r.get("title")).replace("|", "/"), r.get("found_in") or "-"))
    lines.append("")
    lines.append("## 详细说明")
    for r in rows:
        lines.append("")
        lines.append("### %s · %s" % (r.get("id"), r.get("title")))
        lines.append("- 区域：%s ｜ 严重度：%s ｜ 状态：%s" % (r.get("area"), r.get("severity"), r.get("status")))
        lines.append("- 发现于：%s ｜ 登记：%s" % (r.get("found_in") or "-", r.get("created_at")))
        if r.get("detail"):
            lines.append("- 详情：%s" % r.get("detail"))
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines), encoding="utf-8")
    return str(DOC)
