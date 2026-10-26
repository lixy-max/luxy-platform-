# -*- coding: utf-8 -*-
"""model_registry.py —— 主脑基准库（模型清单 / 能力界限 / 可挂载 LoRA）

主脑调度前必须知道：
  · 有哪些模型可用（可调配名称 / 路径 / 端口 / 角色）
  · 每个模型能干什么、不能干什么（能力界限）
  · 每个模型能挂哪些 LoRA（多 LoRA 单步能力）
  · 哪些正在运行 / 可用 / 不可用 / 部分下载

数据：data/models_registry.json（scan() 合并实测事实；保留人工设定字段）
"""
import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = Path(os.environ.get(
    "MODEL_REGISTRY",
    str(PROJECT_DIR / "data" / "models_registry.json")))
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(Path.home() / "models")))

# ── 基准事实（P1/P2 实测，2026-10-26）──
#   source: "measured" = 本机实测；"estimate" = 推断；"unavailable" = 不可用
SEED = [
    {
        "model_id": "qwen2.5-14b-awq",
        "name": "Qwen2.5-14B-Instruct-AWQ",
        "path": str(MODELS_DIR / "Qwen2.5-14B-Instruct-AWQ"),
        "role": "main_brain",
        "role_label": "主脑",
        "port": 8000, "gpu": "GPU0（5060 Ti 16G）",
        "context_limit": 20480, "kv_tokens": 25504,
        "speed_tps": "38–39", "weights_gib": 9.38,
        "lora": {"supported": False, "max_loras": 0, "max_rank": 0, "max_cpu_loras": 0},
        "boundaries": {
            "can": ["任务编排/能力判断", "知识库检索路由", "多步规划", "中文问答/写作", "20K 上下文"],
            "cannot": ["挂载 LoRA（主脑不挂）", ">20480 上下文", "图像/音频输入", "超长输出（>4K tokens 会明显变慢）"],
        },
        "status": "running", "source": "measured",
    },
    {
        "model_id": "qwen2.5-7b-awq",
        "name": "Qwen2.5-7B-Instruct-AWQ",
        "path": str(MODELS_DIR / "Qwen2.5-7B-Instruct-AWQ"),
        "role": "worker",
        "role_label": "工蜂（能力执行）",
        "port": 8002, "gpu": "GPU1（3060 12G）",
        "context_limit": 32768, "kv_tokens": 53680,
        "speed_tps": "41（基座）/ 26（挂 LoRA）", "weights_gib": 6.43,
        "lora": {"supported": True, "max_loras": 2, "max_rank": 128, "max_cpu_loras": 4},
        "boundaries": {
            "can": ["单步专业能力（多 LoRA）", "工具调用", "短文本生成", "32768 上下文"],
            "cannot": ["复杂多步推理", "长上下文 >32768", "同时 >2 个活跃 LoRA", "独立编排/调度"],
        },
        "status": "running", "source": "measured",
    },
    {
        "model_id": "qwen3-8b-awq",
        "name": "Qwen3-8B-AWQ",
        "path": str(MODELS_DIR / "Qwen3-8B-AWQ"),
        "role": "candidate_worker",
        "role_label": "候选工蜂",
        "port": None, "gpu": "GPU1（可）",
        "context_limit": 32768, "kv_tokens": 35376,
        "speed_tps": "30（thinking off）", "weights_gib": 5.71,
        "lora": {"supported": True, "max_loras": 2, "max_rank": 128, "max_cpu_loras": 4},
        "boundaries": {
            "can": ["中文问答", "路由测试 88.9%/94.4%", "单步能力候选"],
            "cannot": ["调度能力弱于 14B（实测）", "thinking 模式延迟高"],
        },
        "status": "available", "source": "measured",
    },
    {
        "model_id": "qwen3-14b-awq",
        "name": "Qwen3-14B-AWQ",
        "path": str(MODELS_DIR / "Qwen3-14B-AWQ"),
        "role": "backup_brain",
        "role_label": "备用主脑",
        "port": None, "gpu": "GPU0（需停主脑）",
        "context_limit": 20480, "kv_tokens": 30448,
        "speed_tps": "8.8–13.6（偏慢）", "weights_gib": 9.36,
        "lora": {"supported": False, "max_loras": 0, "max_rank": 0, "max_cpu_loras": 0},
        "boundaries": {
            "can": ["中文问答", "更宽 KV（30,448 tokens）"],
            "cannot": ["速度不达标（不推荐做主脑）", "挂 LoRA"],
        },
        "status": "available", "source": "measured",
    },
    {
        "model_id": "qwen2.5-32b-awq",
        "name": "Qwen2.5-32B-Instruct-AWQ",
        "path": str(MODELS_DIR / "Qwen2.5-32B-Instruct-AWQ"),
        "role": "quality_mode",
        "role_label": "质量模式（TP=2 双卡）",
        "port": None, "gpu": "GPU0+GPU1（异构 TP=2）",
        "context_limit": 8192, "kv_tokens": 11504,
        "speed_tps": "10–16", "weights_gib": 9.09,
        "lora": {"supported": False, "max_loras": 0, "max_rank": 0, "max_cpu_loras": 0},
        "boundaries": {
            "can": ["更高生成质量", "8192 上下文", "异构双卡 TP=2"],
            "cannot": ["与工蜂同时运行（占满双卡）", "交互式速度", ">8192 上下文（GPU1 限制）"],
        },
        "status": "available", "source": "measured",
    },
    {
        "model_id": "gemma-2-27b-gptq",
        "name": "Gemma-2-27B-it GPTQ-4bit",
        "path": str(MODELS_DIR / "gemma-2-27b-it-gptq-4bit"),
        "role": "unavailable",
        "role_label": "不可用（仅留档）",
        "port": None, "gpu": "—",
        "context_limit": 8192, "kv_tokens": 0,
        "speed_tps": "0", "weights_gib": 18.26,
        "lora": {"supported": False, "max_loras": 0, "max_rank": 0, "max_cpu_loras": 0},
        "boundaries": {
            "can": [],
            "cannot": ["单卡装不下（18.26GB > 15.9GB）", "TP=2 在 3060 OOM", "GPTQ 在 sm_120 内核不兼容", "WSL 无 UVA 不能 offload"],
        },
        "status": "unavailable", "source": "measured",
    },
    {
        "model_id": "qwen3-32b-awq",
        "name": "Qwen3-32B-AWQ",
        "path": str(MODELS_DIR / "Qwen3-32B-AWQ"),
        "role": "quality_mode_candidate",
        "role_label": "质量模式候选（已下载/未测）",
        "port": None, "gpu": "需要双卡 TP=2",
        "context_limit": None, "kv_tokens": 0,
        "speed_tps": "未测", "weights_gib": 19.0,
        "lora": {"supported": False, "max_loras": 0, "max_rank": 0, "max_cpu_loras": 0},
        "boundaries": {
            "can": ["（未测）预期同 Qwen2.5-32B 的 TP=2 质量模式"],
            "cannot": ["未做加载/测速验证（预期同 32B TP=2，约 10–16 tok/s）"],
        },
        "status": "partial", "source": "measured",
    },
    {
        "model_id": "qwen3-4b-awq",
        "name": "Qwen3-4B-AWQ",
        "path": str(MODELS_DIR / "Qwen3-4B-AWQ"),
        "role": "small_candidate",
        "role_label": "小模型候选",
        "port": None, "gpu": "任意（≤12G）",
        "context_limit": 32768, "kv_tokens": 0,
        "speed_tps": "—", "weights_gib": 2.5,
        "lora": {"supported": True, "max_loras": 2, "max_rank": 128, "max_cpu_loras": 4},
        "boundaries": {
            "can": ["极小资源占用", "单步能力候选"],
            "cannot": ["能力有限，不适合做调度/复杂任务"],
        },
        "status": "available", "source": "measured",
    },
    {
        "model_id": "bge-m3",
        "name": "BAAI/bge-m3",
        "path": str(MODELS_DIR / "bge-m3"),
        "role": "embedding",
        "role_label": "海马体（向量化）",
        "port": None, "gpu": "CPU（可切 GPU）",
        "context_limit": 8192, "kv_tokens": 0,
        "speed_tps": "—", "weights_gib": 2.2,
        "lora": {"supported": False, "max_loras": 0, "max_rank": 0, "max_cpu_loras": 0},
        "boundaries": {
            "can": ["知识库 embedding（1024 维）", "中英多语检索"],
            "cannot": ["生成（非生成模型）"],
        },
        "status": "available", "source": "measured",
    },
    {
        "model_id": "qwen3-4b-instruct",
        "name": "Qwen3-4B-Instruct",
        "path": str(MODELS_DIR / "Qwen3-4B-Instruct"),
        "role": "lora_base_legacy",
        "role_label": "旧 LoRA 基座（4B）",
        "port": None, "gpu": "—",
        "context_limit": 32768, "kv_tokens": 0,
        "speed_tps": "—", "weights_gib": 7.6,
        "lora": {"supported": True, "max_loras": 2, "max_rank": 128, "max_cpu_loras": 4},
        "boundaries": {
            "can": ["承载 Qwen3-4B 时代 LoRA（2 个，现与 7B 工蜂不兼容）"],
            "cannot": ["与当前 7B 工蜂混用 LoRA"],
        },
        "status": "available", "source": "measured",
    },
]


