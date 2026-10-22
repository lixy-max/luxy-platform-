# 贡献指南

感谢你愿意改进顿悟 luxy。本文说明如何在本地跑起来、改代码、提 PR。

## 1. 开发环境

```bash
git clone https://github.com/lixy-max/luxy-platform-.git
cd luxy-platform
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 可选：GPU 推理/训练
pip install "vllm==0.11.0"
```

**Python 3.10 ~ 3.12**。Windows 请用 WSL2（后端依赖 Linux 语义的进程/端口管理）。

## 2. 本地运行

```bash
bash install/start.sh            # 后端 :8001 + 前端 :8080
bash install/smoke_test.sh       # 7 项冒烟
python webui/serve.py 8080       # 或只起前端
```

## 3. 提交前必须通过的三道门

```bash
# ① 全量语法（27 个模块 + scripts + tests）
python -m compileall -q .

# ② 单元测试（828 个，0 红）
PYTHON=python bash run_tests.sh

# ③ 密钥扫描（必须 0 命中）
bash scripts/check_no_secrets.sh
```

CI 会跑同样的三道门（见 `.github/workflows/ci.yml`），未通过不会被合入。

## 4. 代码约定

| 项目 | 约定 |
|---|---|
| 注释 | 中文，写**为什么**而不是**做了什么**；踩过的坑要在注释里留下证据（实测数据/复现条件） |
| 诚实性 | 禁止"绝对正确/绝对不行"的无依据断言；不确定就写不确定 |
| 路径 | 一律 `Path(__file__).resolve().parent` / `Path.home()` / 环境变量，**禁止硬编码机器路径** |
| 密钥 | **禁止**写进任何被 git 跟踪的文件；用环境变量（`DEEPSEEK_API_KEY` 等） |
| 后端 | `lora_manager.py` 单文件（历史原因），新增端点请就近放置并写简短 docstring |
| 前端 | `webui/index3.html` + `webui/pages/*.js`；不得引入构建步骤（保持"解压即用"） |
| 数据目录 | `data/` `loras/` `uploads/` `logs/` 等运行产物不得提交 |

## 5. 提 PR

1. 从 `main` 开分支：`feat/xxx` / `fix/xxx` / `docs/xxx`
2. 一个 PR 只做一件事；带复现步骤与验证结果（贴命令输出）
3. 更新 `CHANGELOG.md` 的 `## Unreleased` 段
4. 如果改了行为，同步更新 `docs/` 下对应文档

## 6. 报 Bug

用 `.github/ISSUE_TEMPLATE/bug_report.md`，请附：
- `GET /version` 输出
- 复现命令（curl 优先于截图）
- 期望 vs 实际
- 相关日志片段

## 7. 安全

不要公开提安全 issue，见 [SECURITY.md](SECURITY.md)。

## 关于 `scripts/` 与 `tests/`

这两个目录里的部分脚本是**开发期一次性工具**（分析评测结果、跑离线实验等），
里面仍绑定了历史机器的绝对路径（如 `/mnt/e/WSL/_check/...`、`/home/luxy/...`）。

- 它们**不进发布包**（见 `.gitattributes` 的 `export-ignore`）
- 若你要复用，请把路径改成参数或环境变量
- 核心运行时（`*.py` 顶层模块）**不含**本机绝对路径

