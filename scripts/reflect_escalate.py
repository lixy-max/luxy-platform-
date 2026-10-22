#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reflect_escalate.py —— 反射学习闭环的「失败判定 + 自动升级」。

流程（对应 v-reflect 第 3 步）：
    小模型/本地工作流跑完 → 读它的结果 → 四档判定 → 需要升级就调 DSH 接手 → 采轨迹

四档判定（全部复用工作台【已有且已标定】的信号 ✓ 不自己造阈值 ✗）：

    ① 硬失败   status != success，或 _meta.failures 非空
                （工具报错 / 节点失败这类，工作流自己已经记了 ✓）
    ② 软失败   _meta.quality[node] 触及已标定地板：
                 · chars_per_1k_input < QUALITY_MIN_CHARS_PER_1K   ← 欠抽取（本项目已知故障）
                 · grounding < QUALITY_GROUNDING_MIN               ← 可溯源率低（可能编造）
                 · key_fact_retention < QUALITY_FACT_RETENTION_MIN ← 事实丢失
               仅对 eligible 的节点判；输入短于 QUALITY_SUFFICIENCY_MIN_INPUT 时不判充分度
    ③ 格式失败 _meta.missing_inputs 非空（上游字段为空 / JSON 解析失败 ✓ 工作流已记 ✓）
    ④ unknown  DSH 侧语义：工具调用"结果未持久记录、后果未知" → 一律升级
               （轨迹里 reason=error 且 error.message 含 unknown/interrupted ✓）

阈值来源：优先 import workflow_engine 的常量（单一真源 ✓）；
          import 失败时用内置的同值兜底（并明确打印"用的是兜底值"✗ 不静默 ✓）。

用法：
    python scripts/reflect_escalate.py --selftest           # 用构造样例验证四档判据
    python scripts/reflect_escalate.py --latest --dry-run   # 判最新一个工作流结果，不升级
    python scripts/reflect_escalate.py --latest --task "原任务"     # 判 + 真升级（调 DSH）
    python scripts/reflect_escalate.py --result outputs/result_x.json --dry-run
    python scripts/reflect_escalate.py --list               # 看历史升级记录
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAJ_DIR = Path(os.environ.get("TRAJ_DIR", ROOT / "data" / "trajectories"))

# ---------------------------------------------------------------- 阈值（单一真源）
FALLBACK = {
    "QUALITY_GROUNDING_MIN": 0.9,
    "QUALITY_FACT_RETENTION_MIN": 0.9,
    "QUALITY_MIN_CHARS_PER_1K": 8.0,
    "QUALITY_SUFFICIENCY_MIN_INPUT": 5000,
    "QUALITY_MAX_UNMATCHED_FACTS": 2,
    "QUALITY_UNMATCHED_MIN_FACTS": 5,
}
THRESHOLDS_SOURCE = "workflow_engine"
try:
    sys.path.insert(0, str(ROOT))
    import workflow_engine as _we  # noqa: E402
    TH = {k: getattr(_we, k) for k in FALLBACK if hasattr(_we, k)}
    missing = [k for k in FALLBACK if k not in TH]
    for k in missing:
        TH[k] = FALLBACK[k]
except Exception as e:                                     # 依赖缺失时不静默 ✗
    TH = dict(FALLBACK)
    THRESHOLDS_SOURCE = f"内置兜底（import workflow_engine 失败: {type(e).__name__}）"


def judge_quality_node(name: str, q: dict) -> list:
    """单个节点的软失败判据。返回原因列表（空 = 通过）。"""
    reasons = []
    if not isinstance(q, dict):
        return reasons
    # 只对「参与判定」的节点判（工作流自己的口径 ✓）
    if q.get("eligible") is False:
        return reasons
    kind = str(q.get("source") or "")
    per1k = q.get("chars_per_1k_input")
    inp = q.get("input_chars") or 0
    if per1k is not None and inp >= TH["QUALITY_SUFFICIENCY_MIN_INPUT"] and float(per1k) < TH["QUALITY_MIN_CHARS_PER_1K"]:
        reasons.append(f"{name}: 抽取充分度不足（每千字输入仅 {per1k} 字 < 地板 {TH['QUALITY_MIN_CHARS_PER_1K']}）"
                       f"—— 本项目已知的「欠抽取」故障")
    g = q.get("grounding")
    if g is not None and kind == "type" and float(g) < TH["QUALITY_GROUNDING_MIN"]:
        reasons.append(f"{name}: 可溯源率偏低（{g} < {TH['QUALITY_GROUNDING_MIN']}）—— 输出里可能有原文没有的内容")
    fr = q.get("key_fact_retention")
    if fr is not None and kind == "type" and float(fr) < TH["QUALITY_FACT_RETENTION_MIN"]:
        reasons.append(f"{name}: 关键事实保留率偏低（{fr} < {TH['QUALITY_FACT_RETENTION_MIN']}）")
    facts = q.get("key_facts") or 0
    un = q.get("key_facts_unmatched")
    if un is not None and facts >= TH["QUALITY_UNMATCHED_MIN_FACTS"] and int(un) > TH["QUALITY_MAX_UNMATCHED_FACTS"]:
        reasons.append(f"{name}: 无出处事实 {un} 条 > 容忍 {TH['QUALITY_MAX_UNMATCHED_FACTS']}")
    return reasons


