# -*- coding: utf-8 -*-
"""chimera_compat.py —— 奇美拉 V2（S1）：兼容性判定 + 三档移植决策

对齐《目标与方向统一 v2.0》C 决策：
  · 云模型主要通过【兼容性】判断是否移植
  · 都兼容时优先 star 多的架构
  · 三档：直接移植 / 代码借鉴重建 / 下载模型（云） / 跳过
  · DeepSeek 搜索接口预留（cloud_search.py 待建；当前用本地 GitHub 检索 + Kimi 复审）

本模块只做"判定"（不安装）；安装走 sandbox_installer / 人工执行。
"""
import json
import os
import shutil
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

# 本地环境约束（实测，2026-10-26）
LOCAL_ENV = {
    "python": "3.12.3",
    "pip": True,
    "npm": True,
    "docker": False,            # WSL 无 Docker
    "systemd": False,
    "gpu": [{"name": "RTX 5060 Ti", "vram_gb": 16, "sm": "sm_120"},
            {"name": "RTX 3060", "vram_gb": 12, "sm": "sm_86"}],
    "ram_gb": 15.3,
    "network": {"pypi": True, "github_unauth": "60/h", "modelscope": True, "hf": "受限（镜像可用）"},
}

LICENSE_ALLOW = ("mit", "apache-2.0", "apache 2.0", "bsd", "isc", "mpl-2.0", "unlicense", "cc0")
LICENSE_CAUTION = ("gpl", "agpl", "lgpl", "sspl", "cc-by-nc", "non-commercial")

# KB 链路候选（S1 试点；stars/license 为已知值，会在 enrich 时再用 GitHub 校正）
KB_CHAIN_CANDIDATES = [
    {"name": "Docling", "repo": "docling-project/docling", "lang": "python",
     "install": "pip", "deps_weight": 2, "gpu": "none", "docker": False,
     "system_deps": [], "license": "mit", "stars": 30000, "kb_role": "parse",
     "desc": "IBM 文档解析：PDF/Word/PPT→结构化 Markdown/JSON"},
    {"name": "MinerU", "repo": "opendatalab/MinerU", "lang": "python",
     "install": "pip", "deps_weight": 3, "gpu": "optional", "docker": False,
     "system_deps": [], "license": "agpl-3.0", "stars": 45000, "kb_role": "parse",
     "desc": "中文 PDF 版面/表格/公式解析（模型较大）"},
    {"name": "RAGFlow", "repo": "infiniflow/ragflow", "lang": "python/ts",
     "install": "docker", "deps_weight": 3, "gpu": "optional", "docker": True,
     "system_deps": ["elasticsearch", "mysql", "redis"], "license": "apache-2.0",
     "stars": 65000, "kb_role": "rag", "desc": "深度文档理解 RAG 引擎（需 Docker 全家桶）"},
    {"name": "LightRAG", "repo": "HKUDS/LightRAG", "lang": "python",
     "install": "pip", "deps_weight": 1, "gpu": "optional", "docker": False,
     "system_deps": [], "license": "mit", "stars": 20000, "kb_role": "graph",
     "desc": "轻量图谱 RAG（实体/关系抽取）"},
    {"name": "LlamaIndex", "repo": "run-llama/llama_index", "lang": "python",
     "install": "pip", "deps_weight": 1, "gpu": "none", "docker": False,
     "system_deps": [], "license": "mit", "stars": 40000, "kb_role": "chunk/retrieval",
     "desc": "数据接入/分块/检索框架（可作为 KB 编排层）"},
    {"name": "unstructured", "repo": "Unstructured-IO/unstructured", "lang": "python",
     "install": "pip", "deps_weight": 3, "gpu": "none", "docker": False,
     "system_deps": ["libmagic", "poppler", "tesseract"], "license": "apache-2.0",
     "stars": 12000, "kb_role": "parse", "desc": "多格式文档解析（系统依赖多）"},
]


def _lic_level(lic):
    lic = str(lic or "").strip().lower()
    if not lic or lic in ("none", "noassertion", "unknown", "other"):
        return "unknown"
    if any(a in lic for a in LICENSE_CAUTION):
        return "caution"
    if any(a in lic for a in LICENSE_ALLOW):
        return "allow"
    return "unknown"


