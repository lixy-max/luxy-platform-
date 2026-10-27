# -*- coding: utf-8 -*-
"""S19 · LoRA Registry（能力标签 / 评分 / 兼容性 / 状态）

数据源：/home/luxy/loras/registry.json（平台注册表）
职责：把注册表增强为"可路由"的能力条目：
- 兼容性：adapter base_model 与 worker base 同家族（如 Qwen2.5-7B-Instruct vs Qwen2.5-7B-Instruct-AWQ）
- 能力标签：按名称/描述启发式 + 显式覆盖
- 评分：eval/优先级（人工覆盖，后续由评测自动回填）
- 状态：active / shadow / broken（路径不存在或缺 adapter_config → broken）
"""
import json
import os
import re
from pathlib import Path

REG_PATH = Path(os.environ.get("HIVE_LORA_REGISTRY", "/home/luxy/loras/registry.json"))

# 能力标签规则（按名称/描述关键词）
CAP_RULES = [
    (r"json", ["json", "extract", "format"]),
    (r"semantic_intent", ["intent", "semantic", "classify"]),
    (r"intent", ["intent", "classify"]),
    (r"agri", ["agri", "domain", "expert"]),
    (r"persona", ["persona", "style"]),
    (r"drill", ["drill", "qa"]),
    (r"paper", ["paper", "analyze"]),
    (r"workflow", ["workflow", "authoring"]),
    (r"dimension", ["dimension", "analysis"]),
    (r"claims", ["claims", "extract"]),
    (r"extract", ["extract", "summary"]),
    (r"analyze_data", ["data", "analysis"]),
    (r"code", ["code", "sql"]),
    (r"frontend", ["frontend", "ui"]),
]

# 显式覆盖（评分/能力）；后续可被评测结果自动更新
OVERRIDES = {
    "json_extract": {"score": 0.88, "capability": ["json", "extract", "format"]},
    "semantic_intent_v1": {"score": 0.90, "capability": ["intent", "semantic", "classify"]},
    "agri_expert_v1": {"score": 0.82, "capability": ["agri", "domain", "expert"]},
    "swk_persona_v3": {"score": 0.75, "capability": ["persona", "style"]},
    "drill_v1p": {"score": 0.70, "capability": ["drill", "qa"]},
    "drill_v2r8x": {"score": 0.70, "capability": ["drill", "qa"]},
    "drill_v2r32x": {"score": 0.72, "capability": ["drill", "qa"]},
    "drill_v2r128x": {"score": 0.73, "capability": ["drill", "qa"]},
    "workflow_author_v1": {"score": 0.80, "capability": ["workflow", "authoring"]},
}


def _family(base):
    """把 base 归一为模型家族：qwen2.5-7b / qwen3-4b / qwen2.5-1.5b ..."""
    s = str(base or "").lower()
    s = s.split("/")[-1]
    m = re.search(r"(qwen\d(?:\.\d)?)[-_.]?(\d+(?:\.\d+)?)b", s)
    if m:
        return "%s-%sb" % (m.group(1), m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)b", s)
    if m:
        return "unknown-%sb" % m.group(1)
    return s or "unknown"


def _capabilities(name, desc=""):
    text = (str(name) + " " + str(desc)).lower()
    caps = []
    for pat, cs in CAP_RULES:
        if re.search(pat, text):
            for c in cs:
                if c not in caps:
                    caps.append(c)
    ov = OVERRIDES.get(str(name))
    if ov and ov.get("capability"):
        caps = list(dict.fromkeys(ov["capability"] + caps))
    return caps or ["general"]


def _adapter_info(path):
    p = Path(path)
    cfg = p / "adapter_config.json"
    info = {"path_exists": p.is_dir(), "has_adapter_config": cfg.exists(),
            "base": None, "rank": None, "size_mb": 0.0}
    try:
        if p.is_dir():
            info["size_mb"] = round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6, 1)
        if cfg.exists():
            ac = json.loads(cfg.read_text(encoding="utf-8"))
            info["base"] = ac.get("base_model_name_or_path")
            info["rank"] = ac.get("r") or ac.get("lora_rank")
    except Exception:
        pass
    return info


def load_registry(enrich=True):
    """返回 {lora_id: entry}；entry 含能力/评分/兼容性/状态"""
    try:
        raw = json.loads(REG_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    reg = {}
    for name, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        path = meta.get("path") or str(Path("/home/luxy/loras") / name)
        info = _adapter_info(path)
        ov = OVERRIDES.get(name) or {}
        base = info["base"] or meta.get("base_model") or ""
        rank = info["rank"] or meta.get("rank") or meta.get("lora_rank") or 0
        entry = {
            "id": name,
            "path": path,
            "base_model": base,
            "family": _family(base),
            "rank": rank,
            "capability": _capabilities(name, meta.get("description")),
            "score": ov.get("score", float(meta.get("score") or 0.6)),
            "version": meta.get("version") or "v1",
            "status": "active",
            "path_exists": info["path_exists"],
            "has_adapter_config": info["has_adapter_config"],
            "size_mb": info["size_mb"],
        }
        if not (entry["path_exists"] and entry["has_adapter_config"]):
            entry["status"] = "broken"
        elif meta.get("compatible") is False:
            entry["status"] = "shadow"
        reg[name] = entry
    return reg


def worker_family(worker_base):
    return _family(worker_base)


def compatible(entry, worker_base):
    """同一模型家族 + 路径有效 + 不是 shadow/broken"""
    if entry.get("status") != "active":
        return False
    if not entry.get("path_exists") or not entry.get("has_adapter_config"):
        return False
    return entry.get("family") == worker_family(worker_base)


def list_loras(worker_base=None, compatible_only=True):
    reg = load_registry()
    items = list(reg.values())
    if worker_base is not None and compatible_only:
        items = [e for e in items if compatible(e, worker_base)]
    return sorted(items, key=lambda e: (-e["score"], e["id"]))


def pick(capabilities, worker_base=None):
    """能力匹配 → 评分最高且兼容的 LoRA；无匹配返回 None（用 base）"""
    caps = set(capabilities or [])
    if not caps:
        return None
    best, best_score = None, -1.0
    for e in list_loras(worker_base=worker_base, compatible_only=worker_base is not None):
        overlap = len(caps & set(e.get("capability") or []))
        if overlap <= 0:
            continue
        score = e["score"] + 0.05 * overlap
        if score > best_score:
            best, best_score = e, score
    return best


def get(lora_id):
    return load_registry().get(str(lora_id))


def summary(worker_base=None):
    reg = load_registry()
    items = list(reg.values())
    compat = [e for e in items if worker_base and compatible(e, worker_base)]
    return {"total": len(items), "compatible": len(compat),
            "active": sum(1 for e in items if e["status"] == "active"),
            "broken": sum(1 for e in items if e["status"] == "broken"),
            "shadow": sum(1 for e in items if e["status"] == "shadow"),
            "worker_family": _family(worker_base) if worker_base else None,
            "items": [{"id": e["id"], "family": e["family"], "rank": e["rank"],
                       "score": e["score"], "capability": e["capability"],
                       "status": e["status"]} for e in
                      sorted(items, key=lambda x: -x["score"])[:20]]}
