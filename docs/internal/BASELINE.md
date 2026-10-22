# 基准数据与对照表（Baseline）

> 本文所有数字都由脚本**现测**后写入（`docs/` 的生成过程见提交说明），
> 不是回忆值。复现命令见每节。
> 生成时间：本次提交时刻；被测环境：WSL2 + RTX 5060 Ti 16GB + 本地 vLLM。

## 一、测试门禁

| 层 | 项数 | 状态 | 耗时 | 命令 |
|---|---|---|---|---|
| L1 后端单元测试 | 699 项 | 全绿 ✓ | 16.1s | `make test` |
| L2 前端断言 | 369 项（`make test-frontend`）| 全绿 ✓ | ~2s | `make test-frontend` |
| L3 Agent 工具选择 | 186 题 | 98.9%（184/186，两次一致） | ~6min | `python scripts/agent_tool_eval.py` |
| L4 文档处理任务 | 37 用例/轮 | 37/37 × 2 轮 ✓ | ~15min/轮 | `make doc-test` |
| L5 界面巡视 | 9 个页面 + agent 真跑 | 0 报错 ✓ | ~3min | `make ui-check` |

## 二、规模

| 项 | 数值 |
|---|---|
| 前端 `webui/index3.html` | 7,942 行 / 150 个函数 / 79 个 `window` 导出 / 4 个 `<script>` 块 |
| 后端 `lora_manager.py` | 单文件（含全部路由与文档工具） |
| Agent 工具 | 45 个（`agent_kb/_tools.json`）|
| 文档流程工具 | 19 个（`DOC_FLOW_TOOLS`）|
| 工作流节点类型 | 26 类 |
| 测试代码 | `tests/`（后端）+ `tests/frontend/run.js`（前端）+ `tools/doc_task_battery.py`（文档）|

## 三、运行时基线

| 项 | 实测值 |
|---|---|
| 底座模型 | /home/luxy/models/Qwen3-4B-Instruct |
| LoRA 数量 | 15 个 |
| 工作流配置 | 2 个 |
| 文档流程预设 | 4 个：doc_flow_macro_edit.json, doc_flow_new.json, doc_flow_standard_edit.json, doc_flow_word_outline.json |
| 就绪检查 | 模式 `full`，检查项 5 条 |

## 四、成本对照（云端 token）

数据来源：`outputs/result_*.json` 的 `_meta.usage.by_provider`；**云端 token = openai.total_tokens**。

| 组 | 记录数 | 云端 token 中位 |
|---|---|---|
| 高消耗组 | — | 30896 |
| 低消耗组 | — | 8198 |
| **中位降幅** | 共 52 次可用运行 | **73.5%** |

⚠️ **限界**：这些运行**不是同一批论文**、配置随时间变化 → 属**趋势对照**，非受控 A/B。
受控实验见 `HANDOVER.md` §Z（3 臂、同 3 篇论文、只改汇总节点）。
报告字数两组不同 → **不能只看 token 下降就宣称更好**（详见 `docs/EVAL_PROTOCOL.md` 第四节）。

复现：`make repro`（会打印逐次运行明细与限界说明）。

## 五、性能基线（文档处理，真实模型）

| 阶段 | 实现 | 耗时 | token 成本 |
|---|---|---|---|
| `read_docx` 导入 | python-docx | ~20ms | 0 |
| `format_fix` 格式规范化 | **确定性规则** | ~1ms | 0 |
| `structure_fix` 分段标题 | 本地 LLM | ~1.5s | 本地 |
| `proofread` 校对 | 本地 LLM | ~1.2s | 本地 |
| `polish` 润色 | 本地 LLM | ~1.1s | 本地 |
| `condense` 精简摘要 | 本地 LLM | ~1.8s | 本地 |
| `logic_check` 逻辑核查 | 本地 LLM | ~7.2s | 本地 |
| `terminology` 术语统一 | 本地 LLM | ~4.2s | 本地 |
| `translate` 翻译 | 本地 LLM | ~2.5s | 本地 |
| `write_docx` 导出 | python-docx | ~20ms | 0 |
| **完整 10 步链路** | — | **19.5s** | 全部本地 ✓ |

长文分块：3 万字一次性翻译实测 **89 秒且输出重复** → 分块后按块计时，
每块约 40–80 秒（2,500 字/块），返回带 `chunks` 字段。

## 六、与"移植 DSH 插件"相关的形态数据

| 项 | 数值 |
|---|---|
| DSH 插件机制 | Cordis 框架的 npm 包（`@deepseek-ai/cordis@4.0.4`，独立 MIT）|
| Cordis 插件包数量 | 242 个（`cordis-composition-reference` 生成的清单）|
| 本工作台的工具注册方式 | `const AGENT_TOOLS = {}` + 逐条赋值（派发点**仅一处**）|
| 结论 | 详见 `docs/DSH_PLUGIN_PORTING_ASSESSMENT.md` |
