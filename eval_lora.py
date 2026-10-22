#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LoRA 评测：底座 vs LoRA 的配对对比。

回答一个训练后必须回答的问题：**这个 LoRA 到底比底座好吗？**

做法：
  1. 读一份 **未参与训练** 的评测集（sharegpt，与训练数据同格式）
  2. 同一批样本，同一条 prompt，分别用「底座」和「LoRA」各生成一次
     （vLLM 同时服务底座与 LoRA，无需换模型）
  3. 逐条打分，并给出配对统计（改善/变差/持平）

⚠️ 最重要的一条：**数据泄漏检查**。
   如果评测集就是训练集（或高度重叠），LoRA 只是在背诵，分数会虚高到毫无意义。
   本脚本会明确检测并在报告里标记 leakage_warning。

评测指标（不依赖任何额外模型）：
  参考是 JSON 时：json_valid / field_coverage / non_empty_ratio / similarity
  参考是文本时：similarity（字符二元组 Jaccard）、length_ratio、是否保留编号结构

用法：
  python eval_lora.py --lora test_agent_v1 --dataset training_data/eval_test_agent_v1.jsonl
  python eval_lora.py --lora test_agent_v1 --dataset xxx.jsonl --limit 20 --workers 4
  python eval_lora.py --lora xxx --dataset xxx.jsonl --no-base        # 只测 LoRA
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "outputs"
TRAINING_DATA_DIR = PROJECT_DIR / "training_data"

VLLM_CHAT_API = os.environ.get("VLLM_CHAT_API", "http://127.0.0.1:8000/v1/chat/completions")
LORA_MANAGER_API = os.environ.get("LORA_MANAGER_API", "http://127.0.0.1:8001")
DEFAULT_BASE_MODEL = os.environ.get("BASE_MODEL_PATH", "")

