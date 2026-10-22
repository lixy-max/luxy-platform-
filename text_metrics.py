# -*- coding: utf-8 -*-
"""
文本度量：节点级质量指标（可溯源率、关键事实保留率等）。

为什么单独成模块：workflow_engine 是子进程脚本，需要自包含的度量实现；
spot_check.py 从本模块导入通用度量（只保留自己的句切/套话检测）。

指标口径：
  grounding           输出中的字符二元组有多大比例能在输入里找到（抓"用没用原文的词"）
                      已知局限：对**句子顺序无感**（重排句子仍接近满分）
  key_fact_retention  输出里的数字/缩写能在输入里找到的比例（跨语言可用）
                      ⚠️ 2026-10-06 修口径：改为**数量级感知**
  chars_per_1k_input  每千字输入产出多少字（抽取充分度）

为什么"关键事实"必须数量级感知（真实证据）：
  报告写「英法数据集（3600 万句）」，论文写「36M」——**数值相同**，
  但裸字符串比对把 "3600" 与 "36M" 判为无关。实测这会让聚合节点的
  关键事实保留被系统性低估（0.706 → 修正后 ~0.92），并制造出一批
  "报告里有数字没有出处"的**假警报**。
  与"grounding 满分源于缓存污染"是同一类教训：**度量口径错，结论就错**。

⚠️ 另一个已修的盲区：英文数量级单位后**紧跟汉字**时（`200K规模`、`16K上下文`）
曾因 `\b` 与汉字冲突而识别失败，使中文报告的事实保留率被系统性低估。

一个刻意的取舍：`16K` 与裸 `16` **不匹配**。带数量级词的数字会被归一成
真实数值（16K → 16000），而裸 16 就是 16 —— 数量级不同，
把它们当同一事实才是旧口径的 bug。
"""
import re

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ACRONYM = re.compile(r"\b[A-Z]{2,}\b")

# 数量级词表：长的必须排前面，否则"千万"会被"千"先吃掉
_CN_SCALE = (("万亿", 1e12), ("千万", 1e7), ("百万", 1e6), ("十万", 1e5),
             ("亿", 1e8), ("万", 1e4), ("千", 1e3))
_CN_SCALE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万亿|千万|百万|十万|亿|万|千)")
# 单字母数量级后要求词边界：这样 "8GB" 不会被误读成 "8G"
_EN_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "mn": 1e6,
             "b": 1e9, "billion": 1e9, "bn": 1e9}
# 收尾不用 \b：Python 的 \b 把汉字也算词字符，于是 "200K规模" 里 K 与 规 之间
# 没有边界 → 匹配失败 → 退化成裸 200 → 与源文 200000 对不上（实测踩到，制造了假警报）。
# 改用 (?![A-Za-z])：只要求后面不是 ASCII 字母 —— "200K规模" 正常识别，
# 而 "8GB" 仍不会被误读成 8G（后面是字母 B）。
_EN_SCALE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(thousand|million|billion|mn|bn|[kKmMbB])(?![A-Za-z])",
                          re.IGNORECASE)
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_PLAIN_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def char_bigrams(text):
    text = normalize(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a, b):
    sa, sb = char_bigrams(a), char_bigrams(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def grounding_ratio(output, source):
    """输出有多大比例能在输入里找到。1.0 = 没有外来词汇。

    局限：只看**词汇**是否出现，对句子顺序无感 —— 重排原文句子仍接近 1.0，
    而语义可能已被破坏（这类问题需要别的判据，不要指望它）。
    """
    out_bg = char_bigrams(output)
    if not out_bg:
        return 0.0
    return round(len(out_bg & char_bigrams(source)) / len(out_bg), 4)


def _canon_num(value):
    """数值 → 规范 token（6 位有效数字，避免浮点尾差造成假不等）。"""
    return f"{float(value):.6g}"


def fact_tokens(text):
    """
    抽取"关键事实"token 集合：数量级感知的数字 + 英文缩写。

    - `450万` / `4.5M`            → 同一个 token（4.5e+06）
    - `36,000,000` / `36M` / `3600万` → 同一个 token
    - `16K` → 1.6e+04（**不再**同时保留裸 `16`，避免与裸 16 误匹配）
    - 裸数字原样保留（`512` → 512）
    """
    text = str(text or "")
    tokens = set()
    consumed = []          # 已消费区间，避免同一段被重复解析

    for m in _CN_SCALE_RE.finditer(text):
        scale = dict(_CN_SCALE)[m.group(2)]
        tokens.add(_canon_num(float(m.group(1)) * scale))
        consumed.append(m.span())

    for m in _EN_SCALE_RE.finditer(text):
        scale = _EN_SCALE.get(m.group(2).lower())
        if scale is None:
            continue
        tokens.add(_canon_num(float(m.group(1)) * scale))
        consumed.append(m.span())

    for m in _THOUSANDS_RE.finditer(text):
        tokens.add(_canon_num(float(m.group(0).replace(",", ""))))
        consumed.append(m.span())

    for m in _PLAIN_NUM_RE.finditer(text):
        s, e = m.span()
        if any(a <= s and e <= b for a, b in consumed):
            continue
        tokens.add(_canon_num(m.group(0)))

    tokens |= set(_ACRONYM.findall(text))
    return tokens


def number_acronym_retention(output, source):
    """
    数字与英文缩写的保留率；输出里没有这两类内容时返回 None（无从判断）。
    数量级感知 —— 见模块头部说明。
    """
    out_facts = fact_tokens(output)
    if not out_facts:
        return None, 0, 0
    hit = out_facts & fact_tokens(source)
    return round(len(hit) / len(out_facts), 4), len(out_facts), len(hit)


def language_of(text):
    text = text or ""
    cjk = len(_CJK.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk == 0 and latin == 0:
        return "unknown"
    if cjk > latin:
        return "zh"
    if latin > cjk * 3:
        return "en"
    return "mixed"


def grounding_applicable(output, source):
    """
    grounding（字符二元组重合）只在**同语言**下有意义。

    实测教训：报告是中文、论文是英文时，二语言共享的汉字/字母二元组极少，
    于是 grounding 恒在 0.07 附近 —— 那是**噪声**，不是"没用原文词汇"的证据。
    曾据此差点得出错误结论，故在此显式判定"是否适用"。
    """
    lo, ls = language_of(output), language_of(source)
    if lo in ("zh", "en") and ls in ("zh", "en") and lo != ls:
        return False, lo, ls
    return True, lo, ls


def grounding_report(output, source):
    """
    给一个节点输出算质量指标（引擎直接调用）。

    `grounding`：同语言时是重合度，**跨语言时为 None**（不适用）。
    需要原始数值时读 `grounding_raw`，判断依据请用 `grounding_applicable`。
    """
    output, source = str(output or ""), str(source or "")
    retention, n_facts, n_hit = number_acronym_retention(output, source)
    applicable, out_lang, src_lang = grounding_applicable(output, source)
    raw = grounding_ratio(output, source)
    return {
        "grounding": raw if applicable else None,
        "grounding_raw": raw,
        "grounding_applicable": applicable,
        "output_language": out_lang,
        "source_language": src_lang,
        "key_fact_retention": retention,
        "key_facts": n_facts,
        "key_facts_matched": n_hit,
        "output_chars": len(output),
        "input_chars": len(source),
        "chars_per_1k_input": round(len(output) / max(len(source), 1) * 1000, 1),
    }
