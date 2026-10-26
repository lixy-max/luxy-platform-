# -*- coding: utf-8 -*-
"""trajectory_distill.py —— S4：轨迹拆步 → 工作流沉淀 → 复用检索（路径1 v1）

对齐《目标与方向统一 v2.0》B 决策：
  · 自动评分是否值得蒸馏（distill_score）
  · 实测一条轨迹能拆成多少步
  · 轨迹 → 工作流配置（workflow_engine 可执行）沉淀本地
  · 轨迹 → 单步解耦 LoRA 训练集（sharegpt，预留格式）
  · 相似任务 → 检索已沉淀工作流（下次直接走本地路径）

产物：
  data/distilled/workflows/<wf_id>.json   工作流配置（workflow_engine 格式）
  data/distilled/workflows.jsonl          工作流索引（含任务 embedding，供复用检索）
  training_data/distilled/<name>.jsonl    单步 LoRA 训练集（train/eval）
"""
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
TRAJ_DIR = PROJECT_DIR / "data" / "trajectories"
DISTILL_DIR = PROJECT_DIR / "data" / "distilled"
WF_DIR = DISTILL_DIR / "workflows"
WF_INDEX = DISTILL_DIR / "workflows.jsonl"
DATASET_DIR = PROJECT_DIR / "training_data" / "distilled"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ───────────────────── 轨迹读取/拆步 ─────────────────────
def _traj_files():
    out = []
    if not TRAJ_DIR.exists():
        return out
    for f in sorted(TRAJ_DIR.glob("*.jsonl")):
        if f.name.startswith("escalations"):
            continue
        out.append(f)
    return out


def iter_trajectories(limit_per_file=400, max_total=1600):
    """遍历轨迹记录（跳过 escalations；损坏行跳过）"""
    n = 0
    for f in _traj_files():
        try:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        cnt = 0
        for ln in lines:
            if n >= max_total:
                return
            ln = ln.strip()
            if not ln:
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if not isinstance(d, dict) or "task" not in d:
                continue
            d["_source"] = f.name
            d["_index"] = n
            yield d
            n += 1
            cnt += 1
            if cnt >= limit_per_file:
                break


