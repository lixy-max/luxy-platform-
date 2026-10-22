# 云边协同论文分析工作流：目标方案 / 显存预算 / LoRA 缺口 / 任务表单

> 目标：**8 GB 显存上流畅运行云边协同论文分析预设模板**
> 核心目标函数：**在可接受质量与延迟下最小化云端 token 消耗**（不是最小化延迟）
> 数据基础：本文件所有 token / 耗时数字均来自本项目**实测**（`_meta.node_stats` / `_meta.usage`），
> 非估算的地方会标「实测」；显存为按模型结构计算的估算，并给出可验证的验收方式。

---

## 0. 一页结论

| 问题 | 结论 |
|---|---|
| **训练**能用量化模型吗？ | **不能，必须 FP16**（用户已实测）→ 训练固定放 16 GB 机器；本方案**不再提"8 GB 上 QLoRA 训练"** |
| 8 GB 能不能跑？ | **取决于推理底座能否量化**：<br>**(a) 推理可用 4bit** → 能，4bit 4B 底座约 4.1 GB（现有 LoRA 能否复用见 T2）<br>**(b) 推理也必须 FP16** → **4B 装不下**（权重就 8.0 GB）→ 只能用 **1.7B FP16（约 4.5 GB）**，且 **现有 4B LoRA 全部作废、需按 1.7B 重训**<br>另见 §3.4 的第三条路（CPU offload）。见 §3 |
| 动态挂载 LoRA 最小显存？ | **理论下限约 3.3 GB**（4bit 4B + 1 个 r16 适配器 + 最小 KV/工作区）。LoRA 数量**不占显存** |
| 云端 token 能省多少？ | 现在 **30,726/次**；把 `extract_dimensions` 搬本地后可降到 **~10.5k（−66%）**；四个 LoRA 全就位后 **~7.5k（−76%）** |
| 最该先做的事 | **`extract_dimensions` 换成本地 LoRA** —— 它占云端 token 的 **66%**，是抽取形状，而且**数据构造器已经写好了** |
| 最大风险 | 现有 LoRA 是**对 FP16 底座**训练的，能否直接用在 4bit 底座上**必须先验证**（§6 T2），否则 8 GB 路线不成立 |

---

## 1. 现状：发布预设模板（9 节点，实测）

| # | 节点 | 类型 | 执行位置 | LoRA | 平均 tokens/次 | 占云端 | 平均耗时 |
|---|---|---|---|---|---|---|---|
| 1 | `input_paper` | input | 本地 | — | 0 | — | 0.00s |
| 2 | `kb_context` | retrieval | 本地 | — | 0 | — | 0.62s |
| 3 | `extract_method` | **extract** | **本地** | **extract_v2** | **21,517**（本地） | — | 0.31s（缓存命中） |
| 4 | `extract_dimensions` | process | **云端** | — | **20,257** | **66%** | 2.77s |
| 5 | `analyze_method` | terminal | 本地 | — | 1,411（本地） | — | 18.23s |
| 6 | `analyze_data` | terminal | 本地 | — | 1,017（本地） | — | 40~62s |
| 7 | `extract_claims` | terminal | **云端** | — | 2,856 | 9% | 5.05s |
| 8 | `critique_analysis` | terminal | **云端** | — | 1,554 | 5% | 4.57s |
| 9 | `finaloutcome` | aggregate | **云端** | — | 5,975 | 19% | 7.30s |
| | | | | **云端合计** | **30,726** | 100% | 整轮 101s |

**关键观察**

1. 云端 **56%** 的 token 花在一个节点上：`extract_dimensions`（66% of cloud）。它是**结构化抽取**，不是"大问题"。
2. 本地已有 `extract_method`（21.5k tokens、18 块分块并发、命中缓存时 0.31s）—— **本地链路是通的**。
3. `analyze_method` / `analyze_data` 在本地**没有挂 LoRA**（裸底座），生成 20~25 tokens/s，所以慢（18s / 40~62s）。
   → 这不是缺陷，是"用自有算力换云端 token"的代价；**挂上专用 LoRA 后输出更短更准，时间会显著下降**。
4. 真正该留给云端的只有两个：`finaloutcome`（综合成文）与 `critique_analysis`（批判判断）。

---

## 2. 目标方案：云边协同版（token 优先）

