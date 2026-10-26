# -*- coding: utf-8 -*-
"""cloud_workflow_builder.py —— S15：缺失工作流 → 云端搭建 → 蒸馏沉淀本地

架构要求（用户明确）：
  1) 用户任务进来 → 判断本地是否已有可用工作流（轨迹蒸馏索引命中）
  2) 没有 → **自动路由到云端**：让云端模型设计一个可在本地执行的工作流，
     并生成"目标结果"（采购清单样例）作为蒸馏目标
  3) 云端产物 → 校验/修复 → **沉淀到本地**（distilled workflow 索引 + golden 目标）
  4) 本地执行该工作流（真调平台工具，如 write_sheet 生成 Excel）
  5) 下次同类任务 → 直接命中本地工作流（不再走云端）

说明：目标模型用户指定 deepseek4.1flash1017；当前 deepseek provider 无 key，
builder 会自动选择可用云端（glm/kimi，免费/已配），并把 requested_model 记录在案。
"""
import hashlib
import json
import os
import re
import struct
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SETTINGS = PROJECT_DIR / "settings.json"
DISTILL_DIR = PROJECT_DIR / "data" / "distilled"
WF_DIR = DISTILL_DIR / "workflows"
TARGETS_DIR = DISTILL_DIR / "targets"
BUILDS_LOG = PROJECT_DIR / "data" / "cloud" / "workflow_builds.jsonl"
TARGET_MODEL_REQUESTED = os.environ.get("TARGET_MODEL", "deepseek4.1flash1017")

MAX_REPAIR = 2
INTENT_KEYWORDS = ("工作流", "workflow", "自动识别", "自动化", "采购清单", "搭建", "沉淀",
                   "蒸馏", "自动生成", "一键")

