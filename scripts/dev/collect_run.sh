#!/usr/bin/env bash
# P-87 修复 ①：采集单实例锁（2026-10-19）
#
# 背景（P-87 问题1）：同一条采集命令被启动两次 ✗
#   ⇒ 两个 runner 并发 ✗ ⇒ c12b_r06 产生 50 条重复 ✗
#
# 本脚本 = 采集的唯一入口 ✓ 带 flock 单实例锁 ✓
# 用法:
#   bash scripts/collect_run.sh <rounds>       # 采集 N 轮（默认 12）
#   bash scripts/collect_run.sh --status       # 查看是否在跑 + 进度
#   bash scripts/collect_run.sh --unlock       # 强制清锁（确认没有 runner 时用 ✓）
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK=/tmp/collect_12r.lock
RUNS=/mnt/e/WSL/_check/drill/out/runs.jsonl
STATE=/mnt/e/WSL/_check/drill/out/c12_state.json
LOG=/mnt/e/WSL/_check/drill/out/c12_run.log

show_status() {
    echo "=== 采集状态 ==="
    if [ -f "$LOCK" ]; then
        echo "  ⚠ 锁存在: $LOCK（可能正在跑 ✓）"
        # 看持锁进程
        ps -eo pid,args | grep -E 'collect_12r|run_lora_ab' | grep -v grep | head -5 | sed 's/^/    /'
    else
        echo "  ✓ 无锁（没在跑 ✓）"
    fi
    echo "  runs.jsonl: $(wc -l < "$RUNS" 2>/dev/null || echo 0) 行"
    if [ -f "$STATE" ]; then
        echo "  已完成轮次:"
        /home/luxy/.venv/bin/python -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
try:
    d = json.load(open('$STATE', encoding='utf-8-sig'))
    rs = d.get('rounds') or {}
    done = [k for k, v in rs.items() if v.get('done')]
    print('    %d / 12  (%s)' % (len(done), ', '.join(sorted(done))))
except Exception as e:
    print('    ✗', e)
" 2>/dev/null
    fi
    echo "  vLLM: $(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8000/v1/models 2>/dev/null)"
    echo -n "  显存/温度: "; nvidia-smi --query-gpu=memory.used,temperature.gpu --format=csv,noheader 2>/dev/null
}

case "${1:-}" in
    --status)
        show_status
        exit 0
        ;;
    --unlock)
        echo "⚠ 强制清锁（先确认没有 runner 在跑 ✗）"
        ps -eo pid,args | grep -E 'run_lora_ab|collect_12r' | grep -v grep | sed 's/^/  仍在跑: /'
        rm -f "$LOCK"
        echo "  ✓ 已清锁"
        exit 0
        ;;
esac

ROUNDS="${1:-12}"

# ── 单实例锁（★ P-87 核心 ✓）──
exec 9>"$LOCK"
if ! flock -n 9; then
    echo "✗ 已有采集在跑（锁 $LOCK 被占用）—— 拒绝重复启动 ✓"
    echo "  查看状态: bash scripts/collect_run.sh --status"
    exit 3
fi

echo "=== 采集启动（锁已获取 ✓）==="
echo "  轮数: $ROUNDS"
echo "  锁: $LOCK"
echo -n "  vLLM: "; curl -s -o /dev/null -w '%{http_code}\n' -m 3 http://127.0.0.1:8000/v1/models
echo ""

# 调用实际采集脚本（Windows PowerShell 在 /mnt/e 下 ✓ 需 Windows 侧 node ✓）
# ⇒ 这里只做锁 + 状态检查 ✓ 实际执行用 PowerShell 脚本 ✓
# （PowerShell 脚本本身也应在启动前查一次锁 ✓ 见 collect_12r_v3.ps1）
if [ -f /mnt/e/WSL/_check/collect_12r_v3.ps1 ]; then
    echo "→ 调用 PowerShell 采集脚本…"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'E:\WSL\_check\collect_12r_v3.ps1' 2>&1
else
    echo "⚠ 找不到 collect_12r_v3.ps1 —— 只用锁做保护 ✓"
fi

echo ""
echo "=== 采集结束 ==="
show_status
