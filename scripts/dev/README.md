# `scripts/dev` — 开发期一次性工具

这里的脚本是研发过程中用来分析评测结果、跑离线实验、做数据统计的**一次性工具**，
通常绑定了当时机器的绝对路径（`/home/<user>/...`、`/mnt/e/WSL/_check/...`）。

## 它们不随发布包分发

`.gitattributes` 里已把 `scripts/dev/` 标记为 `export-ignore`，
所以下载的 release tarball 里**没有这个目录**。

## 想复用怎么办

1. 把硬编码路径改成命令行参数或环境变量
2. 确认依赖的数据/中间产物存在
3. 大多数脚本只是读 JSONL 做统计，不必在运行时依赖

> 核心运行时（`scripts/check_no_secrets.sh`、`scripts/gen_agent_kb.py`、`scripts/run.sh`）
> 不含任何本机绝对路径。
