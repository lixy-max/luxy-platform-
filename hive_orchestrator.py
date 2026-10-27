# -*- coding: utf-8 -*-
"""S18 · 蜂巢中枢调度器（Hive Orchestrator）

职责：请求拦截 → 意图识别 → 任务拆解 → **三路由决策**（local / cloud / sim）
     → 严格 JSON（JSON Schema 约束）→ 校验/修复 → 规则兜底 → 决策审计。

设计依据：docs/HIVE_ARCHITECTURE_UNDERSTANDING.md（计划架构图 §1/§4）
- LLM：本地主脑 14B AWQ（vLLM OpenAI 兼容）
- INVENTORY 运行时注入：workers / loras / cloud / sim / tools / kbs / policy
- 硬约束：target ∈ {local, cloud, sim}；sim 关键词强制 sim；local 必须给 worker+lora；
  cloud 必须给 provider+model；fallback_chain 非空；JSON 非法重试 ≤ N 次 → 规则兜底
- 审计：data/hive/decisions.jsonl（请求摘要/决策/延迟/重试/是否兜底）
"""
import json
import os
import re
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
HIVE_DIR = PROJECT_DIR / "data" / "hive"
DECISIONS_FILE = HIVE_DIR / "decisions.jsonl"

LLM_BASE = os.environ.get("HIVE_LLM_URL", "http://127.0.0.1:8000/v1")
LLM_MODEL = os.environ.get("HIVE_MODEL", "")
LLM_TIMEOUT = float(os.environ.get("HIVE_LLM_TIMEOUT_S", "180"))
MAX_RETRIES = int(os.environ.get("HIVE_MAX_RETRIES", "2"))
MAX_TOKENS = int(os.environ.get("HIVE_MAX_TOKENS", "1200"))
# 仿真压测会高频走 S1 规则 → 审计按 N 条采样一次，避免日志刷爆
_AUDIT_SIM_N = max(1, int(os.environ.get("HIVE_SIM_AUDIT_EVERY", "50")))
_audit_sim_counter = 0

# Sim 关键词（政策 S1：命中即 sim，不交给 LLM 猜）
SIM_KEYWORDS = [
    "压测", "压力测试", "并发测试", "并发", "模拟", "仿真", "合成数据", "脏数据",
    "造数据", "生成测试数据", "stress test", "load test", "simulate",
]
# 本地特征（规则兜底用）
LOCAL_HINTS = [
    "列出", "列一下", "查一下", "看一下", "有哪些", "几个", "当前", "现在", "状态",
    "配置", "设置", "读一下", "打开", "写一个", "新建", "删掉", "跑一下", "执行",
    "启动", "停掉", "工具", "知识库", "表格", "excel", "xlsx", "排序", "汇总",
]
# 云端特征（规则兜底用）
CLOUD_HINTS = [
    "最新", "最近", "2025", "2026", "论文", "研究", "文献", "arxiv", "复杂推理",
    "多步", "规划", "分析", "趋势", "联网", "搜索", "网页", "京东", "淘宝", "天猫",
    "阿里巴巴", "1688", "爬取", "抓取", "云端模型", "deepseek", "gpt", "大模型api",
    "合同", "法规", "长文", "报告",
]

TIME_SENSITIVE_RE = re.compile(
    r"(最新|实时|今天|本周|本月|新闻|联网|搜索一下|行情|股价|天气)"
    r"|20(?:25|26|27)\s*年|arxiv|arXiv")

