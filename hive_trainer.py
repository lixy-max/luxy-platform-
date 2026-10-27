# -*- coding: utf-8 -*-
"""S21 · 训练调度器（3060 优先 / 5060 回退 / OOM 防护）

背景（用户要求）：
  3060（GPU1）同时承担工蜂 + 训练，正常不同时发生，但有小概率并发 → OOM。
  因此训练调度必须：
  1) 资源感知：检查 GPU 空闲显存 ≥ 需求（含 5% 余量）
  2) 3060 优先：若显存不足，可停工蜂腾显存（训练后自动恢复）
  3) 回退 5060：3060 无法腾出（不允许停工蜂/停不掉）→ 走 5060；
     5060 需先停主脑 14B（占用 ~15.8GB），训练后恢复
  4) 两个都不可行 → 排队延迟（defer），绝不硬上导致 OOM
  5) 训练进程 OOM 失败 → 自动换 GPU 重试一次（fallback retry）
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parent
VENV_PY = os.environ.get("HIVE_VENV_PY", "/home/luxy/.venv/bin/python")
LOG_DIR = PROJECT_DIR / "logs"
JOB_DIR = PROJECT_DIR / "data" / "hive" / "train_jobs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
JOB_DIR.mkdir(parents=True, exist_ok=True)

GPU_SPECS = {0: {"name": "RTX 5060 Ti", "vram_gb": 16.0},
             1: {"name": "RTX 3060", "vram_gb": 12.0}}
DEFAULT_REQUIRED_GB = float(os.environ.get("HIVE_TRAIN_REQUIRED_GB", "6.0"))
OOM_MARGIN = float(os.environ.get("HIVE_TRAIN_OOM_MARGIN", "1.05"))
TRAIN_TIMEOUT = float(os.environ.get("HIVE_TRAIN_TIMEOUT_S", "1800"))
WORKER_URL = os.environ.get("HIVE_WORKER_URL", "http://127.0.0.1:8002")
MAIN_URL = os.environ.get("HIVE_MAIN_URL", "http://127.0.0.1:8000")


# ══════════════════════════════════════════════════════════════
# GPU / 服务探测
# ══════════════════════════════════════════════════════════════
def gpu_status():
    """返回 {index: {total_mb, used_mb, free_gb, name}}"""
    out = {}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.total,memory.used,name",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20)
        for ln in r.stdout.splitlines():
            parts = [x.strip() for x in ln.split(",")]
            if len(parts) < 4:
                continue
            idx = int(parts[0])
            total = float(parts[1])
            used = float(parts[2])
            out[idx] = {"total_mb": total, "used_mb": used,
                        "free_gb": round((total - used) / 1024, 2),
                        "total_gb": round(total / 1024, 2), "name": parts[3]}
    except Exception as e:
        out["_error"] = str(e)[:200]
    return out


def _service_up(url):
    try:
        r = requests.get(url + "/v1/models", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def services():
    return {"worker": _service_up(WORKER_URL), "main_brain": _service_up(MAIN_URL)}


# ══════════════════════════════════════════════════════════════
# 训练计划（核心：3060 优先 → 5060 回退 → defer）
# ══════════════════════════════════════════════════════════════
def plan_training(required_gb=None, allow_stop_worker=True, allow_stop_main=True,
                  allow_gpu0=True, prefer_gpu=1):
    need = float(required_gb or DEFAULT_REQUIRED_GB)
    gpus = gpu_status()
    svc = services()
    g1 = gpus.get(1, {})
    g0 = gpus.get(0, {})
    plan = {"required_gb": need, "oom_margin": OOM_MARGIN,
            "gpus": {str(k): v for k, v in gpus.items() if k != "_error"},
            "services": svc, "fallback_used": False}

    # ① 3060 直接可用
    if g1.get("free_gb", 0) * (1 / OOM_MARGIN) >= need:
        return {**plan, "action": "direct", "gpu": 1,
                "reason": "3060 空闲显存足够（%.1fGB ≥ %.1fGB）" % (g1.get("free_gb", 0), need),
                "fallback_used": False}
    # ② 3060 停工蜂
    if svc.get("worker") and allow_stop_worker:
        return {**plan, "action": "stop_worker", "gpu": 1,
                "reason": "3060 被工蜂占用（空闲%.1fGB<%.1fGB）→ 停工蜂后训练"
                          % (g1.get("free_gb", 0), need),
                "fallback_used": False}
    # ③ 回退 5060：直接可用
    if allow_gpu0 and g0.get("free_gb", 0) * (1 / OOM_MARGIN) >= need:
        return {**plan, "action": "direct", "gpu": 0,
                "reason": "3060 不可腾出 → 回退 5060（空闲%.1fGB）" % g0.get("free_gb", 0),
                "fallback_used": True}
    # ④ 回退 5060：停主脑
    if allow_gpu0 and svc.get("main_brain") and allow_stop_main:
        return {**plan, "action": "stop_main_brain", "gpu": 0,
                "reason": "3060 不可腾出且 5060 被主脑占用 → 停主脑在 5060 训练（回退路径）",
                "fallback_used": True}
    # ⑤ 不可行 → 排队
    return {**plan, "action": "defer", "gpu": None,
            "reason": "两块卡均不满足训练需求（3060 free=%.1f，5060 free=%.1f，need=%.1f）；"
                      "已排队等待资源，不会强上导致 OOM"
                      % (g1.get("free_gb", 0), g0.get("free_gb", 0), need),
            "fallback_used": False}


# ══════════════════════════════════════════════════════════════
# 服务启停
# ══════════════════════════════════════════════════════════════
def _port_pids(port):
    pids = set()
    try:
        r = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True, timeout=10)
        for ln in r.stdout.splitlines():
            if ":%d" % port in ln:
                for m in re.finditer(r"pid=(\d+)", ln):
                    pids.add(int(m.group(1)))
    except Exception:
        pass
    return pids


def _stop_service(name):
    url = WORKER_URL if name == "worker" else MAIN_URL
    port = 8002 if name == "worker" else 8000
    pids = _port_pids(port)
    for pid in pids:
        try:
            os.kill(pid, 15)
        except Exception:
            pass
    for _ in range(20):
        if not _service_up(url):
            return True, "stopped pids=%s" % sorted(pids)
        time.sleep(1)
    for pid in pids:
        try:
            os.kill(pid, 9)
        except Exception:
            pass
    time.sleep(3)
    return (not _service_up(url)), "killed pids=%s" % sorted(pids)


def _start_worker():
    log = open(LOG_DIR / "hive_train_worker_restart.log", "wb")
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", VLLM_USE_FLASHINFER_SAMPLER="0")
    subprocess.Popen([VENV_PY, "-c",
                      "import sys;sys.path.insert(0,'.');import swarm;print(swarm.start_worker(300))"],
                     cwd=str(PROJECT_DIR), stdout=log, stderr=subprocess.STDOUT,
                     env=env, start_new_session=True)
    for _ in range(60):
        if _service_up(WORKER_URL):
            return True
        time.sleep(5)
    return False


def _start_main():
    log = open(LOG_DIR / "hive_train_main_restart.log", "wb")
    env = dict(os.environ, VLLM_ENABLE_LORA="0", VLLM_HOST="127.0.0.1",
               VLLM_USE_FLASHINFER_SAMPLER="0", CUDA_VISIBLE_DEVICES="0")
    code = ("import sys;sys.path.insert(0,'.');import vllm_manager as vm;"
            "print(vm.start_vllm('/home/luxy/models/Qwen2.5-14B-Instruct-AWQ',"
            "port=8000,extra_args={'max_model_len':20480},"
            "venv_python='/home/luxy/.venv/bin/python'))")
    subprocess.Popen([VENV_PY, "-c", code], cwd=str(PROJECT_DIR), stdout=log,
                     stderr=subprocess.STDOUT, env=env, start_new_session=True)
    for _ in range(90):
        if _service_up(MAIN_URL):
            return True
        time.sleep(4)
    return False


def _wait_vram(gpu, need_gb, timeout_s=90):
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        g = gpu_status().get(gpu, {})
        if g.get("free_gb", 0) * (1 / OOM_MARGIN) >= need_gb:
            return True, g.get("free_gb", 0)
        time.sleep(3)
    g = gpu_status().get(gpu, {})
    return False, g.get("free_gb", 0)


# ══════════════════════════════════════════════════════════════
# 训练执行（含 OOM 回退重试）
# ══════════════════════════════════════════════════════════════
def _launch_train(job, gpu, dataset, base_model, output, epochs, max_len):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(gpu),
               TOKENIZERS_PARALLELISM="false")
    log_path = LOG_DIR / ("hive_train_%s.log" % job)
    log = open(log_path, "wb")
    args = [VENV_PY, str(PROJECT_DIR / "scripts" / "train_hive_lora.py"),
            "--dataset", str(dataset), "--base-model", str(base_model),
            "--output", str(output), "--epochs", str(epochs),
            "--max-len", str(max_len)]
    t0 = time.time()
    proc = subprocess.Popen(args, cwd=str(PROJECT_DIR), stdout=log,
                            stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        code = proc.wait(timeout=TRAIN_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = -9
    dur = round(time.time() - t0, 1)
    tail = ""
    try:
        tail = log_path.read_bytes()[-2000:].decode(errors="ignore")
    except Exception:
        pass
    return {"exit_code": code, "duration_s": dur, "log": str(log_path), "log_tail": tail}


def _is_oom(res):
    t = (res.get("log_tail") or "").lower()
    return ("out of memory" in t or "cuda oom" in t or "cuda error: out of memory" in t)


def execute_training(dataset, base_model="/home/luxy/models/Qwen2.5-1.5B-Instruct",
                     output="", lora_name="hive_auto", epochs=2, max_len=1024,
                     required_gb=None, allow_stop_worker=True, allow_stop_main=True,
                     allow_gpu0=True, dry_run=False):
    """按计划执行训练；不满足资源则 defer；OOM 自动回退重试一次"""
    job = "tr_%s_%s" % (lora_name, time.strftime("%Y%m%d_%H%M%S"))
    need = float(required_gb or DEFAULT_REQUIRED_GB)
    output = output or str(Path("/home/luxy/loras") / lora_name)
    record = {"job": job, "lora_name": lora_name, "dataset": str(dataset),
              "base_model": base_model, "output": output, "epochs": epochs,
              "required_gb": need, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "attempts": [], "restored": {}, "dry_run": bool(dry_run)}

    plan1 = plan_training(need, allow_stop_worker, allow_stop_main, allow_gpu0)
    record["plan"] = plan1
    if plan1["action"] == "defer":
        record.update({"ok": False, "status": "deferred", "reason": plan1["reason"]})
        _save_job(record)
        return record
    if dry_run:
        record.update({"ok": True, "status": "planned", "reason": plan1["reason"]})
        _save_job(record)
        return record

    stopped = []
    if plan1["action"] == "stop_worker":
        ok, msg = _stop_service("worker")
        record["attempts"].append({"gpu": 1, "stop_worker": msg, "ok": ok})
        if not ok:
            record["plan"]["note"] = "停工蜂失败，尝试回退 5060"
            plan2 = plan_training(need, allow_stop_worker=False,
                                  allow_stop_main=allow_stop_main, allow_gpu0=allow_gpu0)
            record["fallback_plan"] = plan2
            if plan2["action"] == "defer":
                record.update({"ok": False, "status": "deferred", "reason": plan2["reason"]})
                _save_job(record)
                return record
            plan1 = plan2
        else:
            stopped.append("worker")
    if plan1["action"] == "stop_main_brain":
        ok, msg = _stop_service("main_brain")
        record["attempts"].append({"gpu": 0, "stop_main_brain": msg, "ok": ok})
        if not ok:
            record.update({"ok": False, "status": "deferred",
                           "reason": "停主脑失败，无法在 5060 训练（不硬上以避免 OOM）"})
            _save_job(record)
            return record
        stopped.append("main_brain")

    gpu = plan1["gpu"]
    ready, free = _wait_vram(gpu, need)
    record["vram_before"] = {"gpu": gpu, "free_gb": free, "ready": ready}
    if not ready:
        _restore(stopped, record)
        record.update({"ok": False, "status": "deferred",
                       "reason": "等待显存释放超时（gpu%d free=%.1fGB < %.1fGB）" % (gpu, free, need)})
        _save_job(record)
        return record

    res = _launch_train(job, gpu, dataset, base_model, output, epochs, max_len)
    record["attempts"].append({"gpu": gpu, "train": res, "oom": _is_oom(res)})
    used_fallback = False
    if _is_oom(res) and allow_gpu0:
        # ★ OOM 自动回退：换另一张卡重试一次
        used_fallback = True
        alt = 0 if gpu == 1 else 1
        record["oom_retry"] = {"from_gpu": gpu, "to_gpu": alt}
        if alt == 0:
            if not _service_up(MAIN_URL) or _stop_service("main_brain")[0]:
                stopped.append("main_brain")
        ready2, free2 = _wait_vram(alt, need)
        record["vram_before_retry"] = {"gpu": alt, "free_gb": free2, "ready": ready2}
        if ready2:
            res2 = _launch_train(job + "_r", alt, dataset, base_model, output, epochs, max_len)
            record["attempts"].append({"gpu": alt, "train": res2, "oom": _is_oom(res2)})
            res = res2
            gpu = alt
    ok = res.get("exit_code") == 0
    record.update({"ok": ok, "status": "done" if ok else "failed",
                   "gpu": gpu, "fallback_used": bool(plan1.get("fallback_used") or used_fallback),
                   "train_result": res,
                   "adapter_exists": (Path(output) / "adapter_config.json").exists()})
    _restore(stopped, record)
    _save_job(record)
    return record


def _restore(stopped, record):
    for name in stopped:
        try:
            ok = _start_worker() if name == "worker" else _start_main()
        except Exception as e:
            ok = False
            record.setdefault("restore_errors", []).append("%s: %s" % (name, e))
        record["restored"][name] = bool(ok)


def _save_job(record):
    try:
        JOB_DIR.mkdir(parents=True, exist_ok=True)
        (JOB_DIR / ("%s.json" % record["job"])).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def list_jobs(limit=10):
    try:
        files = sorted(JOB_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return []
    out = []
    for p in files[:limit]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out
