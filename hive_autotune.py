# -*- coding: utf-8 -*-
"""S21 · 阈值自动训练（Auto-Tuning Daemon 的核心逻辑）

流程：
  轨迹库 → 质检（hive_quality）→ 按 task_type 统计高质量样本
  → 达到阈值（默认 3 条/类）→ 生成 sharegpt 训练集 → 训练调度（hive_trainer：3060 优先→5060 回退）
  → adapter + 指标 → 交给 S22 热加载流水线

安全：默认 enabled=False / dry_run=True，只有显式 trigger 才真正起训；
      资源不足时 trainer 会 defer（不硬上 OOM）。
"""
import json
import os
import time
from pathlib import Path

import hive_quality as qa
import hive_trainer as trainer

PROJECT_DIR = Path(__file__).resolve().parent
AUTO_DIR = PROJECT_DIR / "data" / "hive" / "autotune"
DATA_DIR = PROJECT_DIR / "training_data" / "hive_auto"
JOBS_FILE = AUTO_DIR / "jobs.jsonl"
THRESHOLD = int(os.environ.get("HIVE_AUTOTUNE_N", "3"))
MIN_QUALITY = os.environ.get("HIVE_AUTOTUNE_MIN_QUALITY", "medium")
ENABLED = os.environ.get("HIVE_AUTOTUNE_ENABLED", "0") in ("1", "true", "True")
DEFAULT_REQUIRED_GB = float(os.environ.get("HIVE_AUTOTUNE_REQUIRED_GB", "6.0"))


def _cfg():
    return {"threshold": THRESHOLD, "min_quality": MIN_QUALITY,
            "enabled": ENABLED, "required_gb": DEFAULT_REQUIRED_GB,
            "task_types": sorted(set((qa.stats() or {}) and [])),  # 占位，下面用候选
            "train_gpu_priority": [1, 0],
            "fallback_gpu": 0}


def candidates(qa_result=None):
    """按 task_type 统计通过质检的高质量样本数，判断是否达阈值"""
    res = qa_result or qa.run_qa(persist=True)
    counts = {}
    for it in qa.load_passed():
        tags = it.get("tags") or {}
        if tags.get("quality") not in ("high", "medium"):
            continue
        if MIN_QUALITY == "high" and tags.get("quality") != "high":
            continue
        tt = tags.get("task_type") or "other"
        counts[tt] = counts.get(tt, 0) + 1
    out = []
    for tt, n in sorted(counts.items(), key=lambda x: -x[1]):
        out.append({"task_type": tt, "count": n, "threshold": THRESHOLD,
                    "eligible": n >= THRESHOLD})
    return {"quality": res, "threshold": THRESHOLD, "min_quality": MIN_QUALITY,
            "candidates": out}


def check(dry_run=True, required_gb=None, allow_stop_worker=True,
          allow_stop_main=True, allow_gpu0=True):
    """阈值检查 + 训练计划（默认 dry_run，不起训）"""
    c = candidates()
    need = float(required_gb or DEFAULT_REQUIRED_GB)
    plan = trainer.plan_training(need, allow_stop_worker=allow_stop_worker,
                                 allow_stop_main=allow_stop_main, allow_gpu0=allow_gpu0)
    eligible = [x for x in c["candidates"] if x["eligible"]]
    result = {"ok": True, "dry_run": bool(dry_run), "config": _cfg(),
              "quality": c["quality"], "candidates": c["candidates"],
              "eligible": eligible, "plan": plan,
              "would_trigger": [x["task_type"] for x in eligible] if eligible else []}
    if not eligible:
        result["reason"] = "无 task_type 达到阈值（%d 条）" % THRESHOLD
    elif plan["action"] == "defer":
        result["reason"] = "已达阈值但训练资源不足：" + plan["reason"]
    return result


def _dataset_path(task_type):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    return DATA_DIR / ("%s_%s.jsonl" % (task_type, ts))


def trigger(task_type=None, lora_name=None, dry_run=True, required_gb=None,
            epochs=2, max_len=1024, allow_stop_worker=True, allow_stop_main=True,
            allow_gpu0=True, force=False):
    """对某个 task_type 触发自动训练（默认 dry_run；真实训练需 dry_run=False）"""
    c = candidates()
    targets = [x for x in c["candidates"] if x["eligible"]] if task_type is None else \
        [x for x in c["candidates"] if x["task_type"] == task_type]
    job = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "dry_run": bool(dry_run),
           "task_type": task_type, "lora_name": lora_name,
           "threshold": THRESHOLD, "eligible": [x["task_type"] for x in c["candidates"] if x["eligible"]]}
    if not targets or not any(x["eligible"] for x in targets):
        job.update({"ok": False, "status": "not_eligible",
                    "reason": "task_type=%r 未达阈值 %d（当前候选 %s）"
                              % (task_type, THRESHOLD, c["candidates"])})
        _save(job)
        return job
    tt = targets[0]["task_type"]
    samples = qa.build_training_samples(task_type=tt, min_quality=MIN_QUALITY, limit=200)
    if not samples:
        job.update({"ok": False, "status": "no_samples",
                    "reason": "质检后无可用训练样本"})
        _save(job)
        return job
    ds_path = _dataset_path(tt)
    ds_path.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n",
                       encoding="utf-8")
    base = os.environ.get("HIVE_AUTOTUNE_BASE_MODEL", "/home/luxy/models/Qwen2.5-1.5B-Instruct")
    name = lora_name or ("auto_%s_v%s" % (tt, time.strftime("%m%d_%H%M")))
    job.update({"ok": True, "samples": len(samples), "dataset": str(ds_path),
                "base_model": base, "lora_name": name})
    tr = trainer.execute_training(
        dataset=ds_path, base_model=base,
        output=str(Path("/home/luxy/loras") / name),
        lora_name=name, epochs=epochs, max_len=max_len,
        required_gb=required_gb, allow_stop_worker=allow_stop_worker,
        allow_stop_main=allow_stop_main, allow_gpu0=allow_gpu0,
        dry_run=dry_run)
    job["trainer"] = tr
    if not dry_run:
        job["status"] = "done" if tr.get("ok") else tr.get("status", "failed")
    else:
        job["status"] = "planned"
    _save(job)
    return job


def _save(job):
    try:
        AUTO_DIR.mkdir(parents=True, exist_ok=True)
        with open(JOBS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(job, ensure_ascii=False) + "\n")
    except Exception:
        pass


def list_jobs(limit=10):
    try:
        lines = JOBS_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []


def stats():
    c = candidates()
    return {"config": _cfg(), "candidates": c["candidates"],
            "quality": qa.stats(), "train_jobs": len(trainer.list_jobs(100)),
            "autotune_jobs": len(list_jobs(100))}
