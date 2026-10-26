# 文献与开源依据登记表（LITERATURE_REGISTRY）

> 项目原则 R1：新方向开工前至少 2 篇文献依据；R3：方案文档必须含文献依据章节；R4：决策写清依据哪篇文献的哪条结论。
> 本文件是《奇美拉蜂巢架构依据文件 v1.0》的配套登记表。
> 最后更新：2026-10-26 · 核验方式：arXiv abs 页面标题抓取（2026-10-26 实测）

## A. 已核验文献（arXiv ID + 标题已抓取验证）

| 编号 | arXiv | 标题（核验） | 与本项目的关系 / 采纳结论 |
|---|---|---|---|
| L1 | 2106.09685 | LoRA: Low-Rank Adaptation of Large Language Models | 工蜂单步能力用 LoRA 实现；主脑 14B 不挂 LoRA（避免能力与编排耦合） |
| L2 | 2305.14314 | QLoRA: Efficient Finetuning of Quantized LLMs | 本机 7B/14B AWQ 量化 + 训练可行性依据 |
| L3 | 2309.06180 | Efficient Memory Management for Large Language Model Serving with PagedAttention | vLLM 选型（PagedAttention/KV 管理）；本机 vLLM 0.11.0 实测 |
| L4 | 2310.11511 | Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection | 知识库检索增强（KB 路由 + 动态库注入上下文） |
| L5 | 2410.05779 | LightRAG: Simple and Fast Retrieval-Augmented Generation | 研究项：LightRAG 作为 KB 增强候选（尚未落地） |
| L6 | 2005.11401 | Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks | RAG 基础依据（多知识库 + 动态库作为上下文） |
| L7 | 2404.16130 | From Local to Global: A Graph RAG Approach to Query-Focused Summarization | GraphRAG 对照（研究项） |
| L8 | 2305.16291 | Voyager: An Open-Ended Embodied Agent with Large Language Models | 技能库/能力解耦思想来源（LoRA 单步能力 + 注册表） |
| L9 | 2307.16789 | ToolLLM: Facilitating Large Language Models to Master 16000+ Real-world APIs | 工具调用能力依据（工具链知识库、工具选择基准） |
| L10 | 2305.15334 | Gorilla: Large Language Model Connected with Massive APIs | 工具检索/选择依据 |
| L11 | 2310.03714 | DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines | 第三内化通道候选（程序优化替代 LoRA 训练） |
| L12 | 2507.19457 | GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning | 提示词/程序优化候选（与 DSPy 同族） |
| L13 | 2406.18665 | RouteLLM: Learning to Route LLMs with Preference Data | 云/本地路由对照（本项目用规则+主脑判断，未用学习路由） |
| L14 | 2408.08435 | Automated Design of Agentic Systems | 自动设计 agent 的思想来源（奇美拉能力嫁接的对照） |
| L15 | 2505.22954 | Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents | 飞轮/自进化对照（本项目飞轮为人工可控闭环） |
| L16 | 2506.10943 | Self-Adapting Language Models | 自改权重对照（本项目不做自动改权重） |
| L17 | 2510.17874 | Repairing Tool Calls Using Post-tool Execution Reflection and RAG | 工具调用修复依据（项目原则二引用：提示/检索方案可 +36~56%） |
| L18 | 2409.03215 | xLAM: A Family of Large Action Models to Empower AI Agent Systems | 小模型工具调用能力对照（工蜂选型参考） |
| L19 | 2402.03620 | Self-Discover: Large Language Models Self-Compose Reasoning Structures | 主脑多步编排的对照（我们用显式 steps 计划） |
| L20 | 2412.15115 | Qwen2.5 Technical Report | 主脑/工蜂底座（Qwen2.5-14B/7B-AWQ）依据 |
| L21 | 2404.11943 | AgentCoord: Visually Exploring Coordination Strategy for LLM-based Multi-Agent Collaboration | 多智能体协作对照（**注意：此前误当作 MoL，已纠正**） |

## B. 待复核文献（按标题引用，禁止当作已核验）

