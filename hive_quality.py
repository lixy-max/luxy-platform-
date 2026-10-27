# -*- coding: utf-8 -*-
"""S21 · 轨迹质检（Quality Gate）

职责：把云端/本地轨迹清洗成"可训练入库"的高质量样本：
- 过滤本地不存在的工具调用（对照平台工具白名单）
- 过滤超长 / 超步数 / 过短轨迹
- 打标：task_type / quality / source / schema_ok
- 输出：data/hive/quality/*.jsonl（通过+拒绝原因）与训练样本（sharegpt）
"""
import json
import os
import time
from pathlib import Path

import hive_trajectory as tj

PROJECT_DIR = Path(__file__).resolve().parent
QUALITY_DIR = Path(os.environ.get("HIVE_QUALITY_DIR", str(PROJECT_DIR / "data" / "hive" / "quality")))
PASS_FILE = QUALITY_DIR / "passed.jsonl"
REJECT_FILE = QUALITY_DIR / "rejected.jsonl"

MAX_STEPS = int(os.environ.get("HIVE_QA_MAX_STEPS", "24"))
MIN_FINAL = int(os.environ.get("HIVE_QA_MIN_FINAL", "10"))
MAX_FINAL = int(os.environ.get("HIVE_QA_MAX_FINAL", "8000"))

# 平台可用工具白名单（本地工蜂/平台能执行的）
PLATFORM_TOOLS = {
    "write_sheet", "read_sheet", "paper_lora_kb_query",
    "paper_lora_list_loras", "paper_lora_list_datasets", "paper_lora_train_list",
    "vllm_status", "tool_list", "available_datasets", "workflow_list",
    "kb_list", "kb_query", "browser_open", "browser_read", "browser_find",
    "academic_search", "run_simulation", "list_sim_nodes",
}


def evaluate(traj):
    """返回 {pass, reasons, tags}"""
    reasons = []
    if not isinstance(traj, dict):
        return {"pass": False, "reasons": ["不是对象"], "tags": {}}
    steps = traj.get("steps") or []
    final = str(traj.get("final") or "")
    tools_used = list(traj.get("tools_used") or [])
    # 工具白名单
    tools = set(tools_used)
    for s in steps:
        for t in (s.get("tools_used") or []):
            tools.add(str(t))
        tool = str(s.get("tool") or "").strip()
        if tool:
            tools.add(tool)
    bad_tools = sorted(t for t in tools if t and t not in PLATFORM_TOOLS)
    if bad_tools:
        reasons.append("本地不可用工具: %s" % ",".join(bad_tools[:5]))
    # 步数/长度
    if len(steps) > MAX_STEPS:
        reasons.append("步数超限: %d > %d" % (len(steps), MAX_STEPS))
    if len(final) < MIN_FINAL:
        reasons.append("答案过短: %d < %d" % (len(final), MIN_FINAL))
    if len(final) > MAX_FINAL:
        reasons.append("答案过长: %d > %d" % (len(final), MAX_FINAL))
    # schema
    errs = tj.validate(traj)
    if errs:
        reasons.append("schema: " + "; ".join(errs[:3]))
    src = str(traj.get("source") or "")
    if src not in ("cloud", "local", "sim"):
        reasons.append("未知 source: %r" % src)
    tags = dict(traj.get("tags") or {})
    tags.setdefault("task_type", "other")
    if not tags.get("quality"):
        tags["quality"] = "high" if len(final) >= 120 else ("medium" if len(final) >= 30 else "low")
    tags["schema_ok"] = not errs
    tags["source"] = src or "unknown"
    tags["tools_count"] = len(tools)
    return {"pass": not reasons, "reasons": reasons, "tags": tags}


def run_qa(limit=None, persist=True):
    """对轨迹库跑质检；返回统计"""
    trajs = tj.list_trajectories(limit=limit or 5000)
    passed, rejected = [], []
    by_type, by_quality, reject_reasons = {}, {}, {}
    for t in trajs:
        ev = evaluate(t)
        item = {"trajectory_id": t.get("trajectory_id"), "task": t.get("task"),
                "source": t.get("source"), "tags": ev["tags"],
                "passed": ev["pass"], "reasons": ev["reasons"],
                "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if ev["pass"]:
            item["trajectory"] = t
            passed.append(item)
            tt = ev["tags"].get("task_type") or "other"
            by_type[tt] = by_type.get(tt, 0) + 1
            q = ev["tags"].get("quality") or "low"
            by_quality[q] = by_quality.get(q, 0) + 1
        else:
            rejected.append(item)
            for r in ev["reasons"]:
                key = r.split(":")[0]
                reject_reasons[key] = reject_reasons.get(key, 0) + 1
    if persist:
        QUALITY_DIR.mkdir(parents=True, exist_ok=True)
        with open(PASS_FILE, "w", encoding="utf-8") as f:
            for it in passed:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        with open(REJECT_FILE, "w", encoding="utf-8") as f:
            for it in rejected:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return {"total": len(trajs), "passed": len(passed), "rejected": len(rejected),
            "by_task_type": by_type, "by_quality": by_quality,
            "reject_reasons": reject_reasons,
            "passed_file": str(PASS_FILE), "rejected_file": str(REJECT_FILE)}


def load_passed():
    out = []
    try:
        for ln in PASS_FILE.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                out.append(json.loads(ln))
    except Exception:
        pass
    return out


def build_training_samples(task_type=None, min_quality=None, limit=200):
    """把通过质检的轨迹转为 sharegpt 训练样本：{system, user, assistant}"""
    items = load_passed()
    if not items:
        run_qa()
        items = load_passed()
    quality_rank = {"low": 0, "medium": 1, "high": 2}
    need = quality_rank.get(min_quality or "medium", 1)
    samples = []
    for it in items:
        tags = it.get("tags") or {}
        if task_type and tags.get("task_type") != task_type:
            continue
        if quality_rank.get(tags.get("quality") or "low", 0) < need:
            continue
        t = it.get("trajectory") or {}
        if not t.get("task") or not t.get("final"):
            continue
        samples.append({"conversations": [
            {"role": "system", "content": "你是蜂巢平台的本地执行模型。根据任务给出高质量、可执行的完整答案；"
                                          "如涉及平台工具，只使用本地可用工具。"},
            {"role": "user", "content": str(t.get("task"))[:1500]},
            {"role": "assistant", "content": str(t.get("final"))[:4000]},
        ], "metadata": {"trajectory_id": t.get("trajectory_id"),
                        "task_type": tags.get("task_type"),
                        "quality": tags.get("quality"),
                        "provider": t.get("provider")}})
        if len(samples) >= limit:
            break
    return samples


def stats():
    def _count(path):
        try:
            return len([x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()])
        except Exception:
            return 0
    return {"trajectories": tj.stats().get("total"),
            "passed": _count(PASS_FILE), "rejected": _count(REJECT_FILE),
            "max_steps": MAX_STEPS, "max_final": MAX_FINAL,
            "platform_tools": len(PLATFORM_TOOLS),
            "files": {"passed": str(PASS_FILE), "rejected": str(REJECT_FILE)}}
