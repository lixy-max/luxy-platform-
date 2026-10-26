# -*- coding: utf-8 -*-
"""modality_router.py —— S12：图谱/多模态检测 → 云端路由（架构 D 项）

规则（用户确认的架构要求）：
  · 任务里检测到「图谱 / 多模态（图像/音频/视频/OCR）」→ 路由到云端
  · 云端按 provider 能力匹配：文本模型可做图谱规划；视觉/音频/视频需要带对应 modality 的 provider
  · 没有匹配 provider → 明确 blocked + 配置指引（不静默用文本模型假装能看图）
"""
import json
import os
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SETTINGS = PROJECT_DIR / "settings.json"

MODALITY_KEYWORDS = {
    "vision": ["图片", "图像", "照片", "截图", "视觉", "识别图片", "识别图像", "读图", "看图",
               "ocr", "扫描件", "图纸", "发票", "票据", "手写体"],
    "audio": ["音频", "语音", "录音", "转写", "语音识别", "asr", "tts", "播客"],
    "video": ["视频", "影音", "录屏", "抽帧", "视频理解", "短视频"],
    "graph": ["知识图谱", "图谱", "关系抽取", "实体关系", "实体抽取", "三元组", "neo4j",
              "图数据库", "graphrag", "图检索", "图结构", "实体链接"],
}
MODALITY_LABELS = {"vision": "视觉", "audio": "音频", "video": "视频", "graph": "图谱"}
DEFAULT_MODALITIES = ["text"]


def _load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_providers():
    """列出云端 provider 及其能力（key 打码）"""
    out = []
    env_base = os.environ.get("CLOUD_API_BASE") or ""
    env_key = os.environ.get("CLOUD_API_KEY") or ""
    if env_base and env_key:
        mods = [m.strip() for m in (os.environ.get("CLOUD_MODALITIES") or "text").split(",") if m.strip()]
        out.append({"name": "env", "api_base": env_base,
                    "model": os.environ.get("CLOUD_MODEL") or "deepseek-chat",
                    "modalities": mods or ["text"],
                    "key_masked": "****" + env_key[-4:] if len(env_key) > 4 else "****"})
    st = _load_settings()
    for name, cfg in (st.get("providers") or {}).items():
        if not isinstance(cfg, dict):
            continue
        key = str(cfg.get("api_key") or "")
        base = str(cfg.get("api_base") or "")
        if not key or not base or "127.0.0.1" in base or "localhost" in base:
            continue
        mods = cfg.get("modalities") or DEFAULT_MODALITIES
        if isinstance(mods, str):
            mods = [m.strip() for m in mods.split(",") if m.strip()]
        out.append({"name": name, "api_base": base.rstrip("/"),
                    "model": str(cfg.get("model") or "deepseek-chat"),
                    "modalities": mods or DEFAULT_MODALITIES,
                    "key_masked": "****" + key[-4:] if len(key) > 4 else "****"})
    return out


def _norm(t):
    return str(t or "").lower()


def detect(text):
    """检测文本中的图谱/多模态需求"""
    t = _norm(text)
    kinds, evidence = [], {}
    for kind, kws in MODALITY_KEYWORDS.items():
        hits = [kw for kw in kws if kw in t]
        if hits:
            kinds.append(kind)
            evidence[kind] = hits[:5]
    return {"text": str(text or "")[:200], "kinds": kinds, "evidence": evidence,
            "is_modality": bool(set(kinds) & {"vision", "audio", "video"}),
            "is_graph": "graph" in kinds,
            "needs_cloud": bool(kinds)}


def select_provider(kinds):
    """按需求挑 provider：图谱只需 text；视觉/音频/视频需对应 modality"""
    kinds = list(dict.fromkeys(kinds or []))
    providers = list_providers()
    if not providers:
        return {"ok": False, "provider": None, "missing": kinds,
                "reason": "未配置任何云端 provider",
                "advice": "在 settings.json providers 配置（或设置 CLOUD_API_BASE/CLOUD_API_KEY）"}
    required = set()
    if "graph" in kinds:
        required.add("text")          # 图谱：文本云模型做规划/结构设计
    for k in ("vision", "audio", "video"):
        if k in kinds:
            required.add(k)
    missing = sorted(m for m in required if not any(m in p["modalities"] for p in providers))
    if missing:
        return {"ok": False, "provider": None, "missing": missing,
                "reason": "没有支持 %s 的云端 provider" % "、".join(
                    MODALITY_LABELS.get(m, m) for m in missing),
                "advice": "为 provider 增加 modalities 配置（如 \"vision\"），或保持文本规划并人工处理图片"}
    best = None
    for p in providers:
        score = len(required & set(p["modalities"]))
        if best is None or score > best[0]:
            best = (score, p)
    return {"ok": True, "provider": best[1], "missing": [],
            "required": sorted(required)}


def route(text):
    """路由决策：local / cloud / blocked"""
    d = detect(text)
    if not d["kinds"]:
        return {"route": "local", "reason": "未检测到图谱/多模态需求", **d}
    sel = select_provider(d["kinds"])
    if not sel["ok"]:
        return {"route": "blocked", "reason": sel["reason"], "advice": sel["advice"],
                "missing": sel["missing"], **d}
    target = "cloud:" + "+".join(d["kinds"])
    return {"route": "cloud", "target": target, "provider": sel["provider"]["name"],
            "reason": "检测到%s需求 → 云端（provider=%s）" % (
                "、".join(MODALITY_LABELS.get(k, k) for k in d["kinds"]), sel["provider"]["name"]),
            **d}


def blocked_reason(kinds):
    """执行侧用：返回阻断原因（None=可执行）"""
    kinds = [k for k in (kinds or []) if k]
    if not kinds:
        return None
    sel = select_provider(kinds)
    if sel["ok"]:
        return None
    return "%s（%s）" % (sel["reason"], sel.get("advice") or "")


def summary():
    provs = list_providers()
    return {"providers": provs, "provider_count": len(provs),
            "modalities_supported": sorted({m for p in provs for m in p["modalities"]}),
            "keywords": {k: v[:4] for k, v in MODALITY_KEYWORDS.items()}}
