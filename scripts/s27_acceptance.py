#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S27 验收：7B QLoRA 真实训练 → 热加载 → 灰度自动晋级/自动回滚 + 持久队列"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path("/home/luxy/paper_lora_project")
sys.path.insert(0, str(PROJECT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = PROJECT / "data" / "hive" / "s27_acceptance.json"
RESULTS = []
VENV_PY = "/home/luxy/.venv/bin/python"
BASE_7B = "/home/luxy/models/Qwen2.5-7B-Instruct"
ADAPTER = "/home/luxy/loras/s27_qlora_7b"
LORA_ID = "s27_qlora_7b"
DATASET = PROJECT / "training_data" / "s27_qlora_7b.jsonl"


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "pass": bool(ok), "detail": str(detail)[:500]})
    print("%s %-40s %s" % ("✅" if ok else "❌", name, str(detail)[:130]))


def build_dataset():
    """从质检通过样本构造小数据集（不足则合成）"""
    rows = []
    qf = PROJECT / "data" / "hive" / "quality" / "passed.jsonl"
    if qf.exists():
        for ln in qf.read_text(encoding="utf-8").splitlines()[-8:]:
            try:
                d = json.loads(ln)
            except Exception:
                continue
            tr = d.get("trajectory") or d
            if not isinstance(tr, dict):
                tr = {}
            task = tr.get("task") if isinstance(tr.get("task"), dict) else {}
            req = tr.get("request") or task.get("request") or ""
            ans = tr.get("answer") or tr.get("final_answer") or ""
            if req and ans:
                rows.append({"conversations": [
                    {"role": "user", "content": str(req)[:800]},
                    {"role": "assistant", "content": str(ans)[:1200]}]})
    while len(rows) < 8:
        i = len(rows) + 1
        rows.append({"conversations": [
            {"role": "user", "content": "把采购清单第 %d 行整理成 JSON：名称=物料%d，数量=%d，单价=%.1f"
                                        % (i, i, i * 2, 9.9 + i)},
            {"role": "assistant", "content": '{"name":"物料%d","quantity":%d,"price":%.1f}'
                                            % (i, i * 2, 9.9 + i)}]})
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    DATASET.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                       encoding="utf-8")
    return len(rows)


