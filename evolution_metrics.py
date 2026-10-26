# -*- coding: utf-8 -*-
"""evolution_metrics.py —— S9：进化指标 v2（本地闭环率/云调用/蒸馏/复用/能力/治理/台账）

数据来源（全部本地落盘，不额外依赖）：
  · data/route_tables.db            任务表（本地/云/移植、完成率、复用）
  · data/distilled/workflows.jsonl  沉淀工作流
  · data/distilled/reuse_log.jsonl  工作流复用记录（命中即执行时打点）
  · data/cloud/usage.jsonl          云调用记录（cloud_bridge 打点）
  · data/capabilities/registry.json 能力状态
  · data/governance/audit.jsonl     治理事件（晋级/冻结/执行）
  · data/bug_ledger.jsonl           Bug 台账
指标：本地闭环率、云调用次数/占比、蒸馏工作流数、复用次数、能力活跃数、任务表完成率、
      治理预算使用、Bug 修复率。
快照：data/metrics/evolution.jsonl（append；用于趋势）
"""
import json
import os
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
METRICS_DIR = PROJECT_DIR / "data" / "metrics"
SNAPSHOT_FILE = METRICS_DIR / "evolution.jsonl"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_jsonl(path, limit=None):
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    return rows[-int(limit):] if limit else rows


def _task_table_metrics():
    import sqlite3
    from route_table import DB_PATH as RT_DB
    out = {"tables": 0, "by_status": {}, "items_by_category": {}, "items_by_status": {},
           "completion_rate": None, "workflow_items": 0, "tables_with_artifact": 0,
           "entry_ratio": None, "recent": []}
    try:
        if not Path(RT_DB).exists():
            return out
        c = sqlite3.connect(str(RT_DB))
        c.row_factory = sqlite3.Row
        out["tables"] = c.execute("SELECT COUNT(*) n FROM tables").fetchone()["n"]
        for r in c.execute("SELECT status, COUNT(*) n FROM tables GROUP BY status"):
            out["by_status"][r["status"]] = r["n"]
        for r in c.execute("SELECT category, status, COUNT(*) n FROM items GROUP BY category, status"):
            out["items_by_category"].setdefault(r["category"], {})[r["status"]] = r["n"]
            out["items_by_status"][r["status"]] = out["items_by_status"].get(r["status"], 0) + r["n"]
        total_items = sum(out["items_by_status"].values())
        done = out["items_by_status"].get("done", 0)
        out["completion_rate"] = round(done / total_items, 3) if total_items else None
        out["workflow_items"] = c.execute(
            "SELECT COUNT(*) n FROM items WHERE target LIKE 'workflow:%'").fetchone()["n"]
        out["entry_ratio"] = round(out["workflow_items"] / total_items, 3) if total_items else None
        rows = c.execute("SELECT id, request, status, items_count, meta, created_at FROM tables "
                         "ORDER BY created_at DESC LIMIT 8").fetchall()
        for r in rows:
            meta = {}
            try:
                meta = json.loads(r["meta"] or "{}")
            except Exception:
                meta = {}
            if meta.get("artifact"):
                out["tables_with_artifact"] += 1
            out["recent"].append({"id": r["id"], "request": str(r["request"])[:60],
                                  "status": r["status"], "items": r["items_count"],
                                  "created_at": r["created_at"]})
        c.close()
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


def _workflow_metrics():
    import trajectory_distill as td
    rows = td.list_workflows(limit=1000)
    reuse = _read_jsonl(PROJECT_DIR / "data" / "distilled" / "reuse_log.jsonl")
    by_channel = {}
    for r in reuse:
        ch = r.get("channel") or "unknown"
        by_channel[ch] = by_channel.get(ch, 0) + 1
    return {"workflows": len(rows), "reuse_total": len(reuse),
            "reuse_by_channel": by_channel,
            "last_reuse_at": (reuse[-1].get("at") if reuse else None),
            "latest": [{"wf_id": r.get("wf_id"), "task": str(r.get("task"))[:60],
                        "created_at": r.get("created_at")} for r in rows[-5:]]}


