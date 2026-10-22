#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从真实论文构建【抽取式】训练集。

为什么需要它：
  data_generator.py 的现有做法是让模型"从摘要提取方法"，模型没有足够信息时
  只能靠参数知识补全 —— 于是训练目标本身就含编造，训出的 LoRA 学会的是
  "生成貌似合理的描述"，而不是"忠实抽取"（真实论文抽检已证实）。

本脚本的做法：
  输入 = 真实论文的**原文窗口**
  目标 = 该窗口内命中的**原文句子（逐字）**
  → 目标句 100% 来自输入，结构上不可能编造；脚本还会自检并报告 violations。

注意：因为是逐字抽取，**输出语言 = 原文语言**（英文论文就输出英文）。
      这是刻意的：翻译正是编造最容易混进来的环节。需要中文时请在下游单独做翻译节点。

用法：
  # 单篇（训练用）
  python build_extractive_dataset.py \
      --papers uploads/2946eb29_attention_is_all_you_need.txt \
      --out training_data/method_extract_v1.jsonl

  # 多篇 + 目录扫描
  python build_extractive_dataset.py --papers-dir uploads --extensions .txt \
      --out training_data/method_extract_v1.jsonl

  # JSON 模式（与 workflow 里"七维度抽取"节点一致，字段值仍为逐字句子）
  python build_extractive_dataset.py --papers X.txt --mode json --out ...
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_DIR = Path(__file__).resolve().parent
TRAINING_DATA_DIR = PROJECT_DIR / "training_data"
SYSTEM_PROMPT = "你是一个专业的助手，请准确、完整地回答用户的问题。"

# ============ 方面定义：指令 + 句子选择器 ============
# 每个方面 = 一条真实任务指令 + 一组"这句属于该方面"的判定规则。
# 选择器只做筛选，目标文本**永远**是原文句子本身。
ASPECTS = {
    "method": {
        "instruction": "请提取以下论文中的方法部分",
        "sections": ("method", "approach", "model", "architecture", "methodology"),
        "keywords": (r"\bwe (propose|present|introduce|use|employ|design|train|apply)\b",
                     r"\bour (model|method|approach|architecture|framework|network)\b",
                     r"\bthe (model|architecture|network) (consists|comprises|is composed)\b",
                     r"\balgorithm\b", r"\btraining (procedure|objective|strategy|details)\b",
                     r"本文(提出|采用|使用|设计)", r"方法(部分|包括|如下)"),
    },
    "data": {
        "instruction": "请提取以下论文中使用的数据集与实验设置",
        "sections": (),
        "keywords": (r"\b(dataset|corpus|benchmark|evaluation set|test set|training set)\b",
                     r"\bwe (train|evaluate|test) (on|with)\b",
                     r"\b(WMT|ImageNet|GLUE|SQuAD|CNN/DailyMail)\b",
                     r"数据集", r"语料", r"实验(设置|平台)"),
    },
    "research_question": {
        "instruction": "请提取以下论文的研究问题",
        "sections": ("abstract", "introduction"),
        "keywords": (r"\b(this paper|in this work|we (study|investigate|address|propose|present))\b",
                     r"\bthe (goal|aim|objective) of\b",
                     r"本文(研究|提出|解决)", r"研究(问题|目标)"),
    },
    "contribution": {
        "instruction": "请提取以下论文的主要贡献",
        "sections": ("introduction", "conclusion"),
        "keywords": (r"\b(our (main )?contribution|we (show|demonstrate) that|first to|novel)\b",
                     r"\b(state[- ]of[- ]the[- ]art|outperform)\b",
                     r"主要贡献", r"创新(点|之处)"),
    },
    "conclusion": {
        "instruction": "请提取以下论文的结论",
        "sections": ("conclusion", "discussion"),
        "keywords": (r"\b(we (have )?(conclude|shown|presented|proposed))\b",
                     r"\bin (this|our) (work|paper) we\b", r"\bfuture work\b",
                     r"结论", r"总结"),
    },
}

