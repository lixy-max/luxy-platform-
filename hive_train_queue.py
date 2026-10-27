# -*- coding: utf-8 -*-
"""S27 · 训练持久队列（Training Queue）

解决"S21 defer 无持久化"问题：训练任务落盘，支持排队/重试/审计。
- 任务字段：id/kind/dataset/base_model/output/lora_name/epochs/max_len/qlora/max_steps/
  required_gb/priority/status(pending/running/done/failed/deferred)/attempts/next_retry_at/
  created_at/updated_at/result
- 能力：enqueue / list / get / next_ready / mark_running / mark_done / mark_failed /
  defer / retry_deferred / process_next(executor) / stats
- 存储：data/hive/train_queue.json
"""
import json
import os
import pathlib
import threading
import time
import uuid

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
QUEUE_FILE = pathlib.Path(os.environ.get(
    "HIVE_TRAIN_QUEUE", str(PROJECT_DIR / "data" / "hive" / "train_queue.json")))
MAX_ATTEMPTS = int(os.environ.get("HIVE_TRAIN_MAX_ATTEMPTS", "3"))
RETRY_DELAY_S = int(os.environ.get("HIVE_TRAIN_RETRY_DELAY", "60"))
_lock = threading.RLock()


def _load():
    try:
        if QUEUE_FILE.exists():
            data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save(rows):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue(task):
    """入队一个训练任务（required: dataset）"""
    if not task or not task.get("dataset"):
        return {"ok": False, "error": "dataset 必填"}
    with _lock:
        rows = _load()
        tid = task.get("id") or "tq_%s" % uuid.uuid4().hex[:10]
        row = {
            "id": tid, "kind": task.get("kind") or "lora",
            "dataset": str(task["dataset"]),
            "base_model": task.get("base_model") or "/home/luxy/models/Qwen2.5-1.5B-Instruct",
            "output": task.get("output") or "",
            "lora_name": task.get("lora_name") or "hive_auto",
            "epochs": int(task.get("epochs") or 1),
            "max_len": int(task.get("max_len") or 512),
            "qlora": bool(task.get("qlora")),
            "max_steps": int(task.get("max_steps") or -1),
            "required_gb": float(task.get("required_gb") or 6.0),
            "priority": int(task.get("priority") or 5),
            "status": "pending", "attempts": 0, "next_retry_at": 0,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "result": {},
        }
        rows.append(row)
        _save(rows)
    return {"ok": True, "task": row}


def list_tasks(status=None, limit=100):
    rows = _load()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(rows, key=lambda r: (int(r.get("priority") or 5), r.get("created_at") or ""))[:limit]


def get(tid):
    for r in _load():
        if r.get("id") == tid:
            return dict(r)
    return None


def _update(tid, **fields):
    with _lock:
        rows = _load()
        for r in rows:
            if r.get("id") == tid:
                r.update(fields)
                r["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                _save(rows)
                return dict(r)
    return None


def next_ready(now=None):
    now = time.time() if now is None else now
    ready = [r for r in _load()
             if r.get("status") == "pending" and float(r.get("next_retry_at") or 0) <= now]
    if not ready:
        return None
    ready.sort(key=lambda r: (int(r.get("priority") or 5), r.get("created_at") or ""))
    return dict(ready[0])


def mark_running(tid):
    return _update(tid, status="running", attempts=int((get(tid) or {}).get("attempts") or 0) + 1)


def mark_done(tid, result=None):
    return _update(tid, status="done", result=result or {})


def mark_failed(tid, result=None, retry=True, delay_s=None):
    r = get(tid) or {}
    attempts = int(r.get("attempts") or 0)
    if retry and attempts < MAX_ATTEMPTS:
        delay = int(delay_s if delay_s is not None else RETRY_DELAY_S)
        return _update(tid, status="deferred", result=result or {},
                       next_retry_at=time.time() + delay,
                       last_error=str((result or {}).get("error") or "")[:300])
    return _update(tid, status="failed", result=result or {})


def retry_deferred(delay_s=None):
    rows = [r for r in _load() if r.get("status") == "deferred"]
    for r in rows:
        _update(r["id"], next_retry_at=time.time() + int(delay_s if delay_s is not None else 0))
    return {"ok": True, "retried": len(rows)}


def process_next(executor=None, dry_run=False):
    """取下一个可执行任务并执行（executor(task)->result dict）"""
    task = next_ready()
    if not task:
        return {"ok": False, "reason": "无待执行任务"}
    tid = task["id"]
    mark_running(tid)
    if dry_run:
        return {"ok": True, "task": get(tid), "dry_run": True}
    if executor is None:
        def executor(t):
            try:
                import hive_trainer as tr
                return tr.execute_training(
                    dataset=t["dataset"], base_model=t["base_model"],
                    output=t["output"], lora_name=t["lora_name"],
                    epochs=t["epochs"], max_len=t["max_len"],
                    required_gb=t["required_gb"])
            except Exception as e:
                return {"ok": False, "error": str(e)[:300]}
    try:
        result = executor(task) or {}
    except Exception as e:
        result = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}
    ok = bool(result.get("ok"))
    if ok:
        rec = mark_done(tid, result)
        return {"ok": True, "task": rec, "result": result}
    rec = mark_failed(tid, result, retry=bool(result.get("retry", True)))
    return {"ok": False, "task": rec, "result": result}


def stats():
    rows = _load()
    by_status = {}
    for r in rows:
        by_status[r.get("status")] = by_status.get(r.get("status"), 0) + 1
    return {"queue_file": str(QUEUE_FILE), "total": len(rows), "by_status": by_status,
            "max_attempts": MAX_ATTEMPTS, "retry_delay_s": RETRY_DELAY_S,
            "ready": len([r for r in rows if r.get("status") == "pending"
                          and float(r.get("next_retry_at") or 0) <= time.time()])}