HIGH_RISK_RE = re.compile(
    r"(合同|法规|法律|医疗|诊断|财务|税务|合规).{0,20}(比对|审查|核对|建议|风险|条款)"
    r"|(比对|审查).{0,20}(合同|法规|条款)")

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {"type": "string", "enum": ["local", "cloud", "sim"]},
        "confidence": {"type": "number"},
        "task_type": {"type": "string"},
        "intent_summary": {"type": "string"},
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "desc": {"type": "string"},
                    "executor": {"type": "string"},
                },
                "required": ["id", "desc", "executor"],
            },
        },
        "routing": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string"},
                "lora_id": {"type": "string"},
                "why": {"type": "string"},
                "timeout_s": {"type": "integer"},
                "max_retries": {"type": "integer"},
                "escalate": {"type": "boolean"},
            },
            "required": ["why"],
        },
        "cloud": {
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "model": {"type": "string"},
                "context_policy": {"type": "string"},
                "why": {"type": "string"},
                "est_cost": {"type": "string"},
            },
        },
        "sim": {
            "type": "object",
            "properties": {
                "scenario": {"type": "string"},
                "users": {"type": "integer"},
                "duration_s": {"type": "integer"},
                "dirty_policy": {"type": "string"},
                "report_to": {"type": "string"},
            },
        },
        "fallback_chain": {"type": "array", "items": {"type": "string"}},
        "needs_clarification": {"type": "boolean"},
        "reason_trace": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["target", "confidence", "task_type", "intent_summary", "subtasks",
                 "routing", "cloud", "sim", "fallback_chain", "needs_clarification",
                 "reason_trace"],
}

SYSTEM_PROMPT = """你是「蜂巢中枢调度器」（Hive Orchestrator），运行在本地 14B 模型上。
你的唯一职责：把用户请求路由到三种执行体之一，并输出**严格 JSON**：
  - local：本地工蜂 + 动态 LoRA（快、数据不出机）
  - cloud：云端教师（能力强、有成本、需联网）
  - sim：仿真节点（压测/并发/合成数据/脏数据隔离，不消耗推理）

【硬约束】
1) 只输出一个 JSON 对象，不要 markdown、解释或多余文本。
2) 只能使用 INVENTORY 中真实存在的 worker/lora/provider/scenario，禁止编造。
3) target ∈ {"local","cloud","sim"}；confidence 0~1。
4) 请求涉及压测/并发/模拟/合成数据/脏数据 → target 必须为 "sim"。
5) target=local → routing.worker_id 与 routing.lora_id 必填（没有可用 LoRA 时填 "" 并说明用 base）。
6) target=cloud → cloud.provider 与 cloud.model 必填（来自 INVENTORY.cloud）。
7) target=sim → sim.scenario 必填（来自 INVENTORY.sim）。
8) fallback_chain 必须非空（按顺序写 local:/cloud:/sim: 形式）。
9) 若请求明显缺信息且无法判断 → needs_clarification=true 并保持 target 保守（local）。

【INVENTORY】
{inventory}

【路由政策（按序判断）】
S1 命中 sim 关键词（压测/并发/模拟/合成/脏数据）→ sim
S2 任务能力被 worker+LoRA 覆盖且上下文不大 → local（选分数最高、有空槽的 worker/lora）
S3 缺工具/LoRA、上下文超本地预算、高风险、或失败次数≥阈值 → cloud
S4 其他 → local(base)，confidence 降低并在 reason_trace 说明

【输出 JSON 结构（严格遵守）】
{{"target":"local|cloud|sim","confidence":0.0,"task_type":"code|rag|format|data|workflow|other",
 "intent_summary":"一句话",
 "subtasks":[{{"id":1,"desc":"...","executor":"local:<lora>|cloud:<provider>/<model>|sim:<scenario>"}}],
 "routing":{{"worker_id":"...","lora_id":"...","why":"...","timeout_s":60,"max_retries":1,"escalate":false}},
 "cloud":{{"provider":"","model":"","context_policy":"full|digest","why":"","est_cost":"low|medium|high"}},
 "sim":{{"scenario":"","users":0,"duration_s":0,"dirty_policy":"quarantine|reject","report_to":""}},
 "fallback_chain":["local:...","cloud:..."],
 "needs_clarification":false,
 "reason_trace":["S2: ...","S3: ..."]}}"""


