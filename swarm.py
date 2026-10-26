# -*- coding: utf-8 -*-
"""swarm.py —— 蜂群编排（主脑决策 → 执行）

用途：把已实测通过的"主脑编排"流程落进平台 API（P2.5）。
  · decide(task)  ：14B 主脑根据能力清单 + 知识库白名单输出路由决策 JSON
  · execute(...)  ：按决策执行 direct / kb / capability / kb+capability
  · orchestrate(task)：decide + execute 一步到位（/chat/completions 的 auto_orchestrate 用）

设计原则：
  · 决策只输出 JSON（云端 propose 风格；本地执行仍由策略控制）
  · capability 执行会按需拉起工蜂 7B(:8002) + 热挂 LoRA（实测 22s 冷启动）
  · 本地无匹配能力时 → 交给 chimera.plan()（缺口的云端/开源映射预案）
  · 所有步骤可测试（HTTP 调用可注入 base URL；worker 自动启动可用环境变量关闭）
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

MAIN_BASE = os.environ.get("SWARM_MAIN_BASE", "http://127.0.0.1:8000")
WORKER_BASE = os.environ.get("SWARM_WORKER_BASE", "http://127.0.0.1:8002")
BACKEND_BASE = os.environ.get("SWARM_BACKEND_BASE", "http://127.0.0.1:8001")
MAIN_MODEL = os.environ.get("SWARM_MAIN_MODEL", "/home/luxy/models/Qwen2.5-14B-Instruct-AWQ")
WORKER_MODEL = os.environ.get("SWARM_WORKER_MODEL", "/home/luxy/models/Qwen2.5-7B-Instruct-AWQ")
WORKER_PORT = int(os.environ.get("WORKER_PORT", "8002"))
VENV_PY = os.environ.get("SWARM_VENV_PY", "/home/luxy/.venv/bin/python")
AUTO_WORKER = os.environ.get("SWARM_AUTO_WORKER", "1") != "0"

# 路由白名单（知识库；可用环境变量覆盖）
DEFAULT_KB_ALLOW = (
    "bcpcn", "kb_toolchain", "medical_guidelines", "learned_skills",
    "ab_clean_v2", "ab_clean_20261021", "ab_raw_20261021", "drill_part1_test",
    "tool_docs", "final_transformed_lora_kb",
)
KB_ALLOW = tuple(os.environ.get("SWARM_KB_ALLOW", "").split(",")) if os.environ.get("SWARM_KB_ALLOW") else DEFAULT_KB_ALLOW


# ───────────────────────── HTTP helpers ─────────────────────────
def _get(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url, payload, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ───────────────────────── 上下文 ─────────────────────────
def load_assets():
    """返回 (caps, kb_entries)；caps 排除 disabled，kb 只保留白名单"""
    caps = []
    try:
        d = _get(BACKEND_BASE + "/capabilities?status=")
        caps = [c for c in (d.get("capabilities") or []) if c.get("status") != "disabled"]
    except Exception:
        pass
    kb_entries = []
    try:
        d = _get(BACKEND_BASE + "/kb/list")
        kbs = d.get("knowledge_bases") or d.get("kbs") or []
        for k in kbs:
            name = str(k.get("name") or "")
            if name and name in KB_ALLOW:
                kb_entries.append((name, str(k.get("description") or "")[:60]))
    except Exception:
        pass
    return caps, kb_entries


def _models_text():
    """模型基准库文本（主脑调度依据）；失败返回空"""
    try:
        import model_registry as _mr
        return _mr.prompt_text()
    except Exception:
        return ""


def _text_tokens(text):
    """中英混合检索词：英文/数字词 + 中文 2~3 字 n-gram（解决整句当一个词的问题）"""
    t = str(text or "").lower()
    toks = set(re.findall(r"[a-z0-9_]{2,}", t))
    for run in re.findall(r"[\u4e00-\u9fff]+", t):
        if len(run) <= 3:
            toks.add(run)
        else:
            for n in (2, 3):
                for i in range(0, len(run) - n + 1):
                    toks.add(run[i:i + n])
    return toks


def _cap_tokens(c):
    """能力条目的检索词集合（id/name/desc/tags）"""
    text = " ".join([str(c.get("cap_id") or ""), str(c.get("name") or ""),
                     str(c.get("description") or ""), " ".join(c.get("tags") or [])])
    return _text_tokens(text)


def select_caps(task, caps, limit=14):
    """★ 优化：按任务相关度筛选能力，给主脑 prompt 瘦身（44→≤14）"""
    if not caps:
        return []
    tt = _text_tokens(task)
    scored = []
    for c in caps:
        score = len(tt & _cap_tokens(c)) * 3
        if c.get("kind") == "external" and c.get("installed"):
            score += 2
        if c.get("status") == "active":
            score += 1
        scored.append((score, str(c.get("cap_id") or ""), c))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, _, c in scored[:max(1, int(limit))]]


def _clean_kb_query(text, kb_names=None):
    """修（2026-10-26）：查询里带"从知识库 xxx"会污染检索并诱发编造。
    实测：'从知识库 qiaojiao_toys_2025_v2 列出 5 个产品...' → 检索到无关片段（编造玩具剑等）；
          去掉库名/引导词后 → 回答与资料一致。
    """
    t = str(text or "")
    for n in (kb_names or []):
        t = t.replace("从知识库 " + n, "")
        t = t.replace("从知识库" + n, "")
        t = t.replace("知识库 " + n, "")
        t = t.replace("知识库" + n, "")
        t = t.replace(n, "")
    t = re.sub(r"从?(该|这个)?知识库[\s：:]*", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ，,：:")
    return t or str(text or "")


def build_prompt(caps, kb_entries, task=None):
    if task:
        caps = select_caps(task, caps)
    cap_lines = []
    for c in caps:
        tags = "、".join((c.get("tags") or [])[:4])
        if c.get("kind") == "external":
            entry = c.get("entry") or {}
            schema = c.get("args_schema")
            cap_lines.append("- %s（外部工具 %s.%s；标签：%s；参数：%s）" % (
                c.get("cap_id"), entry.get("module"), entry.get("function"), tags,
                json.dumps(schema, ensure_ascii=False) if schema else "需显式参数"))
        else:
            cap_lines.append("- %s（LoRA 单步能力；%s；标签：%s）" % (
                c.get("cap_id"), str(c.get("name"))[:36], tags))
    kb_lines = ["- %s：%s" % (n, d or "（无描述）") for n, d in kb_entries]
    system = (
        "你是 Luxy 蜂群的主脑（调度器）。根据用户请求决定执行方式，只输出 JSON。\n"
        "\n可选 route：\n"
        "1) direct —— 你自己直接回答（通用知识/写作/翻译/简单问答；不需要检索或专业能力）\n"
        "2) kb —— 需要平台/项目/文档知识，检索知识库\n"
        "3) capability —— 需要单步专业能力（LoRA），调用工蜂 7B + LoRA\n"
        "4) kb+capability —— 既需要知识库、又需要专业能力（多步任务）\n"
        "5) cloud —— 本地没有对应能力、需要云端模型介入（长尾/复杂推理）\n"
        "6) chimera —— 本地没有对应能力、需要嫁接外部开源能力（缺口）\n"
        "7) multi —— 任务需要多个步骤（≥2 步，可能含多个能力），给出 steps 数组\n"
        "   steps 每项：{\"route\":\"kb|capability|direct\",\"kb_names\":[],\"capability_id\":null,\"instruction\":\"该步做什么\"}\n"
        "\n硬规则：\n"
        "- capability_id 只能填【能力清单】里的 cap_id（原样复制，含 lora: 前缀）；\n"
        "- kb_names 只能填【知识库清单】里的库名，绝不能把能力名放进 kb_names；\n"
        "- capability/kb+capability 必须 need_worker=true 且 load_lora=true；direct/kb/cloud/chimera 必须都为 false；\n"
        "- 判断是否需要 LoRA：任务需要特定领域专长（农业/法律/医学/语义/客服/诗歌/办公等）才用；\n"
        "- 判断能力解耦：任务能拆成单个专业步骤时用 capability；多步（先查资料再用专业能力）用 kb+capability。\n"
        "\n示例：\n"
        '用户：用一句话解释什么是 LoRA\n{"route":"direct","kb_names":[],"capability_id":null,"need_worker":false,"load_lora":false,"reason":"通用知识"}\n'
        '用户：BCPCN 协议里 Hub 和 Switch 的区别\n{"route":"kb","kb_names":["bcpcn"],"capability_id":null,"need_worker":false,"load_lora":false,"reason":"需要项目资料"}\n'
        '用户：玉米缺氮有什么表现\n{"route":"capability","kb_names":[],"capability_id":"lora:agri_expert_v1","need_worker":true,"load_lora":true,"reason":"需要农学能力"}\n'
        '用户：先查 BCPCN 知识库再让农学模型总结\n{"route":"kb+capability","kb_names":["bcpcn"],"capability_id":"lora:agri_expert_v1","need_worker":true,"load_lora":true,"reason":"检索+专业能力"}\n'
        '用户：帮我做一个从 PDF 表格抽取结构化数据的工具\n{"route":"chimera","kb_names":[],"capability_id":null,"need_worker":false,"load_lora":false,"reason":"本地无此能力，需要嫁接开源"}\n'
        '用户：把数据排成表格（用 tabulate 工具）\n{"route":"capability","kb_names":[],"capability_id":"ext:github:astanin-python-tabulate","capability_input":{"data":[["A",1]],"headers":["名称","值"]},"need_worker":true,"load_lora":false,"reason":"调用已安装外部工具"}\n'
        '用户：先查 BCPCN 路由器内容，再用农学模型总结，最后写成给领导的摘要\n{"route":"multi","steps":[{"route":"kb","kb_names":["bcpcn"],"instruction":"查 BCPCN 路由器资料"},{"route":"capability","capability_id":"lora:agri_expert_v1","instruction":"用农学模型总结"},{"route":"direct","instruction":"写成给领导的摘要"}],"reason":"多步任务"}\n'
        '\n可用能力清单（__NCAP__ 个）：\n__CAPS__\n'
        '\n可用知识库清单（__MKB__ 个）：\n__KBS__\n'
        '\n__MODELS__\n'
        '\n调度规则（模型基准）：\n'
        '- 主脑（main_brain）不挂 LoRA，只做编排/判断/知识库路由；\n'
        '- 单步专业能力只能派发给 worker（工蜂，可挂 LoRA）；\n'
        '- route 必须是 direct/kb/capability/kb+capability/cloud/chimera/multi 之一；**能力 id 只能填 capability_id，不能填 route**；\n'
        '- quality_mode 模型占双卡，不能与工蜂同时用；unavailable 模型不得选择；\n'
        '- 若所需能力不在能力清单 → route=chimera（让奇美拉找开源/外部能力）；\n'
        '- 若用户要求“做一个/实现/集成/接入”某工具或功能，而能力清单没有对应项 → route=chimera（不要只给教程代码）；\n'
        '- 若选中的是【外部工具】能力，请按参数说明填 capability_input（JSON 对象），执行器会据此调用；\n'
        '- multi 的每个外部工具 step 也应填 capability_input（缺省时执行器会用前序结果兜底）；\n'
        '\n输出 JSON 字段：route, kb_names, capability_id, capability_input, need_worker, load_lora, reason。'
    )
    return (system.replace("__MODELS__", _models_text())
                  .replace("__NCAP__", str(len(cap_lines)))
                  .replace("__CAPS__", "\n".join(cap_lines))
                  .replace("__MKB__", str(len(kb_entries)))
                  .replace("__KBS__", "\n".join(kb_lines)))


# ───────────────────────── 决策 ─────────────────────────
def ask_brain(system, task, max_tokens=300):
    d = _post(MAIN_BASE + "/v1/chat/completions",
              {"model": MAIN_MODEL,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": task}],
               "max_tokens": max_tokens, "temperature": 0.0}, timeout=300)
    return (d.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def parse_decision(text):
    t = str(text or "")
    if "```" in t:
        t = max(t.split("```"), key=len)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _norm_cap(x):
    x = str(x or "").strip().lower()
    return x[5:] if x.startswith("lora:") else x


VALID_ROUTES = ("direct", "kb", "capability", "kb+capability", "cloud", "chimera", "multi")
MAX_STEPS = 5


def validate_decision(dec, caps, kb_entries):
    """把模型输出规整为可执行决策：容忍裸名、过滤不存在的 kb/cap、修正 worker 标志"""
    if not isinstance(dec, dict):
        return None
    route = str(dec.get("route") or "").strip().lower()
    cap_ids = {str(c.get("cap_id")): c for c in caps}
    names = {_norm_cap(k): k for k in cap_ids}
    if route not in VALID_ROUTES:
        # ★ P4 容错：主脑可能把能力 id 直接填进 route（实测）
        if route in cap_ids or names.get(_norm_cap(route)):
            _cid = route if route in cap_ids else names.get(_norm_cap(route))
            dec = dict(dec, route="capability", capability_id=_cid)
            route = "capability"
        else:
            return None
    if route == "workflow":
        try:
            import trajectory_distill as _td2
            wf_id = dec.get("wf_id") or dec.get("capability_id")
            r = _td2.run_workflow(wf_id, input_text=task)
            return {"ok": bool(r.get("ok")), "content": r.get("content") or r.get("error") or "",
                    "route": route, "meta": dict(meta, wf_id=wf_id, duration=r.get("duration"),
                                                 result_path=r.get("path"))}
        except Exception as e:
            return {"ok": False, "content": "", "route": route,
                    "meta": dict(meta, error=str(e)[:200])}
    if route == "multi":
        steps = dec.get("steps") or []
        out_steps = []
        for st in steps[:MAX_STEPS]:
            sub = validate_decision(dict(st, route=str(st.get("route") or "").strip().lower())
                                    if isinstance(st, dict) else None, caps, kb_entries)
            if sub and sub.get("route") in ("direct", "kb", "capability"):
                if st.get("instruction"):
                    sub["instruction"] = str(st.get("instruction"))[:200]
                out_steps.append(sub)
        if not out_steps:
            return None
        return {"route": "multi", "steps": out_steps,
                "reason": str(dec.get("reason") or "")[:200]}
    kb_names = {n for n, _ in kb_entries}
    out = {"route": route,
           "kb_names": [str(x) for x in (dec.get("kb_names") or []) if str(x) in kb_names],
           "capability_id": None, "capability_input": None,
           "need_worker": False, "load_lora": False,
           "reason": str(dec.get("reason") or "")[:200]}
    if route in ("capability", "kb+capability"):
        raw = str(dec.get("capability_id") or "").strip()
        cap_id = raw if raw in cap_ids else names.get(_norm_cap(raw))
        if not cap_id:
            # 没有匹配能力 → 退化为 chimera 缺口流程
            out.update({"route": "chimera", "reason": ("能力未匹配(%s)，转缺口流程；" % raw) + out["reason"]})
            return out
        out["capability_id"] = cap_id
        _cap = cap_ids.get(cap_id) or {}
        _is_ext = (_cap.get("kind") == "external")
        out["need_worker"] = (not _is_ext)
        out["load_lora"] = (not _is_ext)
        _ci = dec.get("capability_input")
        if isinstance(_ci, dict):
            out["capability_input"] = {str(k)[:64]: _ci[k] for k in list(_ci)[:20]}
    return out


def decide(task, caps=None, kb_entries=None):
    all_caps, kb_entries = load_assets() if (caps is None or kb_entries is None) else (caps, kb_entries)
    # ★ 优化：给 prompt 只喂相关能力（瘦身）；但决策校验必须用全量能力清单
    system = build_prompt(all_caps, kb_entries, task=task)
    content = ask_brain(system, task)
    dec = parse_decision(content)
    return validate_decision(dec, all_caps, kb_entries), (all_caps, kb_entries)


# ───────────────────────── 执行 ─────────────────────────
def worker_up():
    try:
        _get(WORKER_BASE + "/v1/models", timeout=5)
        return True
    except Exception:
        return False


def start_worker(timeout=120):
    if not AUTO_WORKER:
        return False, 0.0
    cmd = (
        "cd %s && CUDA_VISIBLE_DEVICES=1 VLLM_USE_FLASHINFER_SAMPLER=0 "
        "VLLM_ALLOW_RUNTIME_LORA_UPDATING=True setsid nohup %s -m vllm.entrypoints.openai.api_server "
        "--model %s --max-model-len 32768 --gpu-memory-utilization 0.80 "
        "--host 127.0.0.1 --port %d --dtype half --enforce-eager --max-num-seqs 8 "
        "--enable-lora --max-loras 2 --max-lora-rank 128 --max-cpu-loras 4 "
        "--enable-prefix-caching --quantization awq_marlin "
        "--enable-auto-tool-choice --tool-call-parser hermes "
        "> logs/vllm_worker_7b.log 2>&1 < /dev/null &"
    ) % (PROJECT_DIR, VENV_PY, WORKER_MODEL, WORKER_PORT)
    subprocess.Popen(["bash", "-c", cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if worker_up():
            return True, time.time() - t0
        time.sleep(2)
    return False, time.time() - t0


def activate_lora(cap_id):
    return _post(BACKEND_BASE + "/capabilities/%s/activate" % urllib.parse.quote(str(cap_id), safe=":"), {}, timeout=180)


def deactivate_lora(cap_id):
    try:
        return _post(BACKEND_BASE + "/capabilities/%s/deactivate" % urllib.parse.quote(str(cap_id), safe=":"), {}, timeout=120)
    except Exception:
        return {}


_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)


def _format_tool_calls(content):
    """修 B004：把 7B 工蜂输出的 <tool_call> 文本转成可读格式（不假装执行）"""
    txt = str(content or "")
    calls = []

    def _repl(m):
        try:
            d = json.loads(m.group(1))
        except Exception:
            return ""
        name = d.get("name") or d.get("tool") or "?"
        args = d.get("arguments") or d.get("input") or {}
        calls.append((name, args))
        return ""

    has_tag = bool(_TOOL_CALL_RE.search(txt))
    rest = _TOOL_CALL_RE.sub(_repl, txt).strip()
    if not calls:
        # 有标签但 JSON 非法：剥掉标签保留其余文本
        return rest if has_tag else txt
    lines = ["（模型请求了工具调用，平台未自动执行：）"]
    for name, args in calls:
        lines.append("· %s(%s)" % (name, json.dumps(args, ensure_ascii=False)[:200]))
    lines.append("")
    lines.append(rest or "（模型未给出文字内容）")
    return "\n".join(lines)


def worker_chat(model, messages, max_tokens=512):
    d = _post(WORKER_BASE + "/v1/chat/completions",
              {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3},
              timeout=300)
    return _format_tool_calls((d.get("choices") or [{}])[0].get("message", {}).get("content", "") or "")


def kb_query(kb_name, query, top_k=3):
    d = _post(BACKEND_BASE + "/kb/%s/query" % urllib.parse.quote(kb_name), {"query": query, "top_k": top_k})
    return d.get("results") or []


def main_answer(task, kb_context=""):
    user = task if not kb_context else ("参考资料：\n%s\n\n用户问题：%s" % (kb_context, task))
    d = _post(MAIN_BASE + "/v1/chat/completions",
              {"model": MAIN_MODEL, "messages": [{"role": "user", "content": user}],
               "max_tokens": 512, "temperature": 0.3}, timeout=300)
    return (d.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


def execute(dec, task, kb_entries=None):
    """执行决策，返回 {content, route, meta, ok}"""
    route = str(dec.get("route") or "")
    meta = {"route": route, "reason": dec.get("reason")}
    if route == "direct":
        ans = main_answer(task)
        return {"ok": bool(ans), "content": ans, "route": route, "meta": meta}
    if route == "kb":
        kbs = [str(x) for x in (dec.get("kb_names") or [])][:2]
        _q = _clean_kb_query(task, kbs)
        # ★ S3 表格答案模式：表格 KB 优先走确定性结构化问答（带行号引用）
        struct_answers = []
        table_kbs = []
        try:
            import table_qa as _tqa
            for kbn in kbs:
                try:
                    _has = _tqa.ts.has_table(kbn)
                except Exception:
                    _has = False
                if not _has:
                    continue
                table_kbs.append(kbn)
                _r = _tqa.try_answer(kbn, _q)
                if _r:
                    struct_answers.append((kbn, _r))
        except Exception:
            pass
        if struct_answers:
            content = "\n\n".join("[%s] %s" % (kbn, r["answer"]) for kbn, r in struct_answers)
            meta.update({"kb_names": kbs, "table_mode": True,
                         "modes": [r["mode"] for _, r in struct_answers],
                         "verified": all(r.get("verified") for _, r in struct_answers)})
            return {"ok": True, "content": content, "route": route, "meta": meta}
        texts = []
        for kbn in kbs:
            try:
                for r in kb_query(kbn, _q, 5):
                    texts.append("[%s] %s" % (kbn, str(r.get("text") or "")[:900]))
            except Exception:
                continue
        # 表格 KB：LLM 兜底时补"结构化行上下文"（带行号，减少串位）
        try:
            import table_qa as _tqa2
            for kbn in table_kbs:
                _rows_ctx = _tqa2.search_rows_text(kbn, _q, limit=8)
                if _rows_ctx:
                    texts.append("[%s·结构化行]\n%s" % (kbn, _rows_ctx))
        except Exception:
            pass
        ans = main_answer(
            _q,
            "\n---\n".join(texts)[:5200]
            + "\n\n【回答要求】只依据以上参考资料回答；资料中没有的信息不要编造，"
              "直接说明未找到；引用的编号/名称/价格必须与资料一致；不要编造行号（行号以结构化模式为准）。")
        # 数字交叉校验（表格 KB 才做）
        warn = ""
        try:
            import table_qa as _tqa3
            for kbn in table_kbs:
                _chk = _tqa3.check_numbers(ans, kbn)
                if not _chk.get("ok"):
                    warn += "\n\n⚠ 数字校验：以下数字未在表格中找到 %s，请人工核对。" % _chk.get("missing")
                _ent = _tqa3.check_entities(ans, kbn)
                if not _ent.get("ok"):
                    warn += "\n⚠ 实体校验：以下编号/实体未在表格中找到 %s，请人工核对。" % _ent.get("missing")
                if warn:
                    break
        except Exception:
            pass
        meta.update({"kb_names": kbs, "hits": len(texts), "table_mode": False,
                     "number_check_failed": bool(warn)})
        return {"ok": bool(ans), "content": ans + warn, "route": route, "meta": meta}
    if route in ("capability", "kb+capability"):
        cap_id = dec.get("capability_id")
        # ★ P4：外部工具能力 → 走沙箱执行器（不用工蜂 LoRA）
        try:
            import capabilities as _caps
            _cap = _caps.get_capability(cap_id)
        except Exception:
            _cap = None
        if _cap and _cap.get("kind") == "external":
            if not _cap.get("installed"):
                return {"ok": False, "content": "", "route": route,
                        "meta": dict(meta, error="外部能力未安装（proposed/approved 需先 install）")}
            try:
                import sandbox_exec as _se
                _kwargs = dec.get("capability_input") or {}
                r = _se.execute_capability(_cap, kwargs=_kwargs)
                if not r.get("ok"):
                    return {"ok": False, "content": "", "route": route,
                            "meta": dict(meta, capability_id=cap_id, error=r.get("error"))}
                val = r.get("result")
                text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
                return {"ok": True, "content": str(text), "route": route,
                        "meta": dict(meta, capability_id=cap_id, kind="external",
                                     entry=(_cap.get("entry") or {}))}
            except Exception as e:
                return {"ok": False, "content": "", "route": route,
                        "meta": dict(meta, capability_id=cap_id, error="执行异常: %s" % str(e)[:150])}
        kb_ctx = ""
        if route == "kb+capability":
            kbs = [str(x) for x in (dec.get("kb_names") or [])][:2]
            texts = []
            for kbn in kbs:
                try:
                    for r in kb_query(kbn, _clean_kb_query(task, kbs), 2):
                        texts.append("[%s] %s" % (kbn, str(r.get("text") or "")))
                except Exception:
                    continue
            kb_ctx = "\n---\n".join(texts)[:3000]
            meta["kb_names"] = kbs
        started = None
        if dec.get("need_worker") and not worker_up():
            okw, sec = start_worker()
            started = round(sec, 1)
            if not okw:
                return {"ok": False, "content": "", "route": route,
                        "meta": dict(meta, error="工蜂启动失败")}
        try:
            act = activate_lora(cap_id)
        except urllib.error.HTTPError as e:
            return {"ok": False, "content": "", "route": route,
                    "meta": dict(meta, error="LoRA 装载失败 HTTP %d" % e.code)}
        if not act.get("ok"):
            return {"ok": False, "content": "", "route": route,
                    "meta": dict(meta, error="LoRA 装载失败")}
        lora_name = str(act.get("lora_name") or str(cap_id).split(":")[-1])
        user = task if not kb_ctx else ("参考资料：\n%s\n\n用户问题：%s" % (kb_ctx, task))
        try:
            ans = worker_chat(lora_name, [{"role": "user", "content": user}])
        finally:
            deactivate_lora(cap_id)
        meta.update({"capability_id": cap_id, "worker_started_sec": started})
        return {"ok": bool(ans), "content": ans, "route": route, "meta": meta}
    if route == "multi":
        steps = dec.get("steps") or []
        if not steps:
            return {"ok": False, "content": "", "route": "multi",
                    "meta": dict(meta, error="multi 但 steps 为空")}
        ctx_parts = []
        step_meta = []
        last = ""
        for i, st in enumerate(steps, 1):
            sub_task = st.get("instruction") or task
            if ctx_parts:
                sub_task = "%s\n\n前序结果：\n%s" % (sub_task, "\n---\n".join(ctx_parts)[-2000:])
            # ★ P5.1 修复（许愿测试）：多步里的外部工具步骤若没给输入，
            #   用前序结果自动构造（单列表格），否则 tabulate 等会缺参数失败
            _st = dict(st)
            try:
                import capabilities as _caps2
                _c2 = _caps2.get_capability(_st.get("capability_id"))
            except Exception:
                _c2 = None
            if _c2 and _c2.get("kind") == "external":
                _schema = (_c2.get("args_schema") or {})
                _need_data = (isinstance(_schema, dict)
                              and (_schema.get("positional") or []) == ["data"])
                _ci = _st.get("capability_input") or {}
                _has_data = isinstance(_ci, dict) and bool(_ci.get("data"))
                # 缺输入 或 主脑给了空 data（实测会出现 {"data": []}）都要兜底
                if _need_data and not _has_data:
                    _prev = ctx_parts[-1] if ctx_parts else ""
                    _lines = [x.strip() for x in str(_prev).splitlines() if x.strip()][:20]
                    _st["capability_input"] = {
                        "data": [[ln[:120]] for ln in _lines] or [["（无内容）"]],
                        "headers": ["内容"],
                    }
                elif not isinstance(_ci, dict):
                    _st["capability_input"] = {}
            r = execute(_st, sub_task, kb_entries=kb_entries)
            step_meta.append({"step": i, "route": st.get("route"), "ok": bool(r.get("ok")),
                              "capability_id": st.get("capability_id")})
            if not r.get("ok"):
                return {"ok": False, "content": r.get("content") or "",
                        "route": "multi", "meta": dict(meta, steps=step_meta,
                                                       failed_step=i, error=r.get("meta", {}).get("error"))}
            last = r.get("content") or ""
            ctx_parts.append("第%d步(%s)：%s" % (i, st.get("route"), last[:800]))
        return {"ok": True, "content": last, "route": "multi",
                "meta": dict(meta, steps=step_meta, count=len(steps))}
    if route == "cloud":
        try:
            import cloud_bridge
            ans = cloud_bridge.ask(task, system="你是云端导师，请直接回答用户问题。")
            return {"ok": bool(ans), "content": ans or "", "route": route,
                    "meta": dict(meta, provider="cloud")}
        except Exception as e:
            ans = main_answer(task)
            return {"ok": bool(ans), "content": ans, "route": "direct",
                    "meta": dict(meta, fallback="cloud不可用:%s" % str(e)[:80])}
    if route == "chimera":
        try:
            import chimera
            plan = chimera.plan(task)
            return {"ok": True, "content": chimera.render_plan_text(plan),
                    "route": route, "meta": dict(meta, plan=plan)}
        except Exception as e:
            ans = main_answer(task)
            return {"ok": bool(ans), "content": ans, "route": "direct",
                    "meta": dict(meta, fallback="chimera失败:%s" % str(e)[:80])}
    return {"ok": False, "content": "", "route": route, "meta": dict(meta, error="未知 route")}


def orchestrate(task):
    """一步到位：决策 + 执行。返回 {ok, route, content, meta}"""
    task = str(task or "").strip()
    if not task:
        return {"ok": False, "content": "", "route": "none", "meta": {"error": "空任务"}}
    # ★ P5 治理：KILL 开关
    try:
        import governance as _gov
        if _gov.is_frozen():
            return {"ok": False, "content": "系统已冻结（KILL 开关）：%s" % (_gov.status().get("frozen_reason") or ""),
                    "route": "frozen", "meta": {"error": "frozen"}}
    except Exception:
        pass
    # ★ S5 命中即执行：相似任务已有沉淀工作流 → 直接跑本地路径（阈值 0.65）
    try:
        import os as _os
        if _os.environ.get("WORKFLOW_REUSE", "1") == "1":
            import trajectory_distill as _td
            _hits = _td.search_workflows(task, limit=1)
            _thr = float(_os.environ.get("WORKFLOW_REUSE_MIN", "0.65"))
            if _hits and float(_hits[0].get("score") or 0) >= _thr:
                _hit = _hits[0]
                _r = _td.run_workflow(_hit.get("wf_id"), input_text=task)
                if _r.get("ok"):
                    try:
                        _td.record_reuse(_hit.get("wf_id"), task=task, score=_hit.get("score"),
                                         channel="chat_fastpath")
                    except Exception:
                        pass
                    return {"ok": True, "content": _r.get("content") or "",
                            "route": "workflow",
                            "meta": {"reason": "命中已蒸馏工作流（%.2f）" % float(_hit.get("score") or 0),
                                     "wf_id": _hit.get("wf_id"), "score": _hit.get("score"),
                                     "workflow_name": _hit.get("workflow_name"),
                                     "duration": _r.get("duration"),
                                     "result_path": _r.get("path")}}
    except Exception:
        pass
    dec, _ = decide(task)
    if not dec:
        ans = main_answer(task)
        return {"ok": bool(ans), "content": ans, "route": "direct",
                "meta": {"fallback": "决策解析失败，直接回答"}}
    out = execute(dec, task)
    out["decision"] = dec
    return out
