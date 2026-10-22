#!/usr/bin/env bash
# ============================================================================
# 一键：用户语义理解 LoRA 训练（QLoRA，无人值守）
#
# 用法:
#   bash scripts/train_semantic.sh                 # 正式跑
#   bash scripts/train_semantic.sh --dry-run       # 只打印将执行的命令
#   LORA_NAME=xxx DATASET=yyy.jsonl bash scripts/train_semantic.sh
#
# 编排（按序，任一步失败即停并说明）:
#   1. 校验基座【不是】AWQ/GPTQ/FP8（train_lora.py 明确拒绝，早失败早好）
#   2. 确保数据集存在（没有就调用生成器）
#   3. 停 vLLM 释放显存（训练必须独占）
#   4. 调 train_lora.py（LLaMA-Factory QLoRA: quantization_bit=4 / bf16 / batch1×grad_accum8）
#   5. 日志落 training_logs/<lora>.log
#   6. 训练后自动重启 vLLM 并校验 200
# ============================================================================
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
PY="${PYBIN:-/home/luxy/.venv/bin/python}"
LORA_NAME="${LORA_NAME:-semantic_intent_v1}"
DATASET="${DATASET:-semantic_intent_v1.jsonl}"
BASE="${BASE:-/home/luxy/models/Qwen2.5-7B-Instruct}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

LOG_DIR="training_logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$LORA_NAME.log"
say() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

say "===== 语义理解训练开始 ====="
say "LoRA=$LORA_NAME · 数据集=$DATASET · 基座=$BASE · dry-run=$DRY"

# ---------- 1. 基座校验（最容易白跑一夜的一步 ✓）----------
if [ ! -d "$BASE" ]; then
  say "✗ 基座不存在: $BASE"; exit 1
fi
case "$BASE" in
  *AWQ*|*awq*|*GPTQ*|*gptq*)
    say "✗ 基座是量化模型（AWQ/GPTQ）—— train_lora.py:159-163 明确拒绝，无法训练 LoRA。"
    say "   请用全量 FP16 基座，例如 /home/luxy/models/Qwen2.5-7B-Instruct"
    exit 1;;
esac
if [ -f "$BASE/config.json" ]; then
  Q=$("$PY" -c "import json;print((json.load(open('$BASE/config.json')).get('quantization_config') or {}).get('quant_method',''))" 2>/dev/null || echo '')
  if [ -n "$Q" ]; then
    say "✗ 基座 config 里带量化配置（$Q）→ 同样不可训。请换 FP16 基座。"; exit 1
  fi
fi
say "✓ 基座校验通过（未量化，可训）"

# ---------- 2. 数据集 ----------
if [ ! -f "training_data/$DATASET" ]; then
  say "数据集不存在 → 调生成器"
  "$PY" scripts/gen_semantic_dataset.py | tee -a "$LOG" || { say "✗ 生成器失败"; exit 1; }
fi
N=$("$PY" -c "print(sum(1 for l in open('training_data/$DATASET',encoding='utf-8') if l.strip()))")
say "✓ 数据集: training_data/$DATASET（$N 条）"
if [ "$N" -lt 50 ]; then say "⚠ 样本偏少（$N），建议先扩充种子（data/user_voice/seeds.jsonl）"; fi

# ---------- 3. 停 vLLM ----------
VRAM_BEFORE=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null || echo '?')
say "停 vLLM 释放显存（停止前占用 ${VRAM_BEFORE} MiB）"
if [ "$DRY" = "0" ]; then
  curl -s -m 60 -X POST "http://127.0.0.1:8001/vllm/stop" >/dev/null 2>&1 || pkill -f 'vllm.entrypoints' || true
  for i in $(seq 1 30); do
    sleep 2
    [ "$(curl -s -o /dev/null -w '%{http_code}' -m 2 http://127.0.0.1:8000/v1/models 2>/dev/null)" = "200" ] || break
  done
  sleep 3
  say "停后显存占用: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null || echo '?') MiB"
fi

# ---------- 4/5. 训练 ----------
say "开始训练（QLoRA；日志: $LOG）"
# ★ P-68 修复（2026-10-19）: 改成 train_lora.py 的位置参数 ✓
#   原写 --lora_name ... 旗标 ⇒ train_lora.py 收 sys.argv[1..5] ⇒ 完全对不上 ✗
#   注：output_dir 以前漏传 ✗ 现在补上 ✓；--auto_restart_vllm 由本脚本第 6 步自己等 vLLM ✓
OUT_DIR="training_output/$LORA_NAME"
CMD=("$PY" train_lora.py "$BASE" "training_data/$DATASET" "$OUT_DIR" "$LORA_NAME" "$N")
say "命令: ${CMD[*]}"
if [ "$DRY" = "1" ]; then
  say "（dry-run，未执行）"; exit 0
fi
"${CMD[@]}" >> "$LOG" 2>&1
RC=$?
say "训练结束，退出码 $RC"
tail -12 "$LOG" | sed 's/^/    /'

# ---------- 6. 重启 vLLM（★ P-69 修复 2026-10-19）----------
# 原逻辑: 只"等"vLLM 回来 ✗ 但第 3 步已经把它停了 ✗ 没人重启它 ✗
#         ⇒ 训练完 vLLM 一直 DOWN ⇒ 平台不可用 ✗
# 修法: 先等 10 秒（训练脚本自己可能会重启 ✓）⇒ 没起来就【主动重启】✓
say "确认推理底座..."
_RESTARTED=0
for i in $(seq 1 2); do
  sleep 5
  [ "$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8000/v1/models 2>/dev/null)" = "200" ] && break
done
if [ "$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8000/v1/models 2>/dev/null)" != "200" ]; then
  say "vLLM 未自动拉起 ⇒ 主动重启（P-69 修复 ✓）"
  _INFER_MODEL=$(/home/luxy/.venv/bin/python -c "
import json,sys
try:
    d=json.load(open('.vllm.args.json',encoding='utf-8'))
    print(d.get('model_path') or '/home/luxy/models/Qwen2.5-7B-Instruct-AWQ')
except Exception:
    print('/home/luxy/models/Qwen2.5-7B-Instruct-AWQ')
" 2>/dev/null)
  # 优先走后端 API（会同时写 settings ✓）；后端不在就直接起 vLLM 进程 ✓
  curl -s -m 180 -X POST http://127.0.0.1:8001/vllm/start \
    -H 'Content-Type: application/json' \
    -d "{\"model_path\":\"$_INFER_MODEL\",\"port\":8000,\"gpu_memory_utilization\":0.55}" >/dev/null 2>&1 || true
  _RESTARTED=1
fi
# 最后等就绪（最多 3 分钟 ✓）
_OK=0
for i in $(seq 1 36); do
  sleep 5
  [ "$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8000/v1/models 2>/dev/null)" = "200" ] && {
    _OK=1; say "✓ vLLM 已就绪（$_INFER_MODEL）"; break; }
done
[ "$_OK" = "0" ] && say "✗ vLLM 3 分钟内未就绪 —— 请手动检查（P-69 建议：看 /tmp 下 vLLM 日志 ✓）"
say "产物目录: /home/luxy/loras/$LORA_NAME"
ls -la "/home/luxy/loras/$LORA_NAME" 2>/dev/null | head -8 | sed 's/^/    /'
say "===== 完成（明早读 $LOG + training_data/semantic_intent_v1_report.md）====="
exit $RC