def enrich_from_github(candidates, limit=6):
    """用 GitHub 检索校正 stars/license（失败则保留原值，标记待核实）"""
    try:
        import sys
        sys.path.insert(0, str(PROJECT_DIR / "tools"))
        import search_existing as se
    except Exception:
        return candidates
    out = []
    for c in candidates[:limit]:
        c = dict(c)
        try:
            repos = se.search_github(c.get("repo", "").split("/")[-1], limit=5) or []
            hit = None
            for r in repos:
                if str(r.get("id") or "").lower() == str(c.get("repo") or "").lower():
                    hit = r
                    break
            if hit:
                c["stars"] = int(hit.get("stars") or c.get("stars") or 0)
                _lic = str(hit.get("license") or "").strip().lower()
                if _lic and _lic not in ("other", "none", "unknown", "noassertion"):
                    c["license"] = _lic
                c["enriched"] = True
            else:
                c["enriched"] = False
        except Exception as e:
            c["enriched"] = False
            c["enrich_error"] = str(e)[:120]
        out.append(c)
    out.extend(candidates[limit:])
    return out


def compat(candidate, env=None):
    """兼容性评估 → {score 0-100, tier, reasons, actions}"""
    env = env or LOCAL_ENV
    c = candidate
    score = 0
    reasons = []
    actions = []
    lic = _lic_level(c.get("license"))
    if lic == "allow":
        score += 20
        reasons.append("许可允许（%s）" % c.get("license"))
    elif lic == "caution":
        score += 5
        reasons.append("⚠ 许可受限（%s），需隔离/替换" % c.get("license"))
        actions.append("评估许可：进程/容器隔离，或改用宽松许可替代")
    else:
        score += 2
        reasons.append("⚠ 许可不明，禁止自动批准")

    install = str(c.get("install") or "pip")
    if install == "pip":
        score += 22
        reasons.append("pip 可装（纯 Python 优先）")
    elif install in ("npm", "binary", "cli"):
        score += 15
        reasons.append("%s 可装" % install)
    else:
        score -= 15
        reasons.append("需容器/服务化安装（%s）" % install)
        actions.append("不直接移植：参考其架构在本地重建轻量版")

    deps = int(c.get("deps_weight") or 0)
    score += {0: 12, 1: 9, 2: 5, 3: 2}.get(min(deps, 3), 0)
    reasons.append("依赖复杂度 %d/3" % deps)
    if deps >= 3:
        actions.append("依赖重：建议只移植核心模块或借鉴实现")

    gpu = str(c.get("gpu") or "none")
    if gpu == "none":
        score += 18
    elif gpu == "optional":
        score += 10
        reasons.append("GPU 可选")
    else:
        score -= 10
        reasons.append("需要 GPU（本地显存紧张）")
        actions.append("GPU 需求评估：本地不足则走云端")

    if not c.get("docker"):
        score += 12
    else:
        score -= 18
        reasons.append("需要 Docker（本机不可用）")

    sysdeps = c.get("system_deps") or []
    if not sysdeps:
        score += 8
    else:
        score -= 8
        reasons.append("需系统依赖：%s" % "、".join(sysdeps[:4]))
        actions.append("系统依赖需 apt/手动安装，移植成本增加")

    stars = int(c.get("stars") or 0)
    score += 10 if stars >= 5000 else (5 if stars >= 500 else 0)
    reasons.append("stars=%d" % stars)

    score = max(0, min(100, score))

    # 三档判定（优先级：跳过 > 云 > 直接 > 重建）
    if lic == "unknown":
        tier = "needs_review"
        verdict = "许可不明 → 人工/云审确认"
    elif c.get("docker") or install == "docker":
        tier = "borrow_rebuild"
        verdict = "需 Docker/服务化 → 架构借鉴，本地轻量重建"
    elif gpu == "required" and not env.get("docker"):
        tier = "cloud"
        verdict = "GPU 重负载 → 走云端"
    elif lic == "caution":
        tier = "borrow_rebuild"
        verdict = "copyleft 许可 → 借鉴实现/隔离使用"
    elif install == "pip" and deps <= 2 and not c.get("docker") and gpu in ("none", "optional"):
        tier = "direct_transplant"
        verdict = "pip 直接移植（建议先 dry-run/隔离安装）"
    elif install in ("npm", "binary", "cli"):
        tier = "direct_transplant"
        verdict = "工具可执行 → 直接封装为外部工具"
    else:
        tier = "borrow_rebuild"
        verdict = "依赖/系统条件偏重 → 代码借鉴本地重建"
    return {"name": c.get("name"), "repo": c.get("repo"), "score": score, "tier": tier,
            "verdict": verdict, "license_level": lic, "reasons": reasons,
            "actions": actions, "stars": stars, "kb_role": c.get("kb_role")}