SECTION_PATTERNS = {
    "abstract": r"(?im)^\s*abstract\b",
    "introduction": r"(?im)^\s*(1\.?\s+)?introduction\b",
    "method": r"(?im)^\s*\d?\.?\s*(model architecture|model|method(s|ology)?|approach|proposed (method|model))\b",
    "conclusion": r"(?im)^\s*\d?\.?\s*(conclusions?|discussion and conclusions?)\b",
}

FIGURE_PREFIX = re.compile(r"^\s*(figure|fig\.|table|algorithm)\s*\d+", re.IGNORECASE)
REFERENCE_CUT = re.compile(r"(?im)^\s*(references|bibliography|参考文献)\s*$")
# 中文标点后通常没有空格，英文句点后必须有空格（否则会切坏 U.S. / 3.5 这类）；
# 换行本身也是边界 —— 否则章节标题会粘到下一句上。
# 常见缩写后的句点不是句末（论文里 "et al." / "Fig." / "i.e." 极常见）。
# 每个否定环视都是定宽，Python 允许。
_ABBR = (r"(?<!et al\.)(?<!al\.)(?<!Fig\.)(?<!fig\.)(?<!Eq\.)(?<!eq\.)"
         r"(?<!Sec\.)(?<!sec\.)(?<!Tab\.)(?<!tab\.)(?<!No\.)(?<!no\.)"
         r"(?<!i\.e\.)(?<!e\.g\.)(?<!cf\.)(?<!vs\.)(?<!approx\.)")
SENT_SPLIT = re.compile(r"(?<=[。！？；])|" + _ABBR + r"(?<=[.!?])\s+|\n+")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
WS_RE = re.compile(r"\s+")


def squash(text):
    """去掉全部空白，并给出"无空白坐标 → 原文坐标"的映射。

    句子在切分时空白会被归一化，直接用 text.find() 定位会失败，
    进而把句子错分到上一个章节 —— 所以统一在无空白坐标里比较。
    """
    out, orig = [], []
    for i, ch in enumerate(text):
        if not ch.isspace():
            out.append(ch)
            orig.append(i)
    return "".join(out), orig


def squash_pos(orig_index, orig_map, lo=0):
    import bisect
    return bisect.bisect_left(orig_map, orig_index, lo)


def min_sentence_len(s):
    """语言自适应的最短句长：中文信息密度高，28 字已是完整句子。"""
    cjk = len(CJK_RE.findall(s))
    return 20 if cjk * 2 > len(s) else 40


SENT_END_RE = re.compile(r"""[.!?]["')\]\u201d\u2019]?\s*$""")
NEXT_CONT_RE = re.compile(r"^\s*(?:[a-z]|\d+\s*[a-z]|[(\[]?[a-z])")   # 续行：小写或数字+小写


def _is_table_row(s):
    """数字占比高的行 —— 表格/结果行，不参与拼接。"""
    toks = s.split()
    if len(toks) < 4:
        return False
    nums = sum(1 for x in toks if re.fullmatch(r"[-+]?\d*\.?\d+%?", x))
    return nums / len(toks) >= 0.4


def join_wrapped_lines(text):
    """把**硬换行**切断的句子拼回去（PDF 常见：80%+ 的行不带句末标点）。

    判据（保守优先，宁可少拼）：
      1) 上一行**不以句末标点结尾**（. ! ? 及其后引号/括号）
      2) 下一行**以小写字母/数字开头** —— 硬换行续行的典型特征；
         新段落与标题以大写开头，因此不会被误拼
      3) 两边都不是表格行；空行天然断开
      4) 上一行以 `-` 结尾时**去掉连字符**再接（distilla- + tion -> distillation）
    """
    if not text:
        return text
    out = []
    for raw in text.split("\n"):
        s = raw.rstrip()
        if not out or not s.strip() or not out[-1].strip():
            out.append(s)
            continue
        prev = out[-1]
        can_join = (not SENT_END_RE.search(prev)
                    and bool(NEXT_CONT_RE.match(s))
                    and not _is_table_row(prev)
                    and not _is_table_row(s)
                    and not prev.rstrip().endswith(":"))
        if can_join:
            if re.search(r"[A-Za-z]\-$", prev.rstrip()):
                out[-1] = prev.rstrip()[:-1] + s.lstrip()
            else:
                out[-1] = prev.rstrip() + " " + s.lstrip()
        else:
            out.append(s)
    return "\n".join(out)


