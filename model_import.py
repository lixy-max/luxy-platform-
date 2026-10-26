# -*- coding: utf-8 -*-
"""model_import.py —— 奇美拉 V2 第三档：下载微调模型（先评估设备能力）

对齐《目标与方向统一 v2.0》C2/C4：
  · 下载模型前**评估设备能力**（参数量/权重体积 → fp16/int8/int4 显存需求 → 单卡/双卡/云端）
  · 评估通过才下载（ModelScope 优先；HF 走 hf-mirror）
  · 下载后校验文件 + 注册到模型基准库（角色/能力界限/可挂 LoRA）

用法：
  assess("FreedomIntelligence/HuatuoGPT-o1-7B", source="hf")
  import_model("Qwen/Qwen2.5-1.5B-Instruct", source="modelscope", approve=True, role="candidate_worker")
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(Path.home() / "models")))
IMPORTS_FILE = PROJECT_DIR / "data" / "models" / "imports.jsonl"
HF_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")

# 本机 GPU（实测；可按需修改）
GPUS = [
    {"id": "GPU0", "name": "RTX 5060 Ti", "vram_gb": 16.0, "usable_gb": 15.2, "sm": "sm_120"},
    {"id": "GPU1", "name": "RTX 3060", "vram_gb": 12.0, "usable_gb": 11.2, "sm": "sm_86"},
]
RAM_GB = 15.3
WEIGHT_SUFFIX = (".safetensors", ".bin", ".gguf", ".pt", ".pth")

BYTES_PER_PARAM = {"fp16": 2.0, "int8": 1.0, "int4": 0.5}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "luxy-model-import/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_text(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "luxy-model-import/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="ignore")


def fetch_meta(repo_id, source="modelscope"):
    """仓库元数据 + 权重文件大小"""
    repo_id = str(repo_id or "").strip()
    enc = urllib.parse.quote(repo_id)
    out = {"repo_id": repo_id, "source": source, "name": repo_id.split("/")[-1],
           "license": "", "downloads": None, "weight_bytes": 0, "files": [],
           "config": None, "error": ""}
    if source == "modelscope":
        d = _get_json("https://modelscope.cn/api/v1/models/%s" % enc).get("Data") or {}
        out["name"] = str(d.get("Name") or out["name"])
        out["license"] = str(d.get("License") or "").lower()
        out["downloads"] = d.get("Downloads")
        try:
            fl = _get_json("https://modelscope.cn/api/v1/models/%s/repo/files?Revision=master&Recursive=True"
                           % enc).get("Data") or {}
            files = fl.get("Files") or []
            out["files"] = [{"path": str(f.get("Name") or ""), "size": int(f.get("Size") or 0)}
                            for f in files if f.get("Name")]
            out["weight_bytes"] = sum(f["size"] for f in out["files"]
                                      if f["path"].lower().endswith(WEIGHT_SUFFIX))
        except Exception as e:
            out["error"] = "files: %s" % e
        try:
            txt = _get_text("https://modelscope.cn/api/v1/models/%s/repo?Revision=master&FilePath=config.json"
                            % enc)
            out["config"] = json.loads(txt)
        except Exception:
            pass
    elif source == "hf":
        d = _get_json("%s/api/models/%s" % (HF_ENDPOINT, repo_id))
        out["license"] = str((d.get("cardData") or {}).get("license") or "").lower()
        out["downloads"] = d.get("downloads")
        try:
            tree = _get_json("%s/api/models/%s/tree/main?recursive=true" % (HF_ENDPOINT, repo_id))
            if isinstance(tree, list):
                out["files"] = [{"path": str(x.get("path") or ""), "size": int(x.get("size") or 0)}
                                for x in tree if x.get("type") == "file"]
                out["weight_bytes"] = sum(f["size"] for f in out["files"]
                                          if f["path"].lower().endswith(WEIGHT_SUFFIX))
        except Exception as e:
            out["error"] = "tree: %s" % e
        try:
            txt = _get_text("%s/%s/resolve/main/config.json" % (HF_ENDPOINT, repo_id))
            out["config"] = json.loads(txt)
        except Exception:
            pass
    else:
        raise ValueError("source 只支持 modelscope/hf")
    out["total_bytes"] = sum(f["size"] for f in out["files"])
    return out


def _params_from_config(cfg):
    """粗略参数估计：12*L*h^2 + vocab*h（transformer 主体 + embedding）"""
    if not isinstance(cfg, dict):
        return None
    def g(k, default=0):
        v = cfg.get(k, default)
        try:
            return int(v)
        except Exception:
            return default
    h = g("hidden_size") or g("n_embd") or g("d_model")
    L = g("num_hidden_layers") or g("n_layer") or g("num_layers")
    V = g("vocab_size")
    inter = g("intermediate_size")
    if not (h and L):
        return None
    if inter:
        per_layer = 12 * h * h  # 近似（attn 4h^2 + mlp 8h^2）
    else:
        per_layer = 12 * h * h
    return int(per_layer * L + (V * h if V else 0))


def estimate(meta):
    """设备能力评估：fp16/int8/int4 显存需求 + 最适合的部署形态 + 量化建议"""
    weight_gb = (int(meta.get("weight_bytes") or 0)) / 1024 ** 3
    params = _params_from_config(meta.get("config"))
    params_src = "config 估算" if params else None
    if not params:
        # 按 fp16 权重反推（2 bytes/param）
        params = int(weight_gb * 1024 ** 3 / 2) if weight_gb > 0 else 0
        params_src = "按权重体积反推（假设 fp16）"
    params_b = round(params / 1e9, 2) if params else 0

    ctx = 0
    cfg = meta.get("config") or {}
    try:
        ctx = int(cfg.get("max_position_embeddings") or cfg.get("max_seq_len") or 0)
    except Exception:
        ctx = 0
    ctx = min(ctx or 4096, 8192)

    modes = {}
    for mode, bpp in BYTES_PER_PARAM.items():
        w = params * bpp / 1024 ** 3 if params else 0
        # KV/运行时开销：权重 20% + 至少 1.0GB；long ctx 再加
        kv = max(1.0, w * 0.2) + (1.0 if ctx >= 8192 else 0.5)
        modes[mode] = {"weights_gb": round(w, 2), "overhead_gb": round(kv, 2),
                       "total_gb": round(w + kv, 2)}

    def fit_mode(mode):
        t = modes[mode]["total_gb"]
        if t <= GPUS[0]["usable_gb"]:
            return "single_16g"
        if t <= GPUS[1]["usable_gb"]:
            return "single_12g"
        if t <= GPUS[0]["usable_gb"] + GPUS[1]["usable_gb"]:
            return "dual_tp2"
        return "cloud_only"

    fits = {m: fit_mode(m) for m in modes}
    # 质量-单卡优先：先找"单卡可部署"里质量最高的（fp16→int8→int4）；
    # 单卡都不行时再看双卡；最后才 cloud_only
    best_mode = None
    for m in ("fp16", "int8", "int4"):
        if fits[m] in ("single_16g", "single_12g"):
            best_mode = m
            break
    if best_mode is None:
        for m in ("fp16", "int8", "int4"):
            if fits[m] == "dual_tp2":
                best_mode = m
                break
    if best_mode is None:
        best_mode = "int4"
    verdict = fits[best_mode]
    quant = None
    if verdict == "cloud_only":
        quant = "云端（或更大显存设备）"
    elif best_mode == "fp16":
        quant = "无需量化（fp16/原格式）"
    else:
        quant = {"int8": "int8 / AWQ-w8a16 / GPTQ-8bit", "int4": "AWQ/GPTQ 4bit"}[best_mode]
    notes = []
    if weight_gb and meta.get("weight_bytes", 0) / 1024 ** 3 > 20:
        notes.append("权重 >20GB，下载耗时较长（ModelScope 40–70MB/s 估算）")
    if meta.get("license") in ("", "other", "unknown") or "agpl" in str(meta.get("license") or ""):
        notes.append("许可需人工确认（%s）" % (meta.get("license") or "未知"))
    # 当前占用（决定"立即部署"还是"需先停服务"）
    gpu_now = gpu_usage()
    max_free = max([g.get("free_gb") or 0 for g in gpu_now] or [0])
    total_best = modes[best_mode]["total_gb"]
    deploy_now = bool(max_free >= total_best)
    deploy_note = ("可立即部署（当前空闲 %.1fGB）" % max_free) if deploy_now else \
        ("需先停掉占用 GPU 的服务（当前最大空闲 %.1fGB，需 %.1fGB）" % (max_free, total_best))
    return {"repo_id": meta.get("repo_id"), "name": meta.get("name"), "license": meta.get("license"),
            "downloads": meta.get("downloads"), "weight_gb": round(weight_gb, 2),
            "total_download_gb": round(int(meta.get("total_bytes") or 0) / 1024 ** 3, 2),
            "params_b": params_b, "params_source": params_src, "context": ctx or "未知",
            "modes": modes, "fits": fits, "best_mode": best_mode, "verdict": verdict,
            "quantization": quant, "gpus": gpu_now, "deploy_now": deploy_now,
            "deploy_note": deploy_note, "notes": notes}



def gpu_usage():
    """当前 GPU 占用（nvidia-smi；失败回退静态总量）"""
    out = [dict(g, used_gb=None, free_gb=g["usable_gb"]) for g in GPUS]
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used,memory.total",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        for ln in (r.stdout or "").strip().splitlines():
            parts = [x.strip() for x in ln.split(",")]
            if len(parts) < 3:
                continue
            idx = int(parts[0]); used = float(parts[1]) / 1024.0; total = float(parts[2]) / 1024.0
            if idx < len(out):
                out[idx]["used_gb"] = round(used, 1)
                out[idx]["total_gb"] = round(total, 1)
                out[idx]["free_gb"] = round(max(0.0, total - used), 1)
    except Exception:
        pass
    return out


def assess(repo_id, source="modelscope"):
    meta = fetch_meta(repo_id, source=source)
    est = estimate(meta)
    est["files_preview"] = [f for f in meta.get("files") or []
                            if f["path"].lower().endswith(WEIGHT_SUFFIX)][:6]
    if meta.get("error"):
        est["meta_error"] = meta["error"]
    return est


def _slug(s):
    return re.sub(r"[^0-9A-Za-z_.-]+", "-", str(s)).strip("-")[:60]


def _append_import(rec):
    IMPORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(IMPORTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def download(repo_id, source="modelscope", local_dir=None, timeout=3600):
    """下载模型到本地（ModelScope 或 hf-mirror）"""
    local_dir = Path(local_dir) if local_dir else (MODELS_DIR / _slug(repo_id.split("/")[-1]))
    local_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if source == "hf":
        os.environ["HF_ENDPOINT"] = HF_ENDPOINT
        from huggingface_hub import snapshot_download
        path = snapshot_download(repo_id, local_dir=str(local_dir),
                                 local_dir_use_symlinks=False, resume_download=True)
    else:
        from modelscope.hub.snapshot_download import snapshot_download
        path = snapshot_download(repo_id, local_dir=str(local_dir))
    return {"path": str(path or local_dir), "dir": str(local_dir),
            "download_seconds": round(time.time() - t0, 1)}


def register_model(est, local_dir, role="candidate_worker", source="modelscope", repo_id=""):
    """注册到模型基准库（data/models_registry.json）"""
    import model_registry as mr
    name = est.get("name") or repo_id.split("/")[-1]
    model_id = _slug(repo_id.split("/")[-1]).lower() + "-fp16"
    cfg = {}
    try:
        cfg = json.loads((Path(local_dir) / "config.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    reg = mr._load()
    # registry 结构：{"models": {model_id: {...}}}（dict，不是 list）
    models = reg.get("models")
    if not isinstance(models, dict):
        models = {}
        reg["models"] = models
    if model_id in models:
        models[model_id].update({"path": str(local_dir), "status": "downloaded",
                                 "imported_at": _now()})
        mr._save(reg)
        return models[model_id]
    entry = {
        "model_id": model_id, "name": name, "path": str(local_dir),
        "role": role,
        "role_label": {"candidate_worker": "候选工蜂", "quality_mode": "质量模式候选",
                       "small_candidate": "小模型候选", "main_brain": "主脑"}.get(role, role),
        "port": None, "gpu": est.get("fits", {}).get(est.get("best_mode") or "fp16"),
        "context_limit": est.get("context") if isinstance(est.get("context"), int) else 4096,
        "kv_tokens": None, "speed_tps": None,
        "weights_gib": est.get("weight_gb"), "lora": {"supported": True, "max_loras": 2,
                                                       "max_rank": 128, "max_cpu_loras": 4},
        "boundaries": {
            "can": ["领域问答（%s）" % (est.get("license") or "未知许可"),
                    "可做单步能力/挂载 LoRA"],
            "cannot": ["未经实测速度/质量（待验证）",
                       "建议量化后部署：%s" % (est.get("quantization") or "")],
        },
        "status": "downloaded", "source": "%s:%s" % (source, repo_id),
        "license": est.get("license"), "imported_at": _now(),
        "quantization_suggestion": est.get("quantization"),
        "params_b": est.get("params_b"),
    }
    models[model_id] = entry
    mr._save(reg)
    return entry


def import_model(repo_id, source="modelscope", approve=False, role="candidate_worker",
                 local_dir=None, timeout=3600):
    """评估 →（通过后）下载 → 校验 → 注册"""
    if not approve:
        raise ValueError("下载模型需要显式 approve=true")
    est = assess(repo_id, source=source)
    if est.get("verdict") == "cloud_only":
        return {"ok": False, "reason": "设备能力不足（cloud_only），建议云端或换小模型",
                "assessment": est}
    dl = download(repo_id, source=source, local_dir=local_dir,
                  timeout=timeout)
    size_gb = round(sum(f.stat().st_size for f in Path(dl["dir"]).rglob("*") if f.is_file())
                    / 1024 ** 3, 2)
    entry = register_model(est, dl["dir"], role=role, source=source, repo_id=repo_id)
    rec = {"repo_id": repo_id, "source": source, "role": role, "assessment": est,
           "download": dl, "local_size_gb": size_gb, "model": entry, "at": _now()}
    _append_import(rec)
    return {"ok": True, **rec}


def list_imports(limit=30):
    if not IMPORTS_FILE.exists():
        return []
    rows = []
    for ln in IMPORTS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    return rows[-int(limit):]
