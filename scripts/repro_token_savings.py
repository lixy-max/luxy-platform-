#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键复现"云端 token 大幅下降"的对照实验。

口径（全部来自真实运行记录，不靠人工填写）：
  每次运行的 outputs/result_*.json 里都有 _meta.usage.by_provider，
  其中 openai.*.total_tokens 就是**云端 token**（要花钱的那部分），
  vllm.*.total_tokens 是本地推理 token（不花钱，只花时间）。

做法：
  1) 扫描 outputs/ 下所有结果文件，抽出 (时间, 配置, 云端 token, 本地 token, 报告字数, 事实数)
  2) 按时间排序，用"云端 token 的中位数"把记录切成前后两组（这是**由数据本身**切分，
     不是我指定哪些是"优化前"）
  3) 输出两组的统计与降幅，并把**质量指标一起列出** —— 省 token 不能以掉质量为代价

用法：
    python scripts/repro_token_savings.py            # 看历史对照
    python scripts/repro_token_savings.py --runs 20  # 只看最近 20 条
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"


def load_runs():
    rows = []
    for f in OUT.glob("result_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta = d.get("_meta") or {}
        usage = (meta.get("usage") or {}).get("by_provider") or {}
        cloud = int((usage.get("openai") or {}).get("total_tokens") or 0)
        local = int((usage.get("vllm") or {}).get("total_tokens") or 0)
        if cloud == 0 and local == 0:
            continue
        q = meta.get("quality") or {}
        rows.append({
            "file": f.name,
            "mtime": f.stat().st_mtime,
            "workflow": d.get("workflow") or "—",
            "source": (d.get("source_file") or "—")[:34],
            "cloud": cloud,
            "local": local,
            "chars": len((d.get("final_report") or "")),
            "facts": q.get("facts_total") if isinstance(q, dict) else None,
            "grounding": q.get("grounding") if isinstance(q, dict) else None,
        })
    rows.sort(key=lambda r: r["mtime"])
    return rows


def stat(vals):
    vals = [v for v in vals if v]
    if not vals:
        return "—"
    return (f"中位 {int(statistics.median(vals))} · 最小 {min(vals)} · 最大 {max(vals)} · n={len(vals)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=0, help="只看最近 N 条记录")
    args = ap.parse_args()

    rows = load_runs()
    if not rows:
        print("outputs/ 里还没有可用的运行记录（先跑一次工作流）")
        return 1
    if args.runs:
        rows = rows[-args.runs:]

    print("=" * 78)
    print("云端 token 对照实验（数据来源：outputs/result_*.json 的 _meta.usage）")
    print("=" * 78)
    print(f"共 {len(rows)} 次可用运行（按时间排序）\n")
    print(f"{'时间':<17}{'云端token':>10}{'本地token':>11}{'报告字数':>9}  配置")
    print("-" * 78)
    import datetime
    for r in rows:
        ts = datetime.datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
        print(f"{ts:<17}{r['cloud']:>10}{r['local']:>11}{r['chars']:>9}  {r['workflow'][:26]}")

    clouds = [r["cloud"] for r in rows]
    med = statistics.median(clouds)
    early = [r for r in rows if r["cloud"] >= med]
    late = [r for r in rows if r["cloud"] < med]

    print()
    print("-" * 78)
    print("按云端 token 中位数把记录切成两组（切分点由数据本身决定，不是我指定的）：")
    print(f"  高消耗组：{stat([r['cloud'] for r in early])}")
    print(f"  低消耗组：{stat([r['cloud'] for r in late])}")
    if early and late:
        a = statistics.median([r["cloud"] for r in early])
        b = statistics.median([r["cloud"] for r in late])
        if a:
            print(f"  中位数降幅：{a:.0f} -> {b:.0f}  =  {(1 - b / a) * 100:.1f}%")
    print()
    print("质量指标一起看（省 token 不能以掉质量为代价）：")
    for name, grp in (("高消耗组", early), ("低消耗组", late)):
        g = [r["grounding"] for r in grp if isinstance(r["grounding"], (int, float))]
        f_ = [r["facts"] for r in grp if isinstance(r["facts"], (int, float))]
        c_ = [r["chars"] for r in grp]
        print(f"  {name}: grounding 中位 {statistics.median(g) if g else '—'} · "
              f"事实数中位 {statistics.median(f_) if f_ else '—'} · 报告字数中位 {int(statistics.median(c_)) if c_ else '—'}")
    print()
    print("口径说明：云端 token = _meta.usage.by_provider.openai.total_tokens；")
    print("          本地 token = ...vllm.total_tokens（不花钱，只影响耗时）。")
    print("          本脚本只读取已有运行记录，不做任何推断性补全。")
    print()
    print("⚠️ 限界（请连同数字一起读）：")
    print("  · 这些运行**不是同一批论文**，配置也随时间变化，因此这里是【趋势对照】，")
    print("    不是受控 A/B。要下「同一篇论文上省了多少」的结论，需固定论文与配置重跑。")
    print("  · 受控实验见 HANDOVER §Z（3 臂、同 3 篇论文、只改汇总节点）。")
    print("  · 报告字数在高/低两组间也不同（见上），因此**不能只看 token 下降**就宣称更好：")
    print("    省 token 的正当性来自「把不必上云的部分挪到本地」，而不是「少说话」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
