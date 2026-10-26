# -*- coding: utf-8 -*-
"""chimera.py —— 奇美拉：能力缺口 → 开源候选映射 → 落地计划（v1）

定位（与架构设计一致）：
  · 本地 14B 主脑发现"没有对应能力"时，进入本模块
  · 候选发现：本地检索（GitHub / HF / arXiv，复用 tools/search_existing.py）
  · 计划生成：**声明式 IR（不含可执行命令）** + 许可/风险/沙箱要求
  · 政策闸门：许可白名单 + stars 阈值 + 来源白名单 → auto_approved / needs_review
  · 云端复审：若配置了云 provider key，则调用云端做二次评审；否则标注跳过（诚实）
  · **不自动安装/执行**（安装是后续 P3 沙箱阶段，需另走审批）

输出：plan dict + 中文可读文本；候选写入 data/chimera/candidates.jsonl
"""
import hashlib
import json
import re
import os
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
CHIMERA_DIR = PROJECT_DIR / "data" / "chimera"
CANDIDATES_FILE = CHIMERA_DIR / "candidates.jsonl"
REVIEW_CACHE_FILE = CHIMERA_DIR / "review_cache.json"
PLAN_CACHE_FILE = CHIMERA_DIR / "plan_cache.json"
CACHE_TTL = int(os.environ.get("CHIMERA_CACHE_TTL", "600"))
REVIEW_CACHE_TTL = int(os.environ.get("CHIMERA_REVIEW_CACHE_TTL", "600"))