def judge_result(doc: dict) -> dict:
    """判定一个工作流结果。返回 {need_escalate, tier, reasons, summary}。"""
    meta = doc.get("_meta") or {}
    status = str(doc.get("status") or "")
    reasons, tier = [], None

    # ① 硬失败
    fails = meta.get("failures") or []
    if status and status != "success":
        tier, reasons = "hard", [f"工作流状态 {status}"]
    if fails:
        tier = "hard"
        reasons.append("节点失败: " + json.dumps(fails, ensure_ascii=False)[:300])

    # ③ 格式失败（上游字段空 / JSON 解析失败）
    miss = meta.get("missing_inputs") or {}
    if miss:
        if tier is None:
            tier = "format"
        reasons.append("缺失输入: " + json.dumps(miss, ensure_ascii=False)[:300])

    # ② 软失败（质量地板）
    qual = meta.get("quality") or {}
    soft = []
    for node, q in qual.items():
        soft += judge_quality_node(node, q)
    if soft:
        if tier is None:
            tier = "soft"
        reasons += soft

    if tier is None:
        tier = "ok"
    return {
        "need_escalate": tier != "ok",
        "tier": tier,
        "reasons": reasons,
        "status": status,
        "missing_inputs": miss,
        "quality": qual,
        "providers_used": meta.get("providers_used"),
        "thresholds_source": THRESHOLDS_SOURCE,
    }


def judge_trajectory(traj: dict) -> dict:
    """判定 DSH 侧的一条轨迹（用于「升级后又失败/未知」的情况 ✓ 也用于自测）。"""
    reasons, tier = [], None
    if traj.get("tool_errors"):
        tier, reasons = "hard", [f"工具报错: {traj['tool_errors']}"]
    if traj.get("reason") and traj["reason"] != "completed":
        if tier is None:
            tier = "unknown"
        reasons.append(f"结束原因 {traj['reason']}")
    msg = str(((traj.get("error") or {}).get("message")) or "")
    if "unknown" in msg.lower() or "interrupted" in msg.lower():
        tier = "unknown"                                  # ④ 结果未知 → 一律升级
        reasons.append("DSH: 结果未持久记录、后果未知")
    if tier is None:
        tier = "ok"
    return {"need_escalate": tier != "ok", "tier": tier, "reasons": reasons}


def build_task(doc: dict, verdict: dict, origin_task: str) -> str:
    """把「原任务 + 小模型失败摘要」拼成给 DSH 的任务（升级时必须带上下文 ✓）。"""
    src = doc.get("source_file") or "（未知输入）"
    lines = [
        origin_task or f"处理文件 {src} 并产出报告",
        "",
        "【背景：本地小模型已经试过并失败，请接手完成】",
        f"本地工作流: {doc.get('workflow')}  状态: {doc.get('status')}",
        f"失败档位: {verdict['tier']}",
    ]
    for r in verdict["reasons"][:6]:
        lines.append(f"  · {r}")
    lines += [
        "",
        "工作台后端地址 http://127.0.0.1:8001；请用你的工具完成任务，",
        "最后用一句话说明你做了什么、产出在哪个文件。",
    ]
    return "\n".join(lines)