# ══════════════════════════════════════════════════════════════
# INVENTORY（运行时注入）
# ══════════════════════════════════════════════════════════════
def _settings_providers():
    try:
        st = json.loads((PROJECT_DIR / "settings.json").read_text(encoding="utf-8"))
        return st.get("providers") or {}
    except Exception:
        return {}


def build_inventory(max_loras=16, max_kbs=12):
    """运行时能力清单（workers/loras/cloud/sim/tools/kbs/policy）"""
    inv = {"workers": [], "loras": [], "cloud": [], "sim": [], "tools": [], "kbs": [],
           "policy": {"sim_keywords": SIM_KEYWORDS,
                      "failover": {"max_errors": 3, "timeout_s": 60},
                      "daily_cloud_budget": int(os.environ.get("HIVE_CLOUD_BUDGET", "200"))}}

    # workers：本地工蜂（:8002）+ LoRA 槽位信息
    try:
        import swarm
        up = bool(swarm.worker_up())
    except Exception:
        up = False
    loaded = []
    if up:
        try:
            import requests
            r = requests.get("http://127.0.0.1:8002/v1/models", timeout=4)
            if r.status_code == 200:
                loaded = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
        except Exception:
            pass
    inv["workers"].append({
        "id": "w1", "endpoint": "http://127.0.0.1:8002",
        "base": "Qwen2.5-7B-Instruct-AWQ", "status": "ready" if up else "offline",
        "max_loras": 2, "loaded": loaded, "slots_free": max(0, 2 - len(loaded)),
    })

    # loras：注册表（含 base 伪条目）
    inv["loras"].append({"id": "base", "base": "7B", "rank": 0, "score": 0.6,
                         "capability": ["general"]})
    try:
        import lora_manager
        reg = lora_manager.load_lora_registry() or {}
        caps_map = {
            "code": ["代码", "sql", "python", "code"],
            "json": ["json", "格式化", "schema", "抽取", "extract"],
            "workflow": ["工作流", "workflow", "搭建", "蒸馏", "自动化"],
            "rag": ["知识库", "检索", "rag", "问答"],
            "format": ["表格", "excel", "xlsx", "格式", "汇总"],
        }
        for name, meta in list(reg.items())[:max_loras]:
            if not isinstance(meta, dict):
                continue
            nm = str(name).lower()
            caps = [c for c, kws in caps_map.items() if any(k in nm for k in kws)] or ["general"]
            inv["loras"].append({
                "id": name, "base": str(meta.get("base_model") or "7B").split("/")[-1],
                "rank": meta.get("rank") or meta.get("lora_rank") or 16,
                "score": 0.8, "capability": caps,
            })
    except Exception:
        pass

    # cloud：有 key 的 provider
    provs = _settings_providers()
    for name, cfg in provs.items():
        if not isinstance(cfg, dict) or not str(cfg.get("api_key") or "").strip():
            continue
        if name in ("vllm", "ollama"):
            continue
        inv["cloud"].append({
            "provider": name, "model": cfg.get("model") or "",
            "modalities": cfg.get("modalities") or ["text"],
            "cost": "low" if name in ("deepseek", "glm") else "medium",
        })

    # sim 场景
    inv["sim"] = [
        {"scenario": "http_concurrent", "desc": "并发压测/路由策略"},
        {"scenario": "dirty_data", "desc": "合成脏数据并验证隔离（不写 KB）"},
    ]

    # tools
    inv["tools"] = ["write_sheet", "read_sheet", "paper_lora_kb_query",
                    "paper_lora_list_loras", "browser_open", "academic_search"]

    # kbs
    try:
        inv["kbs"] = sorted([d.name for d in (PROJECT_DIR / "data" / "kbs").iterdir()
                             if d.is_dir()])[:max_kbs]
    except Exception:
        inv["kbs"] = []
    return inv


# ══════════════════════════════════════════════════════════════
# LLM 调用（vLLM OpenAI 兼容 + JSON Schema 引导）
# ══════════════════════════════════════════════════════════════
def _extract_json(text):
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


