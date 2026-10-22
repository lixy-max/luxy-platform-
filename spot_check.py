#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真实论文人工抽检：无标准答案场景下的 底座 vs LoRA 对照。

与 eval_lora.py 的分工：
  eval_lora.py  有参考输出 → 算 similarity / 覆盖率（适合合成评测集）
  spot_check.py 没有参考输出（真实论文）→ 算【不依赖参考】的质量信号，
                并生成一份可直接人工打分的 Markdown 对照表

无参考指标：
  grounding        输出中的字符片段(bigram)有多大比例能在原文里找到 —— 抓"编造"
  sentence_reuse   输出句子中逐字出现在原文的比例 —— 抽取式任务应偏高
  preamble         输出是否先来一句"方法部分提取如下"之类的套话
  chars / numbered_items / latency

用法：
  python spot_check.py --lora test_agent_v1 --cases cases.json
  python spot_check.py --lora test_agent_v1 --cases cases.json --max-tokens 1024
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import eval_lora as ev
# S5：与引擎共用同一份度量实现（此前这里另有一份等价实现，容易各自漂移）
from text_metrics import grounding_ratio, language_of, number_acronym_retention

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "outputs"

# 与训练数据一致的 prompt 模板（评测必须用同一条指令，否则比的是指令而不是模型）
PROMPT_TEMPLATE = "请提取以下论文中的方法部分：\n\n{content}"

PREAMBLE_PATTERNS = (
    r"^\s*方法部分提取如下",
    r"^\s*提取如下",
    r"^\s*以下(是|为)",
    r"^\s*如下(是|为)",
    r"^\s*好的[，,]",
    r"^\s*根据(该|这篇)?论文",
    r"^\s*Based on",
    r"^\s*Here (is|are)",
    r"^\s*The method section",
    r"^\s*I (will|have|can)\b",
)
# 前言也可能出现在第一句的后半段，例如
# "Based on the provided paper, here is a detailed extraction of the method section:"
PREAMBLE_INLINE = re.compile(
    r"here (is|are) (a |the )?(detailed |complete |comprehensive )?"
    r"(extraction|summary|breakdown|analysis)",
    re.IGNORECASE)


def detect_preamble(text):
    """输出是否以套话开头（真实任务里这属于噪声，需要人工删掉）。"""
    norm = ev.normalize(text)
    head = norm[:60]
    if any(re.search(p, head, re.IGNORECASE) for p in PREAMBLE_PATTERNS):
        return True
    # 第一句内出现"here is a detailed extraction"这类也算前言
    first_sentence = norm[:200].split(". ")[0]
    return bool(PREAMBLE_INLINE.search(first_sentence))


def split_sentences(text):
    """中英混排的粗切句：中文标点直接切，英文句点要求后跟空格。"""
    text = ev.normalize(text)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？；])|(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 8]







def sentence_reuse_ratio(text, source):
    """输出句子中逐字出现在原文里的比例（抽取式任务的核心信号）。"""
    sents = split_sentences(text)
    if not sents:
        return 0.0
    src = ev.normalize(source)
    hit = sum(1 for s in sents if ev.normalize(s) in src)
    return round(hit / len(sents), 4)


def score_no_reference(text, source):
    text = text or ""
    src_lang, out_lang = language_of(source), language_of(text)
    cross = ((out_lang in ("zh", "mixed") and src_lang == "en") or
             (out_lang == "en" and src_lang in ("zh", "mixed")))
    retention, n_facts, n_hit = number_acronym_retention(text, source)
    return {
        "chars": len(text),
        "empty": not text.strip(),
        "output_language": out_lang,
        "cross_language": cross,
        "grounding": grounding_ratio(text, source),
        # 跨语言时用 key_fact_retention（数字+缩写保留率）判断忠实度
        "key_fact_retention": retention,
        "key_facts_in_output": n_facts,
        "key_facts_matched": n_hit,
        "sentence_reuse": sentence_reuse_ratio(text, source),
        "preamble": detect_preamble(text),
        "numbered_items": len(re.findall(r"[1-9][.、)）](?:\s|[\u4e00-\u9fff])", text)),
        # 抽取充分度：每千字输入产出多少字（明显偏低即为"抽取过少"）
        "chars_per_1k_input": round(len(text) / max(len(source), 1) * 1000, 1),
    }


