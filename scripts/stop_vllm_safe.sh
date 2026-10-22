#!/usr/bin/env bash
# P-87 修复 ②：安全停 vLLM（不走会 hang 的接口 ✓）2026-10-19
#
# 背景（P-87 问题3）：`POST /vllm/stop` 会 hang ✗（curl 不返回 ✗ 但 vLLM 实际已停 ✓）
#   ⇒ 夜间链路卡在 STEP 3 ✗ 训练没启动 ✗
#
# 本脚本做法：直接杀进程 + 等显存释放 ✓ 不依赖接口 ✓
# 用法:
#   bash scripts/stop_vllm_safe.sh          # 停 vLLM 并等显存释放
#   bash scripts/stop_vllm_safe.sh --check  # 只看状态
set -u

check() {
    echo "=== vLLM 状态 ==="
    echo -n "  :8000 = "; curl -s -o /dev/null -w '%{http_code}\n' -m 3 http://127.0.0.1:8000/v1/models
    echo -n "  进程数: "; ps -eo args | grep -E 'python.*vllm|EngineCore' | grep -v grep | wc -l
    echo -n "  显存: "; nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
}

if [ "${1:-}" = "--check" ]; then
    check
    exit 0
fi

echo "=== 安全停 vLLM（P-87 修复 ✓ 不走 hang 接口 ✓）==="
check

echo ""
echo "--- 1. 找 vLLM 进程 ---"
PIDS=$(ps -eo pid,args | grep -E 'python.*vllm|EngineCore' | grep -v grep | awk '{print $1}')
if [ -z "${PIDS:-}" ]; then
    echo "  （无 vLLM 进程 ✓ 已经停了）"
else
    echo "  找到: $PIDS"
    echo "--- 2. 先 TERM 再 KILL ---"
    for p in $PIDS; do kill -TERM "$p" 2>/dev/null; done
    for i in 1 2 3 4 5 6; do
        sleep 2
        LEFT=$(ps -eo args | grep -E 'python.*vllm|EngineCore' | grep -v grep | wc -l)
        echo "    TERM 后剩余: $LEFT"
        [ "$LEFT" = "0" ] && break
    done
    # 还活着就 KILL ✓
    PIDS2=$(ps -eo pid,args | grep -E 'python.*vllm|EngineCore' | grep -v grep | awk '{print $1}')
    if [ -n "${PIDS2:-}" ]; then
        echo "  仍有残留 ⇒ KILL: $PIDS2"
        for p in $PIDS2; do kill -9 "$p" 2>/dev/null; done
        sleep 2
    fi
fi

echo ""
echo "--- 3. 等显存释放（最多 90 秒 ✓）---"
for i in $(seq 1 18); do
    sleep 5
    USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' \r')
    echo "    used=${USED:-?}MiB"
    [ "${USED:-99999}" -lt 4000 ] && { echo "  ✓ 显存已释放（${USED}MiB）"; break; }
done

echo ""
echo "=== 最终状态 ==="
check
echo ""
echo "⇒ 现在可以安全训练 ✓（train_lora.py 需要 <4GB 占用 ✓）"
