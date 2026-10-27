# -*- coding: utf-8 -*-
"""S20 · 通用轨迹存储（Trajectory Store）

统一轨迹 schema（计划图 §数据契约）：
{trajectory_id, at, task, task_type, source, provider, model, duration_s,
 steps:[{turn, thought, tool, input, result, status}],
 final, tools_used, tags:{task_type, quality, schema_ok, failover}, failover}
存储：data/hive/trajectories/*.jsonl（按天/统一一个文件）
"""
import json
import os
import time
import uuid
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
TRAJ_DIR = Path(os.environ.get("HIVE_TRAJ_DIR", str(PROJECT_DIR / "data" / "hive" / "trajectories")))
TRAJ_FILE = TRAJ_DIR / "cloud_trajectories.jsonl"


def _short(text, n=800):
    s = str(text or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def build_trajectory(request, answer, provider="", model="", duration_s=0.0,
                     context="", failover=None, task_type="other", extra_steps=None):
    """构造统一轨迹（云端教师成功轨迹）"""
    ans = str(answer or "")
    quality = "high" if len(ans) >= 120 else ("medium" if len(ans) >= 30 else "low")
    steps = [{
        "turn": 1,
        "thought": "云端教师：收到完整 context，生成高质量回答",
        "tool": "",
        "input": {"request": _short(request, 500),
                  "context_digest": _short(context, 800)},
        "result": _short(ans, 2000),
        "status": "completed",
    }]
    for s in extra_steps or []:
        steps.append(s)
    return {
        "trajectory_id": "tr_" + uuid.uuid4().hex[:12],
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "task": _short(request, 500),
        "task_type": task_type,
        "source": "cloud",
        "provider": provider,
        "model": model,
        "duration_s": round(float(duration_s or 0), 2),
        "steps": steps,
        "final": _short(ans, 2000),
        "tools_used": [],
        "tags": {"task_type": task_type, "quality": quality,
                 "schema_ok": True, "failover": bool(failover)},
        "failover": failover or {},
    }


def validate(traj):
    errs = []
    if not isinstance(traj, dict):
        return ["轨迹不是对象"]
    for k in ("trajectory_id", "task", "source", "steps", "final", "tags"):
        if k not in traj:
            errs.append("缺少字段: " + k)
    if not isinstance(traj.get("steps"), list) or not traj.get("steps"):
        errs.append("steps 必须是非空数组")
    else:
        for i, s in enumerate(traj["steps"]):
            if not isinstance(s, dict) or "result" not in s or "status" not in s:
                errs.append("steps[%d] 缺少 result/status" % i)
    if not isinstance(traj.get("tags"), dict):
        errs.append("tags 必须是对象")
    return errs


def record(traj):
    errs = validate(traj)
    if errs:
        return {"ok": False, "errors": errs}
    try:
        TRAJ_DIR.mkdir(parents=True, exist_ok=True)
        with open(TRAJ_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(traj, ensure_ascii=False) + "\n")
        return {"ok": True, "trajectory_id": traj["trajectory_id"], "path": str(TRAJ_FILE)}
    except Exception as e:
        return {"ok": False, "errors": ["%s: %s" % (type(e).__name__, str(e)[:200])]}


def list_trajectories(limit=20, source=None, task_type=None):
    try:
        lines = TRAJ_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for ln in reversed(lines):
        if len(out) >= limit:
            break
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if source and d.get("source") != source:
            continue
        if task_type and (d.get("tags") or {}).get("task_type") != task_type:
            continue
        out.append(d)
    return out


def stats():
    try:
        lines = TRAJ_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = []
    by_provider, by_type, by_quality, by_source = {}, {}, {}, {}
    failovers = 0
    for ln in lines:
        try:
            d = json.loads(ln)
        except Exception:
            continue
        by_provider[d.get("provider") or "-"] = by_provider.get(d.get("provider") or "-", 0) + 1
        tags = d.get("tags") or {}
        by_type[tags.get("task_type") or "-"] = by_type.get(tags.get("task_type") or "-", 0) + 1
        by_quality[tags.get("quality") or "-"] = by_quality.get(tags.get("quality") or "-", 0) + 1
        by_source[d.get("source") or "-"] = by_source.get(d.get("source") or "-", 0) + 1
        if tags.get("failover") or d.get("failover"):
            failovers += 1
    return {"total": len(lines), "file": str(TRAJ_FILE),
            "by_provider": by_provider, "by_task_type": by_type,
            "by_quality": by_quality, "by_source": by_source,
            "failover_trajectories": failovers}
