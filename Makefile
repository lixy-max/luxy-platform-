# Luxy 平台 · 常用命令（README 引用的目标都在这里）
# 按顺序探测 Python：项目内 .venv → 家目录 .venv → PATH 上的 python3
VENV_PY := $(firstword $(wildcard .venv/bin/python $(HOME)/.venv/bin/python))
PY ?= $(if $(VENV_PY),$(VENV_PY),python3)
PORT ?= 8001
WEB_PORT ?= 8080

.PHONY: help install start stop smoke test lint clean
help:            ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
install:         ## 安装依赖
	$(PY) -m pip install -r requirements.txt
start:           ## 启动（后端+前端）
	bash install/start.sh
stop:            ## 停止（需指定 BACKEND_PORT/WEB_PORT，或 --all）
	bash install/stop.sh
smoke:           ## 冒烟自检
	bash install/smoke_test.sh
test:            ## 跑测试
	PYTHON=$(PY) bash run_tests.sh
lint:            ## 语法门（全部 py）
	@$(PY) -m compileall -q . ; echo "compile OK"
clean:           ## 清理缓存
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; echo "cleaned"