def http_ok(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    import hive_trainer as tr
    import hive_train_queue as tq
    import hive_canary as cn
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    # ① 持久队列
    q1 = tq.enqueue({"dataset": str(DATASET), "qlora": True, "max_steps": 8,
                     "base_model": BASE_7B, "lora_name": "s27_qlora_7b", "priority": 1})
    q2 = tq.enqueue({"dataset": "/tmp/future.jsonl", "lora_name": "future_task",
                     "priority": 9, "required_gb": 99})
    qfail = tq.enqueue({"dataset": "/tmp/fail.jsonl", "lora_name": "fail_task", "priority": 5})
    tq.mark_running(qfail["task"]["id"])
    tq.mark_failed(qfail["task"]["id"], {"ok": False, "error": "模拟失败"}, retry=True, delay_s=9999)
    check("持久训练队列（入队/状态/重试）",
          q1.get("ok") and q2.get("ok") and tq.get(qfail["task"]["id"])["status"] == "deferred"
          and tq.stats()["total"] >= 3,
          tq.stats())
    res["queue"] = tq.stats()

    # ② 数据集
    n = build_dataset()
    check("QLoRA 数据集构造", n >= 8, "%d 条 → %s" % (n, DATASET))
    res["dataset"] = {"samples": n, "path": str(DATASET)}

    # ③ 真实 7B QLoRA 训练（先停工蜂，训完恢复）
    ok_worker_stop, msg_stop = tr._stop_service("worker")
    check("训练前停工蜂（释放 GPU1）", ok_worker_stop, msg_stop)
    ready_vram, free_vram = tr._wait_vram(1, 6.0, timeout_s=90)
    check("GPU1 显存可用（等待释放）", ready_vram and free_vram >= 6.0,
          "free=%.1fGB ready=%s" % (free_vram, ready_vram))
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="1", TOKENIZERS_PARALLELISM="false")
    logp = PROJECT / "logs" / "s27_qlora_7b.log"
    logp.parent.mkdir(parents=True, exist_ok=True)
    log = open(logp, "wb")
    t0 = time.time()
    cmd = [VENV_PY, str(PROJECT / "scripts" / "train_hive_lora.py"),
           "--dataset", str(DATASET), "--base-model", BASE_7B,
           "--output", ADAPTER, "--qlora", "--max-steps", "8",
           "--max-len", "512", "--rank", "8", "--batch", "1",
           "--grad-accum", "2", "--lr", "2e-4"]
    proc = subprocess.Popen(cmd, cwd=str(PROJECT), stdout=log, stderr=subprocess.STDOUT,
                            env=env, start_new_session=True)
    try:
        code = proc.wait(timeout=900)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = -9
    dur = round(time.time() - t0, 1)
    tail = logp.read_bytes()[-1200:].decode(errors="ignore")
    check("7B QLoRA 训练完成", code == 0, "exit=%s %.1fs" % (code, dur))
    res["train"] = {"exit_code": code, "duration_s": dur, "log": str(logp),
                    "log_tail": tail}

    # 恢复工蜂
    ok_worker_start = tr._start_worker()
    check("训练后恢复工蜂", ok_worker_start and http_ok("http://127.0.0.1:8002/v1/models"),
          "started=%s" % ok_worker_start)

    # ④ 训练产物/指标
    metrics = {}
    mf = Path(ADAPTER) / "train_metrics.json"
    if mf.exists():
        metrics = json.loads(mf.read_text(encoding="utf-8"))
    adapter_ok = (Path(ADAPTER) / "adapter_config.json").exists()
    loss = metrics.get("train_loss")
    vram = metrics.get("vram_peak_mb") or 0
    check("adapter 产物 + 指标", adapter_ok and loss is not None and loss == loss,
          "loss=%s vram=%.0fMB qlora=%s" % (loss, vram, metrics.get("qlora")))
    check("QLoRA 显存未超 3060 上限", 0 < vram < 12 * 1024,
          "peak=%.0fMB / 12288MB" % vram)
    res["metrics"] = metrics

    # ⑤ 新 adapter 热加载 + 评测
    import hive_hotload as hl
    reg = hl.register_candidate(LORA_ID, base_model=BASE_7B)
    check("7B adapter 注册为候选", reg.get("ok"), str(reg)[:160])
    ev = hl.evaluate(LORA_ID, max_tokens=32)
    evq = (ev.get("evaluation") or {})
    check("热加载评测执行",
          ev.get("ok") is not False and evq.get("quality") is not None,
          "quality=%s base=%s passed=%s" % (evq.get("quality"), evq.get("base_quality"),
                                            ev.get("passed")))
    res["hotload"] = {"register": reg, "evaluate": ev}

    # ⑥ 灰度正常流 → 自动晋级
    cn.clear()
    st = cn.start_canary(LORA_ID, ratio=0.05)
    for _ in range(6):
        cn.record(LORA_ID, True, quality=0.9, latency_ms=800)
    ev2 = cn.evaluate(LORA_ID)
    auto1 = cn.auto(LORA_ID)
    state_after = hl.get_state(LORA_ID) if hasattr(hl, "get_state") else {}
    check("灰度健康 → 自动晋级", ev2["status"] == "healthy"
          and auto1.get("action") == "promote_active",
          "status=%s action=%s" % (ev2["status"], auto1.get("action")))
    res["canary_promote"] = {"evaluate": ev2, "auto": auto1, "state": state_after}
    hl.rollback(LORA_ID, reason="s27 验收清理")

    # ⑦ 灰度故障流 → 自动回滚
    cn.clear()
    cn.start_canary(LORA_ID, ratio=0.05)
    for _ in range(6):
        cn.record(LORA_ID, False, quality=0.1, latency_ms=9000)
    ev3 = cn.evaluate(LORA_ID)
    auto2 = cn.auto(LORA_ID)
    check("灰度故障 → 自动回滚", ev3["status"] == "breaching"
          and auto2.get("action") == "rollback",
          "status=%s action=%s" % (ev3["status"], auto2.get("action")))
    res["canary_rollback"] = {"evaluate": ev3, "auto": auto2}

    # ⑧ 队列统计
    check("队列可审计", tq.stats()["total"] >= 3, tq.stats())
    res["queue_final"] = tq.stats()

    passed = sum(1 for r in RESULTS if r["pass"])
    totaln = len(RESULTS)
    print("\n=== S27 验收汇总 ===\n通过 %d/%d" % (passed, totaln))
    res["summary"] = {"passed": passed, "total": totaln,
                      "verdict": "PASS" if passed == totaln else "FAIL",
                      "checks": RESULTS}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("结果 →", OUT)


if __name__ == "__main__":
    main()
