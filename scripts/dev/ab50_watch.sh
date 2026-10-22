#!/usr/bin/env bash
set -u
cd /home/luxy/paper_lora_project
D=data/trajectories
echo "[$(date '+%F %T')] 守卫启动"
while true; do
  A=0
  while read -r p; do [ -n "${p:-}" ] && kill -0 "$p" 2>/dev/null && A=$((A+1)); done < $D/ab50_pids.txt
  echo "[$(date '+%F %T')] 存活 $A · ctrl $(wc -l < $D/arm_ctrl/trajectories.jsonl 2>/dev/null || echo 0) · learn $(wc -l < $D/arm_learn/trajectories.jsonl 2>/dev/null || echo 0)"
  [ "$A" = "0" ] && break
  sleep 120
done
echo "[$(date '+%F %T')] 全部结束 → 分析"
/home/luxy/.venv/bin/python scripts/ab50_analyze.py 2>&1
echo "[$(date '+%F %T')] 守卫完成"