def _load():
    if REGISTRY_PATH.exists():
        try:
            d = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("models"), dict):
                return d
        except Exception:
            pass
    return {"version": 1, "models": {}, "updated_at": ""}


def _save(reg):
    import time
    reg["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    return reg


def seed():
    """写入基准事实（已存在的模型保留自定义字段，仅补默认）"""
    reg = _load()
    for m in SEED:
        mid = m["model_id"]
        if mid not in reg["models"]:
            reg["models"][mid] = json.loads(json.dumps(m))
        else:
            for k, v in m.items():
                reg["models"][mid].setdefault(k, v)
    return _save(reg)


def _dir_info(path):
    p = Path(str(path or ""))
    if not p.exists():
        return {"exists": False, "files": 0, "size_gb": 0.0, "ctx_from_config": None, "arch": ""}
    files = [f for f in p.rglob("*") if f.is_file()]
    size_gb = round(sum(f.stat().st_size for f in files) / 1e9, 2)
    ctx, arch = None, ""
    try:
        cfg = json.loads((p / "config.json").read_text(encoding="utf-8"))
        ctx = cfg.get("max_position_embeddings")
        archs = cfg.get("architectures") or []
        arch = archs[0] if archs else ""
    except Exception:
        pass
    return {"exists": True, "files": len(files), "size_gb": size_gb,
            "ctx_from_config": ctx, "arch": arch}


def scan(probe_ports=(8000, 8002)):
    """扫描模型目录 + 运行端口，更新 registry（保留实测/人工字段）"""
    import urllib.request
    reg = seed()
    for mid, m in reg["models"].items():
        info = _dir_info(m.get("path"))
        m["exists"] = info["exists"]
        m["size_gb_on_disk"] = info["size_gb"]
        m["arch"] = info["arch"] or m.get("arch", "")
        _expect = float(m.get("expected_size_gb") or 0)
        if info["exists"] and m.get("role") != "unavailable":
            if _expect and info["size_gb"] < _expect * 0.8:
                m["status"] = "partial"          # 下载不完整
            elif m.get("status") in ("missing", "partial"):
                m["status"] = "available"        # 已完整
            m["download_pct"] = round(min(100.0, info["size_gb"] / _expect * 100), 1) if _expect else None
        if not info["exists"] and m.get("role") != "unavailable":
            m["status"] = "missing"
    # 端口探测
    for port in probe_ports:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % int(port), timeout=3) as r:
                ids = [x.get("id") for x in (json.loads(r.read().decode()).get("data") or [])]
        except Exception:
            continue
        for mid, m in reg["models"].items():
            if int(m.get("port") or 0) == int(port):
                m["status"] = "running" if m.get("role") != "unavailable" else m["status"]
                m["served_models"] = ids
    return _save(reg)


