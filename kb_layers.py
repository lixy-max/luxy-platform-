# -*- coding: utf-8 -*-
"""S24 · 知识库分层与挂载策略（KB Layers）

L0 能力基线（必读，代码查询 → 压缩摘要）
L0.5 策略安全（必读，硬规则摘要）
L1 领域知识（按任务域挂载）
L2 专业细化（仅对应能力/LoRA 挂载，工蜂 ACL 隔离）
L3 数据型知识库（供应商清单/Excel/DB：只给 schema，工具+ACL 读取，不 embed）
L4 云端能力说明书（Manifest：缺口升级评估时读）

产出：context_bundle(task, capability_plan) → 必读上下文 + 挂载清单（已 ACL 过滤）
"""
import json
import os
import pathlib
import time

PROJECT_DIR = pathlib.Path(__file__).resolve().parent
DATA_SOURCES = pathlib.Path(os.environ.get(
    "HIVE_DATA_SOURCES", str(PROJECT_DIR / "data" / "hive" / "data_sources.json")))
MANIFEST_DIR = pathlib.Path(os.environ.get(
    "HIVE_MANIFEST_DIR", str(PROJECT_DIR / "data" / "hive" / "manifests")))

# 任务域 → L1 知识库（可配置）
DOMAIN_KB_MAP = {
    "procurement": ["qiaojiao_toys_2025_v2", "bcpcn"],
    "medical": ["medical_guidelines"],
    "tool": ["kb_toolchain", "tool_docs"],
    "paper": ["ab_clean_v2", "ab_clean_20261021"],
    "skill": ["learned_skills"],
    "contract": [],
    "agri": [],
}

# 能力 → L2 专业库（仅该能力/LoRA 激活时挂载）
CAP_KB_MAP = {
    "knowledge.procurement": ["qiaojiao_toys_2025_v2"],
    "knowledge.medical": ["medical_guidelines"],
    "knowledge.paper": ["ab_clean_v2"],
    "tool.*": ["kb_toolchain"],
    "skill.workflow_build": ["tool_docs"],
    "skill.json_extract": [],
    "skill.sheet_ops": [],
}

# 工蜂 ACL：worker → 允许挂载的 KB（缺失 → 默认允许公共库）
WORKER_ACL = {
    "w1": ["qiaojiao_toys_2025_v2", "bcpcn", "medical_guidelines", "kb_toolchain",
           "tool_docs", "learned_skills", "ab_clean_v2", "ab_clean_20261021"],
    "*": [],
}
PUBLIC_KBS = ["kb_toolchain", "tool_docs", "learned_skills"]

# L0.5 策略（硬规则；可被 settings/env 覆盖）
POLICY = {
    "data_boundary": "敏感数据不出机；云端只传任务描述/摘要，不传原始数据",
    "local_first": "能用本地能力就不上云；本地失败/缺口才升级",
    "budget": {"daily_cloud_calls": int(os.environ.get("HIVE_CLOUD_BUDGET", "200"))},
    "risk": {"high_risk_force_cloud": ["合同", "法规", "医疗", "财务", "税务"],
             "sim_keywords": ["压测", "并发", "模拟", "合成数据", "脏数据"]},
    "self_modify": "新增/修改能力需走 Manifest + 沙箱 + 评测门禁（S25+）",
}


def _registry_summary(max_items=18):
    try:
        import capability_registry as cr
        caps = cr.list_caps(status="active", limit=500)
        by_kind = {}
        for c in caps:
            by_kind.setdefault(c.get("kind"), []).append(c.get("cap_id"))
        lines = ["【能力基线 L0（已入库，代码查询）】"]
        for kind in ("lora", "tool", "workflow", "knowledge", "model", "data"):
            items = by_kind.get(kind) or []
            if items:
                lines.append("· %s: %s" % (kind, "、".join(items[:max_items])))
        lines.append("· 未列出的能力=未入库，需走缺口流程（tool/knowledge/model/data）")
        return "\n".join(lines)[:1800]
    except Exception as e:
        return "【能力基线 L0】不可用: %s" % str(e)[:100]


def l05_summary():
    p = POLICY
    return ("【策略 L0.5（必读，硬规则）】\n"
            "· 数据边界: %s\n· 本地优先: %s\n· 预算: 云端日调用上限 %d\n"
            "· 高风险任务(合同/法规/医疗/财务)强制云端；压测/脏数据强制 Sim\n"
            "· 自我修改: %s" % (p["data_boundary"], p["local_first"],
                                 p["budget"]["daily_cloud_calls"], p["self_modify"]))[:900]


def mandatory_context():
    """每次路由必读：L0 能力基线 + L0.5 策略（压缩，≤2.5k 字符）"""
    return (l05_summary() + "\n\n" + _registry_summary())[:2600]


# ══════════════════════════════════════════════════════════════
# 数据型知识库（L3）：只给 schema，不 embed
# ══════════════════════════════════════════════════════════════
def _default_sources():
    return [
        {"id": "local_purchase_excel", "name": "本地采购清单 Excel", "type": "excel",
         "path": "uploads/采购清单*.xlsx",
         "schema": {"columns": ["序号", "商品名称", "规格", "数量", "单价", "小计", "来源平台", "备注"]},
         "owner": "local", "freshness": "static", "acl": ["w1"], "sensitivity": "low"},
        {"id": "local_supplier_list", "name": "供应商清单（本地）", "type": "excel",
         "path": "uploads/supplier_list.xlsx",
         "schema": {"columns": ["供应商", "联系人", "电话", "主营", "参考单价", "交期(天)"]},
         "owner": "local", "freshness": "manual", "acl": ["w1"], "sensitivity": "medium"},
        {"id": "local_uploads_dir", "name": "本地上传目录", "type": "files",
         "path": "uploads/", "schema": {"files": "*.xlsx,*.csv,*.pdf,*.docx"},
         "owner": "local", "freshness": "dynamic", "acl": ["w1"], "sensitivity": "low"},
    ]