def _model_name():
    if LLM_MODEL:
        return LLM_MODEL
    try:
        import requests
        r = requests.get(LLM_BASE + "/models", timeout=5)
        if r.status_code == 200:
            data = r.json().get("data") or []
            if data:
                return data[0].get("id") or ""
    except Exception:
        pass
    return "Qwen2.5-14B-Instruct-AWQ"


def _llm_json(messages, max_tokens=MAX_TOKENS, temperature=0.1):
    """调用本地主脑，优先 JSON Schema 引导；失败退回普通调用"""
    import requests
    payload = {
        "model": _model_name(), "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature,
    }
    guided = dict(payload)
    guided["response_format"] = {"type": "json_schema",
                                 "json_schema": {"name": "route_decision", "schema": ROUTE_SCHEMA}}
    t0 = time.time()
    try:
        r = requests.post(LLM_BASE + "/chat/completions", json=guided, timeout=LLM_TIMEOUT)
        if r.status_code == 200:
            d = r.json()
            text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return text, {"mode": "guided", "http": 200,
                          "latency_ms": round((time.time() - t0) * 1000), "error": ""}
        err = r.text[:300]
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, str(e)[:200])
    # 退回普通调用（prompt 已要求严格 JSON）
    try:
        r2 = requests.post(LLM_BASE + "/chat/completions", json=payload, timeout=LLM_TIMEOUT)
        if r2.status_code == 200:
            d = r2.json()
            text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return text, {"mode": "plain", "http": 200,
                          "latency_ms": round((time.time() - t0) * 1000),
                          "error": "guided 失败: " + err[:160]}
        return "", {"mode": "plain", "http": r2.status_code,
                    "latency_ms": round((time.time() - t0) * 1000), "error": r2.text[:300]}
    except Exception as e:
        return "", {"mode": "plain", "http": 0,
                    "latency_ms": round((time.time() - t0) * 1000),
                    "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


# ══════════════════════════════════════════════════════════════
# 校验 / 归一化
# ══════════════════════════════════════════════════════════════
def _rule_target(request):
    s = str(request or "")
    low = s.lower()
    if any(k in low for k in [k.lower() for k in SIM_KEYWORDS]):
        return "sim"
    has_local = any(k in low for k in [k.lower() for k in LOCAL_HINTS])
    has_cloud = any(k in low for k in [k.lower() for k in CLOUD_HINTS])
    if has_cloud:
        return "cloud"
    if has_local:
        return "local"
    return "local"


def basis_decision(request, inventory=None, reason="规则兜底"):
    """不依赖 LLM 的确定性决策（兜底/预路由）"""
    inv = inventory or build_inventory()
    target = _rule_target(request)
    workers = inv.get("workers") or []
    loras = [l for l in (inv.get("loras") or []) if l.get("id") != "base"]
    cloud = inv.get("cloud") or []
    sims = inv.get("sim") or []
    d = {
        "target": target, "confidence": 0.35,
        "task_type": ("workflow" if target == "local" else
                      ("data" if target == "sim" else "other")),
        "intent_summary": str(request or "")[:120],
        "subtasks": [], "routing": {}, "cloud": {}, "sim": {},
        "fallback_chain": [], "needs_clarification": False,
        "reason_trace": [reason],
    }
    if target == "local":
        w = next((x for x in workers if x.get("status") == "ready"), None) or (workers[0] if workers else None)
        lora = loras[0]["id"] if loras else ""
        d["routing"] = {"worker_id": (w or {}).get("id", "w1"), "lora_id": lora,
                        "why": "本地任务，规则命中（LLM 不可用或校验失败）",
                        "timeout_s": 60, "max_retries": 1, "escalate": False}
        d["subtasks"] = [{"id": 1, "desc": str(request or "")[:80],
                          "executor": "local:%s" % (lora or "base")}]
        d["fallback_chain"] = ["local:%s" % (lora or "base"),
                               "cloud:%s/%s" % ((cloud[0]["provider"], cloud[0]["model"])
                                                if cloud else ("deepseek", "deepseek-flash"))]
    elif target == "cloud":
        c = cloud[0] if cloud else {"provider": "deepseek", "model": "deepseek-flash"}
        d["cloud"] = {"provider": c.get("provider"), "model": c.get("model"),
                      "context_policy": "full", "why": "规则：云端特征命中",
                      "est_cost": c.get("cost", "low")}
        d["subtasks"] = [{"id": 1, "desc": str(request or "")[:80],
                          "executor": "cloud:%s/%s" % (c.get("provider"), c.get("model"))}]
        d["fallback_chain"] = ["cloud:%s/%s" % (c.get("provider"), c.get("model")), "local:base"]
        d["routing"] = {"worker_id": "", "lora_id": "", "why": "云端兜底",
                        "timeout_s": 120, "max_retries": 1, "escalate": True}
    else:
        sc = sims[0]["scenario"] if sims else "http_concurrent"
        d["sim"] = {"scenario": "http_concurrent", "users": 100, "duration_s": 60,
                    "dirty_policy": "quarantine", "report_to": "data/sim/reports/"}
        d["subtasks"] = [{"id": 1, "desc": "仿真/压测执行", "executor": "sim:%s" % sc}]
        d["fallback_chain"] = ["sim:http_concurrent"]
        d["routing"] = {"worker_id": "", "lora_id": "", "why": "sim 关键词命中（隔离执行）",
                        "timeout_s": 600, "max_retries": 0, "escalate": False}
    return d


def validate_decision(d, request, inventory=None):
    """校验 + 归一化；返回 (normalized, errors)"""
    inv = inventory or build_inventory()
    errs = []
    if not isinstance(d, dict):
        return None, ["决策不是 JSON 对象"]

    # 基础字段
    target = str(d.get("target") or "").strip().lower()
    if target not in ("local", "cloud", "sim"):
        errs.append("target 非枚举: %r" % d.get("target"))
        target = ""
    try:
        conf = float(d.get("confidence"))
    except Exception:
        conf = 0.5
        errs.append("confidence 非数字")
    conf = min(1.0, max(0.0, conf))

    out = {
        "target": target or _rule_target(request),
        "confidence": conf,
        "task_type": str(d.get("task_type") or "other")[:40],
        "intent_summary": str(d.get("intent_summary") or str(request or "")[:120])[:300],
        "subtasks": [],
        "routing": {},
        "cloud": {},
        "sim": {},
        "fallback_chain": [],
        "needs_clarification": bool(d.get("needs_clarification")),
        "reason_trace": [str(x)[:200] for x in (d.get("reason_trace") or [])][:8],
    }

    # subtasks
    subs = d.get("subtasks") or []
    if not isinstance(subs, list):
        subs = []
        errs.append("subtasks 不是数组")
    for i, s in enumerate(subs[:8]):
        if not isinstance(s, dict):
            continue
        out["subtasks"].append({
            "id": s.get("id") if isinstance(s.get("id"), int) else i + 1,
            "desc": str(s.get("desc") or "")[:200],
            "executor": str(s.get("executor") or "")[:120],
        })

    # routing
    r = d.get("routing") or {}
    r = r if isinstance(r, dict) else {}
    out["routing"] = {
        "worker_id": str(r.get("worker_id") or "")[:80],
        "lora_id": str(r.get("lora_id") or "")[:120],
        "why": str(r.get("why") or "")[:300],
        "timeout_s": int(r.get("timeout_s") or 60),
        "max_retries": int(r.get("max_retries") or 1),
        "escalate": bool(r.get("escalate")),
    }

    # cloud / sim
    c = d.get("cloud") or {}
    c = c if isinstance(c, dict) else {}
    out["cloud"] = {
        "provider": str(c.get("provider") or "")[:40],
        "model": str(c.get("model") or "")[:80],
        "context_policy": str(c.get("context_policy") or "digest")[:20],
        "why": str(c.get("why") or "")[:300],
        "est_cost": str(c.get("est_cost") or "low")[:20],
    }
    sim = d.get("sim") or {}
    sim = sim if isinstance(sim, dict) else {}
    out["sim"] = {
        "scenario": str(sim.get("scenario") or "")[:40],
        "users": int(sim.get("users") or 0),
        "duration_s": int(sim.get("duration_s") or 0),
        "dirty_policy": str(sim.get("dirty_policy") or "")[:20],
        "report_to": str(sim.get("report_to") or "")[:120],
    }

    # fallback_chain
    fc = d.get("fallback_chain") or []
    if not isinstance(fc, list):
        fc = []
    out["fallback_chain"] = [str(x)[:120] for x in fc][:5]

    # ── 政策校验 ──
    low = str(request or "").lower()
    if any(k in low for k in [k.lower() for k in SIM_KEYWORDS]) and out["target"] != "sim":
        errs.append("S1：请求含 sim 关键词但 target=%r" % out["target"])
        out["target"] = "sim"
        out["reason_trace"].append("S18 后置修正：sim 关键词命中 → target=sim")

    # ★ S3：时效/需联网任务 → 强制 cloud（本地模型无联网检索）
    if out["target"] == "local" and TIME_SENSITIVE_RE.search(str(request or "")):
        errs.append("S3：时效/需联网任务应走 cloud，收到 local")
        out["target"] = "cloud"
        out["reason_trace"].append("S18 后置修正：时效/联网任务 → cloud")
        provs = [x.get("provider") for x in (inv.get("cloud") or [])]
        if provs and not out["cloud"].get("provider"):
            c = inv["cloud"][0]
            out["cloud"] = {"provider": c.get("provider"), "model": c.get("model"),
                            "context_policy": "digest", "why": "需联网/时效信息，云端更合适",
                            "est_cost": c.get("cost", "low")}
            out["routing"]["escalate"] = True

    # ★ S3：高风险任务（合同/法规/医疗/财务等审查）→ 强制 cloud
    if out["target"] == "local" and HIGH_RISK_RE.search(str(request or "")):
        errs.append("S3：高风险任务（合同/法规/医疗/财务审查）应走 cloud，收到 local")
        out["target"] = "cloud"
        out["reason_trace"].append("S18 后置修正：高风险任务 → cloud")
        provs = [x.get("provider") for x in (inv.get("cloud") or [])]
        if provs and not out["cloud"].get("provider"):
            c = inv["cloud"][0]
            out["cloud"] = {"provider": c.get("provider"), "model": c.get("model"),
                            "context_policy": "full", "why": "高风险审查任务，云端更强",
                            "est_cost": c.get("cost", "low")}
            out["routing"]["escalate"] = True

    if out["target"] == "local":
        workers = inv.get("workers") or []
        ready = [w for w in workers if w.get("status") == "ready"] or workers
        wids = [w.get("id") for w in ready]
        if out["routing"]["worker_id"] not in wids:
            errs.append("local：worker_id 不在 INVENTORY.workers（%r）" % out["routing"]["worker_id"])
            out["routing"]["worker_id"] = wids[0] if wids else "w1"
        lids = [l.get("id") for l in (inv.get("loras") or [])] or ["base"]
        if out["routing"]["lora_id"] not in lids:
            errs.append("local：lora_id 不在 INVENTORY.loras（%r）" % out["routing"]["lora_id"])
            out["routing"]["lora_id"] = "base" if "base" in lids else lids[0]
        if not out["fallback_chain"]:
            out["fallback_chain"] = ["local:%s" % out["routing"]["lora_id"]]
    elif out["target"] == "cloud":
        provs = [x.get("provider") for x in (inv.get("cloud") or [])]
        if out["cloud"]["provider"] not in provs:
            errs.append("cloud：provider 不在 INVENTORY.cloud（%r）" % out["cloud"]["provider"])
            if provs:
                out["cloud"]["provider"] = provs[0]
                out["cloud"]["model"] = (inv["cloud"][0].get("model") or "")
        if not out["cloud"]["model"]:
            errs.append("cloud：model 为空")
            out["cloud"]["model"] = "deepseek-flash"
        if not out["fallback_chain"]:
            out["fallback_chain"] = ["cloud:%s/%s" % (out["cloud"]["provider"],
                                                      out["cloud"]["model"])]
    elif out["target"] == "sim":
        scs = [x.get("scenario") for x in (inv.get("sim") or [])] or ["http_concurrent"]
        if out["sim"]["scenario"] not in scs:
            errs.append("sim：scenario 不在 INVENTORY.sim（%r）" % out["sim"]["scenario"])
            out["sim"]["scenario"] = scs[0]
        if not out["sim"]["dirty_policy"]:
            out["sim"]["dirty_policy"] = "quarantine"
        if not out["fallback_chain"]:
            out["fallback_chain"] = ["sim:%s" % out["sim"]["scenario"]]

    if not out["subtasks"]:
        out["subtasks"] = [{"id": 1, "desc": out["intent_summary"][:80],
                            "executor": out["target"]}]
    return out, errs


# ══════════════════════════════════════════════════════════════
# 主决策入口
# ══════════════════════════════════════════════════════════════
def _audit(rec):
    try:
        HIVE_DIR.mkdir(parents=True, exist_ok=True)
        # 审计文件轮转：超过 ~6000 行保留最近 3000 行（防无限增长）
        try:
            if DECISIONS_FILE.exists() and DECISIONS_FILE.stat().st_size > 4 * 1024 * 1024:
                lines = DECISIONS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
                DECISIONS_FILE.write_text("\n".join(lines[-3000:]) + "\n", encoding="utf-8")
        except Exception:
            pass
        with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass



def _mem_record(request, decision, answer=""):
    """记录路由决策到临时知识库（失败不影响主流程）"""
    try:
        import hive_memory as _mem
        return _mem.record(request, decision, answer=answer, kind="route")
    except Exception:
        return None


def decide(request, context="", use_llm=True, max_retries=MAX_RETRIES):
    """三路由决策主入口；返回 {ok, decision, meta, inventory_digest}"""
    request = str(request or "").strip()
    t0 = time.time()
    inventory = build_inventory()
    _mem_ctx = ""
    try:
        import hive_memory as _mem2
        # sim 压测走 S1 硬规则：跳过语义记忆检索（避免每请求 embedding + 内存压力）
        _low_req = request.lower()
        if not any(k in _low_req for k in [k.lower() for k in SIM_KEYWORDS]):
            _mem_ctx = _mem2.context_for(request)
    except Exception:
        _mem_ctx = ""
    inv_digest = {"workers": [(w.get("id"), w.get("status")) for w in inventory["workers"]],
                  "loras": [l.get("id") for l in inventory["loras"][:8]],
                  "cloud": [c.get("provider") for c in inventory["cloud"]],
                  "sim": [s.get("scenario") for s in inventory["sim"]]}
    meta = {"attempts": 0, "llm_used": False, "fallback": False,
            "validation_errors": [], "repaired": False,
            "llm": {}, "model": _model_name(),
            "memory_used": bool(_mem_ctx), "memory_chars": len(_mem_ctx)}
    if not request:
        return {"ok": False, "error": "request 必填", "meta": meta}

    # S1 硬规则：sim 关键词直接决定（不消耗 LLM）
    low = request.lower()
    if any(k in low for k in [k.lower() for k in SIM_KEYWORDS]):
        d = basis_decision(request, inventory, reason="S1 规则命中（sim 关键词）")
        d["confidence"] = 0.95
        d["reason_trace"] = ["S1: 请求含压测/并发/模拟/合成/脏数据关键词 → sim"]
        meta.update({"fallback": False, "rule": "S1", "latency_ms": round((time.time() - t0) * 1000)})
        _mem_record(request, d)
        global _audit_sim_counter
        _audit_sim_counter += 1
        if _audit_sim_counter % _AUDIT_SIM_N == 1:
            meta["audit_sampled"] = "1/%d" % _AUDIT_SIM_N
            rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "request": request[:500],
                   "context": str(context or "")[:300], "decision": d, "meta": meta}
            _audit(rec)
        return {"ok": True, "decision": d, "meta": meta, "inventory_digest": inv_digest}

    errors = []
    last_errors = []
    if use_llm:
        for attempt in range(max_retries + 1):
            meta["attempts"] = attempt + 1
            _sys = (SYSTEM_PROMPT.replace("{{", "{").replace("}}", "}")
                    .replace("{inventory}", json.dumps(inventory, ensure_ascii=False)))
            messages = [
                {"role": "system", "content": _sys},
                {"role": "user", "content": "请求：%s%s%s" % (
                    request,
                    ("\n\n补充背景：\n" + str(context)[:2000]) if context else "",
                    ("\n\n" + _mem_ctx) if _mem_ctx else "")},
            ]
            if errors:
                messages.append({"role": "user", "content":
                                 "上一次输出未通过校验，错误如下，请修复后重新输出 JSON：\n- "
                                 + "\n- ".join(errors[:8])})
            text, llm_meta = _llm_json(messages)
            meta["llm"] = llm_meta
            meta["llm_used"] = bool(text)
            parsed = _extract_json(text)
            if not parsed:
                errors = ["输出不是合法 JSON"]
                last_errors = errors
                continue
            normalized, errs = validate_decision(parsed, request, inventory)
            last_errors = errs
            # 可归一化错误（worker/lora/provider/scenario 纠偏、策略修正）→ 直接采用不重试
            _fixable = errs and all(
                ("不在 INVENTORY" in e) or ("为空" in e) or ("应走 cloud" in e)
                or ("confidence 非数字" in e) or ("subtasks 不是数组" in e)
                or ("非数字" in e) for e in errs)
            if not errs or _fixable:
                meta["latency_ms"] = round((time.time() - t0) * 1000)
                meta["validation_errors"] = errs
                meta["normalized_errors"] = errs if errs else []
                meta["repaired"] = bool(errs) or attempt > 0
                _mem_record(request, normalized)
                rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "request": request[:500],
                       "context": str(context or "")[:300], "decision": normalized,
                       "meta": meta}
                _audit(rec)
                return {"ok": True, "decision": normalized, "meta": meta,
                        "inventory_digest": inv_digest}
            errors = errs
        meta["validation_errors"] = last_errors

    # 兜底：规则决策
    d = basis_decision(request, inventory,
                       reason="规则兜底：LLM 不可用或校验失败（%s）" %
                              ("; ".join(last_errors[:3]) if last_errors else "use_llm=False"))
    d["confidence"] = 0.35
    meta.update({"fallback": True, "latency_ms": round((time.time() - t0) * 1000)})
    _mem_record(request, d)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "request": request[:500],
           "context": str(context or "")[:300], "decision": d, "meta": meta}
    _audit(rec)
    return {"ok": True, "decision": d, "meta": meta,
            "inventory_digest": inv_digest, "warning": "LLM 决策失败，已规则兜底"}


def list_decisions(limit=50):
    try:
        lines = DECISIONS_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []


def summary():
    inv = build_inventory()
    try:
        n = len(DECISIONS_FILE.read_text(encoding="utf-8").splitlines())
    except Exception:
        n = 0
    return {"inventory": {"workers": len(inv["workers"]), "loras": len(inv["loras"]),
                          "cloud": len(inv["cloud"]), "sim": len(inv["sim"]),
                          "tools": len(inv["tools"]), "kbs": len(inv["kbs"])},
            "decisions_total": n, "llm_base": LLM_BASE, "model": _model_name()}


def dispatch(decision, context=""):
    """S18：仅决策；执行层在 S19（Worker 池）/S20（云端）/S22（Sim）"""
    return {"ok": True, "stage": "S18",
            "note": "S18 只输出路由决策；执行将在 S19/S20/S22 按计划图接入",
            "decision": decision}