| # | 节点 | 职责 | 执行位置 | 底座 / LoRA | 输出形状 | 云端 token |
|---|---|---|---|---|---|---|
| 1 | `input_paper` | 载入全文 | 本地 | — | text | 0 |
| 2 | `kb_context` | 知识库检索 | 本地(CPU 可) | bge-m3 向量 | text | 0 |
| 3 | `extract_method` | 方法逐字抽取 | **本地** | Qwen3-4B + **extract_v2 ✅ 已有** | text（分块 3000/500/4 并发） | 0 |
| 4 | `extract_dimensions` | 五维结构化抽取 | **本地（目标）** | + **`dimensions_v1` ✗ 缺** | JSON | **0（省 20.3k）** |
| 5 | `analyze_method` | 方法分析 | 本地 | + **`method_analyzer_v1` ✗ 缺** | text | 0 |
| 6 | `analyze_data` | 数据分析 | 本地 | + **`data_analyzer_v1` ✗ 缺** | text | 0 |
| 7 | `extract_claims` | 论断/证据抽取 | **本地（目标）** | + **`claims_v1` ✗ 缺** | JSON | **0（省 2.9k）** |
| 8 | `critique_analysis` | 批判性判断（大问题） | **云端** | deepseek-chat | text | 1.6k |
| 9 | `finaloutcome` | 综合成文（大问题） | **云端** | deepseek-chat | text | 6.0k |
| | | | | | **云端合计** | **≈7.6k（−75%）** |

**阶段收益**（可分段验收）

| 阶段 | 动作 | 云端 token/次 | 相对现在 |
|---|---|---|---|
| 现在 | — | 30,726 | — |
| 阶段一 | 只搬 `extract_dimensions` | ~10,500 | **−66%** |
| 阶段二 | + `extract_claims` | ~7,600 | −75% |
| 阶段三 | + 本地优先/质量门控升级 | 视升级率 | 视情况 |

**必须给用户的预期**：阶段一之后整轮会**变慢**（本地生成 20~25 tokens/s）。这是刻意的取舍：
用自有 GPU 换云端账单。若某次需要快，切「低延迟模式」配置即可（见 §7）。

---

## 3. 显存预算：8 GB 可行性与热插拔下限

### 3.1 公式

```
显存 = 权重 + KV缓存 + LoRA常驻 + 激活/工作区
KV(FP16) ≈ 层数 × 2 × KV头数 × head_dim × 2B × 序列长度
        Qwen3-4B 约 0.15 MB/token  →  8K≈1.2GB  16K≈2.4GB  32K≈4.7GB
LoRA常驻 = max_loras × 单适配器  （r16 ≈ 126 MB；**活跃 LoRA 数量不额外占显存**）
```

**实测校验**：当前机器 16 GB，vLLM 用 `0.88` 利用率 + 32K 上下文，实际占用 **15,741 MiB**
（≈ 权重 8.0 + KV 4.7 + 其余 3.0）—— 与公式吻合，说明这套估算是可用的。

### 3.2 8 GB 方案对照

| 方案 | 底座 | 上下文 | 权重 | KV | LoRA×1 | 工作区 | 合计 | 结论 |
|---|---|---|---|---|---|---|---|---|
| A | Qwen3-4B **FP16** | 4K | 8.0 | 0.6 | 0.13 | 0.4 | **9.1 GB** | ✗ 权重就超 |
| **B（推荐）** | Qwen3-4B **AWQ/GPTQ 4bit** | **8K** | 2.3 | 1.2 | 0.13 | 0.5 | **4.1 GB** | ✅ 余 ~3.9 GB |
| C | Qwen3-4B 4bit + **FP8 KV** | 16K | 2.3 | 1.2 | 0.13 | 0.6 | **4.2 GB** | ✅ |
| D | Qwen3-1.7B FP16 | 8K | 3.4 | 0.35 | 0.06 | 0.4 | **4.2 GB** | ✅ 能力更弱 |
| E | **热插拔理论下限** | 4K | 2.3 | 0.6 | 0.13 | 0.3 | **≈3.3 GB** | ✅ 下限参考 |

### 3.3 8 GB 推荐启动参数（vLLM）

```bash
--model <Qwen3-4B-AWQ 路径> \
--quantization awq \
--max-model-len 8192 \          # 分块 3000 字符，8K 足够
--kv-cache-dtype fp8 \           # KV 减半（如后端支持）
--enable-lora --max-loras 1 --max-cpu-loras 8 --max-lora-rank 128 \
--gpu-memory-utilization 0.90 \
--enforce-eager \                # 省显存（不建 CUDA 图）
--max-num-seqs 4 \
--enable-prefix-caching
```

