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


def decompose(record):
    """轨迹 → 原子步骤列表

    步骤粒度（v1）：一次工具调用 = 一步；一轮纯文本 = 一步；最终答案 = 一步。
    """
    steps = []
    for turn in (record.get("steps") or []):
        if not isinstance(turn, dict):
            continue
        tcs = turn.get("tool_calls") or []
        if tcs:
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                steps.append({
                    "kind": "tool",
                    "tool": str(tc.get("tool") or ""),
                    "input": tc.get("input"),
                    "output": _tidy(tc.get("result"), 600),
                    "status": str(tc.get("status") or ""),
                })
        else:
            txt = _tidy(turn.get("text"), 600)
            if txt:
                steps.append({"kind": "llm", "text": txt})
    final = _tidy(record.get("final"), 800)
    if final:
        steps.append({"kind": "answer", "text": final})
    if not steps:
        steps = [{"kind": "answer", "text": _tidy(record.get("task"), 300)}]
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


def score_record(record, steps, occurrence_map, doc_tools):
    import distill_score as ds
    tools = list(record.get("tools_used") or [])
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
    for r in records:
        steps = decompose(r)
        k = len(steps)
        steps_list.append(k)
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
    scores.sort()
    return {
        "records": len(records),
        "success_rate": round(success / n, 3),
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
    """轨迹 → workflow_engine 配置（线性：input → process… → aggregate）"""
    import workflow_engine as we
    steps = decompose(record)
    if len(steps) > 12:
        steps = steps[:12]
    wfid = _wf_id(record)
    task = _tidy(record.get("task"), 120)
    nodes = [{
        "id": "n0", "type": "input", "description": "任务输入（轨迹原始任务）",
        "input_from": [], "prompt": "", "position": {"x": 0, "y": 0},
        "provider": None, "model_override": None,
    }]
    prev = "n0"
    for i, st in enumerate(steps, 1):
        nid = "n%d" % i
        last = (i == len(steps))
        ntype = "aggregate" if last else "process"
        if st["kind"] == "tool":
            prompt = ("步骤 %d（原轨迹工具：%s）。工具输入：%s\n上游结果：\n{%s}\n"
                      "请模拟执行该工具步骤并给出结果；若无法真实调用工具，请如实说明这是近似模拟。"
                      % (i, st.get("tool"), json.dumps(st.get("input"), ensure_ascii=False)[:300], prev))
            desc = "工具步骤：%s" % st.get("tool")
        else:
            prompt = ("步骤 %d：%s\n上游结果：\n{%s}\n请执行该步骤并给出结果。"
                      % (i, _tidy(st.get("text"), 400), prev))
            desc = "LLM 步骤 %d" % i
        nodes.append({
            "id": nid, "type": ntype, "description": desc, "input_from": [prev],
            "prompt": prompt, "provider": "vllm", "output_format": "text",
            "max_tokens": 1200, "temperature": 0.2, "cache": False,
            "position": {"x": 240 * i, "y": 0}, "model_override": None,
        })
        prev = nid
    cfg = {
        "workflow_name": name or ("蒸馏工作流 · %s" % task[:40]),
        "description": "由轨迹蒸馏自动生成（S4 v1）：%s" % task[:160],
        "author": "trajectory_distill",
        "tags": ["distilled", "trajectory"],
        "required_loras": [],
        "required_providers": ["vllm"],
        "source": {"trajectory_index": record.get("_index"), "source_file": record.get("_source"),
                   "task": task, "steps": len(steps)},
        "nodes": nodes,
    }
    errors, warnings = we.lint_config(cfg)
    WF_DIR.mkdir(parents=True, exist_ok=True)
    path = WF_DIR / ("wf_%s.json" % wfid)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    idx = _index_append({
        "wf_id": wfid, "task": task, "path": str(path), "nodes": len(nodes),
        "steps": len(steps), "workflow_name": cfg["workflow_name"],
        "created_at": _now(), "lint_errors": errors, "lint_warnings": warnings[:5],
        "source_file": record.get("_source"), "trajectory_index": record.get("_index"),
    })
    return {"wf_id": wfid, "path": str(path), "nodes": len(nodes), "steps": len(steps),
            "lint": {"errors": errors, "warnings": warnings}, "workflow_name": cfg["workflow_name"],
            "config": cfg}


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
        out.append({"wf_id": r.get("wf_id"), "task": r.get("task"), "path": r.get("path"),
                    "workflow_name": r.get("workflow_name"), "score": round(score, 4),
                    "steps": r.get("steps"), "encode_error": _err or None})
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
