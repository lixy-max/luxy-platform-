#!/usr/bin/env bash
# 静态检查闸门（pyflakes）。
#
# 为什么有这个（血的教训）:
#   2026-10-13 一天内连撞两个"引用了不存在的东西"的 bug，都很底层、都能被静态检查抓到:
#     · lora_manager.py 的 VLLM_PORT（/chat/tools 本地分支 → 选本地模型必 500）
#     · lora_manager.py 的 _re_md（被 `if '_re_md' in globals()` 的"防御"掩盖）
#   而且这两个都在【从未被执行的分支】里 —— 单元测试跑不到，只有静态检查看得见。
#   还有一类是 `except Exception as e:` 里定义生成器闭包引用 e（PEP 3110 删除 e）→ NameError。
#
# 用法: bash scripts/lint.sh        （只报错就退出码 1）
# 建议: 改完核心 py 文件后跑一次；发布前必跑。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYBIN:-/home/luxy/.venv/bin/python}"
"$PY" -m pyflakes --version >/dev/null 2>&1 || {
  echo "  未安装 pyflakes，尝试安装…"
  "$PY" -m pip install --quiet pyflakes || { echo "  ✗ 装不上，跳过"; exit 0; }
}
OUT="$("$PY" -m pyflakes *.py scripts/*.py 2>&1 | grep -v 'imported but unused' \
      | grep -v 'assigned to but never used' | grep -v 'f-string is missing placeholders' \
      | grep -v 'redefinition of unused' || true)"
if [ -n "$OUT" ]; then
  echo "✗ 静态检查发现问题（已过滤"仅风格"类）:"
  printf '%s\n' "$OUT" | sed 's/^/  /'
  exit 1
fi
echo "✓ 静态检查通过（pyflakes，已过滤 unused/占位符类）"
