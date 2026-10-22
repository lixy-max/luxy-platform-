#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dataset_toolkit.py — 训练数据格式的【唯一真相】（P-73 / P0）

★ 设计目标（2026-10-19）:
  · 生成端 / 上传端 / 训练端【三处共用】同一套格式规范 ✓
    （现状: 三处各说各话 ✗ → 生成端产非法格式 ✗ 上传端不校验 ✗ 训练端口径错 ✗）

★ 依据:
  · docs/DATA_QUALITY_SPEC.md（F1-F8 格式 + Q1-Q8 质量 + N1-N4 数量 ✓）
  · LLaMA-Factory 官方: human/observation 在奇数位, gpt/function 在偶数位
  · system 是【顶层独立字段】✗ 不在 conversations 里

★ 公开 API（零依赖 ✓ 只用标准库）:
    parse_any(path)            → records[]
    normalize(record)          → 标准 sharegpt record
    validate(record, tools)    → (ok: bool, problems: list[str])
    validate_dataset(records)  → 数据集级报告 dict（Q3/Q5/Q6/Q8 ✓）
    clean(records, opts)       → (cleaned: list, stats: dict)
    find_duplicates(records)   → (exact: list, near: list)
    sort_records(records, by)  → records[]
    split(records, ratio)      → (train, val)
    report(records)            → dict（可直接 JSON 给前端 ✓）
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import random
import re
from collections import Counter
from typing import Any

# ══════════════════════════════════════════════════════════
# 一、格式规范（唯一真相 ✓）
# ══════════════════════════════════════════════════════════
SHAREGPT_SPEC = {
    "system_field": "system",          # 顶层独立字段 ✓
    "conversations_key": "conversations",
    "tools_key": "tools",              # JSON 字符串 ✓ 不是对象
    # LLaMA-Factory: human/observation 在奇数位, gpt/function_call 在偶数位
    "odd_roles": ("human", "observation"),
    "even_roles": ("gpt", "function_call"),
    "last_role": "gpt",
    "function_role": "function_call",
    "observation_role": "observation",
    "human_role": "human",
    "gpt_role": "gpt",
}

# 质量阈值（来自 DATA_QUALITY_SPEC ✓）
Q1_SYSTEM_RATIO_MAX = 0.20      # system 占比 < 20%
Q2_SYSTEM_CHARS_MAX = 400       # system ≤ 400 字符
Q3_DIVERSITY_MIN = 0.60         # 句子多样性 ≥ 60%
Q4_ASK_RATIO_MIN = 0.15         # 追问/拒答 ≥ 15%
Q5_DUP_MAX = 0                 # 零重复
Q6_SAME_TASK_MAX = 8            # 同任务变体 ≤ 8
Q8_SINGLE_TOOL_RATIO_MIN = 0.40  # 单工具样本 ≥ 40%

# 工具白名单缓存 ✓
_TOOLS_CACHE: set[str] | None = None


def _project_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent


def load_tool_whitelist() -> set[str]:
    """从 agent_kb/_tools.json 加载工具白名单 ✓（缓存 ✓）"""
    global _TOOLS_CACHE
    if _TOOLS_CACHE is not None:
        return _TOOLS_CACHE
    p = _project_dir() / "agent_kb" / "_tools.json"
    names: set[str] = set()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            names = set(d.keys())
        elif isinstance(d, list):
            names = {x.get("name") for x in d if isinstance(x, dict) and x.get("name")}
    except Exception:
        names = set()
    _TOOLS_CACHE = names
    return names