def clean_paper_text(text):
    """清洗 PDF 提取文本：去掉参考文献之后的内容，合并硬换行，去掉明显噪声。"""
    text = REFERENCE_CUT.split(text)[0]                 # 参考文献列表整体丢弃
    text = re.sub(r"[ \t]+", " ", text)
    # PDF 断行的三种形态（顺序重要）：
    # 1) 数字中间断行：`(p=0.` + newline + `129)` → 必须**无空格**拼接，否则数字被切成两个
    text = re.sub(r"(?<=[0-9.,])\n(?=[0-9])", "", text)
    # 2) 连字符断行：`sig-` + newline + `nificantly` → 去掉连字符拼接
    #    （副作用：极少数真连字符复合词在行尾会被并成一个词，影响极小）
    text = re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", text)
    # 3) 普通硬换行：小写字母/数字/逗号/分号之后换行 → 合成空格（原规则只覆盖字母/逗号分号）
    text = re.sub(r"(?<=[a-z0-9,;])\n(?=[a-z0-9])", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return join_wrapped_lines(text.strip())


def split_sentences(text):
    """句子切分：中英都覆盖，过滤过短片段与图表标题。"""
    out = []
    for raw in SENT_SPLIT.split(text):
        s = re.sub(r"\s+", " ", (raw or "")).strip()
        if len(s) < min_sentence_len(s):                 # 太短的多半是标题/碎片
            continue
        if FIGURE_PREFIX.match(s):
            continue
        if s.count("=") > 4 or s.count("[") > 3:         # 公式/引用密集
            continue
        out.append(s)
    return out


def find_sections(text):
    """返回 {section: (start, end)}，用于按章节限定选择范围。"""
    marks = []
    for name, pat in SECTION_PATTERNS.items():
        for m in re.finditer(pat, text):
            marks.append((m.start(), name))
    marks.sort()
    bounds = {}
    for i, (pos, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        if name not in bounds:                           # 第一次出现为准
            bounds[name] = (pos, end)
    return bounds


def select_sentences(sentences, aspect, sections, section_texts):
    """
    挑出属于该方面的句子。规则优先级：
      1) 若该方面限定了章节且章节存在 → 章节内命中的句子
      2) 否则全文关键词命中
    返回 (命中句子列表, 命中的索引集合)
    """
    cfg = ASPECTS[aspect]
    kw = [re.compile(p, re.IGNORECASE) for p in cfg["keywords"]]

    allowed_ids = None
    if cfg["sections"]:
        found = set()
        for sec in cfg["sections"]:
            found |= section_texts.get(sec, set())
        # ⚠️ 章节限制只在"该章节确实存在"时生效；否则必须退回全文关键词搜索，
        #    否则会得到一个空集合，把所有句子静默过滤掉（零样本）。
        allowed_ids = found or None

    hits = []
    for i, s in enumerate(sentences):
        if allowed_ids is not None and i not in allowed_ids:
            continue
        if any(p.search(s) for p in kw):
            hits.append(s)
    return hits


def select_sentences_in(sentences, aspect):
    """兼容旧签名：不限定章节，仅关键词。"""
    return select_sentences(sentences, aspect, None, {})


def build_index(text):
    """
    把清洗后的文本切句，并建立"章节 → 句子索引"映射。
    位置比较统一在**无空白坐标**里做（见 squash()），避免因空白差异定位失败后
    静默把句子分到错误章节。
    返回 (sentences, section_texts)
    """
    sentences = split_sentences(text)
    squashed, orig_map = squash(text)
    bounds_orig = find_sections(text)
    bounds = {name: (squash_pos(a, orig_map), squash_pos(b, orig_map))
              for name, (a, b) in bounds_orig.items()}

    positions, cursor = [], 0
    for s in sentences:
        key = squash(s)[0][:40]
        idx = squashed.find(key, cursor) if key else -1
        if idx == -1:
            idx = squashed.find(key) if key else -1      # 回退：全局搜一次
        if idx == -1:
            idx = cursor                                 # 仍找不到：保守沿用顺序
        else:
            cursor = idx + 1
        positions.append(idx)

    section_texts = {}
    for name, (start, end) in bounds.items():
        ids = {i for i, p in enumerate(positions) if start <= p < end}
        if ids:
            section_texts[name] = ids
    return sentences, section_texts


def make_windows(text, size, overlap):
    """重叠窗口；优先在段落边界切开，避免把句子切断。"""
    if size <= 0:
        return [text]
    step = max(size - overlap, size // 2)
    windows = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n\n", start, end)
            if cut != -1 and cut > start + size // 2:
                end = cut
        windows.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return windows


def build_samples(text, aspects, window, overlap, max_target_chars, mode="prose"):
    """
    生成 sharegpt 样本。**目标永远是原文句子**，逐字。
    返回 (samples, stats)
    """
    cleaned = clean_paper_text(text)
    windows = make_windows(cleaned, window, overlap)
    samples, stats = [], {"windows": len(windows), "emitted": 0, "skipped_empty": 0,
                          "skipped_toolong": 0, "violations": 0, "target_chars": [],
                          "input_chars": []}

    for w in windows:
        sents, sec_texts = build_index(w)
        if not sents:
            continue

        if mode == "json":
            fields = {a: select_sentences(sents, a, None, sec_texts) for a in aspects}
            if not any(fields.values()):
                stats["skipped_empty"] += 1
                continue
            target = json.dumps(fields, ensure_ascii=False, indent=2)
            instruction = ("请从以下论文片段中抽取研究问题、方法、数据集、主要贡献、结论，"
                           "以严格 JSON 格式输出，每个字段是原文句子列表（逐字引用，不要改写）")
            candidates = [(instruction, target)]
        else:
            # 一个方面一条样本：指令只问这一个方面，目标也只有这一个方面的句子
            # —— 避免"指令问方法、目标里却混着结论"这种指令/目标不匹配
            candidates = [(ASPECTS[a]["instruction"],
                           "\n".join(select_sentences(sents, a, None, sec_texts)))
                          for a in aspects]
            candidates = [(ins, tgt) for ins, tgt in candidates if tgt.strip()]
            if not candidates:
                stats["skipped_empty"] += 1
                continue

        for instruction, target in candidates:
            if len(target) > max_target_chars:
                stats["skipped_toolong"] += 1
                continue

            human = f"{instruction}：\n\n{w.strip()}"
            # 自检：目标里的每句话都必须逐字出现在输入里
            for raw_line in target.splitlines():
                line = raw_line.strip().strip('",[]')
                if len(line) < 25:
                    continue
                # JSON 模式下目标句里的引号被转义（\"），直接比原文会假报违规。
                # 必须在 **strip 引号之前** 用原始 JSON 行解码；先 strip 会破坏 JSON 语法。
                cand = line
                if mode == "json":
                    try:
                        decoded = json.loads(raw_line.strip().rstrip(","))
                        if isinstance(decoded, str):
                            cand = decoded
                    except Exception:
                        pass
                if cand not in human:
                    stats["violations"] += 1

            samples.append({"conversations": [
                {"from": "system", "value": SYSTEM_PROMPT},
                {"from": "human", "value": human},
                {"from": "gpt", "value": target},
            ]})
            stats["emitted"] += 1
            stats["target_chars"].append(len(target))
            stats["input_chars"].append(len(human))
    return samples, stats


def main():
    ap = argparse.ArgumentParser(description="从真实论文构建抽取式训练集")
    ap.add_argument("--papers", nargs="*", default=[], help="论文 txt 路径（可多个）")
    ap.add_argument("--papers-dir", default=None, help="扫描目录下的论文")
    ap.add_argument("--extensions", default=".txt", help="目录扫描的后缀，逗号分隔")
    ap.add_argument("--out", required=True, help="输出 jsonl")
    ap.add_argument("--aspects", default="method,data,research_question,contribution,conclusion",
                    help="要覆盖的方面（逗号分隔）")
    ap.add_argument("--window", type=int, default=3000, help="窗口字符数（0=整篇）")
    ap.add_argument("--overlap", type=int, default=500)
    ap.add_argument("--max-target-chars", type=int, default=1800,
                    help="目标过长则跳过该窗口（避免超出生成预算）")
    ap.add_argument("--mode", choices=("prose", "json"), default="prose")
    ap.add_argument("--holdout", default=None,
                    help="把这些论文完全排除（按文件名子串匹配），用于跨论文验证")
    args = ap.parse_args()

    papers = [Path(p) for p in args.papers]
    if args.papers_dir:
        exts = tuple(e.strip() for e in args.extensions.split(",") if e.strip())
        papers += [p for p in sorted(Path(args.papers_dir).iterdir())
                   if p.is_file() and p.suffix in exts]
    if args.holdout:
        keys = [k.strip() for k in args.holdout.split(",") if k.strip()]
        papers = [p for p in papers if not any(k in p.name for k in keys)]
    if not papers:
        raise SystemExit("没有输入论文")

    aspects = [a.strip() for a in args.aspects.split(",") if a.strip()]
    for a in aspects:
        if a not in ASPECTS:
            raise SystemExit(f"未知方面: {a}（可选: {', '.join(ASPECTS)}）")

    all_samples, grand = [], {"emitted": 0, "violations": 0, "target_chars": [], "input_chars": []}
    print(f"输入论文 {len(papers)} 篇，方面 {len(aspects)} 个，模式 {args.mode}")
    for p in papers:
        text = p.read_text(encoding="utf-8", errors="ignore")
        samples, st = build_samples(text, aspects, args.window, args.overlap,
                                    args.max_target_chars, args.mode)
        for key in ("emitted", "violations"):
            grand[key] += st[key]
        grand["target_chars"] += st["target_chars"]
        grand["input_chars"] += st["input_chars"]
        print(f"  {p.name}: {len(text)} 字 → 窗口 {st['windows']}，"
              f"产出 {st['emitted']} 条（空 {st['skipped_empty']}，过长 {st['skipped_toolong']}）")
        all_samples += samples

    if not all_samples:
        raise SystemExit("没有产出任何样本（检查论文文本与方面关键词）")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    tc, ic = grand["target_chars"], grand["input_chars"]
    print()
    print(f"总计 {len(all_samples)} 条 → {out}")
    print(f"  目标平均 {sum(tc)/len(tc):.0f} 字（最短 {min(tc)}，最长 {max(tc)}）")
    print(f"  输入平均 {sum(ic)/len(ic):.0f} 字")
    print(f"  每千字输入产出 {sum(tc)/max(sum(ic),1)*1000:.1f} 字")
    print(f"  ✅ 逐字自检 violations = {grand['violations']}（必须为 0，否则说明目标不是原文）")
    print(f"  生成时间 {datetime.now().isoformat(timespec='seconds')}  "
          f"sha256 {hashlib.sha256(out.read_bytes()).hexdigest()[:16]}")
    if grand["violations"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