**为什么分块设计对 8 GB 特别友好**：抽取节点把长文切成 3000 字符块（`extract` 类型契约自带），
单次请求只需要 3~6K 上下文，**不需要 32K**。这正是"长复杂工作流"能在小显存上跑的原因。

### 3.4 热插拔的真实成本

| 项 | 成本 | 说明 |
|---|---|---|
| 显存 | **1 个适配器**（r16 ≈ 126 MB） | `max-loras 1` + `max-cpu-loras 8`：从 CPU 内存换入 |
| 换入延迟 | 约 0.1~0.3s/次（126 MB 走 PCIe） | 引擎的 `schedule_by_lora()` 已把同 LoRA 节点排在一起，**实测三 LoRA 配置只换 3 次/轮** |
| 已做的加固 | LoRA 预检（不兼容直接拒绝）+ 幂等加载（"已加载"不算失败）+ 检出被换出后自动重载 | 见 HANDOVER §AA 与前序提交 |

> **结论**：动态挂载 LoRA 的最小显存由**底座 + KV**决定，与 LoRA 数量无关。
> 4bit 4B 底座下 **≈3.3 GB 是理论下限，8 GB 有充足余量**（还能开 8~16K 上下文）。

### 3.5 8 GB 的三条路线（按推荐度排序）

| 路线 | 做法 | 显存 | 代价 / 风险 | 前置验证 |
|---|---|---|---|---|
| **① 4bit 推理底座 + 复用现有 LoRA**（推荐先试） | 推理用 Qwen3-4B-AWQ；**训练仍在 16 GB 机用 FP16** | ≈4.1 GB | 需验证 FP16 训练的 LoRA 在量化底座上的质量损失 | **T2**：留出 similarity 与 FP16 底座 0.6054 比，差距 <0.05 即可 |
| ② FP16 **1.7B** 底座 + 重训 LoRA | 8 GB 上跑 FP16 1.7B（≈4.5 GB） | ≈4.5 GB | **现有 4B LoRA 全部作废**（hidden 不同）→ extract_v2 等要按 1.7B 重训；1.7B 能力更弱，抽取质量需重新验收 | 小规模试训一个节点验证可行性 |
| ③ FP16 4B + **CPU offload**（`--cpu-offload-gb`） | 权重分一部分到内存 | 可低于 8 GB | 每 token 走 PCIe，**速度大幅下降**；且 LoRA + offload 的组合未验证 | 启动 + 一次抽取实测 |
| ④ 保持 16 GB | 不动硬件 | 现状 | 不符合 8 GB 目标（用户已允许"做不到就忽略"） | — |

---

## 4. LoRA 缺口清单与训练计划

| LoRA | 服务节点 | 现状 | 训练数据来源 | 建议规模 | rank | 训练机 | 预估 GPU 时间 | 验收门槛 |
|---|---|---|---|---|---|---|---|---|
| `extract_v2` | extract_method | **✅ 已有** | — | 190 条 | r16 | 16 GB | — | 留出 similarity **0.6054**（实测） |
| `extract_v3` | （实验） | ✅ 已有但**未采纳**（持平略差） | 443 条 | r16 | — | — | — | 保留作对照 |
| **`dimensions_v1`** | extract_dimensions | **✗ 缺（最优先）** | **`build_extractive_dataset.py` 的五个 aspect 与它输出一一对应**（method/data/research_question/contribution/conclusion）+ 闭环 `export_dataset` 补真实分布 | 400-600 | r16 | 16 GB | 25-35 min | 五维字段完整率 ≥95%；事实保留 **1.0**；无出处事实 **<2** |
| **`claims_v1`** | extract_claims | ✗ 缺 | 闭环导出（云端输出当教师）+ 少量手写模板 | 300-500 | r16 | 16 GB | 20-30 min | claims 条数不低于云端、事实保留 ≥0.95 |
| **`method_analyzer_v1`** | analyze_method | ✗ 缺 | 闭环导出（现有运行里已有输入/输出对） | 300-500 | r16 | 16 GB | 20-30 min | 抽检质量不降 + **输出长度显著短于裸底座** |
| **`data_analyzer_v1`** | analyze_data | ✗ 缺 | 同上 | 300-500 | r16 | 16 GB | 20-30 min | 同上 |
| `json_struct_v1`（可选） | 所有 JSON 节点 | ✗ 缺 | 以云端输出为教师蒸馏 | 500 | r16 | 16 GB | ~30 min | JSON 解析失败率 → 0（现在偶发失败会触发重试，白花 token） |
| `strict_json` / `ml_engineer` | — | ⚠️ **损坏**（缺 `adapter_config.json`） | 清理或补齐 | — | — | — | 10 min | 从 LoRA 列表消失 |
| `json_extract` / `frontend-ui-architect-lora` | — | ⚠️ 底座不兼容（Qwen2.5-7B / qwen2.5-coder-14b） | 归档 | — | — | — | — | 预检已会自动拒绝 |

