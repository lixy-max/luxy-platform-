# LoRA 节点挂载规范（最新）

> 这份文档记录"本地化节点该怎么挂 LoRA"。每次改预设模板都按这里的约定来，
> 避免再出现"同一个节点在不同模板里挂法不同"。

## 一、本地化节点的标准字段

| 字段 | 值 | 说明 |
|---|---|---|
| `provider` | `vllm` | 走本地推理 |
| `lora` | LoRA 名 | **必须与该底座兼容**（`GET /loras` 的 `compatible`，UI 会置灰不兼容项）|
| `chunk_size` | `3000` | 仅 `extract` / `process` 类节点；`terminal` 不参与分块 |
| `chunk_overlap` | `500` | 与训练窗口一致 |
| `chunk_workers` | `4` | 并发分块 |
| `cache` | `true` | 分块结果缓存 |
| `temperature` | `0.0` | 抽取任务要确定性 |
| `prompt` | **与训练指令逐字一致** + `"\n\n{input_paper}"` | ← 最容易漏！不一致会让 LoRA 效果明显打折 |

## 二、按节点类型分三类

1. **input / retrieval / aggregate** — 不挂 LoRA（前两类无模型；aggregate 是汇总，交给云端）
2. **extract / process** — **本地 LoRA 首选**：支持分块 + JSON 合并
   （`merge_json_outputs`：list 有序并集 / str 按句去重 / 标量冲突不静默）
3. **terminal** — 不参与分块；若它的输入已经是本地抽取结果（如 `extract_claims`
   吃 `extract_method` + `extract_dimensions`），本地化**只需改 provider + lora**

## 三、落地检查清单

- [ ] `GET /loras` 里该 LoRA `compatible == true`
- [ ] `prompt` 与训练数据里的指令**逐字一致**（构造器生成样本时用的那句）
- [ ] `chunk_size/overlap` 与训练窗口一致（3000/500）
- [ ] 跑一次端到端，看 `_meta.usage.by_provider.openai.total_tokens` 是否下降
- [ ] 报告质量不退：报告字数、事实数、**无出处事实数**（这三项 + 人工读一遍）
- [ ] `_meta.node_stats` 里该节点 `lora` 字段确实是新 LoRA、分块统计正常

## 四、当前各节点的挂载现状

| 节点 | 类型 | provider | LoRA | 说明 |
|---|---|---|---|---|
| `extract_method` | extract | vllm | `extract_v2` | 早期本地化 |
| `extract_dimensions` | process | vllm | `dimensions_v1` | 阶段一本地化（云端 20.3k → 0）|
| `extract_claims` | terminal | 见发布配置 | 见发布配置 | 输入是本地抽取结果 |
| `analyze_method` | terminal | vllm | **无（裸底座）** | 提示词约束后 14.8s，内容比测试 LoRA 更具体 |
| `analyze_data` | terminal | vllm | **无（裸底座）** | 提示词约束后 13.4s（原 48.9s）|
| `critique_analysis` | terminal | openai | — | **保留云端**（判断类"大问题"）|
| `finaloutcome` | aggregate | openai | — | **保留云端**（汇总类"大问题"）|

## 五、模板与实验残留

- **可用预设**：`workflow_config.json`（通用论文分析）、
  `workflow_config_fast_analyze_data.json`（低延迟模式）
- **实验残留**已移入 `archive/`（不再出现在模板画廊）：
  `ab_coverage` / `ab_agg_prompt` / `ab_agg_budget` / `dimlocal`
  —— 它们是某次 A/B 的快照（挂的是 `paper_analyzer_v5` 等旧挂载），
  保留是为了报告可复现，但**不该被当成预设模板**给用户选。

## 六、尝试过但**未采用**的挂载（附原因，避免重复踩）

### `extract_claims` → 本地 `claims_v1`（2026-10-06 尝试，未采用）

做法：该节点输入是 `{extract_method} + {extract_dimensions}`（本地抽取结果，非原文），
所以**没有用 build_extractive_dataset 造数据**，而是从 56 次历史运行里
**收割真实 (输入, 云端输出) 对** → 41 条可用 → 训练 25 条（rank 8 / 5 epochs / 4 分钟）。

结果（8 条留出）：

| 指标 | 本地 `claims_v1` | 云端 |
|---|---|---|
| JSON 可解析率 | **0.375** | 1.000 |
| claims 均值 | 7.0 | 17.25 |
| evidence 逐字率 | 1.000（⚠️ 指标有盲点，见下） | 0.994 |

诊断（探针 2 条、`max_tokens=6000`）：
- 样本①：输出 5,963 字符、**解析 OK**，但只有 **7 claims vs 云端 32**（覆盖 22%）；
  且**短证据编造 0/32**（证据引用是可信的）
- 样本②：输出 **27,082 字符**仍非合法 JSON —— **模型不学停止、无限生成**（25 条样本的典型退化）

未采用的三个原因
1. **数据量远远不够**：真实轨迹只有 41 条（其中还要留出）→ 模型学不会"何时结束输出"
2. **该节点的输出天然很长**（云端 16~32 claims/样本，约 4.2k 字符）→ 对 4B 是长输出任务
3. 收益有限：该节点只占云端 **2,253 token / 8,509（27%）**，投入产出比远低于
   `extract_dimensions`（20,345 token）

**顺带发现一个真实配置缺陷**：`extract_claims` 没有设 `max_tokens` → 走引擎默认
2048/4096；云端 2.6k token 刚好挤进 4096，而**任何本地化尝试都会截断**。
将来若要重试，先给它设 `max_tokens ≥ 6000`。

