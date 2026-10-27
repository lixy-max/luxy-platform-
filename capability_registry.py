# -*- coding: utf-8 -*-
"""S24 · 能力基线库（Capability Registry）

能力体系的"唯一事实源"：不是 embedding 知识库，而是结构化注册表。
- 登记：LoRA / 工具 / 知识库 / 工作流 / 云端模型 / 数据源
- 字段：kind/status/scope/tags/io/requires/evidence/limits/mount/version/source
- 判定：任务 → capability 需求（规则）→ 注册表查询 → 已入库/部分/缺口（tool|knowledge|model|data）
- 供：decide 输出 capability_plan/gap_tags；KB 分层挂载；Manifest 吸收后注册

存储：data/hive/capability.db（SQLite）
"""
import json
import os
import pathlib
import re
import sqlite3
import time

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
DB_PATH = pathlib.Path(os.environ.get("HIVE_CAP_DB", str(PROJECT_DIR / "data" / "hive" / "capability.db")))

VALID_KINDS = ("lora", "tool", "knowledge", "workflow", "model", "data")
VALID_STATUS = ("active", "shadow", "pending", "broken", "deprecated")

# 任务关键词 → 能力需求（规则；LLM 只做候选受限选择，不做本判定）
CAP_RULES = [
    (r"(json|结构化|schema|字段|抽取|格式化输出)", ["skill.json_extract"]),
    (r"(表格|excel|xlsx|清单|排序|汇总|导出)", ["skill.sheet_ops"]),
    (r"(采购|供应商|比价)", ["skill.sheet_ops", "knowledge.procurement", "data.supplier_list"]),
    (r"(合同|法规|法律|条款|合规)", ["knowledge.contract", "skill.compare"]),
    (r"(论文|文献|arxiv|研究)", ["knowledge.paper", "skill.analyze"]),
    (r"(医疗|诊断|用药|疾病)", ["knowledge.medical"]),
    (r"(农业|作物|种植|病虫害)", ["knowledge.agri"]),
    (r"(意图|分类|语义)", ["skill.intent_classify"]),
    (r"(压测|并发|脏数据|合成数据|仿真)", ["sim.load", "sim.dirty"]),
    (r"(工作流|workflow|搭建|蒸馏)", ["skill.workflow_build"]),
    (r"(训练|lora|微调|finetune)", ["skill.train"]),
    (r"(图像|图片|视频|多模态)", ["model.multimodal"]),
    (r"(最新|实时|今天|新闻|联网|搜索一下|行情|股价|天气|20(?:25|26)年)", ["tool.browser", "tool.search"]),
    (r"(读取|打开|上传|excel|pdf|docx|文件)", ["tool.read_file"]),
    (r"(知识库|检索|rag|问答)", ["tool.kb_query"]),
]

