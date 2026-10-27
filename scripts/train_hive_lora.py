#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 LoRA 训练脚本（S21 自动训练调用；device 由 CUDA_VISIBLE_DEVICES 指定）"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                          DataCollatorForSeq2Seq, Trainer, TrainingArguments)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def load_samples(path):
    rows = []
    for ln in Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        d = json.loads(ln)
        conv = d.get("conversations") or d.get("messages") or []
        if conv:
            rows.append(conv)
    return rows


def build_inputs(tok, convs, max_len):
    msgs = [{"role": m.get("role"), "content": m.get("content")} for m in convs
            if m.get("role") in ("system", "user", "assistant")]
    user_part = [m for m in msgs if m.get("role") in ("system", "user")]
    prompt = tok.apply_chat_template(user_part, tokenize=False, add_generation_prompt=True)
    full = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=False)
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    f_ids = tok(full, add_special_tokens=False, truncation=True,
                max_length=max_len)["input_ids"]
    labels = list(f_ids)
    for i in range(min(len(p_ids), len(labels))):
        labels[i] = -100
    return {"input_ids": f_ids, "labels": labels, "attention_mask": [1] * len(f_ids)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--base-model", default="/home/luxy/models/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--output", required=True)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--max-len", type=int, default=1024)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--qlora", action="store_true",
                    help="4-bit QLoRA（bitsandbytes nf4；7B 在 12GB 卡上训练用）")
    ap.add_argument("--max-steps", type=int, default=-1,
                    help=">0 时覆盖 epochs（用于小步数验证）")
    args = ap.parse_args()

    t0 = time.time()
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    log("device=%s (CUDA_VISIBLE_DEVICES=%s) base=%s" % (
        device, os.environ.get("CUDA_VISIBLE_DEVICES"), args.base_model))
    tok = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    if args.qlora and torch.cuda.is_available():
        log("QLoRA 4-bit 加载 base=%s" % args.base_model)
        bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                 bnb_4bit_compute_dtype=torch.float16,
                                 bnb_4bit_use_double_quant=True)
        model = AutoModelForCausalLM.from_pretrained(
            args.base_model, quantization_config=bnb, torch_dtype=torch.float16,
            trust_remote_code=True, device_map={"": 0})
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            args.base_model, torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            trust_remote_code=True, device_map={"": 0} if torch.cuda.is_available() else None)
    model.config.use_cache = False
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        task_type="CAUSAL_LM"))
    try:
        model.enable_input_require_grads()
    except Exception:
        pass
    samples = load_samples(args.dataset)
    log("样本: %d" % len(samples))
    if not samples:
        raise SystemExit("数据集为空")
    ds = Dataset.from_list([build_inputs(tok, c, args.max_len) for c in samples])
    collator = DataCollatorForSeq2Seq(tok, model=None, padding=True, label_pad_token_id=-100)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(out / "_trainer"),
            per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=args.grad_accum,
            num_train_epochs=args.epochs,
            max_steps=(args.max_steps if args.max_steps and args.max_steps > 0 else -1),
            learning_rate=args.lr,
            logging_steps=1, save_strategy="no", fp16=torch.cuda.is_available(),
            report_to=[], remove_unused_columns=False,
            gradient_checkpointing=bool(args.qlora),
            optim=("paged_adamw_8bit" if args.qlora else "adamw_torch"),
            warmup_ratio=0.05, lr_scheduler_type="cosine"),
        train_dataset=ds, data_collator=collator)
    res = trainer.train()
    model.save_pretrained(str(out))
    tok.save_pretrained(str(out))
    metrics = {"base_model": args.base_model, "dataset": args.dataset,
               "samples": len(samples), "epochs": args.epochs,
               "train_loss": float(res.training_loss),
               "global_step": getattr(res, "global_step", None),
               "duration_s": round(time.time() - t0, 1),
               "qlora": bool(args.qlora), "max_steps": args.max_steps,
            "vram_peak_mb": (torch.cuda.max_memory_allocated() / 2**20
                             if torch.cuda.is_available() else 0),
            "exp_name": os.environ.get("EXP_NAME", ""),
               "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "train_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    log("训练完成 loss=%.4f step=%s → %s" % (metrics["train_loss"],
                                             metrics["global_step"], out))


if __name__ == "__main__":
    main()
