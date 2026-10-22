#!/usr/bin/env bash
# 一键配置环境：建虚拟环境 -> 装依赖 -> 检查模型/配置 -> 打印下一步
# 用法：bash scripts/setup.sh            （默认 CPU/最小依赖）
#      GPU=1 bash scripts/setup.sh       （额外安装推理/训练栈，体积大）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VENV="${VENV:-$ROOT/.venv}"
PY="${PYTHON:-python3}"

echo "=========================================="
echo " 论文 LoRA 工作台 · 环境配置"
echo " 项目目录: $ROOT"
echo "=========================================="

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "错误：找不到 $PY，请先安装 Python 3.10+（推荐 3.11）" >&2
  exit 1
fi
echo "[1/4] Python: $("$PY" --version)"

echo "[2/4] 创建虚拟环境: $VENV"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip -q

echo "[3/4] 安装依赖"
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
else
  echo "  没有 requirements.txt，先由 scripts/gen_requirements.py 生成（若已装齐依赖）"
  pip install fastapi uvicorn requests
fi

if [ "${GPU:-0}" = "1" ]; then
  echo "  额外安装推理/训练栈（vllm / transformers / peft / llamafactory）"
  pip install vllm transformers peft
  pip install "llamafactory" || echo "  （llamafactory 安装失败可稍后手动装，训练功能需要它）"
else
  echo "  跳过推理/训练栈（需要时用 GPU=1 bash scripts/setup.sh）"
fi

echo "[4/4] 检查配置与模型"
python - <<'PYEOF'
import json
import os
from pathlib import Path
root = Path.cwd()
# 配置文件
cfg = root / "settings.json"
if not cfg.exists():
    ex = root / "settings.example.json"
    if ex.exists():
        cfg.write_text(ex.read_text(encoding="utf-8"), encoding="utf-8")
        print("  已从 settings.example.json 生成 settings.json（请按需填写云端 API Key）")
    else:
        print("  警告：没有 settings.json / settings.example.json")
else:
    print("  配置文件存在: settings.json")
# 预设工作流
presets = sorted(p.name for p in root.glob("workflow_config*.json"))
print("  预设工作流:", presets if presets else "（无！）")
# 模型目录
for cand in ("models", os.path.expanduser("~/models")):
    p = Path(cand)
    if p.exists():
        subs = [x.name for x in p.iterdir() if x.is_dir()][:5]
        print(f"  模型目录 {p}: {subs}")
print()
print("  下一步：")
print("    1) 无 GPU：bash scripts/run.sh --no-gpu")
print("    2) 有 GPU：先启动底座（见 README），再 bash scripts/run.sh")
PYEOF

echo
echo "完成。启动：bash scripts/run.sh"

# ===== 预建运行时目录 =====
# 为什么：共享状态守卫（tests/_guards.py）会在测试模块结束时核对
# outputs/ cache/ uploads/ training_data/ data/ 没被动过。全新检出时这些目录**尚不存在**，
# 第一次跑测试会由测试自身创建它们 -> 守卫看到"从无到有"于是判定失败（ERROR: tearDownModule）。
# 预建它们即可让第一次运行就有稳定基线。
RUNTIME_DIRS="outputs cache/nodes uploads training_data data"
for d in $RUNTIME_DIRS; do
  mkdir -p "$d"
  [ -f "$d/.gitkeep" ] || : > "$d/.gitkeep" 2>/dev/null || true
done
echo "运行时目录已就绪: $RUNTIME_DIRS"