若将来要重试，需要同时满足：① 数据扩到数百条（例如对更多论文跑一次云端生成配对，
每篇约 2.9k token）；② 缩短目标格式（如 evidence 由列表改为单字符串）；③ 设好 max_tokens。

### 指标盲点（本次教训）
"evidence 逐字率"原先只统计 **≥20 字符** 的证据 → **漏检了短编造**
（实测出现过 `"evidence": "The grass is green."` 这种 19 字符的胡话）。
必须把阈值降到 8 字符左右，或改成"任何证据都要能在输入里找到"。

### `analyze_data`：**决定不训 LoRA** —— 问题在提示词，不在模型（2026-10-06）

这条线与前两条不同：**先做了零成本实验，发现根本不需要训练**。

1. 先试"直接换上现成 LoRA"：`analyze_data` + `paper_analyzer_v6` → JSON 4/4 合法、
   1,389 字符 / 17.4s（裸底座 3,765 字符 / 36.3s）—— **快 2.1 倍**，但**答错了问题** ✗：
   它输出通用的 `research_problem`/`methodology` 结构，还说"研究问题未在文本中明确说明"
   （输入只有 `data` 字段）。**原因是它的训练指令与这个节点的提示词不匹配**。
   → 又一次印证：**提示词必须与 LoRA 的训练指令一致**。
   （反例：`analyze_method` 用同一个 LoRA 完全切题 ✓，因为指令匹配 ✓）
2. 再试**纯提示词收敛**（零成本、零云端 token）：给裸底座的提示词加输出约束。

端到端对比（同一篇论文，只换 `analyze_data` 提示词）：

| 变体 | 报告字数 | 事实 | **无出处** | data 字符 | data 耗时 | 整轮耗时 |
|---|---|---|---|---|---|---|
| V0 现状 | 2,729 | 21 | **0** | 1,927 | 48.85s | 92.9s |
| V2 极简 | 2,117 | 33 | **1** ✗ | 238 | 5.93s | 44.5s |
| **V3 中等（已采纳）** | 2,247 | **23** | **0** ✓ | 1,148 | **12.82s** | **56.8s** |

**结论：采纳 V3**（3~5 条 issue + recommendations + 字数上限）：
`analyze_data` 耗时 **−74%**、整轮 **−39%**、事实 21→23、**仍然零无出处事实**。
V2 虽然更快，但出现 1 条无出处事实 → 触发质量红线，**否决**。

**教训**：想训 LoRA 之前，先问"是不是提示词没约束输出"。这次答案是**是** ——
省下一次训练、也省下了用云端生成蒸馏目标的 token。


## 七、测试产物黑名单（**不要挂进任何预设**）

用户确认：下面这些 LoRA 是**验证训练流程能否跑通**的测试产物或已损坏，**不可用**。
已在 `/home/luxy/loras/registry.json` 里标记 `unusable: true`（Agent 的 `get_loras_registry` 会读到）。

| LoRA | 问题 |
|---|---|
| `paper_analyzer_v5` / `paper_analyzer_v6` | **测试产物**（用户确认）—— 之前被误挂在 `analyze_method` 上 ✗ |
| `testlora` / `test_agent_v1` | 测试产物 |
| `ml_engineer` / `strict_json` | 缺 `adapter_config.json`，无法加载 |
| `frontend-ui-architect-lora` / `json_extract` | 底座不同（hidden 维度不匹配）|

**真正可用的**：`extract_v1`、`extract_v2`、`extract_v3`、`dimensions_v1`（生产在用）、
`dimensions_v2` / `claims_v1`（实验版，未采用）。

⚠️ 历史报告（`WORKFLOW_STRATEGY.md`、`BATCH1_REPORT.md`、`WORKFLOW_CLOUD_EDGE_PLAN.md`）
里提到过 `paper_analyzer_v5/v6` —— 那时把它们当成可用 LoRA，**结论请以本节为准**。

### `analyze_data` / `analyze_method` 专用 LoRA：**做过，失败，不采纳**（2026-10-06）

用户要求"做本地专用 lora"。由于这两个节点**已经本地化**，训 LoRA 不省云端 token，
只能换质量/速度；而它们**没有云端目标**（历史 0 次云端运行），历史输出又都是
**裸底座的无约束版**（2~3.7k 字符，拿它当目标等于教模型啰嗦）。

于是走**零云端成本的自蒸馏**：目标 = 裸底座 + 当前生产约束提示词。
`analyze_data` 收 20 条有效目标（14 训练 / 6 留出，均为真实运行输入）。

结果（6 条留出）

| 配置 | JSON | 平均字符 | 平均耗时 | 覆盖参考项 |
|---|---|---|---|---|
| **裸底座** | 6/6 | **469** | **6.2s** | **0.866** |
| `analyze_data_v1` | 6/6 | 808 | 9.8s | **0.000** |

**LoRA 更长、更慢、内容与参考完全不相交 → 不采纳**（`analyze_method` 同理，未再做）。

两条原因
1. **样本量根本不够**：14 条（`claims_v1` 用 25 条也失败）
2. **更根本：提示词已经把任务解决了**。裸底座本身 6/6 JSON、469 字符、6.2s ——
   它就是参考的生成者，**LoRA 没有可改进的空间**。要更好只能换**更强的老师**（云端），
   而这会花云端 token 去蒸馏一个本来不花云端 token 的节点，与项目第一目标冲突。

**可复用的结论**：本地节点想"更好"时，先看它的输出是否已经满足格式与长度约束。
若已满足（像这两个），**训 LoRA 是负收益**；瓶颈在内容质量时，才考虑云端蒸馏（要算 token 账）。