AVAILABLE_TOOL_NODES = {
    "write_sheet": "写新 Excel（参数 rows 二维数组、header 表头、name 文件名、sheet 表名）",
    "read_sheet": "读 Excel（参数 filename、sheet、rows）",
    "paper_lora_kb_query": "查询知识库（参数 kb_name、query、top_k）",
    "paper_lora_list_loras": "列出 LoRA",
    "paper_lora_train_list": "列出训练任务",
    "paper_lora_list_datasets": "列出训练数据集",
    "paper_lora_tool_list": "列出平台工具",
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _sha(s, n=10):
    return hashlib.sha1(str(s).encode()).hexdigest()[:n]


def _load_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    return rows


def _append_jsonl(path, rec):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ───────────────────────── ① 本地覆盖判断 ─────────────────────────
def local_hit(request, threshold=None):
    """查本地已沉淀工作流是否覆盖该任务"""
    try:
        import trajectory_distill as td
        thr = float(threshold if threshold is not None
                    else os.environ.get("WORKFLOW_REUSE_MIN", "0.65"))
        hits = td.search_workflows(str(request), limit=3)
        if hits and float(hits[0].get("score") or 0) >= thr:
            return True, {"hit": hits[0], "threshold": thr, "candidates": hits[:3]}
        return False, {"hit": None, "threshold": thr, "candidates": hits[:3]}
    except Exception as e:
        return False, {"error": str(e)[:200]}


def wants_workflow(request):
    t = str(request or "").lower()
    return any(k.lower() in t for k in INTENT_KEYWORDS)


def should_cloud_build(request, allow=None):
    """是否需要云端建工作流：本地无命中 + （显式允许 或 命中工作流意图关键词）"""
    hit, info = local_hit(request)
    if hit:
        return False, "local_workflow_hit", info
    if allow is None:
        allow = os.environ.get("CLOUD_BUILD_AUTO", "1") != "0"
    if not allow:
        return False, "cloud_build_disabled", info
    if wants_workflow(request):
        return True, "no_local_workflow_and_workflow_intent", info
    return False, "no_workflow_intent", info


# ───────────────────────── ② 云端调用 ─────────────────────────
def _pick_cloud_provider():
    """挑可用的文本云端（优先 glm→kimi；deepseek 无 key 则跳过但记录）"""
    st = _load_settings()
    provs = st.get("providers") or {}
    prefer = [x.strip() for x in (os.environ.get("CLOUD_BUILD_PROVIDER")
                                  or "deepseek,glm,kimi").split(",")
              if x.strip()]
    order = prefer + [k for k in provs if k not in prefer]
    for nm in order:
        cfg = provs.get(nm) or {}
        if not isinstance(cfg, dict):
            continue
        # ★ 目标模型别名（如 deepseek4.1flash1017 → deepseek-flash）
        _alias = (cfg.get("model_aliases") or {})
        for _k, _v in _alias.items():
            if _k in TARGET_MODEL_REQUESTED and _v:
                cfg = dict(cfg)
                cfg["model"] = str(_v)
        key = str(cfg.get("api_key") or "").strip()
        base = str(cfg.get("api_base") or "").strip()
        mods = cfg.get("modalities") or ["text"]
        if isinstance(mods, str):
            mods = [m.strip() for m in mods.split(",")]
        if key and base and "text" in mods and "127.0.0.1" not in base:
            return {"name": nm, "api_base": base.rstrip("/"), "api_key": key,
                    "model": str(cfg.get("model") or "deepseek-chat")}
    return {}


def _cloud_ask(prompt, system=None, max_tokens=2600, timeout=180):
    """云端问答（自带 usage 打点）；返回 (content, provider_info)"""
    import requests
    cfg = _pick_cloud_provider()
    if not cfg:
        raise RuntimeError("无可用云端 provider（需要 key + text modality）")
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": str(prompt)})
    body = {"model": cfg["model"], "messages": msgs, "max_tokens": max_tokens,
            "temperature": 0.3}
    _m = str(cfg["model"]).lower()
    if "kimi-k2" in _m or "moonshot" in _m:
        body["temperature"] = 1
    import cloud_bridge as _cb
    t0 = time.time()
    try:
        r = requests.post(cfg["api_base"] + "/chat/completions",
                          headers={"Authorization": "Bearer " + cfg["api_key"],
                                   "Content-Type": "application/json"},
                          json=body, timeout=timeout)
        if r.status_code == 429:
            time.sleep(5)
            r = requests.post(cfg["api_base"] + "/chat/completions",
                              headers={"Authorization": "Bearer " + cfg["api_key"],
                                       "Content-Type": "application/json"},
                              json=body, timeout=timeout)
        r.raise_for_status()
        d = r.json()
        msg = ((d.get("choices") or [{}])[0].get("message") or {})
        content = str(msg.get("content") or "").strip()
        if not content:
            content = str(msg.get("reasoning_content") or "").strip()
        try:
            _cb._log_usage({"api_base": cfg["api_base"], "model": cfg["model"]},
                           True, time.time() - t0)
        except Exception:
            pass
        return content, {"provider": cfg["name"], "model": cfg["model"],
                         "duration": round(time.time() - t0, 2)}
    except Exception as e:
        try:
            _cb._log_usage({"api_base": cfg.get("api_base", ""), "model": cfg.get("model", "")},
                           False, time.time() - t0, str(e)[:150])
        except Exception:
            pass
        raise RuntimeError("云端调用失败（%s）: %s" % (cfg.get("name"), str(e)[:200]))


def _extract_json(text):
    """从云端回复里提取 JSON（容忍 markdown 围栏/前后解释）"""
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


# ───────────────────────── ③ 设计提示词 / 校验修复 ─────────────────────────
SYSTEM_PROMPT = (
    "你是『云端工作流架构师』。平台本地还没有能完成该任务的工作流，需要你设计一个"
    "**可在本地执行**的工作流，并给出该任务的『目标结果』（采购清单样例）。只输出 JSON，不要解释。"
)


def _build_prompt(request, context="", errors=None):
    tools_desc = "\n".join("- %s：%s" % (k, v) for k, v in AVAILABLE_TOOL_NODES.items())
    err_txt = ""
    if errors:
        err_txt = "\n⚠ 上一次设计校验失败，请修正这些错误：\n" + "\n".join("- " + e for e in errors[:8])
    return f"""任务：{request}
{('补充背景：' + str(context)[:800]) if context else ''}

设计约束：
1) 节点类型只用：input / process / aggregate / tool
2) 第一个节点必须是 input（id=n0）；最后必须是 aggregate（汇总最终报告）
3) tool 节点只能使用以下工具（tool 字段填工具名，tool_args 填参数）：
{tools_desc}
4) 要生成 Excel 采购清单：用 tool 节点 write_sheet，tool_args 里给 rows（二维数组，可直接写死
   你生成的目标采购清单数据）、header（表头）、name（文件名）、sheet（表名）
5) process 节点 prompt 里用 {{n0}} 这样的占位符引用上游节点输出
6) 每个节点都要有 id（n0,n1,...）/ type / input_from / position({{x,y}})
7) 采购清单列建议：序号/商品名称/规格/数量/单价/小计/来源平台/备注
8) golden_purchase_list.rows 必须与你 write_sheet 里写的数据一致（这是本地蒸馏的目标结果）
9) 若补充背景里给出了本地 Excel 文件名/路径：必须包含 read_sheet tool 节点读取它（filename 填该文件）
10) 京东/淘宝/阿里巴巴等电商来源：平台当前没有采集工具（需登录/反爬），不要假装抓取；
    在 aggregate 里如实说明"线上来源需人工/后续接入采集工具"，只对本地 Excel/给定数据做真实处理
{err_txt}

只输出一个 JSON 对象：
{{"workflow_name": "...", "description": "...",
  "nodes": [
    {{"id": "n0", "type": "input", "input_from": [], "prompt": "", "position": {{"x":0,"y":0}}}},
    {{"id": "n1", "type": "process", "input_from": ["n0"], "prompt": "...", "provider": "vllm",
      "output_format": "text", "max_tokens": 1200, "temperature": 0.2, "position": {{"x":240,"y":0}}}},
    {{"id": "n2", "type": "tool", "input_from": ["n1"], "tool": "write_sheet",
      "tool_args": {{"rows": [["1","..."]], "header": ["序号","..."], "name": "采购清单", "sheet": "采购清单"}},
      "tool_timeout": 60, "position": {{"x":480,"y":0}}}},
    {{"id": "n3", "type": "aggregate", "input_from": ["n2"], "prompt": "...", "provider": "vllm",
      "output_format": "text", "max_tokens": 1500, "temperature": 0.2, "position": {{"x":720,"y":0}}}}
  ],
  "golden_purchase_list": {{"columns": ["序号","商品名称","规格","数量","单价","小计","来源平台","备注"],
                            "rows": [["1","积木套装","大颗粒 120 片","20","89.0","1780.0","本地Excel","示例"]],
                            "notes": "目标结果由云端模型生成，用于本地蒸馏对照"}}}}"""


def _sanitize_config(data):
    """规整云端返回的 workflow 配置（补齐字段、排序、id 规范化）"""
    nodes = data.get("nodes") or []
    out = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or ("n%d" % i))
        ntype = str(n.get("type") or "process")
        node = {"id": nid, "type": ntype,
                "description": str(n.get("description") or ""),
                "input_from": [str(x) for x in (n.get("input_from") or [])],
                "position": n.get("position") if isinstance(n.get("position"), dict) else {"x": 240 * i, "y": 0}}
        if ntype == "input":
            node["prompt"] = ""
            node["input_from"] = []
        elif ntype == "tool":
            node["tool"] = str(n.get("tool") or "")
            ta = n.get("tool_args")
            node["tool_args"] = ta if isinstance(ta, dict) else {}
            node["tool_timeout"] = int(n.get("tool_timeout") or 60)
            if node["tool"] not in AVAILABLE_TOOL_NODES:
                raise ValueError("云端使用了不可用工具: %s（允许: %s）"
                                 % (node["tool"], ", ".join(AVAILABLE_TOOL_NODES)))
        else:
            node["prompt"] = str(n.get("prompt") or "")
            node["provider"] = n.get("provider") or "vllm"
            node["output_format"] = n.get("output_format") or "text"
            node["max_tokens"] = int(n.get("max_tokens") or 1200)
            node["temperature"] = float(n.get("temperature") or 0.2)
        out.append(node)
    if not out or out[0]["type"] != "input":
        raise ValueError("工作流必须含 input 首节点")
    if out[-1]["type"] != "aggregate":
        out.append({"id": "n%d" % len(out), "type": "aggregate",
                    "description": "汇总最终报告", "input_from": [out[-1]["id"]],
                    "prompt": "根据上游结果输出最终报告：\n{%s}" % out[-1]["id"],
                    "provider": "vllm", "output_format": "text", "max_tokens": 1500,
                    "temperature": 0.2, "position": {"x": 240 * len(out), "y": 0}})
    cfg = {"workflow_name": str(data.get("workflow_name") or "云端构建工作流"),
           "description": str(data.get("description") or ""),
           "author": "cloud_workflow_builder",
           "tags": ["cloud_built", "distilled"],
           "required_loras": [], "required_providers": ["vllm"],
           "nodes": out}
    return cfg


