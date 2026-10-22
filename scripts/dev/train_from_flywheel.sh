#!/usr/bin/env bash
# 飞轮期 2: 从飞轮产出的训练数据训 LoRA（2026-10-21）
# ★ 改良自文献 PILoRA: **增量叠加**（不替换 ✗）⇒ 新 LoRA 共存 ✓ vLLM --max-loras 4 ✓
set -u
cd /home/luxy/paper_lora_project

DATA="${1:-}"
if [ -z "$DATA" ]; then
  DATA=$(ls -t data/flywheel/train_round*.jsonl 2>/dev/null | head -1)
fi
[ -n "$DATA" ] && [ -f "$DATA" ] || { echo "✗ 无训练数据（先跑 flywheel.py solidify --track train）"; exit 1; }

N=$(wc -l < "$DATA")
echo "  训练数据: $DATA（$N 条）"
[ "$N" -ge 10 ] || { echo "✗ 样本太少（$N < 10）"; exit 1; }

# ★ 设备从环境读（不硬编码 ✓）
GPU=$(/home/luxy/.venv/bin/python -c "
import json,sys
d=json.load(open('settings.json',encoding='utf-8'))
print(d.get('train_gpu', 1))
" 2>/dev/null || echo 1)
BASE=$(/home/luxy/.venv/bin/python -c "
import json,sys
d=json.load(open('settings.json',encoding='utf-8'))
print(d.get('base_model_path','/home/luxy/models/Qwen2.5-7B-Instruct-AWQ'))
" 2>/dev/null || echo '/home/luxy/models/Qwen2.5-7B-Instruct-AWQ')

TS=$(date +%Y%m%d_%H%M%S)
LORA="flywheel_${TS}"
echo "  GPU=$GPU  底座=$BASE  LoRA=$LORA"
echo "  ★ 增量叠加: 新 LoRA 保留 ✓ 不删除旧的 ✓"

CUDA_VISIBLE_DEVICES="$GPU" nohup /home/luxy/.venv/bin/python train_lora.py \
  --dataset "$DATA" \
  --base_model "$BASE" \
  --lora_name "$LORA" \
  > "/tmp/train_flywheel_${TS}.log" 2>&1 < /dev/null &

PID=$!
echo "  ✓ 训练已启动 PID=$PID"
echo "  日志: /tmp/train_flywheel_${TS}.log"
echo ""
echo "  ★ 训练完后（手工 ≥ 或 cron）:"
echo "    1. 启 vLLM 加载新 LoRA（--lora-modules $LORA=<loras/$LORA> ✓）"
echo "    2. 跑 A/B: node eval_v2_run.mjs --lora $LORA --tag fw_ab"
echo "    3. 判分: python scripts/eval_v2_score.py"
echo "    4. Bootstrap: python scripts/bootstrap_ci.py --runs ... --tag-a base --tag-b fw_ab"
echo "    5. 通过才 promote: python scripts/flywheel.py promote --version v$TS"