def _block(text, limit=None):
    body = (text or "").strip()
    if limit and len(body) > limit:
        body = body[:limit] + f"\n…（已截断，完整内容见 JSON，共 {len(text)} 字）"
    return "\n".join("> " + ln for ln in body.splitlines()) if body else "> （空）"


def build_markdown(lora_name, base_model, cases, results, sampling):
    lines = []
    lines.append(f"# LoRA 真实论文抽检 · `{lora_name}`")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 底座：`{base_model}`")
    lines.append(f"- 采样：`{json.dumps(sampling, ensure_ascii=False)}`")
    lines.append(f"- 指令模板：`{PROMPT_TEMPLATE}`（与训练数据一致）")
    lines.append("")
    lines.append("## 结论速览（自动指标）")
    lines.append("")
    lines.append("| # | 用例 | 类型 | 关键事实保留<br>底座→LoRA | 输出字数<br>底座→LoRA | "
                 "每千字输入产出<br>底座→LoRA | 套话<br>底座/LoRA | 输出语言 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, (case, r) in enumerate(zip(cases, results), 1):
        b, l = r.get("base") or {}, r.get("lora") or {}
        def num(v):
            return "—" if v is None else v          # — = 输出里没有数字/缩写，无从判断
        def pair(key, b=b, l=l):
            return f"{num(b.get(key))} → **{num(l.get(key))}**"
        lines.append(
            f"| {i} | {case['label']} | {case.get('kind', '-')} "
            f"| {pair('key_fact_retention')} | {b.get('chars')} → {l.get('chars')} "
            f"| {b.get('chars_per_1k_input')} → {l.get('chars_per_1k_input')} "
            f"| {'是' if b.get('preamble') else '否'} / {'是' if l.get('preamble') else '否'} "
            f"| {l.get('output_language')} |")
    lines.append("")
    lines.append("**指标怎么读**")
    lines.append("")
    lines.append("- **关键事实保留**：输出里出现的数字与英文缩写（如 `N=6`、`512`、`BLEU 28.4`、`FFN`）"
                 "能在原文找到的比例。**跨语言也能用**，是判断「有没有编造」的首选信号。")
    lines.append("- **每千字输入产出**：抽取充分度。明显偏低（如 <10）说明输出过短、漏掉了大量方法细节。")
    lines.append("- **套话**：输出是否以「方法部分提取如下」这类前言开头 —— 真实入库时需人工删掉。")
    lines.append("- ⚠️ **grounding / 复用率**：当输出语言与原文不同（英文论文→中文摘要）时，"
                 "字符重合会**失真**，只能反映「保留了多少英文术语」，不能当忠实度用。"
                 "本表因此改以「关键事实保留」为主。")
    lines.append("")
    lines.append("## 人工评分表（请填写）")
    lines.append("")
    lines.append("| # | 用例 | 抽取完整性 /5 | 事实忠实度(无编造) /5 | 格式可用性 /5 | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    for i, case in enumerate(cases, 1):
        lines.append(f"| {i} | {case['label']} |  |  |  |  |")
    lines.append("")
    lines.append("> 评分参考：5=可直接用；3=需小改；1=基本不可用。")
    lines.append("> **忠实度最重要**：一旦混入原文没有的方法描述，整份分析即失效。")
    lines.append("")

    for i, (case, r) in enumerate(zip(cases, results), 1):
        lines.append("---")
        lines.append("")
        lines.append(f"## 用例 {i}：{case['label']}")
        lines.append("")
        lines.append(f"- 类型：**{case.get('kind', '-')}**")
        lines.append(f"- 说明：{case.get('note', '')}")
        lines.append(f"- 输入长度：{len(case['content'])} 字")
        lines.append("")
        lines.append("<details><summary>输入（点击展开）</summary>")
        lines.append("")
        lines.append(_block(case["content"], 1500))
        lines.append("")
        lines.append("</details>")
        lines.append("")
        for tag, title in (("base", "底座"), ("lora", "LoRA")):
            m = r.get(tag) or {}
            lines.append(f"### {title}输出（{m.get('chars', 0)} 字"
                         f"，grounding={m.get('grounding')}"
                         f"，复用率={m.get('sentence_reuse')}）")
            lines.append("")
            lines.append(_block(m.get("text"), 3000))
            lines.append("")
    return "\n".join(lines)


def run_spot_check(lora_name, cases, base_model=None, temperature=0.0,
                   max_tokens=1024, workers=2, markdown_limit_note=True):
    if not cases:
        raise SystemExit("没有用例")

    ok, msg = ev.ensure_lora_loaded(lora_name)
    ev.log(f"LoRA '{lora_name}': {msg}")
    if not ok:
        raise SystemExit(f"LoRA 加载失败: {msg}")

    resolved_base = ev.resolve_base_model(base_model)
    if not resolved_base:
        raise SystemExit("拿不到底座模型名")
    ev.log(f"底座: {resolved_base}")

    def work(item):
        idx, case = item
        prompt = PROMPT_TEMPLATE.format(content=case["content"])
        row = {"label": case["label"]}
        for tag, model in (("base", resolved_base), ("lora", lora_name)):
            try:
                text, el = ev.call_model(model, "", prompt,
                                         temperature=temperature,
                                         max_tokens=max_tokens)
                m = score_no_reference(text, case["content"])
                m["latency_sec"] = round(el, 2)
                m["text"] = text
            except Exception as e:
                m = {"error": str(e), "empty": True, "chars": 0, "text": "",
                     "grounding": 0.0, "sentence_reuse": 0.0, "preamble": False,
                     "numbered_items": 0, "latency_sec": None}
            row[tag] = m
        return row

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(work, list(enumerate(cases, 1))))
    elapsed = round(time.time() - t0, 1)

    for case, r in zip(cases, results):
        b, l = r["base"], r["lora"]
        ev.log(f"[{case['label']}] 底座 grounding={b.get('grounding')} 复用={b.get('sentence_reuse')} "
               f"{b.get('chars')}字{'(有套话)' if b.get('preamble') else ''} | "
               f"LoRA grounding={l.get('grounding')} 复用={l.get('sentence_reuse')} "
               f"{l.get('chars')}字{'(有套话)' if l.get('preamble') else ''}")

    sampling = {"temperature": temperature, "max_tokens": max_tokens,
                "workers": workers, "n_cases": len(cases)}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = OUTPUT_DIR / f"spotcheck_{lora_name}_{ts}.json"
    md_path = OUTPUT_DIR / f"spotcheck_{lora_name}_{ts}.md"

    json_path.write_text(json.dumps({
        "lora_name": lora_name, "base_model": resolved_base,
        "sampling": sampling, "elapsed_sec": elapsed,
        "finished_at": datetime.now().isoformat(),
        "cases": [{k: v for k, v in c.items()} for c in cases],
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    md = build_markdown(lora_name, resolved_base, cases, results, sampling)
    md_path.write_text(md, encoding="utf-8")

    ev.log(f"耗时 {elapsed}s")
    ev.log(f"Markdown 对照表: {md_path}")
    ev.log(f"原始 JSON: {json_path}")
    return str(md_path), str(json_path), results


def main():
    ap = argparse.ArgumentParser(description="真实论文人工抽检：底座 vs LoRA")
    ap.add_argument("--lora", required=True)
    ap.add_argument("--cases", required=True, help="用例 JSON：[{label,kind,note,content}]")
    ap.add_argument("--base-model", default=None)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    run_spot_check(args.lora, cases, base_model=args.base_model,
                   temperature=args.temperature, max_tokens=args.max_tokens,
                   workers=args.workers)


if __name__ == "__main__":
    main()