def load_data_sources():
    try:
        if DATA_SOURCES.exists():
            return json.loads(DATA_SOURCES.read_text(encoding="utf-8"))
    except Exception:
        pass
    data = _default_sources()
    try:
        DATA_SOURCES.parent.mkdir(parents=True, exist_ok=True)
        DATA_SOURCES.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return data


def register_data_source(src):
    if not src or not src.get("id"):
        return {"ok": False, "error": "id 必填"}
    src.setdefault("type", "files")
    src.setdefault("schema", {})
    src.setdefault("acl", ["w1"])
    src.setdefault("sensitivity", "low")
    data = load_data_sources()
    data = [d for d in data if d.get("id") != src["id"]] + [src]
    try:
        DATA_SOURCES.parent.mkdir(parents=True, exist_ok=True)
        DATA_SOURCES.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "id": src["id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def data_sources(worker=None):
    data = load_data_sources()
    if not worker:
        return data
    return [d for d in data if worker in (d.get("acl") or []) or "*" in (d.get("acl") or [])]


def data_context_summary(worker=None, max_chars=800):
    srcs = data_sources(worker)
    if not srcs:
        return ""
    lines = ["【数据型知识库 L3（只给 schema，经工具读取，不 embed）】"]
    for d in srcs[:8]:
        cols = (d.get("schema") or {}).get("columns") or (d.get("schema") or {}).get("files")
        lines.append("· %s（%s）%s | 列/文件: %s" % (
            d.get("name"), d.get("type"), d.get("path"),
            "、".join(cols) if isinstance(cols, list) else str(cols)))
    return "\n".join(lines)[:max_chars]


# ══════════════════════════════════════════════════════════════
# ACL / 挂载
# ══════════════════════════════════════════════════════════════
def allowed_kbs(worker="w1"):
    acl = WORKER_ACL.get(worker) or []
    return list(dict.fromkeys(list(acl) + PUBLIC_KBS))


def _domain_of(task):
    low = str(task or "").lower()
    if any(k in low for k in ("采购", "供应商", "比价")):
        return "procurement"
    if any(k in low for k in ("医疗", "诊断", "用药")):
        return "medical"
    if any(k in low for k in ("论文", "文献", "arxiv")):
        return "paper"
    if any(k in low for k in ("工具", "工作流", "平台", "api")):
        return "tool"
    if any(k in low for k in ("技能", "做法", "经验")):
        return "skill"
    if any(k in low for k in ("合同", "法规", "条款")):
        return "contract"
    if any(k in low for k in ("农业", "作物", "种植")):
        return "agri"
    return ""


def kbs_for_capability(capability):
    caps = CAP_KB_MAP.get(capability)
    if caps is not None:
        return list(caps)
    for pattern, kbs in CAP_KB_MAP.items():
        if pattern.endswith(".*") and capability.startswith(pattern[:-1]):
            return list(kbs)
    return []


def context_bundle(task, capability_plan=None, worker="w1"):
    """返回分层上下文与挂载清单（已按 worker ACL 过滤）"""
    plan = capability_plan or {}
    domain = _domain_of(task) or (plan.get("domain") or "")
    allowed = allowed_kbs(worker)
    l1 = [k for k in (DOMAIN_KB_MAP.get(domain) or []) if k in allowed]
    caps = [x.get("capability") for x in (plan.get("capability_plan") or [])]
    l2 = []
    for cap in caps:
        for k in kbs_for_capability(cap):
            if k in allowed and k not in l2 and k not in l1:
                l2.append(k)
    dropped = [k for k in (DOMAIN_KB_MAP.get(domain) or []) if k not in allowed]
    data = data_sources(worker)
    manifests = list_manifests() if plan.get("gaps") else []
    ctx = {
        "task": str(task or "")[:300],
        "layers": {
            "L0_L05_mandatory": mandatory_context(),
            "L1_domain": {"domain": domain, "kbs": l1},
            "L2_specialist": {"capabilities": caps, "kbs": l2},
            "L3_data": {"sources": [{"id": d.get("id"), "name": d.get("name"),
                                     "type": d.get("type"),
                                     "columns": (d.get("schema") or {}).get("columns")}
                                    for d in data]},
            "L4_cloud_manifests": manifests[:5],
        },
        "mount": {"worker": worker, "must_read": ["L0", "L0.5"],
                  "l1": l1, "l2": l2,
                  "data": [d.get("id") for d in data],
                  "dropped_by_acl": dropped},
        "token_estimate": int((len(mandatory_context()) + sum(len(k) for k in l1 + l2)) / 1.6),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return ctx


def list_manifests(limit=20):
    out = []
    try:
        for p in sorted(MANIFEST_DIR.glob("*.json"))[:limit]:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"file": p.name, "capability_id": d.get("capability_id"),
                            "name": d.get("name"), "type": d.get("type")})
            except Exception:
                continue
    except Exception:
        pass
    return out


def stats():
    import capability_registry as cr
    return {"domain_map": {k: len(v) for k, v in DOMAIN_KB_MAP.items()},
            "cap_kb_map": len(CAP_KB_MAP), "data_sources": len(load_data_sources()),
            "workers_acl": {k: len(v) for k, v in WORKER_ACL.items()},
            "capabilities": cr.stats().get("total"),
            "manifests": len(list_manifests(100)),
            "policy": {"daily_cloud_calls": POLICY["budget"]["daily_cloud_calls"]}}
