#!/usr/bin/env bash
# 飞轮周任务（MAPE ✓ 2026-10-21）
# 用法: 加到 crontab
#   0 3 * * 0  /home/luxy/paper_lora_project/scripts/flywheel_cron.sh >> /tmp/flywheel_cron.log 2>&1
set -u
cd /home/luxy/paper_lora_project
PY=/home/luxy/.venv/bin/python
LOG="/tmp/flywheel_cron_$(date +%Y%m%d).log"
{
  echo "==================== 飞轮周任务 $(date) ===================="
  echo "--- ① Monitor 记录指标 ---"
  $PY scripts/flywheel.py monitor
  echo ""
  echo "--- ② 采集 ---"
  $PY scripts/flywheel.py collect --days 7
  echo ""
  echo "--- ③ 蒸馏（LLM 模式 ✓ 要 vLLM ✓ 挂了就跳过 ⚠）---"
  if curl -sf -o /dev/null -m 5 http://127.0.0.1:8000/v1/models 2>/dev/null; then
    $PY scripts/flywheel.py distill --limit 30 --use-llm
  else
    echo "  ⚠ vLLM 未在线 ⇒ 跳过 LLM 蒸馏（不降级到规则模式 ✗ 质量太低 ✗）"
  fi
  echo ""
  echo "--- ④ 固化（三条通道 ✓ 都不占 GPU ✓）---"
  $PY scripts/flywheel.py solidify --track routing
  $PY scripts/flywheel.py solidify --track prompt
  echo ""
  echo "--- ⑤ 退化检测 ✓ ---"
  $PY scripts/flywheel.py regress
  echo ""
  echo "==================== 完成 $(date) ===================="
} 2>&1 | tee -a "$LOG"