GAP_KIND_HINTS = {
    "knowledge.": "knowledge", "data.": "data", "tool.": "tool",
    "model.": "model", "skill.json_extract": "model", "skill.sheet_ops": "tool",
    "skill.compare": "model", "skill.analyze": "model", "skill.intent_classify": "model",
    "skill.workflow_build": "tool", "skill.train": "tool",
    "sim.load": "tool", "sim.dirty": "tool",
}


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS capabilities(
        cap_id TEXT PRIMARY KEY, kind TEXT, name TEXT, status TEXT, scope TEXT,
        tags TEXT, io TEXT, requires TEXT, evidence TEXT, limits TEXT, mount TEXT,
        version TEXT, source TEXT, updated_at REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_kind ON capabilities(kind)")
    return c


def _row_to_dict(r):
    d = dict(r)
    for k in ("tags", "io", "requires", "evidence", "limits", "mount"):
        try:
            d[k] = json.loads(d[k] or "{}")
        except Exception:
            d[k] = {}
    return d


def register(cap):
    """登记/更新一个能力（cap_id 必填）"""
    if not cap or not cap.get("cap_id"):
        return {"ok": False, "error": "cap_id 必填"}
    kind = str(cap.get("kind") or "")
    if kind not in VALID_KINDS:
        return {"ok": False, "error": "kind 非法: %r（可选 %s）" % (kind, list(VALID_KINDS))}
    status = str(cap.get("status") or "active")
    if status not in VALID_STATUS:
        return {"ok": False, "error": "status 非法: %r" % status}
    now = time.time()
    try:
        with _conn() as c:
            c.execute("""INSERT INTO capabilities(cap_id,kind,name,status,scope,tags,io,requires,
                        evidence,limits,mount,version,source,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(cap_id) DO UPDATE SET
                        kind=excluded.kind,name=excluded.name,status=excluded.status,
                        scope=excluded.scope,tags=excluded.tags,io=excluded.io,
                        requires=excluded.requires,evidence=excluded.evidence,
                        limits=excluded.limits,mount=excluded.mount,version=excluded.version,
                        source=excluded.source,updated_at=excluded.updated_at""",
                      (cap["cap_id"], kind, str(cap.get("name") or cap["cap_id"]), status,
                       str(cap.get("scope") or "local"),
                       json.dumps(cap.get("tags") or [], ensure_ascii=False),
                       json.dumps(cap.get("io") or {}, ensure_ascii=False),
                       json.dumps(cap.get("requires") or {}, ensure_ascii=False),
                       json.dumps(cap.get("evidence") or {}, ensure_ascii=False),
                       json.dumps(cap.get("limits") or {}, ensure_ascii=False),
                       json.dumps(cap.get("mount") or {}, ensure_ascii=False),
                       str(cap.get("version") or "v1"), str(cap.get("source") or "manual"),
                       now))
            c.commit()
        return {"ok": True, "cap_id": cap["cap_id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def get(cap_id):
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM capabilities WHERE cap_id=?", (cap_id,)).fetchone()
        return _row_to_dict(r) if r else None
    except Exception:
        return None


def list_caps(kind=None, status=None, tag=None, limit=500):
    q = "SELECT * FROM capabilities WHERE 1=1"
    args = []
    if kind:
        q += " AND kind=?"; args.append(kind)
    if status:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY kind, cap_id LIMIT ?"; args.append(int(limit))
    try:
        with _conn() as c:
            rows = [_row_to_dict(r) for r in c.execute(q, args).fetchall()]
    except Exception:
        return []
    if tag:
        rows = [r for r in rows if tag in (r.get("tags") or [])]
    return rows


def set_status(cap_id, status):
    if status not in VALID_STATUS:
        return {"ok": False, "error": "status 非法"}
    try:
        with _conn() as c:
            n = c.execute("UPDATE capabilities SET status=?, updated_at=? WHERE cap_id=?",
                          (status, time.time(), cap_id)).rowcount
            c.commit()
        return {"ok": bool(n), "updated": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def delete(cap_id):
    try:
        with _conn() as c:
            n = c.execute("DELETE FROM capabilities WHERE cap_id=?", (cap_id,)).rowcount
            c.commit()
        return {"ok": bool(n), "deleted": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ══════════════════════════════════════════════════════════════
# 规则：任务 → capability 需求 / 缺口
# ══════════════════════════════════════════════════════════════
def required_capabilities(task):
    low = str(task or "").lower()
    req = []
    for pat, caps in CAP_RULES:
        if re.search(pat, low):
            for cap in caps:
                if not any(r["capability"] == cap for r in req):
                    req.append({"capability": cap, "importance": "required",
                                "evidence": "规则:" + pat[:20]})
    return req


def _tokens(s):
    return [t for t in re.split(r"[._\-\s]+", str(s or "").lower()) if t]


def _match_one(capability):
    """在注册表里找匹配：cap_id 精确 / token 与 tags 或 cap_id 重叠"""
    caps = list_caps(limit=2000)
    exact = [c for c in caps if c.get("cap_id") == capability]
    if exact:
        return exact
    toks = set(_tokens(capability))
    out = []
    for c in caps:
        tags = set(t.lower() for t in (c.get("tags") or []))
        cid = set(_tokens(c.get("cap_id")))
        nm = set(_tokens(c.get("name")))
        if (toks & tags) or (toks & cid) or (toks & nm):
            out.append(c)
    return out


def query(required):
    """返回 matches + gaps + verdict + gap_tags"""
    matches, gaps = [], []
    for r in required:
        cap = r["capability"]
        cands = _match_one(cap)
        active = [c for c in cands if c.get("status") == "active"]
        if active:
            best = sorted(active, key=lambda c: -float((c.get("evidence") or {}).get("quality") or 0))[0]
            matches.append({"capability": cap, "matched_by": best.get("cap_id"),
                            "kind": best.get("kind"), "scope": best.get("scope") or "local",
                            "quality": (best.get("evidence") or {}).get("quality")})
        else:
            kind = GAP_KIND_HINTS.get(cap) or GAP_KIND_HINTS.get(cap.rsplit(".", 1)[0] + ".") or "model"
            gaps.append({"capability": cap, "kind": kind,
                         "importance": r.get("importance", "required"),
                         "candidates": [c.get("cap_id") for c in cands][:5]})
    total = max(1, len(required))
    coverage = round(len(matches) / total, 3)
    gap_tags = sorted({g["capability"] for g in gaps})
    local_matches = [m for m in matches if m.get("scope") != "cloud"]
    cloud_matches = [m for m in matches if m.get("scope") == "cloud"]
    sim_gap = bool(gaps) and all("sim" in g["capability"] for g in gaps)
    sim_req = any(r["capability"].startswith("sim.") for r in required)
    hard_gaps = [g for g in gaps if g.get("kind") in ("knowledge", "model", "data")]
    if not required:
        verdict = "unknown"
    elif sim_req or sim_gap:
        # 压测/仿真类任务优先走 Sim
        verdict = "sim"
    elif hard_gaps:
        # 知识/模型/数据型缺口不能靠本地 skill 顶替 → 走云端
        verdict = "cloud"
    elif not gaps and len(local_matches) == len(matches):
        verdict = "local"
    elif not gaps and cloud_matches:
        verdict = "cloud"
    elif coverage >= 0.5:
        verdict = "partial"
    else:
        verdict = "cloud"
    return {"required": required, "matches": matches, "gaps": gaps,
            "coverage": coverage, "gap_tags": gap_tags, "verdict": verdict}


def plan_for_task(task, worker_status=None):
    req = required_capabilities(task)
    q = query(req)
    # 本地资源可用性提示（供路由参考，不强制覆盖 L1）
    resource = {}
    try:
        if worker_status is None:
            import hive_worker_pool as _wp
            worker_status = _wp.worker_status()
        resource = {"worker_ready": bool(worker_status.get("ready")),
                    "base_model": worker_status.get("base_model"),
                    "loaded": worker_status.get("loaded"),
                    "slots_free": worker_status.get("slots_free")}
    except Exception:
        resource = {"worker_ready": None}
    if q["verdict"] == "partial" and resource.get("worker_ready") is False:
        q["verdict"] = "cloud"
    return {"task": str(task or "")[:300],
            "capability_plan": [{"capability": r["capability"],
                                "importance": r["importance"]} for r in req],
            "matches": q["matches"], "gaps": q["gaps"],
            "coverage": q["coverage"], "gap_tags": q["gap_tags"],
            "verdict": q["verdict"], "resource": resource,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def stats():
    caps = list_caps(limit=5000)
    by_kind, by_status = {}, {}
    for c in caps:
        by_kind[c.get("kind")] = by_kind.get(c.get("kind"), 0) + 1
        by_status[c.get("status")] = by_status.get(c.get("status"), 0) + 1
    return {"db": str(DB_PATH), "total": len(caps),
            "by_kind": by_kind, "by_status": by_status}


# ══════════════════════════════════════════════════════════════
# 种子：把平台现有能力登记进注册表
# ══════════════════════════════════════════════════════════════
def _tags_of(name):
    low = str(name).lower()
    tags = []
    rules = [("json", "json"), ("extract", "extract"), ("sheet", "sheet"),
             ("excel", "sheet"), ("csv", "sheet"), ("workflow", "workflow"),
             ("lora", "lora"), ("train", "train"), ("browser", "browser"),
             ("search", "search"), ("paper", "paper"), ("medical", "medical"),
             ("agri", "agri"), ("kb", "knowledge"), ("query", "knowledge"),
             ("contract", "contract"), ("purchase", "procurement"),
             ("procurement", "procurement"), ("persona", "persona"),
             ("intent", "intent"), ("drill", "drill")]
    for pat, tag in rules:
        if pat in low and tag not in tags:
            tags.append(tag)
    return tags


def seed_from_platform():
    """把 LoRA/工具/KB/工作流/云端模型登记进能力基线库（幂等）"""
    stats_out = {"lora": 0, "tool": 0, "knowledge": 0, "workflow": 0, "model": 0, "data": 0}
    # ① LoRA
    try:
        import hive_lora_registry as lr
        for name, e in lr.load_registry().items():
            caps = e.get("capability") or ["general"]
            tags = list(dict.fromkeys(caps + _tags_of(name)))
            vram = round(0.2 + (e.get("rank") or 16) * 0.01, 2)
            r = register({"cap_id": "lora." + name, "kind": "lora", "name": name,
                          "status": "active" if e.get("status") == "active" else "shadow",
                          "scope": "local", "tags": tags,
                          "io": {"input": "text", "output": "text"},
                          "requires": {"base_model": e.get("family"),
                                       "rank": e.get("rank"), "vram_gb": vram},
                          "evidence": {"quality": e.get("score"), "rank": e.get("rank")},
                          "limits": {"family": e.get("family")},
                          "mount": {"nodes": ["w1"], "auto_load": True},
                          "version": e.get("version") or "v1",
                          "source": "lora_registry"})
            if r.get("ok"):
                stats_out["lora"] += 1
    except Exception:
        pass
    # ② 工具（workflow_engine 工具节点 + 平台常用工具）
    try:
        import workflow_engine as we
        tools = dict(we.TOOL_NODE_SUPPORTED)
    except Exception:
        tools = {}
    for extra in ("read_sheet", "write_sheet", "kb_query", "browser_open",
                  "browser_read", "academic_search", "run_simulation"):
        tools.setdefault(extra, {})
    for name, spec in tools.items():
        r = register({"cap_id": "tool." + name, "kind": "tool", "name": name,
                      "status": "active", "scope": "local",
                      "tags": _tags_of(name) or ["tool"],
                      "io": {"method": (spec or {}).get("method"),
                             "path": (spec or {}).get("path")},
                      "requires": {"platform": True}, "evidence": {"quality": 1.0},
                      "limits": {}, "mount": {"nodes": ["w1"]},
                      "version": "v1", "source": "workflow_engine"})
        if r.get("ok"):
            stats_out["tool"] += 1
    # ③ 知识库
    try:
        import kb
        for name in kb.list_kbs():
            meta = kb.get_kb(name) if isinstance(name, str) else (name or {})
            if not isinstance(meta, dict):
                continue
            nm = meta.get("name") or str(name)
            chunks = int(meta.get("total_chunks") or 0)
            register({"cap_id": "knowledge." + nm, "kind": "knowledge", "name": nm,
                      "status": "active" if chunks > 0 else "pending", "scope": "local",
                      "tags": _tags_of(nm + " " + str(meta.get("description") or "")) or ["knowledge"],
                      "io": {"output": "chunks"},
                      "requires": {"chunks": chunks},
                      "evidence": {"quality": min(1.0, chunks / 50.0)},
                      "limits": {"chunks": chunks},
                      "mount": {"nodes": ["w1"], "layer": "L1"},
                      "version": "v1", "source": "platform_kb"})
            if chunks > 0:
                stats_out["knowledge"] += 1
    except Exception:
        pass
    # ④ 工作流
    try:
        wf = PROJECT_DIR / "data" / "distilled" / "workflows.jsonl"
        seen = set()
        if wf.exists():
            for ln in wf.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                wid = d.get("wf_id")
                if not wid or wid in seen:
                    continue
                seen.add(wid)
                nm = d.get("workflow_name") or d.get("name") or wid
                req = d.get("request") or ""
                register({"cap_id": "workflow." + wid, "kind": "workflow", "name": nm,
                          "status": "active", "scope": "local",
                          "tags": _tags_of(nm + " " + req) or ["workflow"],
                          "io": {"input": "text", "output": "excel/text"},
                          "requires": {"engine": "workflow_engine"},
                          "evidence": {"quality": 0.8},
                          "limits": {}, "mount": {"nodes": ["w1"]},
                          "version": "v1", "source": "distilled"})
                stats_out["workflow"] += 1
    except Exception:
        pass
    # ⑤ 云端模型
    try:
        st = json.loads((PROJECT_DIR / "settings.json").read_text(encoding="utf-8"))
        provs = st.get("providers") or {}
        for name, cfg in provs.items():
            if not isinstance(cfg, dict) or not str(cfg.get("api_key") or "").strip():
                continue
            if name in ("vllm", "ollama"):
                continue
            mods = cfg.get("modalities") or ["text"]
            register({"cap_id": "model.cloud." + name, "kind": "model", "name": name,
                      "status": "active", "scope": "cloud",
                      "tags": mods + ["cloud"],
                      "io": {"input": mods, "output": "text"},
                      "requires": {"api": True},
                      "evidence": {"quality": 0.85},
                      "limits": {}, "mount": {"channel": "cloud_teacher"},
                      "version": str(cfg.get("model") or "v1"), "source": "settings"})
            stats_out["model"] += 1
    except Exception:
        pass
    # ⑥ Sim 场景（作为能力登记）
    try:
        import hive_sim as _sim
        for sc in (_sim.SIM_SCENARIOS or {}):
            tags = ["sim"] + [t for t in ("load", "dirty") if t in sc]
            register({"cap_id": "sim." + sc, "kind": "tool", "name": sc,
                      "status": "active", "scope": "local", "tags": tags,
                      "io": {"scenario": sc}, "requires": {"sim_node": True},
                      "evidence": {"quality": 1.0}, "limits": {},
                      "mount": {"nodes": ["w1"], "isolated": True},
                      "version": "v1", "source": "hive_sim"})
    except Exception:
        pass
    # ⑦ 数据型知识库（L3）
    try:
        import kb_layers as _kbl
        for d in _kbl.load_data_sources():
            tags = ["data"] + _tags_of(str(d.get("name")) + " " + str(d.get("id")))
            register({"cap_id": "data." + str(d["id"]), "kind": "data",
                      "name": d.get("name"), "status": "active", "scope": "local",
                      "tags": tags, "io": {"type": d.get("type"),
                                           "schema": d.get("schema")},
                      "requires": {"acl": d.get("acl")},
                      "evidence": {"quality": 1.0},
                      "limits": {"sensitivity": d.get("sensitivity")},
                      "mount": {"nodes": d.get("acl") or ["w1"], "layer": "L3"},
                      "version": "v1", "source": "data_sources"})
            stats_out["data"] = stats_out.get("data", 0) + 1
    except Exception:
        pass
    # ⑧ 云端模型补充 multimodal 标签
    for c in list_caps(kind="model", status="active"):
        mods = (c.get("tags") or [])
        if any(m in ("vision", "image", "video") for m in mods):
            tags = list(dict.fromkeys(mods + ["multimodal"]))
            c2 = dict(c); c2["tags"] = tags
            c2.pop("updated_at", None)
            register(c2)
    return {"ok": True, "seeded": stats_out, "stats": stats()}