def build(request, context="", max_repair=MAX_REPAIR, execute=False, force=False):
    """云端搭建工作流 → 沉淀本地 → （可选）本地执行；返回摘要

    force=True：跳过本地命中检查，强制云端重建（用于录制/更新目标结果）
    """
    request = str(request or "").strip()
    if not request:
        raise ValueError("request 必填")
    hit, hit_info = (False, {}) if force else local_hit(request)
    if hit:
        return {"ok": True, "skipped": "local_workflow_hit", "hit": hit_info.get("hit"),
                "message": "本地已有可用工作流，无需云端搭建"}
    errors, data, cloud_info = [], None, {}
    for attempt in range(int(max_repair) + 1):
        content, cloud_info = _cloud_ask(_build_prompt(request, context, errors),
                                         system=SYSTEM_PROMPT,
                                         max_tokens=2800)
        data = _extract_json(content)
        if not data:
            errors = ["输出不是合法 JSON"]
            continue
        try:
            cfg = _sanitize_config(data)
            import workflow_engine as we
            errs, warns = we.lint_config(cfg)
            if errs:
                errors = errs
                continue
            break
        except Exception as e:
            errors = [str(e)[:300]]
            continue
    else:
        raise RuntimeError("云端工作流设计失败（已修复 %d 轮）: %s" % (max_repair, errors[:5]))

    golden = data.get("golden_purchase_list") or {}
    wf_id = "wf_cloud_" + _sha(request + json.dumps(cfg["nodes"], ensure_ascii=False))
    WF_DIR.mkdir(parents=True, exist_ok=True)
    TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    wf_path = WF_DIR / ("%s.json" % wf_id)
    wf_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    golden_path = TARGETS_DIR / ("%s_golden.json" % wf_id)
    golden_path.write_text(json.dumps({
        "request": request, "target_model_requested": TARGET_MODEL_REQUESTED,
        "generated_by": cloud_info, "at": _now(), **golden}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    tool_nodes = sum(1 for n in cfg["nodes"] if n["type"] == "tool")
    # 沉淀到本地工作流索引（下一同类任务可直接命中）
    rec = {"wf_id": wf_id, "task": request[:300], "path": str(wf_path),
           "nodes": len(cfg["nodes"]), "steps": len(cfg["nodes"]),
           "tool_nodes": tool_nodes, "workflow_name": cfg["workflow_name"],
           "created_at": _now(), "source": "cloud_built",
           "cloud": {"requested_model": TARGET_MODEL_REQUESTED,
                     "builder": cloud_info.get("provider"),
                     "model": cloud_info.get("model")},
           "golden_path": str(golden_path),
           "lint_errors": [], "lint_warnings": warns[:5]}
    try:
        DISTILL_DIR.mkdir(parents=True, exist_ok=True)
        with open(DISTILL_DIR / "workflows.jsonl", "a", encoding="utf-8") as _f:
            _f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        rec["error"] = "索引写入失败: %s" % str(e)[:200]
    out = {"ok": True, "wf_id": wf_id, "workflow": cfg, "workflow_path": str(wf_path),
           "golden_path": str(golden_path), "golden": golden,
           "tool_nodes": tool_nodes, "cloud": cloud_info,
           "target_model_requested": TARGET_MODEL_REQUESTED,
           "route": "cloud_build", "distilled": True, "lint": {"errors": [], "warnings": warns}}
    if execute:
        out["execute"] = execute_workflow(wf_id, request, golden)
    _append_jsonl(BUILDS_LOG, {"at": _now(), "request": request[:300], "wf_id": wf_id,
                               "cloud": cloud_info, "tool_nodes": tool_nodes,
                               "target_model_requested": TARGET_MODEL_REQUESTED,
                               "ok": True})
    return out


# ───────────────────────── ④ 本地执行 + 目标对照 ─────────────────────────
def execute_workflow(wf_id, input_text, golden=None):
    """本地执行工作流（真调平台工具）"""
    t0 = time.time()
    try:
        import trajectory_distill as td
        r = td.run_workflow(wf_id, input_text=str(input_text)[:4000],
                            timeout=int(os.environ.get("WORKFLOW_REUSE_TIMEOUT", "600")))
        res = {"ok": bool(r.get("ok")), "duration": r.get("duration"),
               "status": r.get("status"), "content": str(r.get("content") or "")[:2500],
               "result_path": r.get("path"), "error": r.get("error")}
        if golden:
            res["evaluation"] = evaluate_against_golden(golden, r, since_ts=t0)
        return res
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300])}


