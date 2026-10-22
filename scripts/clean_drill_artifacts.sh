#!/usr/bin/env bash
# 演练产物归档器（Drill Artifacts Cleaner）
#
# 为什么需要（真实教训 ✓）:
#   演练/评估会在项目根目录生成一堆 workflow_config_*.json ✗
#   · 曾经因此卡住发布闸门（make_release.sh 要求"预设工作流=2 个"）✗
#   · 与出厂配置混在一起，容易误以为它们是正式配置 ✗
# 做法（安全 ✓ 幂等 ✓）:
#   · 只动【非出厂】的 workflow_config_*.json ✓
#   · 出厂的两个永远保留: workflow_config.json（默认）+ workflow_config_fast_analyze_data.json
#   · 归档到 data/drill_artifacts/configs/<时间戳>/ ✓ 保留证据（不删除 ✗）
#   · --restore 可以把最近一次归档还原回来 ✓
#   · --dry-run 只看不动 ✓
#
# 用法:
#   bash scripts/clean_drill_artifacts.sh              # 归档
#   bash scripts/clean_drill_artifacts.sh --dry-run    # 只看
#   bash scripts/clean_drill_artifacts.sh --restore    # 还原最近一次
set -u
cd "$(dirname "$0")/.." || exit 1

DRY=0
RESTORE=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --restore) RESTORE=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $a" >&2; exit 2 ;;
  esac
done

ROOT=$(pwd)
ARCHIVE_ROOT="$ROOT/data/drill_artifacts/configs"
# ★ 出厂配置（永远保留 ✓）—— 与 scripts/make_release.sh 的要求一致
KEEP=("workflow_config.json" "workflow_config_fast_analyze_data.json")

is_keep() {
  local f="$1"
  for k in "${KEEP[@]}"; do [ "$f" = "$k" ] && return 0; done
  return 1
}

if [ "$RESTORE" = "1" ]; then
  LAST=$(ls -1d "$ARCHIVE_ROOT"/*/ 2>/dev/null | sort | tail -1)
  if [ -z "${LAST:-}" ]; then
    echo "没有可还原的归档 ✗（$ARCHIVE_ROOT 为空）"; exit 1
  fi
  echo "=== 还原最近一次归档: $LAST ==="
  n=0
  for p in "$LAST"*.json; do
    [ -e "$p" ] || continue
    b=$(basename "$p")
    if is_keep "$b"; then echo "  跳过出厂配置: $b"; continue; fi
    if [ -e "$ROOT/$b" ]; then echo "  已存在，跳过（不覆盖 ✗）: $b"; continue; fi
    cp -p "$p" "$ROOT/$b" && echo "  还原: $b" && n=$((n+1))
  done
  echo "  ⇒ 还原 $n 个文件"
  exit 0
fi

echo "=== 演练产物归档（dry-run=$DRY）==="
echo "  出厂配置（保留）: ${KEEP[*]}"
echo

TS=$(date +%Y%m%d_%H%M%S)
DEST="$ARCHIVE_ROOT/$TS"
n=0
for f in workflow_config*.json; do
  [ -e "$f" ] || continue
  if is_keep "$f"; then
    echo "  保留 ✓ $f"
    continue
  fi
  if [ "$DRY" = "1" ]; then
    echo "  将归档 → $f"
  else
    mkdir -p "$DEST"
    mv "$f" "$DEST/" && echo "  归档 → $f"
  fi
  n=$((n+1))
done

if [ "$n" = "0" ]; then
  echo "  没有需要归档的文件 ✓（当前只剩出厂配置 ✓）"
else
  [ "$DRY" = "1" ] || echo "  ⇒ 归档 $n 个文件到 $DEST ✓（用 --restore 可还原 ✓）"
fi

echo
echo "=== 校验：当前工作流配置数（发布闸门要求出厂 2 个 ✓）==="
ls -1 workflow_config*.json 2>/dev/null | sed 's/^/  /' || echo "  （无）"
cnt=$(ls -1 workflow_config*.json 2>/dev/null | wc -l | tr -d ' ')
echo "  当前 $cnt 个"
if [ "$cnt" = "2" ]; then
  echo "  ✓ 只剩出厂配置，发布闸门不会卡 ✓"
else
  echo "  ⚠ 仍有 $cnt 个（若包含演练产物 ⇒ 再跑一次本脚本 ✓）"
fi