def _tidy(t, limit=400):
    t = str(t or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t[:limit]


_SEM_MARKER_RE = re.compile(
    r"^\s*(?:#{1,4}\s+|第[一二三四五六七八九十百]+[步章节条]|[0-9]{1,2}[.、)]\s*|[-*•]\s+|【[^】]{1,24}】)")


def _semantic_split(text, max_parts=8, min_chars=25):
    """把一段文本按语义边界切分（B013）：标题/编号/列表标记优先，其次空行段落。

    返回 [(label, text)]。没有明确边界且不长的文本保持单步。
    """
    t = str(text or "").strip()
    if not t:
        return []
    lines = t.splitlines()
    marker_lines = [ln for ln in lines if _SEM_MARKER_RE.match(ln)]
    segs, cur, label = [], [], None
    if len(marker_lines) >= 2:
        # ① 结构标记（标题/第X步/1. / 1、/ - / 【小节】）
        for ln in lines:
            m = _SEM_MARKER_RE.match(ln)
            if m:
                if cur and "".join(cur).strip():
                    segs.append((label, "\n".join(cur).strip()))
                label = ln.strip()[:30]
                cur = [ln]
            else:
                cur.append(ln)
        if cur and "".join(cur).strip():
            segs.append((label, "\n".join(cur).strip()))
    elif len(t) >= 120:
        # ② 空行段落
        paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
        if len(paras) >= 2:
            segs = [(None, p) for p in paras]
    segs = [(l, x) for l, x in segs if x and x.strip()]
    if len(segs) < 2:
        return [(None, t)]
    # 合并过短片段：只合并"无标题且极短"的残片；有显式标记的段落保持独立（语义步）
    merged = []
    for l, x in segs:
        if merged and l is None and len(x) < min(15, min_chars):
            pl, px = merged[-1]
            merged[-1] = (pl, px + "\n" + x)
        else:
            merged.append((l, x))
    if len(merged) > max_parts:
        head, rest = merged[:max_parts - 1], merged[max_parts - 1:]
        merged = head + [(rest[0][0], "\n".join(x for _, x in rest))]
    return merged


def decompose(record, semantic=None):
    """轨迹 → 原子步骤列表

    v1（semantic=False）：一次工具调用 = 一步；一轮纯文本 = 一步；最终答案 = 一步。
    v2（semantic=True，B013）：
      · 有工具调用的回合，若文本是实质性思考/说明（≥20 字），先记一个「思考」语义步
      · 纯文本/最终答案按语义边界切分（标题/编号/列表/空行段落）
      · 工具调用仍以一次调用为一步（不把工具结果硬拆成多步）
    环境变量 DECOMPOSE_SEMANTIC=0 可回退 v1 行为。
    """
    if semantic is None:
        semantic = os.environ.get("DECOMPOSE_SEMANTIC", "1") not in ("0", "false", "False")
    steps = []
    for turn in (record.get("steps") or []):
        if not isinstance(turn, dict):
            continue
        tcs = turn.get("tool_calls") or []
        txt = _tidy(turn.get("text"), 1200)
        if tcs:
            # 思考/说明文本：语义上先于工具动作
            if semantic and txt and len(txt) >= 20:
                steps.append({"kind": "llm", "text": txt, "semantic": True,
                              "label": "思考/说明", "origin": "thinking"})
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                steps.append({
                    "kind": "tool",
                    "tool": str(tc.get("tool") or ""),
                    "input": tc.get("input"),
                    "output": _tidy(tc.get("result"), 600),
                    "status": str(tc.get("status") or ""),
                    "semantic": semantic, "label": "工具：%s" % str(tc.get("tool") or "")[:30],
                    "origin": "tool",
                })
        elif txt:
            if semantic:
                for lbl, piece in _semantic_split(txt):
                    steps.append({"kind": "llm", "text": piece, "semantic": True,
                                  "label": lbl, "origin": "text"})
            else:
                steps.append({"kind": "llm", "text": txt, "semantic": False, "origin": "text"})
    final = _tidy(record.get("final"), 1200)
    if final:
        if semantic:
            for lbl, piece in _semantic_split(final):
                steps.append({"kind": "answer", "text": piece, "semantic": True,
                              "label": lbl or "最终答案", "origin": "answer"})
        else:
            steps.append({"kind": "answer", "text": final, "semantic": False, "origin": "answer"})
    if not steps:
        steps = [{"kind": "answer", "text": _tidy(record.get("task"), 300),
                  "semantic": semantic, "label": "任务", "origin": "task"}]
    for i, s in enumerate(steps, 1):
        s["seq"] = i
    return steps


def _norm_task(task, n=80):
    t = str(task or "").lower()
    t = re.sub(r"[\s，,。？?！!：:；;\"'“”‘’（）()【】\[\]]+", "", t)
    return t[:n]


def _occurrence_map(records):
    m = {}
    for r in records:
        k = _norm_task(r.get("task"))
        m[k] = m.get(k, 0) + 1
    return m


def _tool_docs():
    try:
        d = json.loads((PROJECT_DIR / "agent_kb" / "_tool_docs.json").read_text(encoding="utf-8"))
        return set((d.get("tools") or {}).keys())
    except Exception:
        return set()


def _tool_quality(record):
    errs = int(len(record.get("tool_errors") or []))
    if not (record.get("tools_used") or []):
        return "B"
    return "A" if errs == 0 else ("B" if errs == 1 else ("C" if errs <= 3 else "D"))


DEV_TOOLS = {"pwsh", "read", "write", "edit", "grep", "glob", "bash", "web_search",
             "web_fetch", "todo_write", "task", "subagent", "apply_patch", "list_dir"}


def _tool_origin(tools):
    if not tools:
        return "none"
    dev = sum(1 for t in tools if str(t) in DEV_TOOLS)
    return "dev" if dev / len(tools) >= 0.5 else "platform"


def score_record(record, steps, occurrence_map, doc_tools):
    import distill_score as ds
    tools = list(record.get("tools_used") or [])
    origin = _tool_origin(tools)
    if origin == "dev":
        # 开发工具轨迹不是平台能力候选：novelty 按"已覆盖"算（B017 修复）
        covered = True
    else:
        covered = bool(tools) and all(t in doc_tools for t in tools)
    rec = {
        "success": (record.get("reason") == "completed" and not record.get("error")
                    and bool(record.get("final"))),
        "occurrence": max(0, int(occurrence_map.get(_norm_task(record.get("task")), 1)) - 1),
        "covered": covered,
        "steps": len(steps),
        "tool_quality": _tool_quality(record),
        "has_tool_calls": bool(tools),
    }
    s = ds.score(rec)
    rec["tool_origin"] = origin
    rec["dev_trajectory"] = (origin == "dev")
    return s, rec


def stats(limit_per_file=400, max_total=1600):
    """轨迹拆步实测统计（能拆多少步/成功率/工具分布/评分分布）"""
    records = list(iter_trajectories(limit_per_file, max_total))
    occ = _occurrence_map(records)
    docs = _tool_docs()
    buckets = {"1步": 0, "2-3步": 0, "4-6步": 0, "7-10步": 0, "11+步": 0}
    scores = []
    steps_list = []
    tools = {}
    success = 0
    examples = []
    steps_v1_list = []
    for r in records:
        steps = decompose(r)
        k = len(steps)
        steps_list.append(k)
        steps_v1_list.append(len(decompose(r, semantic=False)))
        if k <= 1:
            buckets["1步"] += 1
        elif k <= 3:
            buckets["2-3步"] += 1
        elif k <= 6:
            buckets["4-6步"] += 1
        elif k <= 10:
            buckets["7-10步"] += 1
        else:
            buckets["11+步"] += 1
        if r.get("reason") == "completed" and not r.get("error") and r.get("final"):
            success += 1
        for t in (r.get("tools_used") or []):
            tools[t] = tools.get(t, 0) + 1
        s, feat = score_record(r, steps, occ, docs)
        scores.append(s["score"])
        if s["level"] in ("high", "watch") and 2 <= k <= 6 and len(examples) < 8:
            examples.append({"index": r.get("_index"), "source": r.get("_source"),
                             "task": _tidy(r.get("task"), 100), "steps": k,
                             "score": s["score"], "level": s["level"],
                             "tools": list(r.get("tools_used") or [])[:5]})
    n = len(records) or 1
    steps_list.sort()
    steps_v1_list.sort()

    def _dist(lst):
        if not lst:
            return {"min": 0, "median": 0, "mean": 0, "max": 0,
                    "buckets": {"1步": 0, "2-3步": 0, "4-6步": 0, "7-10步": 0, "11+步": 0}}
        b = {"1步": 0, "2-3步": 0, "4-6步": 0, "7-10步": 0, "11+步": 0}
        for k in lst:
            if k <= 1:
                b["1步"] += 1
            elif k <= 3:
                b["2-3步"] += 1
            elif k <= 6:
                b["4-6步"] += 1
            elif k <= 10:
                b["7-10步"] += 1
            else:
                b["11+步"] += 1
        return {"min": lst[0], "median": lst[len(lst) // 2],
                "mean": round(sum(lst) / len(lst), 2), "max": lst[-1], "buckets": b}
    steps_v1_list.sort()
    scores.sort()
    def _pct(lst, p):
        if not lst:
            return 0
        return lst[min(len(lst) - 1, int(len(lst) * p))]
    return {
        "records": len(records),
        "success_rate": round(success / n, 3),
        "steps_mode": "semantic",
        "steps_v1": _dist(steps_v1_list),
        "steps": {"min": steps_list[0], "median": steps_list[len(steps_list) // 2],
                  "mean": round(sum(steps_list) / n, 1), "max": steps_list[-1],
                  "buckets": buckets},
        "score": {"mean": round(sum(scores) / n, 1),
                  "high": sum(1 for x in scores if x >= 70),
                  "watch": sum(1 for x in scores if 50 <= x < 70),
                  "skip": sum(1 for x in scores if x < 50)},
        "top_tools": sorted(tools.items(), key=lambda x: -x[1])[:10],
        "decomposable_2_6": buckets["2-3步"] + buckets["4-6步"],
        "examples": examples,
        "generated_at": _now(),
    }


# ───────────────────── 轨迹 → 工作流 ─────────────────────
def _wf_id(record):
    raw = "%s|%s|%s" % (record.get("task"), record.get("session_id"), time.time())
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def workflow_from_record(record, name=None):
    """轨迹 → workflow_engine 配置（v2：工具步骤映射为真实 tool 节点）

    · 轨迹里的工具若在 workflow_engine.TOOL_NODE_SUPPORTED 中 → type="tool"（真实 HTTP 调用）
    · 其余步骤 → process（LLM）；末尾追加 aggregate 汇总节点
    """
    import workflow_engine as we
    steps = decompose(record)
    if len(steps) > 12:
        steps = steps[:12]
    supported = getattr(we, "TOOL_NODE_SUPPORTED", {}) or {}
    wfid = _wf_id(record)
    task = _tidy(record.get("task"), 120)
    nodes = [{
        "id": "n0", "type": "input", "description": "任务输入（轨迹原始任务）",
        "input_from": [], "prompt": "", "position": {"x": 0, "y": 0},
        "provider": None, "model_override": None,
    }]
    prev = "n0"
    n_tool = 0
    for i, st in enumerate(steps, 1):
        nid = "n%d" % i
        if st["kind"] == "tool" and st.get("tool") in supported:
            # ★ B014：真实工具节点（不再 LLM 模拟）
            try:
                args = st.get("input") or {}
                if not isinstance(args, dict):
                    args = {"input": str(args)[:500]}
            except Exception:
                args = {}
            nodes.append({
                "id": nid, "type": "tool", "description": "真实工具：%s" % st.get("tool"),
                "input_from": [prev], "tool": st.get("tool"), "tool_args": args,
                "tool_timeout": 60, "position": {"x": 240 * i, "y": 0},
            })
            n_tool += 1
        else:
            prompt = ("步骤 %d（原轨迹工具：%s）。工具输入：%s\n上游结果：\n{%s}\n"
                      "请模拟执行该工具步骤并给出结果；若无法真实调用工具，请如实说明这是近似模拟。"
                      % (i, st.get("tool"), json.dumps(st.get("input"), ensure_ascii=False)[:300], prev)
                      if st["kind"] == "tool" else
                      "步骤 %d：%s\n上游结果：\n{%s}\n请执行该步骤并给出结果。"
                      % (i, _tidy(st.get("text"), 400), prev))
            nodes.append({
                "id": nid, "type": "process", "description": "LLM 步骤 %d" % i,
                "input_from": [prev], "prompt": prompt, "provider": "vllm",
                "output_format": "text", "max_tokens": 1200, "temperature": 0.2,
                "cache": False, "position": {"x": 240 * i, "y": 0}, "model_override": None,
            })
        prev = nid
    # 末尾汇总（aggregate）：把结果整理成最终报告
    fin = "n%d" % (len(steps) + 1)
    nodes.append({
        "id": fin, "type": "aggregate", "description": "汇总最终报告",
        "input_from": [prev],
        "prompt": "以下是执行过程与结果：\n{%s}\n\n请基于以上内容输出最终报告（结构化、可读）。" % prev,
        "provider": "vllm", "output_format": "text", "max_tokens": 1500,
        "temperature": 0.2, "cache": False, "position": {"x": 240 * (len(steps) + 1), "y": 0},
        "model_override": None,
    })
    cfg = {
        "workflow_name": name or ("蒸馏工作流 · %s" % task[:40]),
        "description": "由轨迹蒸馏自动生成（B014：工具步骤真实调用）：%s" % task[:160],
        "author": "trajectory_distill",
        "tags": ["distilled", "trajectory", "tool_node"],
        "required_loras": [],
        "required_providers": ["vllm"],
        "source": {"trajectory_index": record.get("_index"), "source_file": record.get("_source"),
                   "task": task, "steps": len(steps), "tool_nodes": n_tool},
        "nodes": nodes,
    }
    errors, warnings = we.lint_config(cfg)
    WF_DIR.mkdir(parents=True, exist_ok=True)
    path = WF_DIR / ("wf_%s.json" % wfid)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    idx = _index_append({
        "wf_id": wfid, "task": task, "path": str(path), "nodes": len(nodes),
        "steps": len(steps), "tool_nodes": n_tool,
        "workflow_name": cfg["workflow_name"], "created_at": _now(),
        "lint_errors": errors, "lint_warnings": warnings[:5],
        "source_file": record.get("_source"), "trajectory_index": record.get("_index"),
    })
    return {"wf_id": wfid, "path": str(path), "nodes": len(nodes), "steps": len(steps),
            "tool_nodes": n_tool, "lint": {"errors": errors, "warnings": warnings},
            "workflow_name": cfg["workflow_name"], "config": cfg}


# ───────────────────── 轨迹 → 单步 LoRA 数据集 ─────────────────────
def dataset_from_record(record, eval_ratio=0.2):
    """轨迹 → sharegpt 单步样本（train/eval），每条样本=一个步骤（含前序上下文）"""
    steps = decompose(record)
    task = _tidy(record.get("task"), 300)
    samples = []
    prev_outputs = []
    for st in steps:
        ctx = "\n".join(prev_outputs[-3:])[:1500]
        user = "任务：%s\n已完成步骤：\n%s\n当前步骤：%s" % (
            task, ctx or "（无）",
            ("调用工具 %s，输入 %s" % (st.get("tool"), json.dumps(st.get("input"), ensure_ascii=False)[:300]))
            if st["kind"] == "tool" else _tidy(st.get("text"), 500))
        if st["kind"] == "tool":
            assistant = json.dumps({"tool_calls": [{"name": st.get("tool"),
                                                    "input": st.get("input"),
                                                    "result": _tidy(st.get("output"), 800)}]},
                                   ensure_ascii=False)
        else:
            assistant = _tidy(st.get("text"), 1500)
        samples.append({"conversations": [{"role": "user", "content": user},
                                          {"role": "assistant", "content": assistant}],
                        "metadata": {"task": task, "step": st["seq"], "kind": st["kind"],
                                     "tool": st.get("tool"), "source_file": record.get("_source")}})
        prev_outputs.append(_tidy(st.get("text") or st.get("output"), 300))
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(("%s|%s" % (task, time.time())).encode()).hexdigest()[:10]
    n_eval = max(1, int(len(samples) * float(eval_ratio))) if len(samples) > 2 else 0
    train, evals = samples[n_eval:], samples[:n_eval]
    tpath = DATASET_DIR / ("%s_train.jsonl" % name)
    epath = DATASET_DIR / ("%s_eval.jsonl" % name)
    tpath.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in train) + "\n", encoding="utf-8")
    epath.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in evals) + "\n", encoding="utf-8")
    avg = round(sum(len(json.dumps(x, ensure_ascii=False)) for x in samples) / max(1, len(samples)), 1)
    return {"name": name, "samples": len(samples), "train": len(train), "eval": len(evals),
            "train_path": str(tpath), "eval_path": str(epath), "avg_chars": avg,
            "format": "sharegpt", "steps": len(steps)}