def list_models(role=None, status=None):
    reg = _load()
    if not reg["models"]:
        reg = seed()
    out = list(reg["models"].values())
    if role:
        out = [m for m in out if m.get("role") == role]
    if status:
        out = [m for m in out if m.get("status") == status]
    return sorted(out, key=lambda m: (m.get("status") != "running", m.get("role") or "", m.get("model_id") or ""))


def get_model(model_id):
    reg = _load()
    if not reg["models"]:
        reg = seed()
    return reg["models"].get(str(model_id))


def set_status(model_id, status):
    reg = _load()
    m = reg["models"].get(str(model_id))
    if not m:
        raise ValueError("模型不存在: %s" % model_id)
    m["status"] = str(status)
    return _save(reg)["models"][model_id]


def dispatchable():
    """可被主脑调配的模型（running 且 role 不是 unavailable）"""
    return [m for m in list_models() if m.get("status") == "running" and m.get("role") != "unavailable"]


def lora_mountable(model_id):
    """该模型可挂载的 LoRA 列表（从能力注册表按 base_model 兼容性推导）"""
    m = get_model(model_id)
    if not m or not (m.get("lora") or {}).get("supported"):
        return []
    try:
        import capabilities as caps
        out = []
        for c in caps.list_capabilities():
            if not c.get("compatible"):
                continue
            base = str(c.get("base_model") or "").lower()
            name = str(m.get("name") or "").lower()
            if base and (name in base or base.split("/")[-1].lower() in name):
                out.append({"cap_id": c.get("cap_id"), "name": c.get("name"),
                            "status": c.get("status"), "rank": c.get("rank")})
        return out
    except Exception:
        return []


def summary():
    models = list_models()
    by_role = {}
    by_status = {}
    for m in models:
        by_role[m.get("role") or "?"] = by_role.get(m.get("role") or "?", 0) + 1
        by_status[m.get("status") or "?"] = by_status.get(m.get("status") or "?", 0) + 1
    return {"total": len(models), "by_role": by_role, "by_status": by_status,
            "dispatchable": [m.get("model_id") for m in dispatchable()]}


def prompt_text():
    """给主脑 prompt 的紧凑模型清单"""
    lines = ["可用/可调配模型（以基准库为准）："]
    for m in list_models():
        if m.get("status") not in ("running", "available"):
            continue
        lora = m.get("lora") or {}
        lora_txt = ("可挂 LoRA（max %s）" % lora.get("max_loras")) if lora.get("supported") else "不可挂 LoRA"
        b = m.get("boundaries") or {}
        lines.append("- %s（%s；%s；ctx %s；%s；%s）" % (
            m.get("model_id"), m.get("role_label") or m.get("role"),
            m.get("gpu") or "—", m.get("context_limit") or "?",
            lora_txt, "；".join((b.get("can") or [])[:3])))
        if b.get("cannot"):
            lines.append("  限制：%s" % "；".join(b.get("cannot")[:3]))
    return "\n".join(lines)
