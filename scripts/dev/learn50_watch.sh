#!/usr/bin/env bash
set -u
cd /home/luxy/paper_lora_project
PY=/home/luxy/.venv/bin/python
D=data/trajectories
echo "[$(date '+%F %T')] 守卫启动（等 50 条跑完 → 清洗 → 提炼 → 入库）"
while true; do
  A=0
  while read -r p; do [ -n "${p:-}" ] && kill -0 "$p" 2>/dev/null && A=$((A+1)); done < $D/learn50_pids.txt
  N=$(wc -l < $D/learn50/trajectories.jsonl 2>/dev/null || echo 0)
  echo "[$(date '+%F %T')] 存活 $A · 轨迹 $N/50"
  [ "$A" = "0" ] && break
  sleep 120
done
echo "[$(date '+%F %T')] 全部跑完 → 走阶段3 链路（清洗 → 提炼 → 入库 learned_chat）"
$PY - <<'PYEOF'
import json, sys, time
sys.path.insert(0, '/home/luxy/paper_lora_project/scripts')
sys.path.insert(0, '/home/luxy/paper_lora_project')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
import learn_sink as ls
D = Path('/home/luxy/paper_lora_project/data/trajectories/learn50')
rows = [json.loads(l) for l in (D / 'trajectories.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()] if (D / 'trajectories.jsonl').exists() else []
print(f'  原始轨迹 {len(rows)} 条')
cfgs = {'clean': {'drop_incomplete': False, 'drop_short_task': 25, 'drop_long_steps': 12,
                  'dedup': True, 'strip_abs_paths': True},
        'kb_mode': 'new', 'kb_name': 'learned_chat', 'build_kb': True}
kept = dropped = 0
reasons = {}
for r in rows:
    t = dict(r)
    t.setdefault('final', t.get('final') or '（见 steps 与 tool 结果）')
    if not t.get('final'):
        t['final'] = '；'.join([str((c.get('result') or ''))[:200]
                                for s in (t.get('steps') or []) for c in (s.get('tool_calls') or [])][:3]) or '有产出'
    st = ls.sink_session(t, cfgs)
    if st.get('kept'):
        kept += 1
    else:
        dropped += 1
        for d in (st.get('dropped') or ['?']):
            reasons[d.split('（')[0]] = reasons.get(d.split('（')[0], 0) + 1
print(f'  清洗: 保留 {kept} · 丢弃 {dropped}')
print(f'  丢弃原因: {reasons}')
print(f'  → 知识库 learned_chat 应已生成 ✓')
PYEOF
echo "[$(date '+%F %T')] 看结果:"
curl -s -m 60 http://127.0.0.1:8001/kb/learned_chat | $PY -c "
import sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
try:
    d = json.load(sys.stdin); docs = d.get('documents') or []
    print(f'  learned_chat: {len(docs)} 条目 / {d.get(\"total_chunks\")} 块')
    for x in docs: print('   ', x.get('name'), x.get('chunks'), '块')
except Exception as e: print('  读取失败:', e)"
echo "[$(date '+%F %T')] 守卫完成 ✓"
