#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S21 实测：轨迹质检 → 阈值自动训练（3060，停/恢复工蜂）→ 5060 回退计划"""
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
OUT = Path("/home/luxy/paper_lora_project/data/hive/s21_live_test.json")
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def call(method, path, body=None, timeout=1200):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode()), round((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:800]}, round((time.time() - t0) * 1000)
    except Exception as e:
        return None, {"error": "%s: %s" % (type(e).__name__, str(e)[:400])}, round((time.time() - t0) * 1000)


def svc(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status
    except Exception:
        return 0


def nvidia():
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used,memory.total",
                               "--format=csv,noheader"], capture_output=True, text=True,
                              timeout=20).stdout.strip().replace("\n", " | ")
    except Exception:
        return "?"


def main():
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    print("=== 1) 轨迹质检 ===")
    st, q, wall = call("POST", "/hive/quality/run", {"limit": 100})
    print("total=%s passed=%s rejected=%s | by_type=%s | reject=%s"
          % (q.get("total"), q.get("passed"), q.get("rejected"),
             q.get("by_task_type"), q.get("reject_reasons")))
    res["quality"] = q

    print("\n=== 2) 阈值检查（dry_run）===")
    st, c, wall = call("POST", "/hive/autotune/check", {"dry_run": True})
    print("candidates:", json.dumps(c.get("candidates"), ensure_ascii=False))
    print("plan(3060 优先):", json.dumps({k: c["plan"].get(k) for k in
                                          ("action", "gpu", "reason", "fallback_used")},
                                         ensure_ascii=False))
    res["autotune_check"] = c

    print("\n=== 3) 5060 回退计划（不允许停工蜂）===")
    st, p5060, _ = call("POST", "/hive/train/plan",
                        {"required_gb": 6.0, "allow_stop_worker": False,
                         "allow_stop_main": True})
    plan = p5060.get("plan") or {}
    print("action=%s gpu=%s fallback=%s" % (plan.get("action"), plan.get("gpu"),
                                            plan.get("fallback_used")))
    print("reason:", str(plan.get("reason"))[:160])
    res["plan_5060_fallback"] = p5060

    print("\n=== 4) 真实自动训练（3060：停工蜂→训练→恢复工蜂）===")
    print("GPU before:", nvidia())
    st, job, wall = call("POST", "/hive/autotune/trigger",
                         {"task_type": "rag", "lora_name": "auto_rag_s21",
                          "dry_run": False, "required_gb": 6.0,
                          "epochs": 1, "max_len": 512,
                          "allow_stop_worker": True, "allow_stop_main": True},
                         timeout=1500)
    print("trigger wall=%.1fs ok=%s status=%s" % (wall / 1000, job.get("ok"), job.get("status")))
    tr = job.get("trainer") or {}
    print("trainer: status=%s gpu=%s fallback_used=%s restored=%s"
          % (tr.get("status"), tr.get("gpu"), tr.get("fallback_used"), tr.get("restored")))
    print("dataset:", job.get("dataset"), "| samples:", job.get("samples"))
    if tr.get("train_result"):
        print("train result: exit=%s dur=%ss adapter=%s"
              % (tr["train_result"].get("exit_code"), tr["train_result"].get("duration_s"),
                 tr.get("adapter_exists")))
    if not job.get("ok"):
        print("reason:", str(job.get("reason"))[:300])
    res["autotune_trigger"] = job

    print("\n=== 5) 训练后服务状态 ===")
    print("GPU:", nvidia())
    w = svc("http://127.0.0.1:8002/v1/models")
    m = svc("http://127.0.0.1:8000/v1/models")
    print("worker 8002=%s | main 8000=%s" % (w, m))
    res["after"] = {"gpu": nvidia(), "worker_http": w, "main_http": m}

    adapter = Path("/home/luxy/loras/auto_rag_s21")
    res["adapter"] = {"path": str(adapter), "exists": adapter.exists(),
                      "files": sorted(p.name for p in adapter.glob("*"))[:8] if adapter.exists() else []}
    print("adapter:", res["adapter"])

    st, jobs, _ = call("GET", "/hive/autotune/jobs?limit=3")
    res["jobs"] = jobs.get("jobs")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n结果 →", OUT)


if __name__ == "__main__":
    main()