def judge_with_cloud(candidates):
    """云模型兼容性复审（Kimi/DeepSeek 接口；失败则不阻塞）"""
    try:
        import cloud_bridge
        if not cloud_bridge.available():
            return {"status": "skipped", "reason": "云端未配置 key"}
        prompt = (
            "你是奇美拉移植评审。判断下列开源项目在【本地环境】的兼容性："
            "Python 3.12 + pip；无 Docker；GPU 16GB(sm_120)+12GB(sm_86)；RAM 15.3GB。"
            "请对每个项目给出：ready(直接pip移植)/rebuild(代码借鉴重建)/cloud(必须云端)/reject(拒绝)，"
            "并给一句理由。只输出 JSON：{\"items\":[{\"repo\":\"...\",\"decision\":\"ready|rebuild|cloud|reject\",\"why\":\"...\"}]}\n"
            + json.dumps(candidates, ensure_ascii=False)[:4000]
        )
        import chimera
        for attempt in range(2):
            ans = cloud_bridge.ask(prompt, system="你是严格的移植评审，只输出 JSON。", max_tokens=2000)
            data = chimera._extract_json(ans)
            if isinstance(data, dict) and data.get("items"):
                return {"status": "ok", "review": data}
            if attempt == 0:
                prompt = ("只输出 JSON，不要解释。{\"items\":[{\"repo\":\"...\",\"decision\":\"ready|rebuild|cloud|reject\","
                          "\"why\":\"...\"}]}\n候选：" + json.dumps(candidates, ensure_ascii=False)[:2500])
        return {"status": "parse_error", "raw": str(ans)[:800]}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:200]}


def port_plan(candidates=None, use_cloud=True):
    """候选 → 兼容分 + 三档 + 排序（都兼容优先 stars 多）"""
    cands = enrich_from_github(candidates or KB_CHAIN_CANDIDATES)
    rows = [compat(c) for c in cands]
    cloud = judge_with_cloud([{k: c.get(k) for k in ("name", "repo", "license", "install",
                                                    "deps_weight", "gpu", "docker", "stars")}
                              for c in cands]) if use_cloud else {"status": "skipped"}
    # 合并云审意见（advisory，不覆盖硬规则，只补充/降级）
    if cloud.get("status") == "ok":
        by_repo = {}
        for it in (cloud.get("review") or {}).get("items") or []:
            by_repo[str(it.get("repo") or "").lower()] = it
        for r in rows:
            it = by_repo.get(str(r.get("repo") or "").lower())
            if not it:
                continue
            r["cloud_decision"] = it.get("decision")
            r["cloud_why"] = it.get("why")
            if it.get("decision") == "reject":
                r["tier"] = "skip"
                r["verdict"] = "云审拒绝：" + str(it.get("why") or "")[:120]
    order = {"direct_transplant": 0, "borrow_rebuild": 1, "cloud": 2, "needs_review": 3, "skip": 4}
    rows.sort(key=lambda x: (order.get(x["tier"], 9), -int(x.get("stars") or 0)))
    return {"generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            "env": LOCAL_ENV, "candidates": rows, "cloud_review": cloud,
            "summary": {t: sum(1 for r in rows if r["tier"] == t) for t in order}}
