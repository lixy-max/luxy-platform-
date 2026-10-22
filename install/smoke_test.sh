#!/usr/bin/env bash
# Luxy 平台 · 冒烟自检（不启动训练、不动 vLLM）
set -u
BASE="${1:-http://127.0.0.1:8001}"
PY="${PYTHON:-python3}"
pass=0; fail=0
ck() {
  if [ "$2" = "$3" ]; then echo "  ✓ $1 ($3)"; pass=$((pass+1));
  else echo "  ✗ $1 期望 $3 实际 $2"; fail=$((fail+1)); fi
}
echo "== 冒烟自检 $BASE =="
ck "health"      "$(curl -s -m 5 -o /dev/null -w '%{http_code}' $BASE/health)" "200"
ck "version"     "$(curl -s -m 5 -o /dev/null -w '%{http_code}' $BASE/version)" "200"
ck "readiness"   "$(curl -s -m 8 -o /dev/null -w '%{http_code}' $BASE/readiness)" "200"
ck "agent/tools" "$(curl -s -m 8 -o /dev/null -w '%{http_code}' $BASE/agent/tools)" "200"
ck "sim/nodes"   "$(curl -s -m 8 -o /dev/null -w '%{http_code}' $BASE/sim/nodes)" "200"
ck "workflow/cfg" "$(curl -s -m 8 -o /dev/null -w '%{http_code}' $BASE/workflow/config)" "200"
ck "hallucination/check" "$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X POST $BASE/hallucination/check -H 'Content-Type: application/json' -d '{"answer":"今天天气不错。"}')" "200"
echo ""
echo "== 结果: 通过 $pass / 失败 $fail =="
[ "$fail" = "0" ] || exit 1
