#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gpu_config.py — GPU 配置中心（2026-10-19）

★ 设计原则（用户纠正后 ✓）:
  1. **默认单卡**（gpu_mode="auto"）⇒ 与改动前行为完全一致 ✓
  2. **不假设任何卡号** ✗ 所有卡号都从配置/检测来 ✓
  3. **双卡必须用户显式启用** ✓（gpu_mode="dual" + 指定两张卡 ✓）
  4. **配置不全 ⇒ 安全退回单卡** ✓（不报错 ✗ 不崩 ✗）
  5. **兼容旧字段** ✓（vllm_gpu_index / train_gpu_index / dual_gpu_mode ✓）

配置 schema:
  {
    "gpu_mode": "auto" | "dual",     # auto(默认) = 单卡 · dual = 双卡分离
    "inference_gpu": int | null,     # dual 模式下必填
    "train_gpu": int | null          # dual 模式下必填
  }

行为矩阵:
  auto  + 任意卡数  ⇒ 单卡：训练时停 vLLM（原行为 ✓）
  dual  + ≥2 卡    ⇒ 双卡：训练/推理分离 ✓ 不停 vLLM ✓
  dual  + <2 卡    ⇒ 安全退回 auto ✓（警告日志 ✓）
"""
from __future__ import annotations

import subprocess

MODE_AUTO = "auto"
MODE_DUAL = "dual"


# ══════════════════════════════════════════════════════════
# GPU 检测
# ══════════════════════════════════════════════════════════
def detect_gpus() -> list[dict]:
    """检测所有 NVIDIA GPU（不假设数量 ✓）

    Returns:
        [{"index": 0, "name": "...", "mem_total": 16311}, ...]
        检测失败返回 [] ✓（不抛异常 ✓）
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10).stdout or ""
        gpus = []
        for line in out.strip().split("\n"):
            if not line.strip():
                continue
            parts = [x.strip() for x in line.split(",")]
            if len(parts) < 3:
                continue
            try:
                gpus.append({
                    "index": int(float(parts[0])),
                    "name": parts[1],
                    "mem_total": int(float(parts[2])),
                })
            except (ValueError, TypeError):
                continue
        return gpus
    except Exception:
        return []


def gpu_count() -> int:
    """GPU 数量（检测失败返回 0 ✓）"""
    return len(detect_gpus())


def has_multiple_gpus() -> bool:
    """是否有 ≥2 张卡（用于 UI 提示"可以启用双卡"✓ 不强制 ✓）"""
    return gpu_count() >= 2


# ══════════════════════════════════════════════════════════
# 配置解析（含旧字段迁移 ✓）
# ══════════════════════════════════════════════════════════
def get_gpu_config(settings: dict | None) -> dict:
    """解析 GPU 配置（★ 唯一入口 ✓ 所有地方都用它 ✓）

    Returns:
        {
          "mode": "auto" | "dual",
          "inference_gpu": int | None,
          "train_gpu": int | None,
          "enabled": bool,          # 是否真的启用了双卡 ✓
          "reason": str,            # 为什么（日志/UI 用 ✓）
        }

    保证返回的 mode 一定是安全的 ✓:
        dual 但配置不全/卡数不足 ⇒ 退回 auto ✓
    """
    s = settings or {}

    # ── ① 迁移旧字段 ✓ ──
    mode = s.get("gpu_mode")
    if mode not in (MODE_AUTO, MODE_DUAL):
        # 没有新字段 ⇒ 从旧字段推断 ✓
        if s.get("dual_gpu_mode") is True:
            mode = MODE_DUAL
        else:
            mode = MODE_AUTO

    # inference_gpu: 新字段优先 ✓ 旧字段兜底 ✓
    inf = s.get("inference_gpu")
    if inf is None:
        inf = s.get("vllm_gpu_index")
    tr = s.get("train_gpu")
    if tr is None:
        tr = s.get("train_gpu_index")

    # 类型规整 ✓
    try:
        inf = int(inf) if inf is not None else None
    except (ValueError, TypeError):
        inf = None
    try:
        tr = int(tr) if tr is not None else None
    except (ValueError, TypeError):
        tr = None

    # ── ② 安全检查 ✓ ──
    reason = ""
    enabled = False
    if mode == MODE_DUAL:
        if inf is None or tr is None:
            mode = MODE_AUTO
            reason = "双卡模式配置不全（缺 inference_gpu 或 train_gpu）⇒ 安全退回单卡"
        elif inf == tr:
            mode = MODE_AUTO
            reason = "双卡模式指定的两张卡相同 ⇒ 安全退回单卡"
        else:
            n = gpu_count()
            if n >= 2:
                enabled = True
                reason = "双卡模式已启用（推理=GPU%d · 训练=GPU%d）" % (inf, tr)
            elif n == 0:
                mode = MODE_AUTO
                reason = "检测不到 GPU ⇒ 退回单卡"
            else:
                mode = MODE_AUTO
                reason = "只有 %d 张卡，双卡模式需要 ≥2 张 ⇒ 安全退回单卡" % n
    else:
        reason = "单卡模式（默认 · 训练时停 vLLM · 与旧行为一致）"

    return {
        "mode": mode,
        "inference_gpu": inf,
        "train_gpu": tr,
        "enabled": enabled,
        "reason": reason,
    }


