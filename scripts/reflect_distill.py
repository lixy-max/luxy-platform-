#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reflect_distill.py —— 反射学习闭环第 4 步：把轨迹蒸馏成知识库条目。

设计要点（都是为了"能长期跑"✓）：
  · 产物是 agent_kb/90_learned.md —— 【整体重写】✓ 幂等 ✓
    （不是追加 ✗：追加会导致每次跑完 KB 里多一份重复条目 ✗
     实测 agent_use 库里同批文件已被重复入库 10 次，就是这类问题的后果 ✓）
  · 它【由轨迹派生】✓：真相在 data/trajectories/*.jsonl，这个 md 只是视图 ✓
    → 想回滚就删文件、重跑；不需要手工维护 ✓
  · 90_ 前缀安全 ✓：scripts/gen_agent_kb.py 只生成 10_tool_catalog.md（实测核实）✓
    所以这个文件不会被生成器覆盖 ✓
  · 不写「漂亮但没用」的总结 ✗ —— 只蒸馏【可操作的】东西：
      ① 触发场景 → 可用的工具调用序列（成功轨迹）
      ② 报错工具 + 错误分类 + 正确做法（失败步骤）
      ③ 每个工具的成功/失败/耗时【可靠性先验】← 对选工具最有用
      ④ 成本基线（token/耗时）← 决定"该不该升级"

用法：
    python scripts/reflect_distill.py                 # 蒸馏并写入 agent_kb/90_learned.md
    python scripts/reflect_distill.py --print         # 只看，不写文件
    python scripts/reflect_distill.py --min-count 2   # 只收录出现≥2次的序列
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAJ_DIR = Path(os.environ.get("TRAJ_DIR", ROOT / "data" / "trajectories"))
OUT = ROOT / "agent_kb" / "90_learned.md"

# 错误分类：把原始报错归成「该怎么办」，而不是原样堆上去 ✗
ERROR_RULES = [
    (r"GetNamedSecurityInfoW|ACL|sandbox", "环境/cwd 类",
     "cwd 落在 WSL 路径（\\\\wsl.localhost\\…）时 pwsh 会做 ACL 检查并失败 → 把子进程 cwd 设为 Windows 真实目录（如 E:\\WSL）"),
    (r"未知文档工具|ToolNotFound|unknown tool|not found", "工具名类",
     "工具名不存在 → 先调用 paper_lora_tool_list 拿准确名字，不要凭记忆猜"),
    (r'timed out|timeout', '超时类', '换更小的范围或分块处理；不要在原参数上重试'),
    (r"non-public IP|hostname .* resolves", "网络策略类", "127.0.0.1 这类非公网地址被策略拒绝 → 改用工作台工具（走 HTTP 后端）"),
    (r'还没有任何|empty|为空', '空数据类', '先确认上游是否真的产出了数据（读 _meta / 用 list 类工具核对），不要在空数据上继续推理'),
    (r'outcome is unknown|interrupted|未持久记录', '结果未知类', '工具已执行但结果未落库 → 只读操作可重试；有副作用的先核对状态，不要盲目重试'),
    (r'JSON|解析失败|parse', '格式类', '输出不是合法 JSON → 收紧提示词要求（只输出 JSON、不要代码块标记）'),
]
def classify(msg: str):
    # 按长度解包 ✓ 实测崩过: 有元组只有 2 个元素 ✗ 直接 ValueError 整条蒸馏挂掉 ✓
    for rule in ERROR_RULES:
        if len(rule) == 3:
            pat, kind, advice = rule
            if re.search(pat, msg or "", re.I):
                return kind, advice
        elif len(rule) == 2:
            continue          # 无正则 → 不参与匹配（留作兜底登记）
        else:
            continue
    return '其它', f'记下原始报错再处理（样例：{(msg or "")[:80]}）'


def load_jsonl(p: Path):
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def distill(min_count: int = 1) -> str:
    trajs = load_jsonl(TRAJ_DIR / "trajectories.jsonl")
    escs = load_jsonl(TRAJ_DIR / "escalations.jsonl")

    # 把 escalations 里的轨迹也当成轨迹（它们是「升级后」的，含大量真实工具序列 ✓）
    all_trajs = list(trajs)
    for e in escs:
        t = e.get("trajectory")
        if isinstance(t, dict) and t.get("tools_used"):
            all_trajs.append({"task": e.get("task") or "(升级任务)",
                              "tools_used": t.get("tools_used"), "reason": t.get("reason"),
                              "usage": t.get("usage") or {}, "ms": t.get("ms"),
                              "session_id": t.get("session_id"), "steps": []})

    # ① 成功序列（按任务关键词聚合）
    # 可模仿性过滤（实测教训 ✓）：实测一条开放式升级轨迹有 70+ 步（一路 glob/read/grep 徘徊 ✗），
    # 把它当 playbook 蒸进去是反效果 —— 小模型学不来也不该学。
    # 只收【step 骨架 ≤ MAX_SKELETON_STEPS】的序列，长探索轨迹另行记录计数、不进 playbook。
    MAX_SKELETON_STEPS = 12
    seqs = defaultdict(lambda: {"count": 0, "ok": 0, "samples": []})
    skipped_long = 0
    for t in all_trajs:
        tools = [x for x in (t.get("tools_used") or []) if x]
        if not tools:
            continue
        # 只看成功完成的（失败轨迹的"顺序"没有参考价值 ✗）
        if t.get("reason") != "completed":
            continue
        # 去掉连续重复，压成「序列骨架」（同一个工具连调 8 次不该算 8 个不同步骤 ✓）
        skel = []
        for x in tools:
            if not skel or skel[-1] != x:
                skel.append(x)
        if len(skel) > MAX_SKELETON_STEPS:
            skipped_long += 1
            continue
        key = tuple(skel)
        seqs[key]["count"] += 1
        seqs[key]["ok"] += 1
        if len(seqs[key]["samples"]) < 3:
            seqs[key]["samples"].append((str(t.get("task"))[:60], str(t.get("session_id"))[:12]))

    # ② 工具统计（可靠性先验 ✓）
    stat = defaultdict(lambda: {"ok": 0, "err": 0, "ms": [], "errors": Counter()})
    for t in all_trajs:
        for s in (t.get("steps") or []):
            for c in (s.get("tool_calls") or []):
                name = c.get("tool") or "?"
                st = c.get("status")
                if st == "completed":
                    stat[name]["ok"] += 1
                elif st:
                    stat[name]["err"] += 1
                    kind, _ = classify(str(c.get("result") or ""))
                    stat[name]["errors"][kind] += 1

    # ③ 错误分类汇总
    errs = defaultdict(list)
    for t in all_trajs:
        for s in (t.get("steps") or []):
            for c in (s.get("tool_calls") or []):
                if c.get("status") and c.get("status") != "completed":
                    kind, advice = classify(str(c.get("result") or ""))
                    errs[kind].append((c.get("tool"), str(c.get("result"))[:160], advice))

    # ④ 成本基线
    costs = [(int((t.get("usage") or {}).get("inputTokens") or 0), int(t.get("ms") or 0))
             for t in all_trajs if (t.get("usage") or {}).get("inputTokens")]
    n = max(1, len(costs))
    avg_tok = sum(c[0] for c in costs) // n
    avg_ms = sum(c[1] for c in costs) // n

    L = []
    L.append("# 从真实轨迹蒸馏出的经验（自动生成，勿手改）")
    L.append("")
    L.append(f"> 由 `scripts/reflect_distill.py` 从 `data/trajectories/*.jsonl` 生成 ✓")
    L.append(f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M')}    来源: "
             f"{len(trajs)} 条直接轨迹 + {len(escs)} 条升级记录    幂等: 每次整体重写 ✓")
    L.append(">")
    L.append("> 为什么有这个文件：内置小模型的问题不是「不懂工具」，而是**不知道该按什么顺序调、")
    L.append("> 哪些工具可靠、失败了该怎么改**。下面四节分别回答这四个问题。")
    L.append("")

    L.append("## 一、可用的工具调用序列（成功轨迹蒸馏）")
    L.append("")
    got = [(k, v) for k, v in seqs.items() if v["count"] >= min_count]
    got.sort(key=lambda kv: (-kv[1]["ok"], -kv[1]["count"]))
    if not got:
        L.append("（还没有足够轨迹 ✓ 先跑 `scripts/reflect_run.py` 攒几条）")
    for k, v in got[:12]:
        L.append(f"### 序列（成功 {v['ok']}/{v['count']} 次）")
        L.append("")
        L.append("```")
        for i, name in enumerate(k, 1):
            L.append(f"{i}. {name}")
        L.append("```")
        if v["samples"]:
            L.append("触发场景示例:")
            for task, sid in v["samples"]:
                L.append(f"- {task}  （session {sid}）")
        L.append("")

    L.append("## 二、工具可靠性先验（成功/失败 实测计数）")
    L.append("")
    L.append("> 选工具时优先用成功率高、没报过错的；下表是实测，不是文档说法 ✓")
    L.append("")
    L.append("| 工具 | 成功 | 失败 | 主要失败类型 |")
    L.append("|---|---|---|---|")
    for name, v in sorted(stat.items(), key=lambda kv: (-(kv[1]["ok"] + kv[1]["err"]))):
        kinds = "、".join(f"{k}×{c}" for k, c in v["errors"].most_common(3)) or "—"
        L.append(f"| `{name}` | {v['ok']} | {v['err']} | {kinds} |")
    L.append("")

    L.append("## 三、踩过的坑与正确做法（按错误类型归类）")
    L.append("")
    if not errs:
        L.append("（还没有报错记录 ✓）")
    for kind, items in sorted(errs.items(), key=lambda kv: -len(kv[1])):
        advice = items[0][2]
        tools = "、".join(sorted({t for t, _, _ in items if t}))
        L.append(f"### {kind}（{len(items)} 次，涉及: {tools}）")
        L.append("")
        L.append(f"**正确做法**：{advice}")
        L.append("")
        L.append("实测报错样例:")
        for t, msg, _ in items[:2]:
            L.append(f"- `{t}`: {msg}")
        L.append("")

    if skipped_long:
        L.insert(len(L) - 2, f"> 注：另有 {skipped_long} 条轨迹因「步骤骨架超过 {MAX_SKELETON_STEPS}」未进第一节 ✗ —— 那类长探索（几十步 glob/read/grep 徘徊）\n> 是升级过程本身的痕迹，不是可照抄的套路 ✓ 故意不收。\n")
    L.append("## 四、成本与耗时基线（决定「该不该升级到云端」）")
    L.append("")
    L.append(f"- 完成一次任务平均 **{avg_tok:,} input token / {avg_ms/1000:.1f} 秒**（{len(costs)} 条样本）")
    L.append("- 本地小模型跑得动的任务不要升级；只有命中失败判据（见 "
             "`scripts/reflect_escalate.py` 四档）才升级 ✓")
    L.append("- 升级任务要**窄口径** ✗：实测一次开放任务烧掉 10 万 token / 6 分钟 ✓")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="把轨迹蒸馏成知识库条目")
    ap.add_argument("--print", dest="only_print", action="store_true", help="只打印不写文件")
    ap.add_argument("--min-count", type=int, default=1, help="序列至少出现几次才收录")
    args = ap.parse_args()

    # ★★ 防护（血的教训 ✓）: 曾因"守卫误判 worker 结束 + 轨迹刚被清空"
    #    → 蒸出只有表头的空文件 ✗ 覆盖了 7762 字符的成果 ✓
    #    agent_kb/90_learned.md 是宝贵财富，绝不能被空数据覆盖 ✓
    _min = int(os.environ.get("MIN_TRAJ", "20"))
    _tf = TRAJ_DIR / "trajectories.jsonl"
    try:
        _n = sum(1 for l in _tf.read_text(encoding="utf-8").splitlines() if l.strip()) if _tf.exists() else 0
    except Exception:
        _n = 0
    if _n < _min:
        print(f"[distill] 拒绝重写: 轨迹只有 {_n} 条（< MIN_TRAJ={_min}）→ 保留现有 90_learned.md ✓")
        print("          如确实要用少量轨迹，显式设 MIN_TRAJ=<更小的数> 重跑")
        return 1

    md = distill(args.min_count)
    if args.only_print:
        print(md)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(md, encoding="utf-8")
    print(f"  ✓ 已写入 {OUT}（{len(md)} 字符 / {md.count(chr(10))} 行）")
    print("  提示: 这是幂等重写 ✓ 想回滚就删掉它 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