# ───────────────────── 工作流索引/复用检索 ─────────────────────
def _index_append(rec):
    DISTILL_DIR.mkdir(parents=True, exist_ok=True)
    with open(WF_INDEX, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def list_workflows(limit=30):
    if not WF_INDEX.exists():
        return []
    rows = []
    for ln in WF_INDEX.read_text(encoding="utf-8", errors="ignore").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    return rows[-int(limit):]



def record_reuse(wf_id, task="", score=None, channel="unknown"):
    """S9：记录工作流复用（命中即执行时打点，供进化指标）"""
    try:
        import json as _json
        import time as _time
        p = DISTILL_DIR / "reuse_log.jsonl"
        DISTILL_DIR.mkdir(parents=True, exist_ok=True)
        rec = {"at": _time.strftime("%Y-%m-%dT%H:%M:%S"), "wf_id": wf_id,
               "task": str(task)[:200], "score": score, "channel": channel}
        with open(p, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def search_workflows(task, limit=5):
    """相似任务 → 已沉淀工作流（复用路径）"""
    rows = list_workflows(limit=500)
    if not rows:
        return []
    import kb
    import struct
    q = str(task or "")
    _err = ""
    try:
        qv = kb._encode([q], is_query=True)[0]
    except Exception:
        qv = None
    out = []
    for r in rows:
        t = ""
        try:
            t = (Path(r.get("path") or "").exists() and
                 json.loads(Path(r["path"]).read_text(encoding="utf-8")).get("workflow_name")) or ""
        except Exception:
            pass
        score = 0.0
        if qv is not None:
            try:
                tv = kb._encode([r.get("task") or t], is_query=True)[0]
                na = math.sqrt(sum(x * x for x in qv)) or 1
                nb = math.sqrt(sum(x * x for x in tv)) or 1
                score = sum(a * b for a, b in zip(qv, tv)) / (na * nb)
            except Exception as _e2:
                _err = "%s: %s" % (type(_e2).__name__, str(_e2)[:200])
                score = 0.0
        if score <= 0:
            # 关键词兜底（embedding 冷启动失败/不相关时）：字符 2-gram 重合度
            def _grams(x):
                x = re.sub(r"[\s，,。？?！!：:；;]+", "", str(x or "").lower())
                return {x[i:i + 2] for i in range(max(0, len(x) - 1))}
            qg, tg = _grams(task), _grams(r.get("task") or t)
            score = (len(qg & tg) / max(1, len(qg))) * 0.6 if qg and tg else 0.0
        _created = str(r.get("created_at") or "")
        _age = None
        try:
            import datetime as _dt
            _age = round((_dt.datetime.now() - _dt.datetime.fromisoformat(_created)).total_seconds() / 86400, 1)
        except Exception:
            pass
        _stale_days = float(os.environ.get("WORKFLOW_STALE_DAYS", "7"))
        out.append({"wf_id": r.get("wf_id"), "task": r.get("task"), "path": r.get("path"),
                    "workflow_name": r.get("workflow_name"), "score": round(score, 4),
                    "steps": r.get("steps"), "created_at": _created,
                    "age_days": _age, "stale": bool(_age is not None and _age > _stale_days),
                    "encode_error": _err or None})
    out.sort(key=lambda x: -x["score"])
    return out[:int(limit)]


def build(record_index=None):
    """完整蒸馏：选轨迹 → 评分 → 工作流 + 数据集（返回两者路径）"""
    records = list(iter_trajectories())
    if not records:
        raise ValueError("没有可用轨迹")
    occ = _occurrence_map(records)
    docs = _tool_docs()
    scored = []
    for r in records:
        steps = decompose(r)
        if not (2 <= len(steps) <= 10):
            continue
        s, feat = score_record(r, steps, occ, docs)
        scored.append((s["score"], s, r, steps))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        raise ValueError("没有 2–10 步的轨迹可蒸馏")
    if record_index is not None:
        for item in scored:
            if item[2].get("_index") == int(record_index):
                chosen = item
                break
        else:
            chosen = scored[0]
    else:
        chosen = scored[0]
    score, s, record, steps = chosen
    wf = workflow_from_record(record)
    ds = dataset_from_record(record)
    return {"record_index": record.get("_index"), "source_file": record.get("_source"),
            "task": _tidy(record.get("task"), 160), "steps": len(steps),
            "score": s, "workflow": wf, "dataset": ds}

# ───────────────────── 工作流执行（命中即执行）─────────────────────
WF_RUN_DIR = DISTILL_DIR / "runs"


def get_workflow(wf_id):
    """按 wf_id 从索引取工作流记录"""
    for r in list_workflows(limit=1000):
        if str(r.get("wf_id")) == str(wf_id):
            return r
    return None




def _record_wf_exec(wf_id, ok):
    """工作流执行计入治理预算（B020）"""
    try:
        import governance as _gov
        _gov.record_exec("workflow:%s" % wf_id, ok=bool(ok))
    except Exception:
        pass

def run_workflow(wf_id, input_text="", timeout=None):
    """执行已沉淀工作流：输入文本 → workflow_engine.run_workflow → 解析结果

    · 用子进程执行（避免阻塞后端；workflow_engine 日志不会污染 API 进程）
    · 返回 {ok, content, path, duration, usage, status, wf_id, workflow_name}
    """
    import subprocess
    import sys as _sys
    wf = get_workflow(wf_id)
    if not wf or not Path(str(wf.get("path") or "")).exists():
        raise ValueError("工作流不存在或文件缺失: %s" % wf_id)
    # 修 B020：工作流执行纳入治理（冻结/预算）
    try:
        import governance as _gov
        if _gov.is_frozen():
            return {"ok": False, "content": "", "wf_id": wf_id,
                    "error": "系统已冻结（KILL 开关）", "duration": 0}
        _ok, _why = _gov.check_exec_allowed()
        if not _ok:
            return {"ok": False, "content": "", "wf_id": wf_id,
                    "error": "治理层拒绝: %s" % _why, "duration": 0}
    except Exception:
        pass
    WF_RUN_DIR.mkdir(parents=True, exist_ok=True)
    inp = WF_RUN_DIR / ("input_%s_%s.txt" % (wf_id, time.strftime("%Y%m%d_%H%M%S")))
    inp.write_text(str(input_text or "")[:6000] or "（无输入）", encoding="utf-8")
    timeout = int(timeout or os.environ.get("WORKFLOW_REUSE_TIMEOUT", "600"))
    code = (
        "import sys, json\n"
        "sys.path.insert(0, %r)\n"
        "import workflow_engine as we\n"
        "print(json.dumps(we.run_workflow(%r, %r)))\n"
    ) % (str(PROJECT_DIR), str(inp), str(wf["path"]))
    t0 = time.time()
    try:
        r = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=timeout, cwd=str(PROJECT_DIR))
    except subprocess.TimeoutExpired:
        return {"ok": False, "content": "", "error": "工作流执行超时（%ss）" % timeout,
                "duration": round(time.time() - t0, 1), "wf_id": wf_id}
    duration = round(time.time() - t0, 1)
    out_path = ""
    for ln in reversed((r.stdout or "").strip().splitlines()):
        ln = ln.strip()
        if ln.startswith('"') and ln.endswith('"'):
            try:
                import json as _json
                out_path = _json.loads(ln)
            except Exception:
                out_path = ln.strip('"')
            break
        if ln and Path(ln).exists():
            out_path = ln
            break
    if not out_path or not Path(out_path).exists():
        return {"ok": False, "content": "", "duration": duration, "wf_id": wf_id,
                "error": "未找到运行结果（stderr: %s）" % (r.stderr or "")[-300:]}
    try:
        d = json.loads(Path(out_path).read_text(encoding="utf-8"))
        status = str(d.get("status") or "")
        content = str(d.get("final_report") or "")
        if not content:
            nodes = d.get("nodes") or {}
            content = str(list(nodes.values())[-1] if nodes else "")[:4000]
        usage = (d.get("_meta") or {}).get("usage") or {}
        _record_wf_exec(wf_id, status == "success")
        return {"ok": status == "success", "content": content[:6000], "path": out_path,
                "duration": duration, "status": status, "usage": usage,
                "wf_id": wf_id, "workflow_name": wf.get("workflow_name")}
    except Exception as e:
        _record_wf_exec(wf_id, False)
        return {"ok": False, "content": "", "duration": duration, "wf_id": wf_id,
                "error": "结果解析失败: %s" % str(e)[:200], "path": out_path}