| 编号 | 文献 | 当前状态 |
|---|---|---|
| L22 | AgentBench: Evaluating LLMs as Agents | arXiv ID 2308.03688 抓取失败（页面异常）；暂按标题+仓库引用 |
| L23 | Qwen3 Technical Report | 未能确定 arXiv ID（2506.07934 经核验是无关数学论文） |
| L24 | Mixture of LoRA Experts (MoL) | 未找到正确 arXiv ID（2404.11943 经核验是 AgentCoord），按标题引用 |
| L25 | EasyDistill（开源蒸馏工具） | 未核验论文，按 GitHub 仓库引用 |
| L26 | Anthropic Tool Search Tool（工程文档，非论文） | 工程博客/文档；作为"工具上下文预算"实测依据 |

## C. 开源依据（GitHub API 实测；数值会漂移）

### C1. 2026-10-26 本轮实测（架构选型直接相关）

| 仓库 | stars | 许可 | 用途/结论 |
|---|---|---|---|
| vercel-labs/agent-browser | 44,607 | apache-2.0 | 奇美拉候选（浏览器自动化） |
| SeleniumHQ/selenium | 34,567 | apache-2.0 | 候选（浏览器自动化） |
| apify/crawlee | 26,265 | apache-2.0 | 候选（爬虫/浏览器） |
| citrolabs/ego-lite | 19,622 | mit | 候选 |
| hangwin/mcp-chrome | 12,510 | mit | MCP/浏览器候选 |
| jsvine/pdfplumber | 10,850 | （本轮 GitHub 返回 noassertion，需复核） | PDF 表格抽取候选 |
| atlanhq/camelot | 3,705 | （本轮返回 noassertion） | PDF 表格抽取候选 |
| ExtractPDF4J/ExtractPDF4J | 509 | （本轮返回 noassertion） | 候选（stars 低，needs_review） |

> 说明：GitHub API 的 license 字段可能返回 NOASSERTION（如仓库许可文件无法识别）；政策闸门因此判 needs_review——这是刻意保守。

### C2. 2026-10-23 项目前序实测（OSS 全景，摘自《架构分析与规划 v1.1》）

| 类别 | 代表仓库（stars / 许可 / 备注） |
|---|---|
| Agent/工具生态 | Composio 30,751 / MIT；MCP servers 91,567 / NOASSERTION；MCP python-sdk 24,666 / MIT；smolagents 30,057 / Apache；OpenHands 91,864 / MIT；browser-use 119,149 |
| 技能库/自动设计 | Voyager 7,292 / MIT（2024-04 停更）；ADAS 1,659；DGM 2,453；SEAL 1,875；Gödel Agent 240 |
| 蒸馏/数据 | EasyDistill 483 / Apache；xLAM 640 / Apache；mLoRA 386；LoraHub 668 |
| RAG | LightRAG 40,332 / MIT；GraphRAG 36,448 / MIT；LlamaIndex 52,668 / LangChain 148,471 |
| 推理服务 | vLLM 95,538；sglang 38,174；llama.cpp 132,381；ollama 184,797 |
| 训练 | ms-swift 15,935；unsloth 78,572；peft 21,851；trl 19,602 |
| 评测 | lm-eval-harness 14,316；inspect_ai 3,093；AgentBench 3,791 |
| 平台/框架 | MetaGPT 71,018 / MIT；AutoGen 61,566 / CC-BY-4.0；CAMEL 17,973；Dify 159,560 / NOASSERTION |

## D. 内部依据（项目自有证据）

| 编号 | 文件/位置 | 内容 |
|---|---|---|
| I1 | docs/PROJECT_PRINCIPLES.md | R1–R5 原则（调研先行/先找不训/评测口径先行） |
| I2 | docs/PHANTOM_TOOLS_20261022.json | 幻影工具实测（37 个工具有文档无 handler） |
| I3 | scripts/tool_retriever.py | 工具检索 92→8（省 token） |
| I4 | tests/（888 项） | 全量测试基线 |
| I5 | data/models_registry.json | 主脑基准库（10 模型/能力界限/可挂 LoRA） |
| I6 | data/capabilities/registry.json | 能力注册表（20 LoRA + 5 外部 proposed） |
| I7 | data/chimera/candidates.jsonl | 奇美拉候选库（21 条） |

## E. 使用规则

1. 新决策若引用文献，必须写编号（如"依据 L1：LoRA 只更新低秩矩阵 → 工蜂挂 LoRA"）。
2. **禁止**把 B 节"待复核"当作已核验结论引用；引用时必须标注状态。
3. 开源 stars/许可会漂移：引用时注明测量日期；许可以本地 LICENSE 检测为准（项目 R 规则）。
4. 本文件随架构依据文件同步更新；每次新增方向至少补 2 条 A 节文献（R1）。