def escalate(task: str, timeout: int = 900) -> dict:
    """调 DSH（headless）接手 —— 直接复用 reflect_run 的运行与解析 ✓ 不重复实现 ✓。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import reflect_run as rr

    class A:
        pass
    a = A()
    a.timeout = timeout
    a.keep_raw = True
    return rr.run_one(task, a)


def append_escalation(rec: dict) -> Path:
    TRAJ_DIR.mkdir(parents=True, exist_ok=True)
    f = TRAJ_DIR / "escalations.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return f


def selftest() -> int:
    print(f"阈值来源: {THRESHOLDS_SOURCE}")
    print(f"  充分度地板={TH['QUALITY_MIN_CHARS_PER_1K']}  可溯源={TH['QUALITY_GROUNDING_MIN']}  "
          f"事实保留={TH['QUALITY_FACT_RETENTION_MIN']}  短输入门槛={TH['QUALITY_SUFFICIENCY_MIN_INPUT']}")
    ok = 0
    cases = [
        ("① 硬失败-状态", {"status": "failed", "_meta": {}}, "hard"),
        ("① 硬失败-节点", {"status": "success", "_meta": {"failures": [{"node": "x"}]}}, "hard"),
        ("② 软失败-欠抽取", {"status": "success", "_meta": {"quality": {
            "extract_method": {"eligible": True, "source": "type", "input_chars": 8000,
                               "chars_per_1k_input": 3.5, "grounding": 0.99}}}}, "soft"),
        ("② 软失败-编造", {"status": "success", "_meta": {"quality": {
            "extract": {"eligible": True, "source": "type", "input_chars": 8000,
                        "chars_per_1k_input": 30.0, "grounding": 0.32}}}}, "soft"),
        ("③ 格式失败", {"status": "success", "_meta": {"missing_inputs": {"a": ["b.c"]}}}, "format"),
        ("✓ 通过", {"status": "success", "_meta": {"quality": {
            "extract": {"eligible": True, "source": "type", "input_chars": 8000,
                        "chars_per_1k_input": 30.0, "grounding": 0.999,
                        "key_fact_retention": 1.0, "key_facts": 3}}}}, "ok"),
    ]
    for name, doc, want in cases:
        v = judge_result(doc)
        good = v["tier"] == want
        ok += good
        print(f"  {'✓' if good else '✗'} {name:<16} 判为 {v['tier']:<7}（期望 {want}）"
              + ("" if good else f"  原因={v['reasons']}"))
    t1 = judge_trajectory({"reason": "completed", "tool_errors": []})
    t2 = judge_trajectory({"reason": "error", "error": {"message": "… out of durability … outcome is unknown"}})
    print(f"  {'✓' if t1['tier'] == 'ok' else '✗'} ④ 轨迹-正常     判为 {t1['tier']}")
    print(f"  {'✓' if t2['tier'] == 'unknown' else '✗'} ④ 轨迹-未知     判为 {t2['tier']}")
    ok += (t1["tier"] == "ok") + (t2["tier"] == "unknown")
    print(f"\n  通过 {ok}/{len(cases)+2}")
    return 0 if ok == len(cases) + 2 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="反射学习：失败判定 + 自动升级")
    ap.add_argument("--result", help="工作流结果 JSON 路径")
    ap.add_argument("--latest", action="store_true", help="用 outputs/ 里最新那个结果")
    ap.add_argument("--task", default="", help="原任务描述（升级时带上，强烈建议给）")
    ap.add_argument("--dry-run", action="store_true", help="只判定不升级")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if args.list:
        f = TRAJ_DIR / "escalations.jsonl"
        if not f.exists():
            print("（还没有升级记录）")
            return 0
        n = 0
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            n += 1
            print(f"  [{n}] {r.get('ts')}  档位={r.get('verdict',{}).get('tier'):<8}"
                  f"升级={'是' if r.get('escalated') else '否'}  轨迹原因={r.get('trajectory',{}).get('reason')}")
        print(f"  共 {n} 条 → {f}")
        return 0

    if args.latest:
        cands = sorted(glob.glob(str(ROOT / "outputs" / "result_*.json")), key=os.path.getmtime)
        if not cands:
            print("✗ outputs/ 里没有结果文件"); return 1
        path = cands[-1]
    elif args.result:
        path = args.result
    else:
        ap.error("需要 --result / --latest / --selftest / --list")

    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    v = judge_result(doc)
    print(f"=== 判定 {Path(path).name} ===")
    print(f"  阈值来源: {v['thresholds_source']}")
    print(f"  档位: {v['tier']}    需要升级: {v['need_escalate']}")
    for r in v["reasons"]:
        print(f"    · {r}")
    if not v["reasons"]:
        print("    （无问题 ✓）")

    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "result": str(path),
           "verdict": {k: v[k] for k in ("tier", "need_escalate", "reasons", "thresholds_source")},
           "escalated": False}
    if v["need_escalate"] and not args.dry_run:
        task = build_task(doc, v, args.task)
        print("\n=== 升级：交给 DSH 接手 ===")
        print("  任务（前 300 字）:", task[:300].replace("\n", " / "))
        traj = escalate(task, args.timeout)
        j = judge_trajectory(traj)
        rec["escalated"] = True
        rec["task"] = task
        rec["trajectory"] = {"session_id": traj.get("session_id"), "reason": traj.get("reason"),
                             "tools_used": traj.get("tools_used"), "usage": traj.get("usage"),
                             "ms": traj.get("ms"), "final": str(traj.get("final"))[:500]}
        rec["trajectory_verdict"] = j
        print(f"  DSH: reason={traj.get('reason')} 工具={traj.get('tools_used')} "
              f"token={traj.get('usage',{}).get('inputTokens')} 耗时={traj.get('ms')}ms")
        print(f"  升级后判定: {j['tier']}")
        print(f"  final: {str(traj.get('final'))[:200]}")
    f = append_escalation(rec)
    print(f"\n✓ 记录已追加 → {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
