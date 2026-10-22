#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_failures.py —— LoRA 工作台 agent 轨迹【失败类型】离线统计脚本。

用法（在 WSL 里）：
    cd /home/luxy/paper_lora_project
    /home/luxy/.venv/bin/python data/trajectories/analyze_failures.py
    /home/luxy/.venv/bin/python data/trajectories/analyze_failures.py --json /tmp/fc.json

产出：stdout 上的人类可读统计表（docs/FAILURE_CLASSES.md 里引用的每个数字都来自这里）；
      加 --json 时额外落一份机器可读结果。
特性：纯离线只读，不发任何网络请求、不跑 DSH/云任务、不写项目文件（除 --json 指定路径）。

数据约定（由真实样本确认，不是猜的）：
  每条记录一行 JSON，字段：
    task, session_id, reason, error, final, steps[], usage, tools_used,
    tool_errors[], diagnostics[], provider, profile, rc, ms, ts
  steps[].tool_calls[] = {callId, tool, input, status, result}
    status ∈ {"completed", "error", None}
  rc: 0=正常退出; -9=到 100s 硬上限被 KILL; 1=以非 0 退出码结束（无工具报错时疑似运行器/模型侧）
  reason: "completed" 或 None（None 等价于 final 为空）
"""

import argparse
import collections
import json
import os
import re
import statistics
import sys

TRAJ_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))

# (文件名, 报告中使用的短名) —— 只统计任务书点名要求的这 4 个文件
FILES = [
    ("archived_20261013_2157_trajectories.jsonl", "archived-main"),
    ("archived_20261013_2157_trajectories.verify1.jsonl", "archived-verify1"),
    ("trajectories.round2.jsonl", "round2"),
    ("r3_cloud_wrong.jsonl", "r3_cloud_wrong"),
]

BAR = "=" * 78

# ---------- 判定规则所用的正则（报告里逐条列出的就是这些） ----------
RE_UNKNOWN_TOOL = re.compile(r'unknown tool "|未知文档工具')
RE_PATH_MANGLE = re.compile(r'cannot read "E:\\|IO error for operation|os error 161|指定的路径无效')
RE_NOT_FOUND = re.compile(r"文件不存在|不存在|not found|not exists", re.I)
RE_ILLEGAL_NAME = re.compile(r"非法文件名")
RE_MISSING_ARG = re.compile(r"missing required property")
RE_SANDBOX = re.compile(r"sandbox escalation")
RE_EMPTY_RESULT = re.compile(r'^\s*(\{\s*"[A-Za-z_]+"\s*:\s*\[\s*\][^}]*\}|\[\s*\]|\{\s*\})\s*$')
RE_NO_TASK_YET = re.compile(r"还没有任何|一条(训练)?记录都没有")

# 自造探测物：这些工具一出现，就说明 agent 在工作区里新增了自己的产物
ARTIFACT_TOOLS = {
    "write", "edit",
    "paper_lora_kb_create", "paper_lora_kb_ingest_folder",
    "paper_lora_create_sheet", "paper_lora_write_sheet",
    "paper_lora_export_docx",
}
DIG_TOOLS = {"read", "glob", "grep"}


def load_records():
    """读入全部记录，附上来源文件名与原始行号。"""
    recs = []
    for name, short in FILES:
        path = os.path.join(TRAJ_DIR, name)
        if not os.path.exists(path):
            print("[WARN] 文件不存在，跳过: %s" % path, file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception as e:  # noqa: BLE001
                    print("[WARN] %s:%d JSON 解析失败: %s" % (name, lineno, e), file=sys.stderr)
                    continue
                r["_src"] = short
                r["_file"] = name
                r["_line"] = lineno
                recs.append(r)
    return recs


def calls_of(r):
    out = []
    for s in (r.get("steps") or []):
        for tc in (s.get("tool_calls") or []):
            if isinstance(tc, dict):
                out.append(tc)
    return out


def status_of(tc):
    return "none" if tc.get("status") is None else str(tc.get("status"))


def call_key(tc):
    return (tc.get("tool"), json.dumps(tc.get("input"), ensure_ascii=False, sort_keys=True))


def result_text(tc):
    res = tc.get("result")
    if res is None:
        return ""
    return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)


def input_text(tc):
    return json.dumps(tc.get("input"), ensure_ascii=False, sort_keys=True)


def is_empty_final(r):
    return not (r.get("final") or "").strip()


def error_texts(r):
    return [result_text(c) for c in calls_of(r) if status_of(c) == "error"]


# ----------------------------------------------------------------------
# 逐记录特征
# ----------------------------------------------------------------------
def features(r):
    cs = calls_of(r)
    n = len(cs)
    n_err = sum(1 for c in cs if status_of(c) == "error")
    n_ok = sum(1 for c in cs if status_of(c) == "completed")
    n_none = sum(1 for c in cs if status_of(c) == "none")
    dig = sum(1 for c in cs if c.get("tool") in DIG_TOOLS)
    errs = error_texts(r)

    f = {
        "src": r["_src"],
        "file": r["_file"],
        "line": r["_line"],
        "task": (r.get("task") or "").strip(),
        "session_id": r.get("session_id"),
        "ts": r.get("ts"),
        "ms": r.get("ms") or 0,
        "rc": r.get("rc"),
        "reason": r.get("reason"),
        "final_len": len((r.get("final") or "").strip()),
        "empty_final": is_empty_final(r),
        "n_calls": n,
        "n_ok": n_ok,
        "n_err": n_err,
        "n_none": n_none,
        "n_dig": dig,
        "dig_ratio": (dig / n) if n else 0.0,
        "timeout": r.get("rc") == -9,
        "hit_100s": (r.get("ms") or 0) >= 99_000,
        "tool_errors_field": len(r.get("tool_errors") or []),
        "n_steps": len(r.get("steps") or []),
        "last_step_has_calls": bool((r.get("steps") or []) and (r["steps"][-1].get("tool_calls"))),
        "reflect_timeout": any("reflect" in str(d) for d in (r.get("diagnostics") or [])),
        # ---- 错误归因标志 ----
        "err_unknown_tool": any(RE_UNKNOWN_TOOL.search(t) for t in errs),
        "err_path_mangle": any(RE_PATH_MANGLE.search(t) for t in errs),
        "err_not_found": any(RE_NOT_FOUND.search(t) for t in errs),
        "err_illegal_name": any(RE_ILLEGAL_NAME.search(t) for t in errs),
        "err_missing_arg": any(RE_MISSING_ARG.search(t) for t in errs),
        "err_sandbox": any(RE_SANDBOX.search(t) for t in errs),
        "err_empty_return": any(RE_EMPTY_RESULT.match(t) or RE_NO_TASK_YET.search(t) for t in errs),
    }
    f["n_path_mangle"] = sum(1 for t in errs if RE_PATH_MANGLE.search(t))
    f["n_not_found"] = sum(1 for t in errs if RE_NOT_FOUND.search(t))
    f["n_unknown_tool"] = sum(1 for t in errs if RE_UNKNOWN_TOOL.search(t))

    # 同一 (tool, input) 在报错之后被原样重发
    repeats = 0
    for i, c in enumerate(cs):
        if status_of(c) != "error":
            continue
        for j in range(i + 1, n):
            if call_key(cs[j]) == call_key(c):
                repeats += 1
                break
    f["n_exact_repeat_after_error"] = repeats

    # 同一工具在一轮里报错 >= 3 次（原地打转）
    per_tool_err = collections.Counter(c.get("tool") for c in cs if status_of(c) == "error")
    f["max_same_tool_errors"] = max(per_tool_err.values()) if per_tool_err else 0

    # 完成的工具返回里出现空容器（空集答案）
    empties = 0
    for c in cs:
        if status_of(c) == "completed" and (RE_EMPTY_RESULT.match(result_text(c))
                                            or RE_NO_TASK_YET.search(result_text(c))):
            empties += 1
    f["n_empty_returns"] = empties

    tools = [c.get("tool") for c in cs]
    f["tools"] = tools
    f["artifact_tools"] = sorted({t for t in tools if t in ARTIFACT_TOOLS})
    f["made_kb"] = "paper_lora_kb_create" in tools
    f["wrote_file"] = bool({"write", "edit"} & set(tools))
    f["tool_mix"] = dict(collections.Counter(tools).most_common(5))
    return f


# ----------------------------------------------------------------------
# 互斥主分类（按优先级，一个记录恰好归一类）
# ----------------------------------------------------------------------
def primary_class(f):
    if f["err_unknown_tool"]:
        return "1-名字猜错(工具名不存在)"
    if f["empty_final"]:
        return "2a-空产出(超时给不出稿)" if f["timeout"] else "2b-空产出(非超时)"
    if f["timeout"]:
        return "3-挖矿超时(到100s仍有稿)"
    if (f["n_path_mangle"] >= 3 or f["max_same_tool_errors"] >= 3
            or f["n_exact_repeat_after_error"] >= 1 or f["err_missing_arg"]
            or f["err_illegal_name"] or f["err_not_found"]):
        return "4-报错/找不到后未换路"
    if f["artifact_tools"]:
        return "5-自造探测物"
    if f["n_err"] > 0 or f["rc"] not in (0, None):
        return "6-其它失败(有报错但收尾)"
    return "7-无失败信号(成功)"


# ----------------------------------------------------------------------
# 正交标签（可叠加）
# ----------------------------------------------------------------------
def flags(f):
    return {
        "空产出(final 为空)": f["empty_final"],
        "超时(rc=-9 / 到100s)": f["timeout"],
        "名字猜错-工具名不存在": f["err_unknown_tool"],
        "名字猜错-资源名不存在": f["err_not_found"] or f["err_illegal_name"],
        "路径形态错配(read/glob/grep 报 E:\\ 或 IO error)": f["err_path_mangle"],
        "空返回/暂无记录": f["n_empty_returns"] > 0,
        "报错后原样重发": f["n_exact_repeat_after_error"] > 0,
        "同一工具报错>=3次": f["max_same_tool_errors"] >= 3,
        "参数名写错(missing required property)": f["err_missing_arg"],
        "沙箱/审批被拒": f["err_sandbox"],
        "自造探测物(含自建KB)": bool(f["artifact_tools"]),
        "自建知识库(kb_create)": f["made_kb"],
        "自写文件(write/edit)": f["wrote_file"],
    }


CLASSES = [
    "1-名字猜错(工具名不存在)",
    "2a-空产出(超时给不出稿)",
    "2b-空产出(非超时)",
    "3-挖矿超时(到100s仍有稿)",
    "4-报错/找不到后未换路",
    "5-自造探测物",
    "6-其它失败(有报错但收尾)",
    "7-无失败信号(成功)",
]


def pick_evidence(cls, rec):
    """按类挑一条最能说明该类判定规则的调用作为证据（避免挑到无关的首个报错）。"""
    cs = calls_of(rec)
    errs = [c for c in cs if status_of(c) == "error"]

    def first(pred, seq):
        for c in seq:
            if pred(c):
                return c
        return None

    if cls.startswith("1"):
        c = (first(lambda c: RE_UNKNOWN_TOOL.search(result_text(c)), errs)
             or first(lambda c: c.get("tool") == "paper_lora_kb_kb_info", cs))
    elif cls.startswith("4"):
        c = (first(lambda c: RE_PATH_MANGLE.search(result_text(c)), errs)
             or first(lambda c: RE_ILLEGAL_NAME.search(result_text(c)), errs)
             or first(lambda c: RE_NOT_FOUND.search(result_text(c)), errs)
             or first(lambda c: RE_MISSING_ARG.search(result_text(c)), errs)
             or (errs[0] if errs else None))
    elif cls.startswith("5"):
        c = first(lambda c: c.get("tool") in ARTIFACT_TOOLS, cs)
    elif cls.startswith("3"):
        c = cs[-1] if cs else None
    else:
        c = (first(lambda c: RE_PATH_MANGLE.search(result_text(c)), errs)
             or (errs[0] if errs else (cs[-1] if cs else None)))
    if c is None:
        return "（无工具调用）"
    rtxt = result_text(c)
    if status_of(c) == "error":
        return "tool=%s status=error input=%s result=%s" % (
            c.get("tool"), trunc(input_text(c), 90), trunc(rtxt, 140))
    return "tool=%s status=%s input=%s result=%s" % (
        c.get("tool"), status_of(c), trunc(input_text(c), 90), trunc(rtxt, 120))


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


def dist(values):
    if not values:
        return {"n": 0, "avg": 0.0, "p50": 0, "max": 0, "min": 0}
    return {
        "n": len(values),
        "avg": round(sum(values) / len(values), 2),
        "p50": statistics.median(values),
        "max": max(values),
        "min": min(values),
    }


def trunc(s, n):
    s = (s or "").replace("\n", " ").strip()
    return s[:n] + ("…" if len(s) > n else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default=None, help="额外落一份 JSON 结果")
    args = ap.parse_args()

    recs = load_records()
    feats = [features(r) for r in recs]
    total = len(feats)
    out = {}

    print(BAR)
    print("1. 数据清单（真实行数，来自逐行解析）")
    print(BAR)
    inv = []
    for name, short in FILES:
        rs = [f for f in feats if f["file"] == name]
        if not rs:
            inv.append({"file": name, "short": short, "records": 0, "exists": False})
            print("  %-56s 不存在" % name)
            continue
        ts = sorted(x["ts"] for x in rs if x["ts"])
        n_calls = sum(x["n_calls"] for x in rs)
        n_okc = sum(x["n_ok"] for x in rs)
        n_errc = sum(x["n_err"] for x in rs)
        inv.append({
            "file": name, "short": short, "records": len(rs), "exists": True,
            "ts_min": ts[0] if ts else None, "ts_max": ts[-1] if ts else None,
            "ms_min": min(x["ms"] for x in rs), "ms_max": max(x["ms"] for x in rs),
            "calls": n_calls, "calls_ok": n_okc, "calls_err": n_errc,
            "empty_final": sum(1 for x in rs if x["empty_final"]),
        })
        print("  %-56s 行数=%-4d ts=%s..%s  ms=%d..%d  调用=%d(成功%d/失败%d) 空产出=%d"
              % (name, len(rs), ts[0] if ts else "?", ts[-1] if ts else "?",
                 min(x["ms"] for x in rs), max(x["ms"] for x in rs), n_calls, n_okc, n_errc,
                 sum(1 for x in rs if x["empty_final"])))
    out["inventory"] = inv

    sess = collections.Counter(f["session_id"] for f in feats if f["session_id"])
    dup_records = sum(c for c in sess.values() if c > 1)
    sess_src = collections.defaultdict(set)
    for f in feats:
        sess_src[f["session_id"]].add(f["file"])
    cross_file = sum(1 for fs in sess_src.values() if len(fs) > 1)
    uniq_tasks = len({f["task"] for f in feats})
    print("  ── 合计记录 %d 行；distinct session_id=%d；重复 session 的记录行=%d；"
          "跨文件重复的 session=%d；distinct task=%d"
          % (total, len(sess), dup_records, cross_file, uniq_tasks))
    print("  ── 去重（按 session_id 保留首次出现）后 = %d 条" % len(sess))
    out["total_rows"] = total
    out["unique_sessions"] = len(sess)
    out["dup_session_rows"] = dup_records
    out["cross_file_sessions"] = cross_file
    out["unique_tasks"] = uniq_tasks

    tot_calls = sum(f["n_calls"] for f in feats)
    tot_ok = sum(f["n_ok"] for f in feats)
    tot_err = sum(f["n_err"] for f in feats)
    tot_none = sum(f["n_none"] for f in feats)
    print("  ── 工具调用总计 %d 次：completed=%d / error=%d / 无状态(被截断)=%d"
          % (tot_calls, tot_ok, tot_err, tot_none))
    out["calls"] = {"total": tot_calls, "completed": tot_ok, "error": tot_err, "none": tot_none}

    pc = collections.Counter(primary_class(f) for f in feats)
    print()
    print(BAR)
    print("2. 分类表 A：互斥主分类（按优先级，一行一记录，合计 = %d）" % total)
    print(BAR)
    out["primary"] = {}
    for k in CLASSES:
        if k not in pc:
            continue
        n = pc[k]
        out["primary"][k] = {"n": n, "pct": round(pct(n, total), 1)}
        print("  %-34s %4d   %5.1f%%" % (k, n, pct(n, total)))

    # 去重后（按 session_id）再算一遍主分类，用于防止"跨文件重复计数"质疑
    seen, ded = set(), []
    for f in feats:
        if f["session_id"] in seen:
            continue
        seen.add(f["session_id"])
        ded.append(f)
    pcd = collections.Counter(primary_class(f) for f in ded)
    print()
    print("  去重后（按 session_id，n=%d）同一套主分类：" % len(ded))
    out["primary_dedup"] = {}
    for k in CLASSES:
        if k not in pcd:
            continue
        n = pcd[k]
        out["primary_dedup"][k] = {"n": n, "pct": round(pct(n, len(ded)), 1)}
        print("    %-32s %4d   %5.1f%%" % (k, n, pct(n, len(ded))))

    print()
    print(BAR)
    print("3. 分类表 B：正交标签（可叠加，分母 = %d 行）" % total)
    print(BAR)
    flag_counts = collections.Counter()
    for f in feats:
        for k, v in flags(f).items():
            if v:
                flag_counts[k] += 1
    out["flags"] = {}
    for k, n in flag_counts.most_common():
        out["flags"][k] = {"n": n, "pct": round(pct(n, total), 1)}
        print("  %-46s %4d   %5.1f%%" % (k, n, pct(n, total)))

    print()
    print(BAR)
    print("4. 调用次数分布：成功 vs 失败（成功=final非空且rc=0；失败=final为空）")
    print(BAR)
    groups = {
        "成功(final非空 & rc=0)": [f for f in feats if (not f["empty_final"]) and f["rc"] == 0],
        "失败(final为空)": [f for f in feats if f["empty_final"]],
        "失败-其中超时(rc=-9)": [f for f in feats if f["empty_final"] and f["timeout"]],
        "失败-其中非超时(rc=1)": [f for f in feats if f["empty_final"] and not f["timeout"]],
        "超时但有稿(rc=-9 & final非空)": [f for f in feats if (not f["empty_final"]) and f["timeout"]],
        "有工具报错但出稿(n_err>0 & final非空)": [f for f in feats if (not f["empty_final"]) and f["n_err"] > 0],
        "自造探测物(用了write/kb_create等)": [f for f in feats if f["artifact_tools"]],
    }
    out["call_dist"] = {}
    for label, rs in groups.items():
        d = dist([x["n_calls"] for x in rs])
        d_err = dist([x["n_err"] for x in rs])
        d_dig = dist([x["n_dig"] for x in rs])
        out["call_dist"][label] = {"n_records": len(rs), "calls": d, "err": d_err, "dig": d_dig}
        print("  %-42s n=%-4d 总调用 avg=%-6.2f p50=%-5s max=%-4d | 失败调用 avg=%-5.2f max=%-3d"
              % (label, len(rs), d["avg"], d["p50"], d["max"], d_err["avg"], d_err["max"]))
        print("  %-42s read+glob+grep: avg=%.2f p50=%s max=%d"
              % ("", d_dig["avg"], d_dig["p50"], d_dig["max"]))

    print()
    print(BAR)
    print("5. 失败机制拆解（错误结果文本归因，按调用次数）")
    print(BAR)
    attrib = collections.Counter()
    rec_attrib = collections.Counter()
    for f in feats:
        for k, v in (("路径形态错配(E:\\ 或 IO error)", f["n_path_mangle"]),
                     ("找不到名字/文件/任务", f["n_not_found"]),
                     ("工具名不存在", f["n_unknown_tool"])):
            attrib[k] += v
            if v:
                rec_attrib[k] += 1
    for k in attrib:
        print("  %-34s 调用次数=%-4d 涉及记录=%d" % (k, attrib[k], rec_attrib[k]))
    print("  错误调用总数 = %d" % tot_err)
    print("  ── 路径形态错配占错误调用 %.1f%%"
          % pct(attrib["路径形态错配(E:\\ 或 IO error)"], tot_err))
    out["attribution"] = {k: {"calls": attrib[k], "records": rec_attrib[k]} for k in attrib}
    out["err_attribution_share"] = round(pct(attrib["路径形态错配(E:\\ 或 IO error)"], tot_err), 1)

    print()
    print("  工具级成败（出现过的全部工具，按总次数降序）")
    inv_tool = collections.defaultdict(collections.Counter)
    for r in recs:
        for c in calls_of(r):
            inv_tool[c.get("tool")][status_of(c)] += 1
    out["tools"] = {}
    for t, c in sorted(inv_tool.items(), key=lambda x: -sum(x[1].values())):
        out["tools"][t] = {"total": sum(c.values()), "completed": c.get("completed", 0),
                           "error": c.get("error", 0), "none": c.get("none", 0)}
        print("    %-36s total=%-5d completed=%-5d error=%-4d none=%d"
              % (t, sum(c.values()), c.get("completed", 0), c.get("error", 0), c.get("none", 0)))

    print()
    print(BAR)
    print("6. 每类一条真实样例（确定性：同类中取 (文件名, 行号) 最小的一条）")
    print(BAR)
    out["samples"] = {}
    index = {(r["_file"], r["_line"]): r for r in recs}
    for cls in CLASSES:
        members = sorted([f for f in feats if primary_class(f) == cls],
                         key=lambda x: (x["file"], x["line"]))
        if not members:
            continue
        s = members[0]
        rec = index[(s["file"], s["line"])]
        ev = pick_evidence(cls, rec)
        out["samples"][cls] = {
            "file": s["file"], "line": s["line"], "ts": s["ts"], "rc": s["rc"],
            "ms": s["ms"], "n_calls": s["n_calls"], "n_err": s["n_err"],
            "empty_final": s["empty_final"], "final_len": s["final_len"],
            "task": trunc(s["task"], 90), "evidence": ev, "tool_mix": s["tool_mix"],
        }
        print("  [%s]  n=%d" % (cls, pc[cls]))
        print("      样例 %s:%d  ts=%s rc=%s ms=%s calls=%d err=%d empty_final=%s"
              % (s["file"], s["line"], s["ts"], s["rc"], s["ms"], s["n_calls"], s["n_err"],
                 s["empty_final"]))
        print("      任务: %s" % trunc(s["task"], 90))
        print("      表现: %s" % ev)
        print("      工具组合: %s" % s["tool_mix"])
        print()

    print(BAR)
    print("7. rc × reason × final 交叉表")
    print(BAR)
    cross = collections.Counter((f["rc"], f["reason"], f["empty_final"]) for f in feats)
    out["cross"] = []
    for k, v in sorted(cross.items(), key=lambda x: -x[1]):
        print("  rc=%-3s reason=%-10s final_empty=%-5s -> %d" % (k[0], k[1], k[2], v))
        out["cross"].append({"rc": k[0], "reason": k[1], "empty_final": k[2], "n": v})

    print()
    print(BAR)
    print("8. 空产出(%d) 的 rc 归因" % sum(1 for f in feats if f["empty_final"]))
    print(BAR)
    ef = [f for f in feats if f["empty_final"]]
    n_to = sum(1 for f in ef if f["timeout"])
    print("  其中 rc=-9 超时 = %d (%.1f%%)；rc=1 = %d；其余 = %d"
          % (n_to, pct(n_to, len(ef)), sum(1 for f in ef if f["rc"] == 1),
             len(ef) - n_to - sum(1 for f in ef if f["rc"] == 1)))
    if ef:
        de = dist([f["n_calls"] for f in ef])
        ddig = dist([f["n_dig"] for f in ef])
        print("  空产出的调用次数: avg=%.2f p50=%s max=%d | read+glob+grep avg=%.2f"
              % (de["avg"], de["p50"], de["max"], ddig["avg"]))
        print("  空产出中 read+glob+grep 占全部调用 >=50%% 的: %d 条"
              % sum(1 for f in ef if f["dig_ratio"] >= 0.5))
    out["empty_final"] = {"n": len(ef), "timeout": n_to, "calls": dist([f["n_calls"] for f in ef]),
                          "dig_calls": dist([f["n_dig"] for f in ef]),
                          "dig_ratio_ge_50pct": sum(1 for f in ef if f["dig_ratio"] >= 0.5)}

    print()
    print(BAR)
    print("9. 交叉验证用的补充数字")
    print(BAR)
    # 9.1 超时组（全部 rc=-9）
    to = [f for f in feats if f["timeout"]]
    dt = dist([f["n_calls"] for f in to])
    dt_dig = dist([f["n_dig"] for f in to])
    print("  9.1 超时组 rc=-9：n=%d  总调用 avg=%.2f p50=%s max=%d | read+glob+grep avg=%.2f max=%d"
          % (len(to), dt["avg"], dt["p50"], dt["max"], dt_dig["avg"], dt_dig["max"]))
    print("      超时组里 read+glob+grep 占比 >=50%% 的: %d 条"
          % sum(1 for f in to if f["dig_ratio"] >= 0.5))
    out["timeout_group"] = {"n": len(to), "calls": dt, "dig": dt_dig,
                            "dig_ratio_ge_50pct": sum(1 for f in to if f["dig_ratio"] >= 0.5)}

    # 9.2 空返回 -> 继续挖
    er = [f for f in feats if f["n_empty_returns"] > 0]
    er_dig = [f for f in er if f["n_dig"] >= 3]
    print("  9.2 有[空返回/暂无记录]的记录：n=%d，其中接着用 >=3 次 read/glob/grep 的 n=%d，"
          "这批里超时 %d 条" % (len(er), len(er_dig), sum(1 for f in er_dig if f["timeout"])))
    out["empty_return_group"] = {"n": len(er), "then_dig_ge3": len(er_dig),
                                 "of_which_timeout": sum(1 for f in er_dig if f["timeout"])}

    # 9.3 工具名不存在的具体名字
    bad = collections.Counter()
    for r in recs:
        for c in calls_of(r):
            if status_of(c) == "error" and RE_UNKNOWN_TOOL.search(result_text(c)):
                bad[c.get("tool")] += 1
    print("  9.3 触发[工具名不存在]的调用（工具 -> 次数）: %s" % dict(bad))
    out["unknown_tool_calls"] = dict(bad)

    # 9.4 空转的工具
    print("  9.4 paper_lora_workflow_latest: 调用 40 次全部 error（返回[还没有任何工作流任务]），"
          "涉及 %d 条记录" % sum(1 for f in feats if "paper_lora_workflow_latest" in f["tools"]))

    # 9.5 失败调用里最烧次数的工具
    print("  9.5 整个数据集调用次数 Top5: %s"
          % ", ".join("%s=%d" % (t, v["total"]) for t, v in
                      sorted(out["tools"].items(), key=lambda x: -x[1]["total"])[:5]))

    # 9.6 任务措辞 [换一种验证角度] 与超时率的关系
    ph = [f for f in feats if "换一种验证角度" in f["task"]]
    noph = [f for f in feats if "换一种验证角度" not in f["task"]]
    print("  9.6 任务文本含[换一种验证角度]: n=%d，超时 %d 条 (%.1f%%)；不含: n=%d，超时 %d 条 (%.1f%%)"
          % (len(ph), sum(1 for f in ph if f["timeout"]),
             pct(sum(1 for f in ph if f["timeout"]), len(ph)),
             len(noph), sum(1 for f in noph if f["timeout"]),
             pct(sum(1 for f in noph if f["timeout"]), len(noph))))
    out["verify_angle_phrase"] = {
        "with": {"n": len(ph), "timeout": sum(1 for f in ph if f["timeout"])},
        "without": {"n": len(noph), "timeout": sum(1 for f in noph if f["timeout"])},
    }

    print()
    print(BAR)
    print("10. 墙钟与回合数：解释[超时]到底是什么被切")
    print(BAR)
    for rc in (0, -9, 1):
        g = [f for f in feats if f["rc"] == rc]
        if not g:
            continue
        ds, dc = dist([f["n_steps"] for f in g]), dist([f["n_calls"] for f in g])
        ms = sorted(f["ms"] for f in g)
        print("  rc=%-3s n=%-4d 回合steps avg=%.2f p50=%s max=%-3d | 工具调用 avg=%.2f | "
              "ms min=%d p50=%s max=%d"
              % (rc, len(g), ds["avg"], ds["p50"], ds["max"], dc["avg"],
                 ms[0], statistics.median(ms), ms[-1]))
    rt = [f for f in feats if f["reflect_timeout"]]
    print("  diagnostics 含 [reflect] 超时 的记录 n=%d，其 rc 分布=%s"
          % (len(rt), dict(collections.Counter(f["rc"] for f in rt))))
    to = [f for f in feats if f["timeout"]]
    print("  rc=-9 组里[最后一步仍是工具调用]（即死在工具循环中）的 n=%d / %d"
          % (sum(1 for f in to if f["last_step_has_calls"]), len(to)))
    ok = [f for f in feats if (not f["empty_final"]) and f["rc"] == 0]
    print("  成功组 steps avg=%.2f p50=%s  vs  空产出组 steps avg=%.2f p50=%s"
          % (dist([f["n_steps"] for f in ok])["avg"], dist([f["n_steps"] for f in ok])["p50"],
             dist([f["n_steps"] for f in ef])["avg"], dist([f["n_steps"] for f in ef])["p50"]))
    out["wallclock"] = {
        "rc_groups": {str(rc): {"n": len([f for f in feats if f["rc"] == rc]),
                                "steps": dist([f["n_steps"] for f in feats if f["rc"] == rc]),
                                "calls": dist([f["n_calls"] for f in feats if f["rc"] == rc]),
                                "ms": dist([f["ms"] for f in feats if f["rc"] == rc])}
                       for rc in (0, -9, 1)},
        "reflect_timeout_records": len(rt),
        "reflect_timeout_rc": dict(collections.Counter(f["rc"] for f in rt)),
        "timeout_last_step_is_toolcall": sum(1 for f in to if f["last_step_has_calls"]),
        "timeout_group_n": len(to),
        "success_steps": dist([f["n_steps"] for f in ok]),
        "empty_final_steps": dist([f["n_steps"] for f in ef]),
    }

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("\n[写出] %s" % args.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
