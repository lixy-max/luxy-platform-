#!/usr/bin/env bash
# 统一入口：cd 到项目根 + 自动探测 python3（说明书里引用的是这个脚本）
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
export PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
exec bash install/start.sh "$@"
