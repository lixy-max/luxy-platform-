#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""训练"工作流作者" LoRA（Qwen2.5-1.5B-Instruct）

流程：基座基线评估 → LoRA 训练 → 训练后评估（JSON 合法率/节点结构）
GPU: CUDA_VISIBLE_DEVICES=1（先停 7B 工蜂释放显存）
"""
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

ROOT = Path("/home/luxy/paper_lora_project")
BASE = Path("/home/luxy/models/Qwen2.5-1.5B-Instruct")
DATA_DIR = ROOT / "training_data" / "workflow_authoring"
OUT_DIR = Path("/home/luxy/loras/workflow_author_v1")
LOG = ROOT / "logs" / "workflow_author_train.log"
MAX_LEN = 1536
EPOCHS = 14

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_samples(path):
    rows = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        d = json.loads(ln)
        rows.append(d.get("conversations") or d.get("messages") or [])
    return rows


def build_inputs(tokenizer, convs, max_len=MAX_LEN):
    """构造 input_ids / labels（只在 assistant 部分算 loss）"""
    msgs = [{"role": m.get("role"), "content": m.get("content")} for m in convs
            if m.get("role") in ("user", "assistant", "system")]
    user_part = [m for m in msgs if m.get("role") == "user"]
    prompt = tokenizer.apply_chat_template(user_part, tokenize=False,
                                           add_generation_prompt=True)
    full = tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
    p_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    f_ids = tokenizer(full, add_special_tokens=False, truncation=True,
                      max_length=max_len)["input_ids"]
    labels = list(f_ids)
    for i in range(min(len(p_ids), len(labels))):
        labels[i] = -100
    return {"input_ids": f_ids, "labels": labels, "attention_mask": [1] * len(f_ids)}


def evaluate(model, tokenizer, samples, tag=""):
    """生成工作流 JSON 并评估合法率/结构"""
    import torch
    model.eval()
    results = []
    for convs in samples:
        task = next((m["content"] for m in convs if m.get("role") == "user"), "")
        # 与 v2 训练数据同构：system 提示 + 短任务
        _sys = ('你是工作流架构师。只输出一个 JSON 对象，不要解释。JSON 必须包含 workflow_name、description、'
                'nodes、golden_purchase_list。nodes 必须是 input→process/tool→aggregate 结构；'
                'tool 节点只可用 write_sheet/read_sheet/paper_lora_kb_query；write_sheet 的 tool_args 必须含 '
                'rows（二维数组）、header、name、sheet。节点必须有 id/type/input_from/position。')
        _task = task.split("任务：")[-1].split("\n")[0] if "任务：" in task else task
        msgs = [{"role": "system", "content": _sys}, {"role": "user", "content": _task}]
        prompt = tokenizer.apply_chat_template(msgs, tokenize=False,
                                               add_generation_prompt=True)
        ids = tokenizer(prompt, return_tensors="pt").to(model.device)
        t0 = time.time()
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=700, do_sample=False,
                                 temperature=None, top_p=None,
                                 pad_token_id=tokenizer.eos_token_id)
        text = tokenizer.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
        dur = round(time.time() - t0, 1)
        # 解析 JSON
        import re as _re
        t = text.strip()
        t = _re.sub(r"^```(?:json)?", "", t).strip()
        t = _re.sub(r"```$", "", t).strip()
        parsed, err = None, ""
        start = t.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(t)):
                if t[i] == "{":
                    depth += 1
                elif t[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(t[start:i + 1])
                        except Exception as e:
                            err = str(e)[:120]
                        break
        nodes = (parsed or {}).get("nodes") or []
        tools = [n.get("tool") for n in nodes if n.get("type") == "tool"]
        ok_json = bool(parsed and isinstance(parsed, dict))
        ok_struct = bool(ok_json and nodes and nodes[0].get("type") == "input"
                         and nodes[-1].get("type") == "aggregate")
        results.append({"tag": tag, "task": task[:80], "json_ok": ok_json,
                        "structure_ok": ok_struct, "nodes": len(nodes),
                        "tools": tools[:5], "duration": dur,
                        "output_head": text[:300], "err": err})
        log("  [%s] json_ok=%s struct_ok=%s nodes=%d tools=%s %.1fs"
            % (tag, ok_json, ok_struct, len(nodes), tools[:3], dur))
    return results


def main():
    import torch
    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                              TrainingArguments, DataCollatorForSeq2Seq)
    from peft import LoraConfig, get_peft_model

    log("=== 加载基座 %s（GPU1）===" % BASE)
    tok = AutoTokenizer.from_pretrained(str(BASE), trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        str(BASE), torch_dtype=torch.float16, trust_remote_code=True,
        device_map={"": 0})
    model.config.use_cache = False
    log("基座加载完成: %.2f GB" % (sum(p.numel() for p in model.parameters()) / 1e9))

    train_convs = load_samples(DATA_DIR / "workflow_author_v2_train.jsonl")
    eval_convs = load_samples(DATA_DIR / "workflow_author_v2_eval.jsonl")
    log("样本: train=%d eval=%d" % (len(train_convs), len(eval_convs)))

    log("=== ① 训练前基线评估 ===")
    base_results = evaluate(model, tok, eval_convs, tag="base")

    log("=== ② LoRA 训练（r=16, epochs=%d）===" % EPOCHS)
    lora_cfg = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
                         target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                                         "gate_proj", "up_proj", "down_proj"],
                         task_type="CAUSAL_LM")
    model = get_peft_model(model, lora_cfg)
    try:
        model.enable_input_require_grads()
    except Exception as _e:
        log("enable_input_require_grads 跳过: %s" % _e)
    model.print_trainable_parameters()

    train_ds = [build_inputs(tok, c) for c in train_convs]
    collator = DataCollatorForSeq2Seq(tok, model=None, padding=True,
                                      label_pad_token_id=-100)
    args = TrainingArguments(
        output_dir=str(OUT_DIR / "_trainer"),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        num_train_epochs=EPOCHS,
        learning_rate=2e-4,
        logging_steps=2,
        save_strategy="no",
        fp16=True,
        report_to=[],
        remove_unused_columns=False,
        gradient_checkpointing=False,
        optim="adamw_torch",
        warmup_ratio=0.05,
        lr_scheduler_type="cosine",
    )
    trainer = Trainer(model=model, args=args, train_dataset=train_ds,
                      data_collator=collator)
    t0 = time.time()
    res = trainer.train()
    dur = round(time.time() - t0, 1)
    log("训练完成: %.1fs | loss=%.4f | steps=%s"
        % (dur, res.training_loss, getattr(res, "global_step", "?")))

    log("=== ③ 保存 adapter → %s ===" % OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(OUT_DIR))
    tok.save_pretrained(str(OUT_DIR))
    log("adapter 已保存")

    log("=== ④ 训练后评估 ===")
    model.config.use_cache = True
    after_results = evaluate(model, tok, eval_convs, tag="lora")

    def rate(rows, key):
        return round(sum(1 for r in rows if r.get(key)) / max(1, len(rows)), 3)

    metrics = {
        "base_model": str(BASE),
        "adapter": str(OUT_DIR),
        "train_samples": len(train_convs),
        "eval_samples": len(eval_convs),
        "epochs": EPOCHS,
        "train_seconds": dur,
        "train_loss": float(res.training_loss),
        "base": {"json_ok_rate": rate(base_results, "json_ok"),
                 "structure_ok_rate": rate(base_results, "structure_ok")},
        "lora": {"json_ok_rate": rate(after_results, "json_ok"),
                 "structure_ok_rate": rate(after_results, "structure_ok")},
        "base_results": base_results,
        "lora_results": after_results,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (ROOT / "data" / "workflow_author_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    log("=== 指标 ===\n" + json.dumps({k: metrics[k] for k in
        ("base", "lora", "train_loss", "train_seconds")}, ensure_ascii=False))
    log("DONE")


if __name__ == "__main__":
    main()