# 许可政策（与许可证闸门一致）
LICENSE_ALLOW = ("mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "isc", "mpl-2.0")
LICENSE_CAUTION = ("lgpl-2.1", "lgpl-3.0", "gpl-2.0", "gpl-3.0", "agpl-3.0")
SOURCE_ALLOW = ("github", "hf", "arxiv")
AUTO_APPROVE_MIN_STARS = int(os.environ.get("CHIMERA_MIN_STARS", "500"))

# 落地计划 IR（声明式，不含可执行命令）
PLAN_STEPS = [
    {"step": "discover", "desc": "候选发现与去重（GitHub/HF/arXiv）"},
    {"step": "evaluate", "desc": "评估：维护活跃度 / 许可 / 依赖面 / 安全信号"},
    {"step": "adapter_spec", "desc": "生成适配器规格（manifest：输入输出契约 / 权限 / 沙箱要求）"},
    {"step": "sandbox_dry_run", "desc": "沙箱内 dry-run（断网 + 只读输入 + 资源限额）"},
    {"step": "supply_chain", "desc": "供应链校验（包名+版本+sha256 锁定，离线安装）"},
    {"step": "register", "desc": "注册为能力（shadow 影子期，平台侧测量成功率）"},
    {"step": "promote", "desc": "影子达标后 active；否则回滚"},
]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _ensure_dir():
    CHIMERA_DIR.mkdir(parents=True, exist_ok=True)


# ───────────────────── 候选发现（本地检索） ─────────────────────
def _search_tools():
    sys.path.insert(0, str(PROJECT_DIR / "tools"))
    import search_existing  # noqa: F401
    return search_existing


# 中文缺口 → 英文检索词（GitHub/HF 检索主要吃英文；无云翻译时的本地兜底）
_ZH2EN = {
    "表格": "table", "抽取": "extraction", "结构化": "structured", "数据": "data",
    "图片": "image", "图像": "image", "ocr": "ocr", "文字": "text", "识别": "recognition",
    "浏览器": "browser", "自动化": "automation", "爬虫": "crawler", "抓取": "scraping",
    "语音": "speech", "音频": "audio", "视频": "video", "翻译": "translation",
    "摘要": "summarization", "问答": "question answering", "检索": "retrieval",
    "文档": "document", "解析": "parsing", "转换": "convert", "导出": "export",
    "知识库": "knowledge base", "向量": "embedding", "训练": "training",
    "压缩": "compression", "加密": "encryption", "测试": "testing", "监控": "monitoring",
    "下载": "download", "上传": "upload", "报告": "report", "图表": "chart",
    "数据库": "database", "接口": "api", "部署": "deployment", "沙箱": "sandbox",
}


def _english_query(gap):
    """把中文缺口转成英文检索词（无翻译服务时的启发式兜底）"""
    g = str(gap or "").lower()
    words = []
    for zh, en in _ZH2EN.items():
        if zh in g and en not in words:
            words.append(en)
    # 保留原文里的英文单词
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", g):
        lw = w.lower()
        if lw not in words and lw not in ("the", "and", "for", "with"):
            words.append(lw)
    if not words:
        return ""
    return " ".join(words[:6])


def _search_queries(gap):
    """生成多个候选检索词（英文优先、由短到长）"""
    g = str(gap or "").lower()
    en_tokens = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", g)
                 if w not in ("the", "and", "for", "with")]
    mapped = []
    for zh, en in _ZH2EN.items():
        if zh in g and en not in mapped:
            mapped.append(en)
    qs = []
    if en_tokens and mapped:
        qs.append(" ".join(en_tokens[:2] + mapped[:2]))      # pdf table extraction
        qs.append(" ".join(en_tokens[:2] + mapped[:1]))      # pdf table
        qs.append(" ".join(mapped[:2]))                      # table extraction
        qs.append(" ".join(en_tokens[:2] + mapped[:3]))
    elif mapped:
        qs.append(" ".join(mapped[:2]))
        qs.append(" ".join(mapped[:3]))
    elif en_tokens:
        qs.append(" ".join(en_tokens[:4]))
    qs.append(str(gap))
    out, seen = [], set()
    for q in qs:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _dedupe(cands):
    seen, out = set(), []
    for c in cands:
        key = (c.get("source"), c.get("id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def discover(gap, limit_github=5, limit_hf=3):
    """返回候选列表：[{source,id,url,stars,desc,license}]

    中文缺口会自动生成英文检索词（GitHub/HF 检索主要吃英文）。
    ★ 优化：同缺口候选缓存 10 分钟（GitHub 未认证 60/h，重复计划不再搜）。
    """
    _dk = _plan_cache_key("discover:" + str(gap), 0)
    _dc = _plan_cache_get(_dk)
    if _dc:
        return list(_dc)
    out = []
    try:
        se = _search_tools()
    except Exception:
        return out
    queries = _search_queries(gap)
    for q in queries[:4]:
        try:
            repos = se.search_github(q, limit=limit_github) or []
        except Exception:
            repos = []
        for r in repos:
            out.append({
                "source": "github",
                "id": str(r.get("id") or ""),
                "url": str(r.get("url") or ""),
                "stars": int(r.get("stars") or 0),
                "desc": str(r.get("desc") or "")[:200],
                "license": str(r.get("license") or "").lower(),
                "updated": str(r.get("pushed_at") or r.get("updated") or ""),
            })
        if len(out) >= max(2, limit_github // 2):
            break
    try:
        hf = se.search_hf(queries[0], limit=limit_hf) or []
    except Exception:
        hf = []
    for r in hf:
        out.append({
            "source": "hf",
            "id": str(r.get("id") or r.get("model_id") or ""),
            "url": str(r.get("url") or ""),
            "stars": int(r.get("downloads") or r.get("likes") or 0),
            "desc": str(r.get("desc") or r.get("pipeline_tag") or "")[:200],
            "license": str(r.get("license") or "").lower(),
            "updated": str(r.get("lastModified") or ""),
        })
    _res = _dedupe(out)
    _plan_cache_put(_plan_cache_key("discover:" + str(gap), 0), _res)
    return _res


def _license_level(lic):
    lic = str(lic or "").strip().lower()
    if not lic or lic in ("none", "noassertion", "other", "unknown"):
        return "unknown"
    if any(a in lic for a in LICENSE_ALLOW):
        return "allow"
    if any(a in lic for a in LICENSE_CAUTION):
        return "caution"
    return "unknown"


def gate_candidate(cand):
    """政策闸门：允许/需复核/拒绝（不自动安装）"""
    lic = _license_level(cand.get("license"))
    stars = int(cand.get("stars") or 0)
    src = str(cand.get("source") or "")
    if lic == "caution":
        return "needs_review", "copyleft/受限许可（需进程边界或替换）"
    if lic == "unknown":
        return "needs_review", "许可不明（禁止自动批准）"
    if src not in SOURCE_ALLOW:
        return "needs_review", "来源不在白名单"
    if stars < AUTO_APPROVE_MIN_STARS:
        return "needs_review", "stars 低于自动批准阈值(%d)" % AUTO_APPROVE_MIN_STARS
    return "auto_approved", "许可允许 + 来源白名单 + stars≥%d" % AUTO_APPROVE_MIN_STARS


# ───────────────────── 云端复审（可选） ─────────────────────
def _extract_json(text):
    """从云端输出提取 JSON 对象（容忍 markdown / 前后解释 / 思考文本）"""
    t = str(text or "")
    if "```" in t:
        parts = t.split("```")
        # 优先 json 代码块
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:]
            i, j = p.find("{"), p.rfind("}")
            if i >= 0 and j > i:
                try:
                    return json.loads(p[i:j + 1])
                except Exception:
                    continue
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            return None
    return None


def _plan_cache_get(key):
    try:
        d = json.loads(PLAN_CACHE_FILE.read_text(encoding="utf-8"))
        rec = (d or {}).get(key)
        if rec and (time.time() - float(rec.get("ts") or 0)) < CACHE_TTL:
            return rec.get("plan")
    except Exception:
        pass
    return None


def _plan_cache_put(key, plan):
    try:
        CHIMERA_DIR.mkdir(parents=True, exist_ok=True)
        d = {}
        if PLAN_CACHE_FILE.exists():
            try:
                d = json.loads(PLAN_CACHE_FILE.read_text(encoding="utf-8")) or {}
            except Exception:
                d = {}
        d[key] = {"ts": time.time(), "plan": plan}
        PLAN_CACHE_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _plan_cache_key(gap, top_k):
    import hashlib
    return hashlib.sha1(("v1|%s|%s" % (gap, top_k)).encode()).hexdigest()[:16]


def _review_cache_key(gap, candidates, version="v2"):
    """★ 修（2026-10-26）：缓存 key 只用候选 id 集合（stars 会漂移，不应导致缓存失效）"""
    import hashlib
    ids = sorted(str(c.get("id") or "") for c in (candidates or []) if c.get("id"))
    raw = "%s|%s|%s" % (version, gap, "\n".join(ids))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _cache_get(key):
    try:
        d = json.loads(REVIEW_CACHE_FILE.read_text(encoding="utf-8"))
        rec = (d or {}).get(key)
        if rec and (time.time() - float(rec.get("ts") or 0)) < REVIEW_CACHE_TTL:
            return rec.get("result")
    except Exception:
        pass
    return None


def _cache_put(key, result):
    try:
        CHIMERA_DIR.mkdir(parents=True, exist_ok=True)
        d = {}
        if REVIEW_CACHE_FILE.exists():
            try:
                d = json.loads(REVIEW_CACHE_FILE.read_text(encoding="utf-8")) or {}
            except Exception:
                d = {}
        d[key] = {"ts": time.time(), "result": result}
        REVIEW_CACHE_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def cloud_review(gap, candidates):
    """若配置了云 provider key 则调用（Kimi）；输出 JSON 解析失败会重试一次

    ★ 优化：结果按 (gap+候选) 缓存 10 分钟（Kimi RPM=3，重复计划不再调用）。
    """
    _ck = _review_cache_key(gap, candidates)
    _cached = _cache_get(_ck)
    if _cached is not None:
        c = dict(_cached)
        c["cached"] = True
        return c
    try:
        import cloud_bridge
        if not cloud_bridge.available():
            return {"status": "skipped",
                    "reason": "云 provider 未配置 key（settings.providers.*.api_key 为空）"}
        prompt = (
            "你是奇美拉能力的云端评审。给定能力缺口和候选开源项目（含 stars/license），"
            "请评审并**只输出一个 JSON 对象**（不要 markdown、不要解释）："
            "{\"recommend\": [\"候选id\"], \"concerns\": [\"...\"], \"license_notes\": \"...\", "
            "\"sandbox_requirements\": [\"...\"], \"verdict\": \"approve|reject|need_more\"}\n"
            "缺口：%s\n候选：%s" % (gap, json.dumps(candidates[:6], ensure_ascii=False)[:3000])
        )
        last_raw = ""
        for attempt in range(2):
            ans = cloud_bridge.ask(
                prompt if attempt == 0 else (prompt + "\n注意：上一次输出无法解析成 JSON。请严格只输出 JSON 对象。"),
                system="你是严格的开源选型评审，只输出 JSON。", max_tokens=2500)
            last_raw = str(ans or "")
            data = _extract_json(last_raw)
            if isinstance(data, dict) and (data.get("verdict") or data.get("recommend") is not None):
                _res = {"status": "ok", "review": data}
                _cache_put(_ck, _res)
                return _res
        return {"status": "parse_error", "raw": last_raw[:1500]}
    except Exception as e:
        return {"status": "error", "reason": str(e)[:200]}


def plan(gap, top_k=5):
    gap = str(gap or "").strip()
    _cpk = _plan_cache_key(gap, top_k)
    _cached = _plan_cache_get(_cpk)
    if _cached:
        p = dict(_cached)
        p["cached_plan"] = True
        return p
    cands = discover(gap)[:max(1, int(top_k))]
    scored = []
    for c in cands:
        status, why = gate_candidate(c)
        c = dict(c)
        c["license_level"] = _license_level(c.get("license"))
        c["gate"] = status
        c["gate_reason"] = why
        c["candidate_id"] = hashlib.sha1(("%s:%s" % (c.get("source"), c.get("id"))).encode()).hexdigest()[:12]
        scored.append(c)
    scored.sort(key=lambda x: (x["gate"] != "auto_approved", -int(x.get("stars") or 0)))
    review = cloud_review(gap, scored)
    import hashlib as _hl
    plan_id = _hl.sha1(("%s|%s" % (gap, _now())).encode()).hexdigest()[:12]
    plan_obj = {
        "chimera_version": "v1",
        "created_at": _now(),
        "gap": gap,
        "candidates": scored,
        "plan_ir": {
            "steps": PLAN_STEPS,
            "sandbox": {
                "needs_network_during_install": True,
                "network_at_runtime": False,
                "permissions": ["read:task_input", "write:artifact_only"],
                "resource_limits": {"cpu": 1, "mem_mb": 2048, "wall_sec": 300},
            },
            "approval": "policy_gate + optional_cloud_review（不自动安装 ✓）",
        },
        "cloud_review": review,
        "auto_approved": [c["candidate_id"] for c in scored if c["gate"] == "auto_approved"],
        "needs_review": [c["candidate_id"] for c in scored if c["gate"] == "needs_review"],
        "next_action": "对 auto_approved 候选走沙箱 dry-run（P3），needs_review 需人工/云审通过",
        "plan_id": plan_id,
    }
    # ★ 奇美拉闭环（P2.7）：新能力自动入库（proposed；不参与路由，待 P3 安装后晋级）
    auto_registered = []
    try:
        import capabilities as _caps
        for c in scored:
            e = _caps.register_proposed_capability(c, gap=gap, plan_id=plan_id)
            auto_registered.append(e.get("cap_id"))
    except Exception:
        pass
    plan_obj["capabilities_registered"] = auto_registered
    # 落库（追加）
    try:
        _ensure_dir()
        with open(CANDIDATES_FILE, "a", encoding="utf-8") as f:
            for c in scored:
                rec = dict(c, gap=gap, stored_at=_now(),
                           cloud_review_status=review.get("status"))
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    _plan_cache_put(_plan_cache_key(gap, top_k), plan_obj)
    return plan_obj


def render_plan_text(p):
    if not p:
        return ""
    lines = ["【奇美拉缺口计划】缺口：%s" % p.get("gap")]
    cands = p.get("candidates") or []
    if not cands:
        lines.append("未检索到候选（可换个更具体的缺口描述，或检查网络/检索工具）。")
    for i, c in enumerate(cands[:5], 1):
        lines.append("%d. [%s] %s ⭐%s 许可=%s → %s" % (
            i, c.get("source"), c.get("id"), c.get("stars"),
            c.get("license") or "unknown", c.get("gate_reason")))
    lines.append("计划：%s" % " → ".join(s["step"] for s in (p.get("plan_ir") or {}).get("steps") or []))
    cr = p.get("cloud_review") or {}
    lines.append("云端复审：%s%s" % (cr.get("status"), ("：" + str(cr.get("reason"))[:120]) if cr.get("reason") else ""))
    lines.append("下一步：%s" % p.get("next_action"))
    return "\n".join(lines)


# ───────────────────── 候选查询/批准 ─────────────────────
def list_candidates(limit=50):
    if not CANDIDATES_FILE.exists():
        return []
    out = []
    for ln in CANDIDATES_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out[-int(limit):]


def get_candidate(candidate_id):
    for c in list_candidates(1000):
        if c.get("candidate_id") == candidate_id:
            return c
    return None


def candidate_capability_id(cand):
    """候选 → 能力注册表里的 cap_id（与 capabilities.register_proposed_capability 一致）"""
    import re as _re
    slug = _re.sub(r"[^0-9A-Za-z_\-]+", "-", str(cand.get("id") or "")).strip("-")[:64]
    return "ext:%s:%s" % (str(cand.get("source") or "?"), slug)


def approve_candidate(candidate_id, approved_by="policy"):
    """把候选标记为 approved（写回 jsonl 末尾追加一条 approve 记录）"""
    c = get_candidate(candidate_id)
    if not c:
        raise ValueError("候选不存在: %s" % candidate_id)
    _ensure_dir()
    rec = {"candidate_id": candidate_id, "action": "approve", "by": approved_by, "at": _now()}
    with open(CANDIDATES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # ★ 同步能力注册表：proposed → approved_pending_install（等 P3 安装）
    try:
        import capabilities as _caps
        _cid = candidate_capability_id(c)
        if _caps.get_capability(_cid):
            _caps.set_status(_cid, "approved_pending_install")
            rec["capability_id"] = _cid
    except Exception:
        pass
    return rec