def cuda_device_env(settings: dict | None, which: str) -> dict:
    """返回应注入的环境变量（★ 关键 ✓）

    Args:
        which: "inference"（vLLM）或 "train"（训练）

    Returns:
        {"CUDA_VISIBLE_DEVICES": "N"} （双卡模式 ✓）
        {}                            （单卡模式 ⇒ 不设置 ✓ 用默认卡 ✓ 原行为 ✓）
    """
    cfg = get_gpu_config(settings)
    if not cfg["enabled"]:
        return {}   # ★ 单卡模式：不设置 ✓ 让 CUDA 自己选 ✓
    idx = cfg["inference_gpu"] if which == "inference" else cfg["train_gpu"]
    if idx is None:
        return {}
    return {"CUDA_VISIBLE_DEVICES": str(idx)}


# ══════════════════════════════════════════════════════════
# 设置迁移（写回 settings.json ✓）
# ══════════════════════════════════════════════════════════
def migrate_settings_file(path: str) -> dict:
    """把旧字段迁移成新 schema ✓ 写回文件 ✓

    迁移规则:
        dual_gpu_mode=True  → gpu_mode="dual" + inference_gpu/train_gpu（从旧字段）
        dual_gpu_mode=False → gpu_mode="auto"
        保留旧字段 ✓（不删除 ✓ 向后兼容 ✓）

    Returns:
        迁移后的 settings dict ✓
    """
    import json
    import pathlib
    p = pathlib.Path(path)
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

    changed = []
    if "gpu_mode" not in s:
        if s.get("dual_gpu_mode") is True:
            s["gpu_mode"] = MODE_DUAL
            if "inference_gpu" not in s and s.get("vllm_gpu_index") is not None:
                s["inference_gpu"] = s["vllm_gpu_index"]
            if "train_gpu" not in s and s.get("train_gpu_index") is not None:
                s["train_gpu"] = s["train_gpu_index"]
            changed.append("gpu_mode=dual（从 dual_gpu_mode=true 迁移）")
        else:
            s["gpu_mode"] = MODE_AUTO
            changed.append("gpu_mode=auto（默认单卡）")
    if "inference_gpu" not in s:
        s["inference_gpu"] = s.get("vllm_gpu_index")
        changed.append("inference_gpu=%s" % s["inference_gpu"])
    if "train_gpu" not in s:
        s["train_gpu"] = s.get("train_gpu_index")
        changed.append("train_gpu=%s" % s["train_gpu"])

    if changed:
        p.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"settings": s, "changed": changed}


# ══════════════════════════════════════════════════════════
# 自测（python gpu_config.py ✓）
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=== GPU 检测 ===")
    for g in detect_gpus():
        print("  GPU%d  %-28s %5.1f GB" % (g["index"], g["name"], g["mem_total"] / 1024))
    print("  数量:", gpu_count(), "| 多卡:", has_multiple_gpus())

    print("")
    print("=== 配置解析（4 种场景）===")
    cases = [
        ("空配置（新装）", {}),
        ("旧字段: dual=false", {"dual_gpu_mode": False, "vllm_gpu_index": 1, "train_gpu_index": 0}),
        ("旧字段: dual=true", {"dual_gpu_mode": True, "vllm_gpu_index": 1, "train_gpu_index": 0}),
        ("新 schema: auto", {"gpu_mode": "auto"}),
        ("新 schema: dual 完整", {"gpu_mode": "dual", "inference_gpu": 1, "train_gpu": 0}),
        ("新 schema: dual 不全", {"gpu_mode": "dual", "inference_gpu": 1}),
        ("新 schema: dual 同卡", {"gpu_mode": "dual", "inference_gpu": 0, "train_gpu": 0}),
    ]
    for label, st in cases:
        c = get_gpu_config(st)
        print("  %-22s → mode=%-4s enabled=%-5s inf=%s train=%s" % (
            label, c["mode"], c["enabled"], c["inference_gpu"], c["train_gpu"]))
        print("      %s" % c["reason"])

    print("")
    print("=== cuda_device_env（单卡应返回空 ✓）===")
    for label, st in [("单卡", {"gpu_mode": "auto"}), ("双卡", {"gpu_mode": "dual", "inference_gpu": 1, "train_gpu": 0})]:
        print("  %-6s inference=%s train=%s" % (
            label, cuda_device_env(st, "inference"), cuda_device_env(st, "train")))