**训练约束（用户已实测）**：**训练只能用全精度（FP16）模型，量化模型不行**。因此

- 训练**固定放在 16 GB 机器**（现有机器），本方案**不提"8 GB 上做 QLoRA 训练"**（原 T9 作废）
- LoRA 是**可移植文件**（r16 约 126 MB），训练/推理可以不同机
- ⚠️ **但换底座 = 换 LoRA**：LoRA 与底座的 hidden 维度绑定。若 8 GB 上最终用 1.7B 底座
  （路线②），则**现有面向 Qwen3-4B 的 LoRA（extract_v1/v2/v3、paper_analyzer_v5/v6、
  test_agent_v1、testlora）全部不能在 1.7B 上加载**，5 个新 LoRA 都要按 1.7B 重训。
  这是路线②的**主要成本**，必须在决策前算清楚。

---

## 5. 任务方向表单（可执行）

| ID | 任务 | 依赖 | 产出 | 验收 | 预估 |
|---|---|---|---|---|---|
| **T1** | 8 GB 底座改造：准备 Qwen3-4B-AWQ；写 8 GB 启动脚本（§3.3 参数） | — | 启动脚本 + 空载显存实测 | 空载 <6.5 GB；8K 上下文可跑通一次抽取 | 1-2 h |
| **T2** | **风险闸门：现有 LoRA 能否用在 4bit 推理底座上**（决定 8 GB 走路线①还是②） | T1 | 结论 + 数值 | 留出 similarity 与 FP16 的 0.6054 差距 **<0.05** → 走路线①；否则走路线②（1.7B + 重训） | 2-3 h |
| **T3** | 构造 `dimensions` 数据集（构造器 + 闭环 `export_dataset` 补真实样本） | — | `training_data/dimensions_v1.jsonl` | 逐字违规 **0**；论文级留出不相交 | 1 h |
| **T4** | 训练 + 评测 `dimensions_v1` | T3 | LoRA + 指标 + 台账记录 | 五维完整率 ≥95%、事实保留 1.0、无出处 <2 | 1 h（GPU 30 min） |
| **T5** | `extract_dimensions` 切到本地并验证 token 下降 | T4 | 发布配置改动 + 实测 | 云端 token **30.7k → ~10.5k**；报告质量不降 | 30 min |
| **T6** | 训练 `claims_v1` / `method_analyzer_v1` / `data_analyzer_v1` | T3 模式复用 | 3 个 LoRA | 同 T4 门槛 | 3-4 h |
| **T7** | **本地优先 + 质量门控升级**（引擎特性：本地先跑，质量判据不过才升云端） | T5 | 引擎改动 + 测试 | 质量判据作为升级触发器；升级率可观测 | 2-3 h |
| **T8** | Agent 侧补工具：`get_token_cost`（成本台账）+ `validate_workflow`（跑前预检） | — | 2 个工具 + 提示词 | 自然语言可查 token 成本、可预检再跑 | 1 h |
| ~~T9~~ | ~~8 GB 上的 QLoRA 训练~~ **作废：训练只能用 FP16（用户已实测）** | — | — | — | — |
| **T9'** | 路线②的前置：按 1.7B 底座重训一个节点（如 extract_method）验证质量 | T1 | LoRA + 指标 | 与 4B/extract_v2 的差距可接受（决定路线②是否值得） | 2-3 h || **T10** | 本地节点并发（A4：波次内并行） | 需先做 S2b（`execute_node` 提到模块级） | 引擎重构 | 本地波次耗时下降；**不增加 token** | 3-5 h |

**建议执行顺序**：T1 → **T2（风险闸门）** → T3 → T4 → T5（阶段一收益）→ T7 → T6 → T8 → T10

---

## 6. 优化建议（按"省云端 token"排序）

1. **搬 `extract_dimensions` 到本地**（−66% 云端 token）—— 数据构造器现成，最高性价比，先做。
2. **把 JSON 解析失败率压到 0**：一次失败重试 = 白花一整次云端 token。可选 `json_struct_v1` 或
   强化提示词；`_meta.usage` 里"调用数 > 节点数"就说明有重试，可直接观测。
