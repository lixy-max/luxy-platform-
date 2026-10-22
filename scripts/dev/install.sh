#!/usr/bin/env bash
# 一键安装：检测环境 -> 装依赖 -> 生成配置 -> 建目录 -> 查模型 -> 自检
# 用法：
#   bash scripts/install.sh              完整安装（含知识库等较重依赖）
#   bash scripts/install.sh --minimal    只装核心（跑界面 + 文档工具够用，不装 chromadb）
#   bash scripts/install.sh --check      只做环境检测，不安装任何东西（安全）
#   WORKBENCH_VENV=~/myenv bash scripts/install.sh    指定 venv 位置
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="full"
for a in "$@"; do
  case "$a" in
    --minimal) MODE="minimal" ;;
    --check)   MODE="check" ;;
  esac
done

VENV="${WORKBENCH_VENV:-$HOME/.venv}"
PY="$VENV/bin/python"

ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad()   { printf '  \033[31m✗\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$1"; }
info()  { printf '    %s\n' "$1"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

echo "=================================================="
echo " 论文分析工作台 · 一键安装（模式：$MODE）"
echo "=================================================="
[ "$MODE" = "check" ] && info "（--check：只检测，不安装任何东西）"

FAIL=0

# ---------- [1/6] 环境检测 ----------
head_ "[1/6] 环境检测"

if [ -f /proc/version ] && grep -qi microsoft /proc/version 2>/dev/null; then
  ok "运行在 WSL（$(grep -o 'WSL[0-9]*' /proc/version | head -1)）"
else
  warn "看起来不是 WSL（原生 Linux 也能用；Windows 上的 .bat 入口需要 WSL）"
fi

# Python
SYS_PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then SYS_PY="$(command -v "$c")"; break; fi
done
if [ -n "$SYS_PY" ]; then
  PV="$("$SYS_PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "?")"
  if awk "BEGIN{exit !(${PV%%.*} >= 3 && ${PV#*.} >= 10)}" 2>/dev/null; then
    ok "Python $PV（$SYS_PY）"
  else
    warn "Python $PV 版本偏低（建议 3.10+；3.11/3.12 最稳）"
  fi
else
  bad "找不到 python3（先装：sudo apt install -y python3 python3-venv python3-pip）"
  FAIL=1
fi
command -v pip3 >/dev/null 2>&1 && ok "pip3 可用" || warn "pip3 不在 PATH（用 python -m pip 也行）"
command -v curl >/dev/null 2>&1 && ok "curl 可用" || warn "没有 curl（健康检查会退化）"

# GPU
if command -v nvidia-smi >/dev/null 2>&1; then
  GPU="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)"
  ok "GPU：${GPU:-（未知）}"
  info "底座用得到它；没有 GPU 也能用（界面 + 文档工具 + 云模型）"
else
  warn "没有 nvidia-smi：可跑 --no-gpu 模式（界面、知识库、文档工具、云模型都可用）"
fi

# 磁盘
AVAIL="$(df -Pk "$ROOT" | awk 'NR==2{printf "%.1f", $4/1048576}')"
if awk "BEGIN{exit !($AVAIL >= 5)}" 2>/dev/null; then ok "磁盘可用 ${AVAIL} GB"; else warn "磁盘只剩 ${AVAIL} GB（模型往往要 8GB+）"; fi

# 网络（pip 源可达性）
if curl -s -o /dev/null -m 8 https://pypi.org/simple/ 2>/dev/null; then
  ok "能访问 PyPI"
else
  warn "访问 PyPI 失败/慢（可换镜像：pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple）"
fi

# 端口
for p in 8000 8001 8080; do
  if curl -s -o /dev/null -m 2 "http://127.0.0.1:$p/" 2>/dev/null; then
    warn "端口 $p 已有服务在响应（安装不影响它；启动时脚本会复用）"
  fi
done

if [ "$MODE" = "check" ]; then
  head_ "检测完成（--check 模式，未做任何改动）"
  [ "$FAIL" = "1" ] && exit 1 || exit 0
fi

# ---------- [2/6] 依赖安装 ----------
head_ "[2/6] 依赖安装（venv: $VENV）"
if [ -x "$PY" ]; then
  ok "venv 已存在，复用"
else
  mkdir -p "$(dirname "$VENV")"
  "$SYS_PY" -m venv "$VENV" && ok "已创建 venv" || { bad "创建 venv 失败（缺 python3-venv？）"; exit 1; }
fi
"$PY" -m pip install --upgrade pip -q 2>/dev/null && ok "pip 已更新" || warn "pip 更新失败（继续）"

if [ "$MODE" = "minimal" ]; then
  CORE="fastapi uvicorn pydantic requests python-docx openpyxl pdfplumber pypdf pypdfium2"
  info "只装核心：$CORE"
  "$PY" -m pip install -q $CORE && ok "核心依赖安装成功" || { bad "核心依赖安装失败"; exit 1; }
  warn "跳过 requirements.txt 里的较重依赖（chromadb 等）；知识库语义检索可能不可用"
else
  if [ -f requirements.txt ]; then
    if "$PY" -m pip install -q -r requirements.txt; then
      ok "requirements.txt 安装成功（$(grep -cvE '^\s*#|^\s*$' requirements.txt) 个包）"
    else
      bad "requirements.txt 安装失败 —— 常见原因：torch/onnx 与 Python 版本不匹配"
      info "可改用：bash scripts/install.sh --minimal"
      exit 1
    fi
  else
    warn "没有 requirements.txt，跳过（--minimal 可装核心依赖）"
  fi
fi

# ---------- [3/6] 配置生成 ----------
head_ "[3/6] 配置生成"
if [ -f settings.json ]; then
  ok "settings.json 已存在，**不覆盖**（你的配置保持原样）"
else
  if [ -f settings.example.json ]; then
    "$PY" - <<PYEOF
import json, os
from pathlib import Path
root = Path("$ROOT")
home = Path.home()
src = json.loads((root / "settings.example.json").read_text(encoding="utf-8"))
# 把示例里的占位路径换成本机真实路径（示例写的是 /home/luxy/...）
def fix(p):
    if not isinstance(p, str) or not p.startswith("/home/luxy"):
        return p
    return str(home) + p[len("/home/luxy"):]
for k, v in list(src.items()):
    src[k] = fix(v)
prov = src.get("providers") or {}
for name, cfg in prov.items():
    if isinstance(cfg, dict):
        for k, v in list(cfg.items()):
            cfg[k] = fix(v)
src.setdefault("venv_python", "$PY")
(root / "settings.json").write_text(json.dumps(src, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("    已按本机家目录 " + str(home) + " 生成本地化配置")
PYEOF
    ok "已从示例生成 settings.json"
  else
    warn "没有 settings.example.json，跳过（首次启动会写默认值）"
  fi
  info "云模型 Key 请在「⚙️ 系统设置」页填，不要写进仓库"
fi

# ---------- [4/6] 运行目录 ----------
head_ "[4/6] 运行目录"
for d in outputs uploads cache/nodes training_data data logs doc_flows; do
  mkdir -p "$d"
done
ok "已确保目录存在：outputs uploads cache/nodes training_data data logs doc_flows"

# ---------- [5/6] 模型检查 ----------
head_ "[5/6] 模型检查"
MODEL_DIR=""
if [ -f settings.json ]; then
  MODEL_DIR="$("$PY" - <<'PYEOF' 2>/dev/null
import json
from pathlib import Path
try:
    s = json.loads(Path('settings.json').read_text(encoding='utf-8'))
    p = s.get('base_model_path') or ''
    print(p)
except Exception:
    print('')
PYEOF
)"
fi
if [ -n "$MODEL_DIR" ] && [ -d "$MODEL_DIR" ]; then
  ok "底座模型存在：$MODEL_DIR"
else
  warn "底座模型未就绪（${MODEL_DIR:-未配置}）"
  info "去网页「🎛️ 底座管理」页填 ModelScope 仓库名下载，例如 Qwen/Qwen3-4B-Instruct"
  info "不下模型也能用：界面、知识库、文档工具、以及云模型（选 openai:...）"
fi

# ---------- [6/6] 自检 ----------
head_ "[6/6] 自检"
if "$PY" -c "import fastapi, docx, openpyxl" 2>/dev/null; then
  ok "核心依赖可导入（fastapi / python-docx / openpyxl）"
else
  bad "核心依赖导入失败 —— 建议重跑：bash scripts/install.sh --minimal"
fi
if [ -d tests ]; then
  info "跑一遍单元测试（约 15 秒）…"
  if "$PY" -m unittest discover -s tests -t . > /tmp/install_selftest.txt 2>&1; then
    ok "自检通过：$(grep -E '^Ran ' /tmp/install_selftest.txt | head -1)"
  else
    bad "自检未通过，日志：/tmp/install_selftest.txt"
    tail -5 /tmp/install_selftest.txt | sed 's/^/      /'
  fi
fi

echo
echo "=================================================="
echo " 安装完成 ✓"
echo "=================================================="
echo "  启动：bash scripts/start.sh        （或双击 启动工作台.bat）"
echo "  打开：http://127.0.0.1:8080/index3.html"
echo "  停止：bash scripts/stop.sh         （全停加 --all）"
echo "  无 GPU：bash scripts/start.sh --no-gpu"
echo
echo "  说明：Windows 首次装请确认已开 WSL2；模型从「🎛️ 底座管理」页下载。"
echo "=================================================="