# ══════════════════════════════════════════════════════════
# 二、解析（多格式 → 原始记录列表）
# ══════════════════════════════════════════════════════════
def parse_any(path: str | pathlib.Path) -> list[dict]:
    """解析 jsonl / json / csv ✓ 自动识别 ✓"""
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    suffix = p.suffix.lower()

    if suffix == ".jsonl" or suffix == ".ndjson":
        out = []
        with open(p, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    if isinstance(d, dict):
                        out.append(d)
                except Exception:
                    continue   # 跳过坏行 ✓（不中断 ✓）
        return out

    if suffix == ".json":
        raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            # 可能是 {data: [...]} / {samples: [...]} ✓
            for k in ("data", "samples", "records", "items", "rows"):
                if isinstance(raw.get(k), list):
                    return [x for x in raw[k] if isinstance(x, dict)]
            return [raw]
        return []

    if suffix == ".csv":
        out = []
        with open(p, encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                out.append(dict(row))
        return out

    # 兜底: 按 jsonl 试 ✓
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = line.strip()
        if line:
            try:
                d = json.loads(line)
                if isinstance(d, dict):
                    out.append(d)
            except Exception:
                continue
    return out


# ══════════════════════════════════════════════════════════
# 三、归一化（任意格式 → 标准 sharegpt ✓）
# ══════════════════════════════════════════════════════════
def _to_sharegpt_turn(role: str, content: Any) -> dict:
    """把一条消息转成 sharegpt turn ✓"""
    return {"from": role, "value": "" if content is None else str(content)}


def _norm_role(r: str) -> str:
    """角色名归一化 ✓"""
    r = (r or "").strip().lower()
    m = {
        "system": "system",
        "human": "human", "user": "human", "prompter": "human", "question": "human",
        "gpt": "gpt", "assistant": "gpt", "model": "gpt", "answer": "gpt", "bot": "gpt",
        "function_call": "function_call", "function": "function_call", "tool_call": "function_call",
        "observation": "observation", "tool": "observation", "tool_result": "observation",
        "function_result": "observation",
    }
    return m.get(r, r)


def normalize(record: dict, *, strict: bool = False) -> dict:
    """把任意格式归一化成标准 sharegpt ✓

    支持输入:
      · sharegpt（conversations / messages ✓）
      · alpaca（instruction / input / output ✓）
      · openai messages（[{role, content}] ✓）
      · 旧格式（conversations[0].from == 'system' ✗ → 提到顶层 ✓）

    Returns:
        标准结构:
        {"system": str?, "tools": str?, "conversations": [{"from":..,"value":..}],
         "_source_format": str, "_fixes": [str]}
    """
    fixes: list[str] = []
    out: dict[str, Any] = {}
    system = None
    tools = None
    conv: list[dict] = []

    # ── ① system 独立字段 ✓ ──
    if isinstance(record.get("system"), str) and record["system"].strip():
        system = record["system"]

    # ── ② tools（必须是 JSON 字符串 ✓）──
    raw_tools = record.get("tools")
    if raw_tools is not None:
        if isinstance(raw_tools, str):
            tools = raw_tools
        elif isinstance(raw_tools, (list, dict)):
            tools = json.dumps(raw_tools, ensure_ascii=False)
            fixes.append("tools 对象 → JSON 字符串 ✓")

    # ── ③ conversations / messages ──
    src = None
    if isinstance(record.get("conversations"), list):
        src = "sharegpt"
        conv = [x for x in record["conversations"] if isinstance(x, dict)]
    elif isinstance(record.get("messages"), list):
        src = "openai"
        conv = [{"from": _norm_role(str(m.get("role", ""))), "value": m.get("content", "")}
                for m in record["messages"] if isinstance(m, dict)]
        fixes.append("openai messages → sharegpt ✓")
    elif "instruction" in record or "output" in record:
        src = "alpaca"
        instr = str(record.get("instruction") or "").strip()
        inp = str(record.get("input") or "").strip()
        outp = str(record.get("output") or "").strip()
        human = (instr + ("\n" + inp if inp else "")).strip()
        conv = [
            {"from": "human", "value": human},
            {"from": "gpt", "value": outp},
        ]
        fixes.append("alpaca → sharegpt ✓")

    if src is None:
        out["_source_format"] = "unknown"
        out["_fixes"] = fixes + ["无法识别的格式 ✗"]
        out["conversations"] = conv
        if system:
            out["system"] = system
        if tools:
            out["tools"] = tools
        return out

    # ── ④ 角色归一化 ✓ ──
    norm_conv = []
    for c in conv:
        f = _norm_role(str(c.get("from", "")))
        v = c.get("value", "")
        norm_conv.append({"from": f, "value": "" if v is None else str(v)})
    conv = norm_conv

    # ── ⑤ 旧格式修复: conversations[0] 是 system ✗ → 提到顶层 ✓ ──
    if conv and conv[0].get("from") == "system":
        if not system:
            system = conv[0]["value"]
        conv = conv[1:]
        fixes.append("conversations[0] 的 system → 顶层 system 字段 ✓")
    # 中间混入的 system 也提出来 ✓
    cleaned = []
    for c in conv:
        if c.get("from") == "system":
            if not system:
                system = c["value"]
            fixes.append("conversations 中混入 system → 已剔除 ✓")
            continue
        cleaned.append(c)
    conv = cleaned

    # ── ⑥ 位置重排检查（human/observation 奇数位 ✓）──
    #     旧格式错位: [system, human, function_call, observation, gpt] → 取掉 system 后自然对齐 ✓
    #     但要防 [human, human, gpt] 这种 → 合并连续 human ✓
    merged: list[dict] = []
    for c in conv:
        if merged and c["from"] == "human" and merged[-1]["from"] == "human":
            merged[-1]["value"] = merged[-1]["value"] + "\n" + c["value"]
            fixes.append("连续 human 合并 ✓")
        else:
            merged.append(c)
    conv = merged

    # ── ⑦ 首尾补齐（F1: 首位必须 human ✓ / F4: 末位必须 gpt ✓）──
    if conv and conv[0]["from"] != "human":
        if conv[0]["from"] == "observation":
            # 没有 human 的观测 → 丢弃该观测 ✓
            conv = conv[1:]
            fixes.append("首位 observation 无对应 human → 丢弃 ✓")
    if conv and conv[-1]["from"] != "gpt":
        if conv[-1]["from"] == "human":
            conv.append({"from": "gpt", "value": ""})
            fixes.append("末位 human 后补空 gpt ✓")
        elif conv[-1]["from"] in ("function_call", "observation"):
            conv.append({"from": "gpt", "value": ""})
            fixes.append("末位 function/observation 后补 gpt ✓")

    out["_source_format"] = src
    out["_fixes"] = fixes
    out["conversations"] = conv
    if system:
        out["system"] = system
    if tools:
        out["tools"] = tools
    return out


# ══════════════════════════════════════════════════════════
# 四、校验（F1-F8 ✓ 逐条）
# ══════════════════════════════════════════════════════════
def validate(record: dict, tool_whitelist: set[str] | None = None) -> tuple[bool, list[str]]:
    """逐条校验 F1-F8 + Q1/Q2 ✓

    Returns:
        (ok, problems)  problems = ['F1: ...', 'Q2: ...']
    """
    problems: list[str] = []
    wl = tool_whitelist if tool_whitelist is not None else load_tool_whitelist()

    conv = record.get("conversations")
    if not isinstance(conv, list) or not conv:
        return False, ["F0: 缺少 conversations 或为空 ✗"]

    # ── F5: system 不能在 conversations 里 ✓ ──
    for i, c in enumerate(conv):
        if isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "system":
            problems.append("F5: conversations[%d] 是 system ✗（必须是顶层 system 字段）" % i)
            break

    # ── 角色序列 ✓ ──
    roles = [_norm_role(str(c.get("from", ""))) if isinstance(c, dict) else "?" for c in conv]

    # F1: 首位 human ✓
    if roles and roles[0] != "human":
        problems.append("F1: conversations[0].from = %r ✗（必须 human）" % roles[0])

    # F2: 偶数位（1-based 偶数 = 索引奇数）必须 gpt/function_call ✓
    for idx in range(1, len(roles), 2):
        if roles[idx] not in ("gpt", "function_call"):
            problems.append("F2: conversations[%d].from = %r ✗（必须 gpt/function_call）" % (idx, roles[idx]))
            break

    # F3: 奇数位（索引偶数, >=2）必须 observation/human ✓
    for idx in range(2, len(roles), 2):
        if roles[idx] not in ("observation", "human"):
            problems.append("F3: conversations[%d].from = %r ✗（必须 observation/human）" % (idx, roles[idx]))
            break

    # F4: 末位 gpt ✓
    if roles and roles[-1] != "gpt":
        problems.append("F4: 末位 from = %r ✗（必须 gpt）" % roles[-1])

    # ── F6: tools 必须是 JSON 字符串 ✓ ──
    if "tools" in record and record["tools"] is not None:
        t = record["tools"]
        if not isinstance(t, str):
            problems.append("F6: tools 是 %s ✗（必须是 JSON 字符串）" % type(t).__name__)
        else:
            try:
                parsed = json.loads(t)
                if not isinstance(parsed, list):
                    problems.append("F6: tools JSON 不是数组 ✗")
            except Exception as e:
                problems.append("F6: tools 不是合法 JSON ✗（%s）" % str(e)[:50])

    # ── F7 + F8: function_call 结构 + 工具白名单 ✓ ──
    for i, c in enumerate(conv):
        if not isinstance(c, dict):
            continue
        if _norm_role(str(c.get("from", ""))) != "function_call":
            continue
        v = c.get("value")
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                problems.append("F8: conversations[%d].value 不是合法 JSON ✗" % i)
                continue
        if not isinstance(v, dict):
            problems.append("F8: conversations[%d].value 不是对象 ✗" % i)
            continue
        name = v.get("name")
        args = v.get("arguments")
        if not name:
            problems.append("F8: conversations[%d] 缺 name ✗" % i)
        elif wl and name not in wl:
            problems.append("F7: 工具 %r 不在白名单 ✗" % name)
        if not isinstance(args, dict):
            problems.append("F8: conversations[%d].arguments 不是对象 ✗（是 %s）" % (
                i, type(args).__name__))

    # ── Q1/Q2: system 瘦身 ✓ ──
    # ★ 修（2026-10-22）：TRAIN_ALLOW_SYSTEM_HEAVY=1 时必须真的跳过
    #   （报错信息里一直这么提示，但校验器没读这个变量 ⇒ 覆盖无效 ✗）
    import os as _os_q
    _allow_heavy = str(_os_q.environ.get("TRAIN_ALLOW_SYSTEM_HEAVY") or "").strip() in ("1", "true", "True", "yes")
    system = record.get("system") or ""
    if system and not _allow_heavy:
        total_chars = len(system) + sum(len(str(c.get("value", ""))) for c in conv if isinstance(c, dict))
        # ★ 修（2026-10-22 实测）：短样本里 system 占比天然很高 ⇒ 误杀合格数据 ✗
        #   实测: system="你是助手。"(5 字) / 样本 10 字 ⇒ 占比 50% > 20% ⇒ 拒收 ✗✗
        #   这正是本模块 L587 已有、但校验器漏掉的"短样本豁免"
        #   规则: system 绝对长度 < 60 字符时不看占比（本来就无害）；≥60 才按占比判
        if total_chars > 0 and len(system) >= 60 \
                and len(system) / total_chars > Q1_SYSTEM_RATIO_MAX:
            problems.append("Q1: system 占比 %.0f%% ✗（必须 < 20%%；system %d 字）"
                            % (len(system) / total_chars * 100, len(system)))
        if len(system) > Q2_SYSTEM_CHARS_MAX:
            problems.append("Q2: system 长度 %d 字符 ✗（必须 ≤ 400）" % len(system))

    return (len(problems) == 0), problems


# ══════════════════════════════════════════════════════════
# 五、去重（精确 + MinHash 近似 ✓）
# ══════════════════════════════════════════════════════════
def _record_fingerprint(record: dict) -> str:
    """精确去重指纹 ✓"""
    system = record.get("system") or ""
    conv = record.get("conversations") or []
    payload = system + "|" + json.dumps(conv, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _shingles(text: str, k: int = 5) -> set[str]:
    """字符 k-gram ✓"""
    t = re.sub(r"\s+", "", text)
    if len(t) <= k:
        return {t}
    return {t[i:i + k] for i in range(len(t) - k + 1)}


def _minhash_sig(text: str, num_hashes: int = 64) -> list[int]:
    """MinHash 签名（纯 Python ✓ 零依赖 ✓）"""
    sh = _shingles(text)
    if not sh:
        return [0] * num_hashes
    sig = []
    for i in range(num_hashes):
        # 用不同种子的 hash ✓
        h = min(hashlib.md5((str(i) + "|" + s).encode("utf-8")).hexdigest() for s in sh)
        sig.append(int(h[:8], 16))
    return sig


def _jaccard_from_sig(a: list[int], b: list[int]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same / len(a)


def find_duplicates(records: list[dict], *, near_threshold: float = 0.8) -> tuple[list, list]:
    """找重复 ✓

    Returns:
        (exact_dup_groups, near_dup_pairs)
        exact: [[idx, idx, ...], ...]
        near:  [(i, j, jaccard), ...]
    """
    # 精确 ✓
    groups: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        fp = _record_fingerprint(r)
        groups.setdefault(fp, []).append(i)
    exact = [idxs for idxs in groups.values() if len(idxs) > 1]

    # 近似 ✓（只在非精确重复里找 ✓ 省时间 ✓）
    texts = []
    for r in records:
        conv = r.get("conversations") or []
        texts.append(json.dumps(conv, ensure_ascii=False))
    sigs = [_minhash_sig(t) for t in texts]
    near = []
    n = len(records)
    limit = min(n, 2000)   # 防 O(n^2) 爆炸 ✓
    for i in range(limit):
        for j in range(i + 1, limit):
            if groups[_record_fingerprint(records[i])] is groups[_record_fingerprint(records[j])]:
                continue   # 已精确重复 ✓ 跳过 ✓
            jc = _jaccard_from_sig(sigs[i], sigs[j])
            if jc >= near_threshold:
                near.append((i, j, round(jc, 3)))
    return exact, near


# ══════════════════════════════════════════════════════════
# 六、清洗（修复 + 去重 + 截断 + 过滤 ✓）
# ══════════════════════════════════════════════════════════
DEFAULT_CLEAN_OPTS = {
    "fix_format": True,        # 归一化修复 ✓
    "dedup_exact": True,       # 精确去重 ✓
    "dedup_near": False,       # 近似去重 ⚠ 默认关 ✓
    "near_threshold": 0.8,
    "filter_empty": True,      # 过滤空 ✓
    "min_chars": 10,           # < 10 字丢弃 ✓
    "max_chars": 20000,        # 超长丢弃 ✓
    "trim_system": True,       # system > 400 截断 ✓
    "system_max": 400,
}


def clean(records: list[dict], opts: dict | None = None) -> tuple[list[dict], dict]:
    """清洗 ✓ 永远不覆盖原文件 ✓（调用方负责写新文件 ✓）

    Returns:
        (cleaned_records, stats)
    """
    o = dict(DEFAULT_CLEAN_OPTS)
    if opts:
        o.update(opts)

    stats = {
        "input": len(records),
        "normalized": 0,
        "fixed": 0,
        "dropped_empty": 0,
        "dropped_short": 0,
        "dropped_long": 0,
        "dropped_dup": 0,
        "system_trimmed": 0,
        "output": 0,
        "fix_reasons": {},
    }

    # ── ① 归一化 ✓ ──
    norm = []
    for r in records:
        n = normalize(r)
        stats["normalized"] += 1
        if n.get("_fixes"):
            stats["fixed"] += 1
            for f in n["_fixes"]:
                stats["fix_reasons"][f] = stats["fix_reasons"].get(f, 0) + 1
        norm.append(n)

    # ── ② 过滤 ✓ ──
    filtered = []
    for n in norm:
        conv = n.get("conversations") or []
        total = len(n.get("system") or "") + sum(len(str(c.get("value", ""))) for c in conv)
        if not conv:
            stats["dropped_empty"] += 1
            continue
        if total < o["min_chars"]:
            stats["dropped_short"] += 1
            continue
        if total > o["max_chars"]:
            stats["dropped_long"] += 1
            continue
        filtered.append(n)

    # ── ③ 瘦身 system ✓ ──
    if o["trim_system"]:
        for n in filtered:
            s = n.get("system") or ""
            if len(s) > o["system_max"]:
                n["system"] = s[:o["system_max"]]
                stats["system_trimmed"] += 1

    # ── ④ 去重 ✓ ──
    if o["dedup_exact"]:
        seen = set()
        deduped = []
        for n in filtered:
            fp = _record_fingerprint(n)
            if fp in seen:
                stats["dropped_dup"] += 1
                continue
            seen.add(fp)
            deduped.append(n)
        filtered = deduped

    # 近似去重 ⚠ 可选 ✓
    if o["dedup_near"]:
        exact, near = find_duplicates(filtered, near_threshold=o["near_threshold"])
        drop = set()
        for i, j, jc in near:
            drop.add(j)   # 保留先出现的 ✓
        if drop:
            stats["dropped_dup"] += len(drop)
            filtered = [r for k, r in enumerate(filtered) if k not in drop]

    stats["output"] = len(filtered)
    return filtered, stats


# ══════════════════════════════════════════════════════════
# 七、排列 / 切分 / 报告
# ══════════════════════════════════════════════════════════
def _record_length(r: dict) -> int:
    conv = r.get("conversations") or []
    return len(r.get("system") or "") + sum(len(str(c.get("value", ""))) for c in conv if isinstance(c, dict))


def _record_quality_score(r: dict) -> int:
    """规则质量分（零成本 ✓ 用于排列 ✓ 不做删除 ✓）"""
    score = 0
    conv = r.get("conversations") or []
    n_tools = sum(1 for c in conv if isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "function_call")
    if n_tools > 0:
        score += 2
    # 有 observation（真实错误观测 ✓ 最有价值 ✓）
    if any(isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "observation" for c in conv):
        score += 2
    ln = _record_length(r)
    if 800 <= ln <= 8000:      # 约 200-2000 token 粗估 ✓
        score += 1
    system = r.get("system") or ""
    if ln > 0 and len(system) / ln < Q1_SYSTEM_RATIO_MAX:
        score += 1
    # 空观测 -3 ✓
    for c in conv:
        if isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "observation":
            if not str(c.get("value", "")).strip():
                score -= 3
                break
    return score


def sort_records(records: list[dict], by: str = "task") -> list[dict]:
    """一键排列 ✓

    by: 'task'（按任务分组 ✓ 默认）/ 'length'（按 token ✓）/ 'quality'（质量分 ✓）/ 'random'（随机 ✓）
    """
    if by == "length":
        return sorted(records, key=_record_length)
    if by == "quality":
        return sorted(records, key=_record_quality_score, reverse=True)
    if by == "random":
        out = list(records)
        random.Random(42).shuffle(out)   # 固定种子 ✓ 可复现 ✓
        return out
    # task: 按第一条 human 的 hash 分组 ✓（让同任务相邻 ✓）
    def _task_key(r: dict) -> str:
        conv = r.get("conversations") or []
        first_human = ""
        for c in conv:
            if isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "human":
                first_human = str(c.get("value", ""))
                break
        # 用前 20 字符做粗分组 ✓（同任务开头通常相似 ✓）
        return hashlib.md5(first_human[:20].encode("utf-8")).hexdigest()
    return sorted(records, key=_task_key)


def split(records: list[dict], ratio: float = 0.1, *, seed: int = 42) -> tuple[list[dict], list[dict]]:
    """分层切分 train/val ✓（按任务分层 ✓ 防同任务泄漏 ✓）

    Returns:
        (train, val)
    """
    if not records:
        return [], []
    n_val = max(1, int(len(records) * ratio))
    # 按任务分组 ✓
    by_task: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        conv = r.get("conversations") or []
        first_human = ""
        for c in conv:
            if isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "human":
                first_human = str(c.get("value", ""))
                break
        key = hashlib.md5(first_human[:20].encode("utf-8")).hexdigest()[:8]
        by_task.setdefault(key, []).append(i)
    # 每任务按比例抽 ✓
    rng = random.Random(seed)
    val_idx = set()
    for k, idxs in by_task.items():
        if len(idxs) >= 2:
            k_take = max(1, int(len(idxs) * ratio))
            val_idx.update(rng.sample(idxs, min(k_take, len(idxs))))
    # 如果 val 太少 ✓ 随机补 ✓
    if len(val_idx) < n_val:
        remain = [i for i in range(len(records)) if i not in val_idx]
        rng.shuffle(remain)
        val_idx.update(remain[:n_val - len(val_idx)])
    train = [r for i, r in enumerate(records) if i not in val_idx]
    val = [r for i, r in enumerate(records) if i in val_idx]
    return train, val


def validate_dataset(records: list[dict], tool_whitelist: set[str] | None = None) -> dict:
    """数据集级校验（F1-F8 汇总 + Q3/Q5/Q6/Q8 ✓）"""
    wl = tool_whitelist if tool_whitelist is not None else load_tool_whitelist()
    n = len(records)
    if n == 0:
        return {"total": 0, "valid": 0, "invalid": 0, "problems": {"F0": 1}}

    ok_count = 0
    problem_counter: Counter = Counter()
    invalid_examples = []
    for i, r in enumerate(records):
        ok, probs = validate(r, wl)
        if ok:
            ok_count += 1
        else:
            for p in probs:
                problem_counter[p.split(":")[0]] += 1
            if len(invalid_examples) < 5:
                invalid_examples.append({"index": i, "problems": probs[:3]})

    # ── Q3 句子多样性 ✓ ──
    sentences = []
    for r in records:
        for c in (r.get("conversations") or []):
            if isinstance(c, dict):
                v = str(c.get("value", "")).strip()
                if v:
                    sentences.append(v)
    uniq = len(set(sentences))
    diversity = uniq / len(sentences) if sentences else 0.0

    # ── Q5 重复 ✓ ──
    fps = [_record_fingerprint(r) for r in records]
    n_dup = len(fps) - len(set(fps))

    # ── Q6 同任务变体 ✓ ──
    task_counter: Counter = Counter()
    for r in records:
        first_human = ""
        for c in (r.get("conversations") or []):
            if isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "human":
                first_human = str(c.get("value", ""))[:20]
                break
        task_counter[first_human] += 1
    max_same_task = max(task_counter.values()) if task_counter else 0

    # ── Q8 单工具占比 ✓ ──
    n_single = 0
    n_tool_samples = 0
    for r in records:
        n_tools = sum(1 for c in (r.get("conversations") or [])
                      if isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "function_call")
        if n_tools > 0:
            n_tool_samples += 1
            if n_tools == 1:
                n_single += 1
    single_ratio = n_single / n_tool_samples if n_tool_samples else 0.0

    return {
        "total": n,
        "valid": ok_count,
        "invalid": n - ok_count,
        "valid_ratio": round(ok_count / n, 3) if n else 0,
        "problems": dict(problem_counter.most_common()),
        "invalid_examples": invalid_examples,
        "Q3_diversity": round(diversity, 3),
        "Q3_ok": diversity >= Q3_DIVERSITY_MIN,
        "Q5_duplicates": n_dup,
        "Q5_ok": n_dup == 0,
        "Q6_max_same_task": max_same_task,
        "Q6_ok": max_same_task <= Q6_SAME_TASK_MAX,
        "Q8_single_tool_ratio": round(single_ratio, 3),
        "Q8_ok": single_ratio >= Q8_SINGLE_TOOL_RATIO_MIN,
        "Q8_tool_samples": n_tool_samples,
    }


def report(records: list[dict]) -> dict:
    """完整报告 ✓（可直接 JSON 给前端 ✓）"""
    ds = validate_dataset(records)
    lengths = [_record_length(r) for r in records]
    systems = [len(r.get("system") or "") for r in records]
    n_tools = [sum(1 for c in (r.get("conversations") or [])
                   if isinstance(c, dict) and _norm_role(str(c.get("from", ""))) == "function_call")
               for r in records]
    return {
        "dataset": ds,
        "length": {
            "min": min(lengths) if lengths else 0,
            "max": max(lengths) if lengths else 0,
            "avg": round(sum(lengths) / len(lengths), 1) if lengths else 0,
            "p50": sorted(lengths)[len(lengths) // 2] if lengths else 0,
        },
        "system": {
            "min": min(systems) if systems else 0,
            "max": max(systems) if systems else 0,
            "avg": round(sum(systems) / len(systems), 1) if systems else 0,
            "over_400": sum(1 for x in systems if x > Q2_SYSTEM_CHARS_MAX),
        },
        "tools": {
            "samples_with_tools": sum(1 for x in n_tools if x > 0),
            "max_per_sample": max(n_tools) if n_tools else 0,
            "distribution": dict(Counter(n_tools).most_common(8)),
        },
    }


# ══════════════════════════════════════════════════════════
# 八、写文件（标准格式 ✓）
# ══════════════════════════════════════════════════════════
def write_jsonl(records: list[dict], path: str | pathlib.Path) -> int:
    """写标准 sharegpt jsonl ✓（去掉内部 _ 字段 ✓）"""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            clean_r = {k: v for k, v in r.items() if not k.startswith("_")}
            # 字段顺序: system, tools, conversations ✓
            ordered = {}
            if clean_r.get("system"):
                ordered["system"] = clean_r["system"]
            if clean_r.get("tools"):
                ordered["tools"] = clean_r["tools"]
            ordered["conversations"] = clean_r.get("conversations") or []
            f.write(json.dumps(ordered, ensure_ascii=False) + "\n")
            n += 1
    return n


# ══════════════════════════════════════════════════════════
# 九、自测（python dataset_toolkit.py ✓）
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=" * 70)
    print("  dataset_toolkit 自测")
    print("=" * 70)

    # 工具白名单 ✓
    wl = load_tool_whitelist()
    print("  工具白名单: %d 个" % len(wl))

    # ── ① 标准格式 ✓ 应通过 ──
    good = {
        "system": "你是 LoRA 工作台 Agent。",
        "conversations": [
            {"from": "human", "value": "列一下最近的模型"},
            {"from": "function_call", "value": json.dumps({"name": "list_available_models", "arguments": {}})},
            {"from": "observation", "value": json.dumps({"models": ["a"]})},
            {"from": "gpt", "value": "最近有 a。"},
        ],
    }
    ok, probs = validate(good, wl)
    print("\n  [标准格式] ok=%s problems=%s" % (ok, probs))

    # ── ② 旧格式（system 在 conversations[0] ✗）应被修 ✓ ──
    bad_old = {
        "conversations": [
            {"from": "system", "value": "你是 Agent"},
            {"from": "human", "value": "你好"},
            {"from": "gpt", "value": "你好"},
        ]
    }
    n2 = normalize(bad_old)
    print("\n  [旧格式] 修复: %s" % n2.get("_fixes"))
    print("           system=%r conv=%s" % (n2.get("system"), [c["from"] for c in n2["conversations"]]))
    ok2, probs2 = validate(n2, wl)
    print("           校验: ok=%s problems=%s" % (ok2, probs2))

    # ── ③ alpaca → sharegpt ✓ ──
    alpaca = {"instruction": "写一句话", "input": "", "output": "好的。"}
    n3 = normalize(alpaca)
    print("\n  [alpaca] 修复: %s" % n3.get("_fixes"))
    print("           conv=%s" % [c["from"] for c in n3["conversations"]])

    # ── ④ openai messages → sharegpt ✓ ──
    oai = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}
    n4 = normalize(oai)
    print("\n  [openai] 修复: %s" % n4.get("_fixes"))
    print("           conv=%s" % [c["from"] for c in n4["conversations"]])

    # ── ⑤ 错误格式（位置错 ✗）应报 F1/F4 ✓ ──
    bad_pos = {"conversations": [{"from": "gpt", "value": "x"}, {"from": "human", "value": "y"}]}
    ok5, probs5 = validate(bad_pos, wl)
    print("\n  [位置错] ok=%s problems=%s" % (ok5, probs5[:2]))

    # ── ⑥ tools 是对象 ✗ 应报 F6 ✓ ──
    bad_tools = dict(good)
    bad_tools["tools"] = [{"type": "function"}]
    ok6, probs6 = validate(bad_tools, wl)
    print("\n  [tools 对象] ok=%s problems=%s" % (ok6, [p for p in probs6 if p.startswith("F6")]))

    # ── ⑦ 数据集报告 ✓ ──
    rep = report([good, n2, n3, n4])
    print("\n  [报告] total=%d valid=%d valid_ratio=%s" % (
        rep["dataset"]["total"], rep["dataset"]["valid"], rep["dataset"]["valid_ratio"]))
    print("          Q3=%s Q5=%s Q6=%s Q8=%s" % (
        rep["dataset"]["Q3_ok"], rep["dataset"]["Q5_ok"], rep["dataset"]["Q6_ok"], rep["dataset"]["Q8_ok"]))

    # ── ⑧ 清洗 ✓ ──
    cleaned, stats = clean([good, n2, n3, n4, dict(good)])   # 最后一条重复 ✓
    print("\n  [清洗] %s" % json.dumps(stats, ensure_ascii=False))

    # ── ⑨ 排列/切分 ✓ ──
    for by in ("task", "length", "quality", "random"):
        s = sort_records(cleaned, by)
        print("  [排序 %-7s] %d 条 首位长度=%d" % (by, len(s), _record_length(s[0]) if s else 0))
    tr, va = split(cleaned, 0.2)
    print("  [切分] train=%d val=%d" % (len(tr), len(va)))

    print("\n  ✅ 自测完成")