3. **让 LoRA 调度真正发挥作用**：`schedule_by_lora()` 已实现（实测三 LoRA 只换 3 次），
   新增本地 LoRA 后**不要手工打乱节点顺序**，否则换入换出会把省下的时间还回去。
4. **缓存优先**：同论文重跑命中缓存时抽取只要 0.31s（实测 45s → 0.31s）。
   调参/改下游节点时**不要清缓存**，只有改抽取提示词或换 LoRA 才需要（缓存键已含这两者）。
5. **KV 用 FP8 + 开 prefix caching**：省显存与时间，**不花 token**。
6. **质量门控升级要设阈值**：如果"总是升级"，token 会反弹回云端水平。
   建议先只对**低置信**（质量判据接近阈值）的节点升级，并统计升级率。
7. **8 GB 上的取舍**：上下文 8K 足够（分块 3000 字符）；embedding 模型放 CPU；
   `max-num-seqs` 降到 4（本地并发主要靠分块内的 4 workers，与 seqs 是两回事）。

---

## 7. 低延迟模式（可选，非默认）

`workflow_config_fast_analyze_data.json`：把 `analyze_data` 放云端。
实测 **101.4s → 49.0s（−52%）**，代价 **+约 1.3k 云端 token/次**。
仅在"这次要快"时切用；**默认配置按 token 优先**（该节点在本地）。

---

## 8. Agent 集成：现状与缺口

**已实现（22 个工具，可自然语言调用）**
`run_workflow`（内部轮询）、`get_workflow_status`、`list_workflow_uploads`、
`update_workflow_node`、`add_workflow_node`、`delete_workflow_node`、
`build_dataset`（按节点导出训练数据）、`start_training`、`get_training_status`、`run_eval`、
`get_loras_registry`、`get_quality_summary`、`load_lora`、`unload_lora`、`list_loras`、
`switch_model`、`list_knowledge_bases`、`query_knowledge_base`、`create_knowledge_base`、
`list_available_datasets`、`list_trainings`、`list_available_models`
加上模板画廊（卡片 + 依赖检查 + 一键切换/克隆）。

**缺口（按对目标的贡献排序）**

| 缺口 | 为什么重要 | 做法 |
|---|---|---|
| **`get_token_cost`** | 目标是省 token，但 Agent **看不到 token** | 暴露 `/cost/summary`（已有 `_meta.usage` 数据）+ 工具 |
| **`validate_workflow`** | 跑前预检可避免"跑一半才发现配置错"（浪费 token） | 已有 `/workflow/validate`，加个 Agent 工具即可 |
| `create_workflow_from_description` | "个性化搭建"目前要一个个加节点 | 让 Agent 一次产出整张图（节点+连线），再用 validate 校验 |
| 工具参数 **JSON Schema** | 22 个工具靠自然语言描述参数，偶发漏参 | 每个工具声明参数 schema（名称/类型/必填/枚举） |
| `list_templates` / `apply_template` | 模板画廊在 UI 有、Agent 侧没有 | 补两个工具，实现"用论文分析模板搭一套" |

---

## 9. 风险与不做的事

| 风险 | 影响 | 缓解 |
|---|---|---|
| **4bit 底座 + 现有 LoRA 不兼容/质量掉** | 8 GB 路线不成立 | **T2 作为风险闸门先做**；失败则改 1.7B 底座或保留 16 GB |
| 本地化后整轮变慢（20~25 tokens/s） | 体验下降 | 明确取舍；提供低延迟模式；挂专用 LoRA 后输出变短可抵消一部分 |
| 小底座做不好抽取（质量下降） | 报告变差 | 用**已建好的质量判据**验收（事实保留/无出处/欠抽取），不达标就不切 |
| 质量门控升级失效 | token 反弹 | 记录升级率并设上限（如 ≤20% 的节点升级） |
| 8 GB 上训练 | **不可行（用户实测：训练只能用 FP16）** | 训练固定放 16 GB 机；8 GB 只做推理 |
| 换底座导致 LoRA 全部作废（路线②） | 5 个 LoRA 需按 1.7B 重训 | 决策前先做 T9' 小规模验证，评估质量是否够用 |
| 量化推理底座与 FP16 LoRA 的质量损失（路线①） | 8 GB 路线可能不成立 | **T2 作为风险闸门，先做再投入** |

**明确不做**：为了跑得快而把本地节点搬去云端（见 §7，与项目目标相反）；在 8 GB 上强行用 FP16 4B。
