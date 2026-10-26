#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地模型"工作流作者"评估：base vs LoRA（JSON 合法率 + workflow lint 通过率）"""
import json
import os
import sys
import time

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
ROOT = "/home/luxy/paper_lora_project"
sys.path.insert(0, ROOT)
BASE = "/home/luxy/models/Qwen2.5-1.5B-Instruct"
ADAPTER = "/home/luxy/loras/workflow_author_v1"
DATA_DIR = ROOT + "/training_data/workflow_authoring"
OUT = ROOT + "/data/workflow_author_eval.json"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROMPTS = [
    "做一个自动识别用户意图生成采购清单 Excel 的本地工作流：来源=本地 Excel+京东/淘宝/阿里巴巴；输出=采购清单表格",
    "把每周零散工作记录汇总成周报 Excel（任务/完成度/风险）",
    "读取盘点 Excel，计算库存差异并输出差异表 Excel",
    "根据培训名单生成签到表 Excel（姓名/部门/签到/备注）",
]


def extract_json(text):
    import re
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


def generate(model, tok, prompt, max_new_tokens=1400):
    import torch
    _sys = ('你是工作流架构师。只输出一个 JSON 对象，不要解释。JSON 必须包含 workflow_name、description、nodes、golden_purchase_list。nodes 必须是 input→process/tool→aggregate 结构；tool 节点只可用 write_sheet/read_sheet/paper_lora_kb_query；write_sheet 的 tool_args 必须含 rows（二维数组）、header、name、sheet。节点必须有 id/type/input_from/position。')
    msgs = [{"role": "system", "content": _sys}, {"role": "user", "content": prompt}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    ids = tok(text, return_tensors="pt").to(model.device)
    t0 = time.time()
    with torch.no_grad():
        out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False,
                             pad_token_id=tok.eos_token_id)
    gen = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
    return gen, round(time.time() - t0, 1)


def evaluate(model, tok, tag):
    import workflow_engine as we
    rows = []
    for p in PROMPTS:
        gen, dur = generate(model, tok, p)
        d = extract_json(gen)
        lint_ok = False
        lint_errs = []
        if isinstance(d, dict) and (d.get("nodes") or d.get("workflow")):
            cfg = d if "nodes" in d else d.get("workflow")
            try:
                errs, warns = we.lint_config(cfg)
                lint_ok = not errs
                lint_errs = errs[:3]
            except Exception as e:
                lint_errs = [str(e)[:120]]
        rows.append({"tag": tag, "prompt": p[:80], "json_ok": bool(d),
                     "lint_ok": lint_ok, "lint_errors": lint_errs,
                     "duration": dur, "output_head": gen[:400]})
        print("[%s] json_ok=%s lint_ok=%s %.1fs | %s"
              % (tag, bool(d), lint_ok, dur, p[:40]), flush=True)
    return rows


def main():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    tok = AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    print("加载基座…", flush=True)
    model = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.float16, trust_remote_code=True, device_map={"": 0})
    model.eval()
    base_rows = evaluate(model, tok, "base")

    print("加载 LoRA adapter…", flush=True)
    model = PeftModel.from_pretrained(model, ADAPTER)
    model.eval()
    lora_rows = evaluate(model, tok, "lora")

    def rate(rows, k):
        return round(sum(1 for r in rows if r[k]) / max(1, len(rows)), 3)

    out = {
        "base_model": BASE, "adapter": ADAPTER, "prompts": len(PROMPTS),
        "base": {"json_ok_rate": rate(base_rows, "json_ok"),
                 "lint_ok_rate": rate(base_rows, "lint_ok")},
        "lora": {"json_ok_rate": rate(lora_rows, "json_ok"),
                 "lint_ok_rate": rate(lora_rows, "lint_ok")},
        "base_rows": base_rows, "lora_rows": lora_rows,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    open(OUT, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))
    print("\n=== 汇总 ===")
    print(json.dumps({"base": out["base"], "lora": out["lora"]}, ensure_ascii=False, indent=1))
    print("→", OUT)


if __name__ == "__main__":
    main()