UPLOAD_DIR = PROJECT_DIR / "uploads"


def _find_latest_xlsx(since_ts=None):
    """找最近生成的 xlsx（优先 uploads/，即 write_sheet 的产物目录；可按运行时间过滤）"""
    cands = []
    for base in (UPLOAD_DIR, PROJECT_DIR / "outputs", PROJECT_DIR / "docs_out"):
        if base.exists():
            cands += [p for p in base.rglob("*.xlsx") if p.is_file()]
    if not cands:
        cands = [p for p in PROJECT_DIR.rglob("*.xlsx") if p.is_file()]
    if since_ts:
        cands = [p for p in cands if p.stat().st_mtime >= float(since_ts) - 5]
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def evaluate_against_golden(golden, run_result=None, since_ts=None):
    """本地产出（本次运行生成的 xlsx）vs 云端目标清单：行/列/单元格匹配率"""
    try:
        import openpyxl
        g_rows = golden.get("rows") or []
        g_cols = golden.get("columns") or []
        path = _find_latest_xlsx(since_ts=since_ts)
        if not path:
            return {"ok": False, "error": "未找到本地生成的 xlsx"}
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        ws = wb.active
        rows = [[("" if c is None else str(c)) for c in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
        # 去掉表头行后比较
        data_rows = rows[1:] if rows else []
        g_norm = [[str(c).strip() for c in r] for r in g_rows]
        n = min(len(data_rows), len(g_norm))
        total = sum(len(r) for r in g_norm[:n]) or 1
        matched = 0
        for i in range(n):
            for j in range(min(len(data_rows[i]), len(g_norm[i]))):
                if str(data_rows[i][j]).strip() == g_norm[i][j]:
                    matched += 1
        return {"ok": True, "xlsx_path": str(path),
                "golden_rows": len(g_norm), "local_rows": len(data_rows),
                "cell_match_rate": round(matched / total, 3),
                "row_count_match": len(data_rows) == len(g_norm)}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def list_builds(limit=20):
    rows = _read_jsonl(BUILDS_LOG)
    return rows[-int(limit):]


def summary_text():
    rows = list_builds(50)
    return "云端建工作流 %d 次；最近: %s" % (
        len(rows), (rows[-1].get("wf_id") if rows else "无"))
