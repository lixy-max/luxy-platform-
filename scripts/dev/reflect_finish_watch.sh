#!/usr/bin/env bash
# 守卫：等 4 个 worker 全部结束 → 自动蒸馏 → 自动入库到 agent_use。
# 用法（已由外层 setsid nohup 启动，无需手动跑）：
#   setsid nohup bash scripts/reflect_finish_watch.sh > data/trajectories/finish.log 2>&1 &
set -u
cd /home/luxy/paper_lora_project
PY=/home/luxy/.venv/bin/python
D=data/trajectories

echo "[$(date '+%F %T')] 守卫启动，等待 worker 结束…"
while true; do
  ALIVE=0
  if [ -f $D/batch_pids.txt ]; then
    while read -r p; do [ -n "$p" ] && kill -0 "$p" 2>/dev/null && ALIVE=$((ALIVE+1)); done < $D/batch_pids.txt
  fi
  N=$(wc -l < $D/trajectories.jsonl 2>/dev/null || echo 0)
  echo "[$(date '+%F %T')] 存活 worker=$ALIVE  轨迹=$N 条"
  [ "$ALIVE" = "0" ] && break
  sleep 60
done

# ★★ 防护（血的教训 ✓）: 曾因【守卫误判 + 轨迹被清空】→ 蒸出只有表头的空文件 ✗
#    覆盖了 7762 字符的成果（agent_kb/90_learned.md 是宝贵财富 ✓ 绝不能再被空数据覆盖）
MIN_TRAJ=${MIN_TRAJ:-20}
N_TRAJ=$(wc -l < $D/trajectories.jsonl 2>/dev/null || echo 0)
if [ "$N_TRAJ" -lt "$MIN_TRAJ" ]; then
  echo "[$(date '+%F %T')] ✗ 轨迹只有 $N_TRAJ 条（少于 $MIN_TRAJ）→ 拒绝蒸馏与入库，保留现有成果 ✓"
  echo "    如确实要用少量轨迹蒸馏，请显式设 MIN_TRAJ=<更小的数> 重跑 ✓"
  exit 1
fi
echo "[$(date '+%F %T')] 全部结束 → 蒸馏（轨迹 $N_TRAJ 条 ✓ 达到阈值 $MIN_TRAJ ✓）"
$PY scripts/reflect_distill.py 2>&1 | sed 's/^/  /'

echo "[$(date '+%F %T')] 入库到 agent_use（幂等 ✓ 同名替换 ✓）"
curl -s -m 900 -X POST http://127.0.0.1:8001/reflect/ingest \
  | $PY -c "import sys,json; sys.stdout.reconfigure(encoding='utf-8',errors='replace'); print('  ', json.dumps(json.load(sys.stdin), ensure_ascii=False)[:300])"

echo "[$(date '+%F %T')] 入库后状态"
curl -s -m 60 http://127.0.0.1:8001/kb/agent_use \
  | $PY -c "
import sys,json
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
d=json.load(sys.stdin)
from collections import Counter
c=Counter(x['name'] for x in (d.get('documents') or []))
print('   条目', len(d.get('documents') or []), '块', d.get('total_chunks'), 'embedding', d.get('embedding'))
print('   重复:', {k:v for k,v in c.items() if v>1} or '无 ✓')
print('   文件:', sorted(c))"

echo "[$(date '+%F %T')] 守卫完成 ✓"