TIE_THRESHOLD = 0.02          # |delta| 小于此值视为持平


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ============ 文本与评分（纯函数，可单测） ============
def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def char_bigrams(text):
    text = normalize(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def similarity(a, b):
    """字符二元组 Jaccard，0~1。中英文都适用，且不需要额外模型。"""
    sa, sb = char_bigrams(a), char_bigrams(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def parse_json_object(text):
    """尽力从模型输出里取出一个 JSON 对象；失败返回 None。"""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", str(text)).replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(cleaned[start:end + 1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def score_output(text, reference):
    """给单条输出打分。返回的 dict 会被存进报告，便于逐条复核。"""
    text = text or ""
    ref = reference or ""
    out = {
        "chars": len(text),
        "length_ratio": round(len(text) / max(len(ref), 1), 3),
        "empty": not text.strip(),
    }
    ref_obj = parse_json_object(ref)
    out_obj = parse_json_object(text)

    if ref_obj is not None:
        out["kind"] = "json"
        keys = list(ref_obj.keys())
        out["keys_expected"] = len(keys)
        out["json_valid"] = out_obj is not None
        if out_obj is not None:
            found = [k for k in keys if k in out_obj]
            nonempty = [k for k in found if str(out_obj.get(k) or "").strip()]
            out["field_coverage"] = round(len(found) / max(len(keys), 1), 4)
            out["non_empty_ratio"] = round(len(nonempty) / max(len(keys), 1), 4)
            out["similarity"] = round(
                sum(similarity(str(ref_obj.get(k) or ""), str(out_obj.get(k) or ""))
                    for k in keys) / max(len(keys), 1), 4)
            out["extra_keys"] = [k for k in out_obj if k not in keys]
        else:
            out["field_coverage"] = 0.0
            out["non_empty_ratio"] = 0.0
            out["similarity"] = round(similarity(ref, text), 4)
    else:
        out["kind"] = "text"
        out["json_valid"] = None
        out["similarity"] = round(similarity(ref, text), 4)
        # 编号列表检测：允许行内写法（"1. …；2. …"），但不把 "3.5" 这类小数当列表
        out["numbered_items"] = len(re.findall(r"[1-9][.、)）](?:\s|[\u4e00-\u9fff])", text))
        out["has_numbered_list"] = out["numbered_items"] >= 2
    return out


def composite_score(metrics):
    """用于配对胜负的单一分数：空输出直接 0，否则用 similarity。"""
    if not metrics or metrics.get("empty"):
        return 0.0
    return float(metrics.get("similarity") or 0.0)


# ============ 评测集加载与泄漏检查 ============
def load_eval_samples(path, limit=None):
    """读 sharegpt 评测集，返回 [(system, human, reference)]，并统计跳过的行。"""
    samples, skipped = [], 0
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                skipped += 1
                continue
            convs = item.get("conversations") or item.get("messages") or []
            system = human = reference = None
            for turn in convs:
                role = (turn.get("from") or turn.get("role") or "").lower()
                value = turn.get("value") or turn.get("content") or ""
                if role == "system":
                    system = value
                elif role in ("human", "user"):
                    human = value
                elif role in ("gpt", "assistant"):
                    reference = value
            if human and reference:
                samples.append((system or "", human, reference))
            else:
                skipped += 1
    if limit:
        samples = samples[:limit]
    return samples, skipped


def _fingerprint(system, human, reference):
    payload = f"{normalize(system)}|{normalize(human)}|{normalize(reference)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def leakage_check(dataset_path, lora_name, samples):
    """
    判断评测集是否就是（或大量重叠于）该 LoRA 的训练集。
    返回 (warning: bool, detail: str)
    """
    ds = Path(dataset_path).resolve()
    train = (TRAINING_DATA_DIR / f"{lora_name}.jsonl").resolve()
    if ds == train:
        return True, f"评测集与该 LoRA 的训练集是同一个文件：{train}"

    if not train.exists():
        return False, f"未找到训练集 {train.name}，无法比对（评测集: {ds.name}）"

    try:
        train_fps = set()
        with open(train, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                convs = item.get("conversations") or []
                system = human = reference = ""
                for turn in convs:
                    role = (turn.get("from") or "").lower()
                    value = turn.get("value") or ""
                    if role == "system":
                        system = value
                    elif role == "human":
                        human = value
                    elif role == "gpt":
                        reference = value
                if human:
                    train_fps.add(_fingerprint(system, human, reference))
    except Exception as e:
        return False, f"读取训练集失败（跳过比对）: {e}"

    hits = sum(1 for s in samples if _fingerprint(*s) in train_fps)
    ratio = hits / max(len(samples), 1)
    if ratio >= 0.5:
        return True, (f"{hits}/{len(samples)} 条评测样本（{ratio:.0%}）出现在训练集 "
                      f"{train.name} 中 —— LoRA 很可能只是记住了这些样本")
    if hits:
        return False, f"{hits}/{len(samples)} 条与训练集重叠（低于 50%，可接受但要留意）"
    return False, f"与训练集 {train.name} 无重叠"


def training_references(lora_name):
    """读该 LoRA 训练集里的所有参考输出，用于"模板重叠"检测。"""
    train = TRAINING_DATA_DIR / f"{lora_name}.jsonl"
    refs = []
    if not train.exists():
        return refs
    try:
        with open(train, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    convs = (json.loads(line).get("conversations") or [])
                except Exception:
                    continue
                for turn in convs:
                    if (turn.get("from") or "").lower() == "gpt" and turn.get("value"):
                        refs.append(turn["value"])
    except Exception:
        pass
    return refs


def template_overlap_ratio(samples, train_refs, threshold=0.85):
    """
    评测集的参考文本里，有多少条在训练集中存在"近乎一模一样"的版本。

    这不是严格意义的泄漏（输入不同），但意味着高分可能来自**复述模板**
    而非泛化能力 —— 对合成训练数据来说这是很常见的陷阱。
    返回 (ratio, hits)。
    """
    if not train_refs:
        return 0.0, 0
    hits = 0
    for _, _, ref in samples:
        if not ref:
            continue
        if any(similarity(ref, t) >= threshold for t in train_refs):
            hits += 1
    return hits / max(len(samples), 1), hits


def input_overlap_check(dataset_path, samples, probe=200):
    """
    论文级输入重叠检测：评测集的**输入**是否与训练集的输入来自同一篇论文。

    为什么必须单独查：指纹检查只比对 (system, human, reference) 整体，
    两次构建用了不同窗口大小时指纹不会相同，于是同一篇论文同时出现在训练集与
    评测集里也**检测不出来** —— 而这会让评测分数虚高。
    做法：从评测输入里取几段 200 字符的探针，看是否出现在任何训练输入中。
    返回 (warning, ratio, detail)
    """
    train = TRAINING_DATA_DIR / f"{Path(dataset_path).stem.split('_eval')[0]}.jsonl"
    if not train.exists():
        # 退化：按 LoRA 名找（调用方已用 leakage_check 报过缺失）
        return False, 0.0, "未找到训练集，跳过输入重叠检查"

    train_texts = []
    try:
        with open(train, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    convs = json.loads(line).get("conversations") or []
                except Exception:
                    continue
                for turn in convs:
                    if (turn.get("from") or "").lower() == "human" and turn.get("value"):
                        train_texts.append(turn["value"])
    except Exception as e:
        return False, 0.0, f"读取训练集失败: {e}"
    if not train_texts:
        return False, 0.0, "训练集为空，跳过输入重叠检查"

    blob = "\n<<<SPLIT>>>\n".join(train_texts)
    hits = 0
    for _, human, _ in samples:
        text = human or ""
        if len(text) < probe + 50:
            continue
        probes = [text[50:50 + probe],
                  text[len(text) // 2: len(text) // 2 + probe],
                  text[-probe - 20:-20]]
        if any(p and p in blob for p in probes):
            hits += 1
    ratio = hits / max(len(samples), 1)
    # 200 字符逐字命中已是强证据（不同论文不可能有相同的 200 字连续片段），
    # 因此阈值取低一些；真实命中主要来自"同一篇论文被同时用于训练与评测"。
    warn = ratio >= 0.1
    detail = (f"{hits}/{len(samples)} 条评测输入与训练输入来自同一批文本"
              f"（{ratio:.0%}）")
    return warn, ratio, detail


# ============ 模型调用 ============
def get_served_models():
    api_base = VLLM_CHAT_API.rsplit("/v1/", 1)[0]
    r = requests.get(f"{api_base}/v1/models", timeout=5)
    r.raise_for_status()
    return r.json().get("data", [])


def resolve_base_model(explicit=None):
    if explicit:
        return explicit
    for m in get_served_models():
        if str(m.get("id", "")).startswith("/"):
            return m["id"]
    return DEFAULT_BASE_MODEL or None


def ensure_lora_loaded(lora_name):
    try:
        r = requests.get(f"{LORA_MANAGER_API}/loras/loaded", timeout=5)
        loaded = (r.json() or {}).get("loaded", [])
        if lora_name in loaded:
            return True, "已在显存"
    except Exception:
        pass
    try:
        r = requests.post(f"{LORA_MANAGER_API}/loras/load",
                          json={"lora_name": lora_name}, timeout=120)
        if r.status_code == 200:
            return True, "已加载"
        return False, f"加载失败 {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"加载异常: {e}"


def call_model(model, system, human, temperature=0.0, max_tokens=1024, timeout=300):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": human})
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": {"enable_thinking": False},
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    t0 = time.time()
    r = requests.post(VLLM_CHAT_API, json=payload, timeout=timeout)
    if r.status_code != 200:
        payload.pop("chat_template_kwargs", None)
        payload.pop("extra_body", None)
        r = requests.post(VLLM_CHAT_API, json=payload, timeout=timeout)
    elapsed = time.time() - t0
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    return data["choices"][0]["message"]["content"], elapsed


def aggregate(per_model):
    """per_model: {"base": [metrics...], "lora": [metrics...]} -> 汇总"""
    def mean_of(rows, key):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 4) if vals else None

    out = {}
    for name, rows in per_model.items():
        out[name] = {
            "n": len(rows),
            "json_valid_ratio": (
                round(sum(1 for r in rows if r.get("json_valid")) /
                      max(sum(1 for r in rows if r.get("json_valid") is not None), 1), 4)
                if any(r.get("json_valid") is not None for r in rows) else None),
            "field_coverage": mean_of(rows, "field_coverage"),
            "non_empty_ratio": mean_of(rows, "non_empty_ratio"),
            "similarity": mean_of(rows, "similarity"),
            "avg_chars": mean_of(rows, "chars"),
            "empty_count": sum(1 for r in rows if r.get("empty")),
        }
    return out


def paired_stats(base_rows, lora_rows):
    """配对比较：改善/变差/持平 + 平均 delta。"""
    improved = worse = tie = 0
    deltas = []
    for b, l in zip(base_rows, lora_rows):
        sb, sl = composite_score(b), composite_score(l)
        d = sl - sb
        deltas.append(d)
        if abs(d) < TIE_THRESHOLD:
            tie += 1
        elif d > 0:
            improved += 1
        else:
            worse += 1
    return {
        "improved": improved,
        "worse": worse,
        "tie": tie,
        "mean_delta": round(sum(deltas) / len(deltas), 4) if deltas else None,
    }


# ============ 主流程 ============
def run_eval(lora_name, dataset_path, limit=None, base_model=None,
             temperature=0.0, max_tokens=1024, workers=2, with_base=True,
             output_file=None):
    if not os.path.exists(dataset_path):
        raise SystemExit(f"评测集不存在: {dataset_path}")

    samples, skipped = load_eval_samples(dataset_path, limit)
    if not samples:
        raise SystemExit("评测集里没有可用样本（需要 human + gpt 两轮）")

    log(f"评测集: {dataset_path}")
    log(f"样本数: {len(samples)}（跳过 {skipped} 行）")

    leak_warn, leak_detail = leakage_check(dataset_path, lora_name, samples)
    log(("⚠️ " if leak_warn else "✓ ") + "数据泄漏检查: " + leak_detail)
    if leak_warn:
        log("⚠️ 分数将严重虚高：LoRA 背诵了评测样本，无法反映泛化能力。")
        log("   建议：用 data_generator.py 另生成一份不参与训练的评测集。")

    # 模板重叠：合成训练数据的常见陷阱（输入不同、参考却是同一套模板）
    train_refs = training_references(lora_name)
    overlap_ratio, overlap_hits = template_overlap_ratio(samples, train_refs)
    overlap_warn = overlap_ratio >= 0.5
    if train_refs:
        log(("⚠️ " if overlap_warn else "✓ ") +
            f"模板重叠检查: {overlap_hits}/{len(samples)} 条评测参考在训练集里存在"
            f"≥85% 相似的版本（训练集参考共 {len(train_refs)} 条）")
        if overlap_warn:
            log("⚠️ 参考输出高度模板化：高分可能来自复述模板，而非真正的抽取能力。")
            log("   建议：配合真实论文 + 人工抽检，或改用更难的评测集。")

    # 论文级输入重叠：同一篇论文同时进了训练集与评测集（比目标指纹检查更严）
    in_warn, in_ratio, in_detail = input_overlap_check(dataset_path, samples)
    log(("⚠️ " if in_warn else "✓ ") + "输入重叠检查: " + in_detail)
    if in_warn:
        log("⚠️ 评测输入与训练输入同源：分数不能用于判断泛化能力。")
        log("   建议：构建评测集时用 --holdout 排除训练所用论文。")

    ok, msg = ensure_lora_loaded(lora_name)
    log(f"LoRA '{lora_name}': {msg}")
    if not ok:
        raise SystemExit(f"LoRA 加载失败，无法评测: {msg}")

    resolved_base = resolve_base_model(base_model) if with_base else None
    if with_base and not resolved_base:
        log("⚠️ 拿不到底座名，跳过底座对比（只测 LoRA）")
        with_base = False
    if with_base:
        log(f"底座: {resolved_base}")

    models = [("lora", lora_name)] + ([("base", resolved_base)] if with_base else [])
    results = {name: [None] * len(samples) for name, _ in models}
    latencies = {name: [] for name, _ in models}

    def work(idx_sample):
        idx, (system, human, reference) = idx_sample
        row = {"idx": idx}
        for name, model in models:
            try:
                text, el = call_model(model, system, human,
                                      temperature=temperature,
                                      max_tokens=max_tokens)
                metrics = score_output(text, reference)
                metrics["latency_sec"] = round(el, 2)
                metrics["output_head"] = normalize(text)[:300]
            except Exception as e:
                metrics = {"error": str(e), "empty": True, "similarity": 0.0,
                           "chars": 0, "latency_sec": None}
            row[name] = metrics
        return row

    t0 = time.time()
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for row in pool.map(work, list(enumerate(samples, 1))):
            done += 1
            for name, _ in models:
                results[name][row["idx"] - 1] = row[name]
                if row[name].get("latency_sec"):
                    latencies[name].append(row[name]["latency_sec"])
            if done % 5 == 0 or done == len(samples):
                log(f"  进度 {done}/{len(samples)}")
    elapsed = round(time.time() - t0, 1)

    per_model = {name: results[name] for name, _ in models}
    agg = aggregate(per_model)
    summary = {
        "lora_name": lora_name,
        "base_model": resolved_base,
        "dataset": str(Path(dataset_path).resolve()),
        "dataset_sha256": hashlib.sha256(
            Path(dataset_path).read_bytes()).hexdigest()[:16],
        "leakage_warning": leak_warn,
        "leakage_detail": leak_detail,
        "template_overlap_warning": overlap_warn,
        "template_overlap_hits": overlap_hits,
        "template_overlap_ratio": round(overlap_ratio, 4),
        "input_overlap_warning": in_warn,
        "input_overlap_ratio": round(in_ratio, 4),
        "input_overlap_detail": in_detail,
        "sampling": {"temperature": temperature, "max_tokens": max_tokens,
                     "limit": limit, "workers": workers, "with_base": with_base},
        "elapsed_sec": elapsed,
        "finished_at": datetime.now().isoformat(),
        "aggregate": agg,
        "pairwise": paired_stats(results["base"], results["lora"]) if with_base else None,
        "avg_latency_sec": {k: (round(sum(v) / len(v), 2) if v else None)
                            for k, v in latencies.items()},
        "samples": [
            {"idx": i + 1,
             "reference_head": normalize(samples[i][2])[:300],
             **{name: results[name][i] for name, _ in models}}
            for i in range(len(samples))
        ],
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not output_file:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = str(OUTPUT_DIR / f"eval_{lora_name}_{ts}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ---- 打印结论 ----
    log("=" * 62)
    if leak_warn:
        log("⚠️ 本次结果不可用于判断泛化能力（评测集与训练集重叠）")
    if overlap_warn:
        log("⚠️ 评测参考高度模板化（见上方模板重叠检查），请结合人工抽检解读")
    if in_warn:
        log("⚠️ 评测输入与训练输入同源（见上方输入重叠检查），分数不代表泛化能力")
    for name in (["base"] if with_base else []) + ["lora"]:
        a = agg[name]
        log(f"{name:5s} | n={a['n']:3d} | similarity={a['similarity']} "
            f"| 覆盖率={a['field_coverage']} | 非空率={a['non_empty_ratio']} "
            f"| 平均字数={a['avg_chars']} | 空输出={a['empty_count']}")
    if with_base:
        p = summary["pairwise"]
        log(f"配对比较: 改善 {p['improved']} / 变差 {p['worse']} / 持平 {p['tie']} "
            f"| 平均 Δsimilarity = {p['mean_delta']}")
        log(f"平均耗时: 底座 {summary['avg_latency_sec']['base']}s / "
            f"LoRA {summary['avg_latency_sec']['lora']}s")
    log(f"总耗时 {elapsed}s，报告已保存: {output_file}")
    log("=" * 62)
    return output_file, summary


def main():
    p = argparse.ArgumentParser(description="LoRA 评测：底座 vs LoRA 配对对比")
    p.add_argument("--lora", required=True, help="LoRA 名称（需已存在于 ~/loras）")
    p.add_argument("--dataset", required=True, help="评测集 jsonl（sharegpt，建议与训练集分离）")
    p.add_argument("--limit", type=int, default=20, help="最多评测多少条（默认 20）")
    p.add_argument("--base-model", default=None, help="底座模型（默认取 vLLM 当前服务的）")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--no-base", action="store_true", help="只测 LoRA，不做底座对比")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    run_eval(args.lora, args.dataset, limit=args.limit, base_model=args.base_model,
             temperature=args.temperature, max_tokens=args.max_tokens,
             workers=args.workers, with_base=not args.no_base,
             output_file=args.output)


if __name__ == "__main__":
    main()