def _cloud_metrics():
    rows = _read_jsonl(PROJECT_DIR / "data" / "cloud" / "usage.jsonl", limit=500)
    ok = [r for r in rows if r.get("ok")]
    lat = [r.get("duration") for r in rows if isinstance(r.get("duration"), (int, float))]
    by_model = {}
    for r in rows:
        m = r.get("model") or "?"
        by_model[m] = by_model.get(m, 0) + 1
    return {"calls_total": len(rows), "calls_ok": len(ok),
            "success_rate": round(len(ok) / len(rows), 3) if rows else None,
            "avg_duration": round(sum(lat) / len(lat), 2) if lat else None,
            "by_model": by_model, "last_at": (rows[-1].get("at") if rows else None)}


def _capability_metrics():
    try:
        import capabilities as caps
        rows = caps.list_capabilities()
        by_status, by_kind = {}, {}
        for c in rows:
            st = c.get("status") or "?"
            kd = c.get("kind") or "?"
            by_status[st] = by_status.get(st, 0) + 1
            by_kind[kd] = by_kind.get(kd, 0) + 1
        return {"total": len(rows), "by_status": by_status, "by_kind": by_kind,
                "active": by_status.get("active", 0), "shadow": by_status.get("shadow", 0)}
    except Exception as e:
        return {"error": str(e)[:150]}


def _governance_metrics():
    import governance as gov
    rows = gov.read_audit(limit=1000)
    actions = {}
    for r in rows:
        a = r.get("action") or "?"
        actions[a] = actions.get(a, 0) + 1
    st = gov.status()
    return {"audit_events": len(rows), "by_action": actions,
            "exec_today": st.get("exec_today"), "exec_per_day": st.get("exec_per_day"),
            "frozen": st.get("frozen")}


def _bug_metrics():
    import bug_ledger as bl
    st = bl.stats()
    return {"total": st.get("total"), "by_status": st.get("by_status"),
            "fixed": st.get("by_status", {}).get("fixed", 0),
            "fixed_rate": round(st.get("by_status", {}).get("fixed", 0) / st["total"], 3)
            if st.get("total") else None}


def _kpis(tables, workflows, cloud, caps, bugs):
    return {
        "local_closure_rate": tables.get("completion_rate"),
        "workflow_reuse_count": workflows.get("reuse_total"),
        "distilled_workflows": workflows.get("workflows"),
        "cloud_calls": cloud.get("calls_total"),
        "cloud_success_rate": cloud.get("success_rate"),
        "capability_active": caps.get("active"),
        "task_tables": tables.get("tables"),
        "bug_fixed_rate": bugs.get("fixed_rate"),
    }


def current():
    tables = _task_table_metrics()
    workflows = _workflow_metrics()
    cloud = _cloud_metrics()
    caps = _capability_metrics()
    gov = _governance_metrics()
    bugs = _bug_metrics()
    return {"at": _now(),
            "kpis": _kpis(tables, workflows, cloud, caps, bugs),
            "task_tables": tables, "workflows": workflows, "cloud": cloud,
            "capabilities": caps, "governance": gov, "bugs": bugs}


def snapshot(save=True):
    m = current()
    if save:
        METRICS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SNAPSHOT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return m


def trend(limit=30):
    rows = _read_jsonl(SNAPSHOT_FILE, limit=limit)
    return [{"at": r.get("at"), "kpis": r.get("kpis")} for r in rows]


def summary_text():
    m = current()
    k = m["kpis"]
    lines = ["【进化指标】%s" % m["at"]]
    lines.append("· 任务表：%s 张 / 完成率 %s / 工作流复用项 %s" % (
        k.get("task_tables"), k.get("local_closure_rate"), (m["task_tables"] or {}).get("workflow_items")))
    lines.append("· 蒸馏工作流：%s 条 / 复用 %s 次" % (k.get("distilled_workflows"), k.get("workflow_reuse_count")))
    lines.append("· 云调用：%s 次 / 成功率 %s / 均耗时 %ss" % (
        k.get("cloud_calls"), k.get("cloud_success_rate"), m["cloud"].get("avg_duration")))
    lines.append("· 能力：active %s / shadow %s / 总 %s" % (
        k.get("capability_active"), m["capabilities"].get("shadow"), m["capabilities"].get("total")))
    lines.append("· Bug 台账：修复率 %s（%s/%s）" % (
        k.get("bug_fixed_rate"), m["bugs"].get("fixed"), m["bugs"].get("total")))
    return "\n".join(lines)
