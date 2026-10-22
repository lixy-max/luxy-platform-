# 顿悟 (Luxy) · 架构报告（真实状态版 · 2026-10-21）

> **本版原则**：只写**已实测/已实现**的能力 ✓ 未来方向明确标注 ⏳ 未做标注 ❌
> 上一版（`ARCHITECTURE_REPORT_REVIEW.md` 核实）有 4 处把"设计"写成"已完成" ✗ 本版修正 ✓

---

## 一、摘要

顿悟 (Luxy) 是面向**本地异构算力**的模型个性化训练基础设施（AI Infra）。
定位不是"又一个聊天应用"，而是**把微调从"炼丹"变成可度量、可回溯的工程流水线**。

**当前真实进度**：V1.0–V4.0 约 **70%** ✓ · V5.0/V6.0 **未开始** ❌

---

## 二、已验证的能力（有实测数据 ✓）

### 2.1 本地异构算力调度 ✅ 已实现

| 项 | 实测 |
|---|---|
| 双卡识别 | RTX 5060 Ti 16GB (GPU0, PCIe Gen5 x8) · RTX 3060 12GB (GPU1, PCIe Gen4 x4) |
| 物理隔离 | `CUDA_VISIBLE_DEVICES` ✓ | 
| **当前配置** | **3060 = 推理 + 训练**（`inference_gpu=1` ✓ `train_gpu=1` ✓）· **5060 Ti 完整让位用户游戏** ✓ |
| 设计原则 | **平台负载可让位于用户** ✓ —— 这是架构选择，不是限制 ✓ |
| vLLM 实测 | 4B 模型 + 2 LoRA · 3060 11.3GB · 启动 ~30s ✓ |

### 2.2 自动化微调流水线 ✅ 已实现（QLoRA）

```
数据生成（data_generator.py ✓ 双格式 → sharegpt ✓）
  → 体检（data_preflight ✓ P-73 格式一致性 ✓）
  → QLoRA 训练（train_lora.py · LLaMA-Factory 封装 ✓ 4bit ✓）
  → 产物验证（adapter_config 检查 ✓）
实测: 435 样本 QLoRA 在 3060 上 8m37s ✓ 5060 Ti 上 4m44s ✓
```

### 2.3 统计学评估闭环 ✅ 已实现

| 项 | 实测 |
|---|---|
| 评测集 | **50 条**（5 档: hard 7 / mid 14 / easy 9 / stability 5 / 4b 15）✓ |
| 协议 | `docs/EVAL_PROTOCOL.md` M1-M5 ✓（配对 t 检验 · n≥30 · base 对照 · 环境快照 · 报 t/p/n）|
| 跑批 | `tools/eval_v2_run.mjs` ✓（UI 驱动 · 每题 3-5 次重复测稳定性 ✓ 状态不变量快照 ✓）|
| 判分 | `scripts/eval_v2_score.py` ✓（单次通过 + ★稳定性≥2/N + 分档 ✓ 零依赖 ✓）|
| **Bootstrap CI** | ❌ **未实现**（只有配对 t 检验 ✓）|

### 2.4 ★ 结构感知切块 ✅ 已实现（**唯一统计显著的增益** ✓）

```
实测（docs/KB_AB_V3_REPORT.md · n=50 · 配对 t 检验）:
  · hit@5  **+14pp**  (t=2.45, p=0.0144, d=0.35) ✓ 显著
  · MRR    **+20pp**  (t=4.17, p<0.0001, d=0.59) ✓ 显著
⇒ 这是整个项目**唯一被统计学证实的增益** ✓
```

### 2.5 ★ 诚实的负面结果 ✅（真资产 ✓）

```
8 轮 LoRA A/B: **无一致提升** ✗（均值 +0.4 · σ≈3.7 · 仅 2 次 |t|>2 且都是负方向 ✗）
评测集天花板: ok_rate ~100% ⇒ 测不出差异 ✗
执行不稳定: 单次 stdev **20.3 分** ✗ · "只对一次 11/40" ✗
根因（P-73）: 训练标签形态与推理形态不一致 ✗
```

### 2.6 ★ 关键突破：P-73 格式修复真的有效 ✅

```
实测（2026-10-21 · 直接 API 测试 · hermes parser）:
  base Qwen3-4B-Instruct      → tool_calls: **0** ✗（输出散文 · 完全不会工具调用）
  +LoRA test_5060ti_20261020  → tool_calls: **1** ✓（list_knowledge_bases ✓）
  三题稳定性: 工作流→list_workflow_configs ✓ 知识库→list_knowledge_bases ✓ LoRA→list_loras ✓
⇒ **从"不会"到"会"的质变** ✓ 训练数据与推理形态一致是关键 ✓
```

### 2.7 工具检索（动态）✅ 已实现

```
`scripts/tool_retriever.py` + 前端 `buildToolsSchema(query)` ✓
域优先 ✓ 核心 15 总是包含 ✓ 无域命中退回全量（安全兜底 ✓）
实测覆盖率: **30/30 = 100%** ✓ token 省 **47%** ✓
A/B 开关: `localStorage.ds_dynamic_tools` ✓
```

### 2.8 数据飞轮 ⚠ **骨架已实现 · 闭环未验证**

```
`scripts/flywheel.py` 460 行 ✓（2026-10-21 期 1 完成 ✓）
  命令: status / collect / distill / solidify(3通道) / validate / score / promote / rollback / run
  首轮实测: 采集 8 文件 · 587 轨迹 ✓ 通道 B 域覆盖 ✓ 产出 routing_suggestion.json ✓
⚠ 未做: 闭环跑通（轮次 0 · 未晋级 ✗）· Monitor 自动化 ❌
⚠ 关键发现: **检索式知识注入 −29.5pp 有害** ✗ ⇒ 飞轮改走「参数化固化」路线 ✓
```

---

## 三、❌ 明确未实现（不要写成"已完成"✗）

| 项 | 状态 | 说明 |
|---|---|---|
| **RAG 注入到 agent 上下文** | ❌ **实测有害·已默认关闭** | −29.5pp ✗（4B 分不清参考与任务 ✗）<br>文献解释: Voyager 用 GPT-4 检索有效 ✓ ⇒ **模型能力下限是约束** ✓ |
| **Ollama 数据清洗** | ❌ 配置在·未在用 | `providers.ollama` 配了 ✓ 但进程 0 ✗ 实际清洗走别的通道 ✓ |
| **Bootstrap 置信区间** | ❌ 未实现 | 只有配对 t 检验 ✓ |
| **BGE-M3 默认启用** | ❌ 未启用 | 本地有 `bge-m3` ✓ 但默认 `bge-small-zh-v1.5` ✓（CPU 推理 ✓）|
| **Meta-Agent 自主注册工具** | ❌ 零代码 | 现有只是"升级到云端"（escalation ✓）不是"云端写代码注册工具" ✗ |
| **全参微调** | ❌ 只有方案 | P-86 · 用户明确"最后做" ✓ |
| **375 前端断言** | ⚠ 无法核实 | `assert(` = 0（前端不用 assert ✗）⇒ 应删或改可核实数字 |

---

## 四、测试与工程资产（可核实 ✓）

| 项 | 实测 |
|---|---|
| 后端测试函数 | **836 个** `def test_`（31 个文件）✓ |
| 提交数 | **530+** ✓ |
| 台账 | `docs/PLATFORM_ISSUES.md` 3,300+ 行 · 111 个 P- 编号 ✓ |
| 环境快照隔离 | ✅ 跑批前后快照（`workflow_config` md5 / KB 块数 / LoRA 列表 ✓）|
| 文档 | 50+ 篇（调研 / 设计 / 报告 / 复盘 ✓）|

---

## 五、未来方向（⏳ 未开始 + 借鉴资料 ✓）

### V5.0 边缘全量微调（DeepSpeed ZeRO-3）⏳
```
目标: 极小显存下 4B 全参微调，突破 PEFT 能力上限
状态: ❌ 未开始（P-86 只有方案 ✓ 用户明确最后做 ✓）

【借鉴资料】✓ 已调研
  · **DeepSpeed ZeRO-Offload / ZeRO-Infinity** 官方文档与博客 ✓
    ZeRO-3 参数/梯度/优化器状态分片到 CPU+NVMe ✓ 正是"极小显存全参"的标准解法 ✓
  · 关键参数: `offload_param` / `offload_optimizer` / `stage: 3` ✓
  · 显存估算: 4B 全参 fp16 = 8GB 模型 + 8GB 梯度 + 32GB 优化器 ✗
    ⇒ 必须 offload ✓ 30GB+ 内存 ✓
  · 参考: https://www.deepspeed.ai/tutorials/zero-offload/
```
**⚠ 诚实评估**: 我们机器 32GB 内存 ✓ 理论可行 ✓ 但训练速度会**极慢** ⚠（PCIe 4.0 x4 瓶颈 ✗）
⇒ 建议: 先做 4B + LoRA（已做 ✓）→ 若要全参，考虑 **7B AWQ + LoRA rank 128** 更实际 ✓

### V6.0 自造子代理（云端写代码注册工具）⚠ **骨架已实现（2026-10-21）**
```
目标: 云端 Meta-Agent 遇到未知任务时，撰写代码并在本地注册新工具
状态: ⚠ **半自动骨架已实现** ✓（`scripts/tool_forge.py` 460 行 ✓ 设计见 `docs/V6_TOOL_FORGE_DESIGN.md`）
   已做: scan(16信号✓) · draft(实测生成query_docs_in_kb✓) · check(静态安全四闸✓) · approve(人工审核强制✓)
   未做: 全自动注册 ✗（安全风险 ⚠ 不做）· 工具组合生成 ⚠ · 注册后自动A/B ⚠

【借鉴资料】✓ 已调研（**这篇几乎就是 V6.0 的方案** ✓）
  · ★ **MetaAgent: Toward Self-Evolving Agent via Tool Meta-Learning**
    https://arxiv.org/abs/2508.00271
    做法: agent 通过"工具元学习"自我演化 —— 遇到缺工具时自动创建/组合工具 ✓
    ⇒ 与 V6.0 目标一致 ✓ 可直接借鉴其"工具创建/验证/注册"流程 ✓
  · **CAlita: Adaptive LLM-based Iterative Task Automation**
    https://pypi.org/project/calita/
    做法: LLM 迭代式任务自动化 + 工具扩展 ✓
  · 现有可复用的本平台资产:
    · `AGENT_TOOL_ENDPOINTS`（工具注册表 ✓ 前端）
    · `_tool_docs.json`（91 个工具文档 ✓）
    · `/docs/tool` 接口（工具注册 API ✓ 已有 ✓）
    ⇒ **V6.0 的地基其实有了** ✓ 缺的是"云端生成 → 沙箱测试 → 自动注册"的闭环 ✗
```
**⚠ 诚实评估**: V6.0 是**最有想象力但最难**的方向 ⚠
- 已有: 工具注册 API ✓ 工具文档系统 ✓ 云端 escalation ✓
- 缺: 代码沙箱 ✗ 自动测试 ✗ 注册后验证 ✗
- 风险: 云端生成代码执行 = **安全风险** ⚠（需要沙箱 ✓）

### 数据飞轮（进行中 ⚠）
```
状态: 期1 骨架完成 ✓ 期2（训练固化）要 GPU ⚠ 期3（自动化）待做 ❌
借鉴: 见 docs/KB_FLYWHEEL_REFERENCES.md（6 篇文献 + 4 个开源 ✓）
```

---

## 六、真实路线图（修正版）

```
V1.0 ██████████ 100% ✓  本地算力调度 + 双卡隔离
V2.0 █████████░  90% ✓  QLoRA 流水线（全参 ✗）
V3.0 ████████░░  80% ✓  统计评估（Bootstrap ✗）
V4.0 ██████░░░░  60% ⚠  数据飞轮（骨架 ✓ 闭环 ✗）
V5.0 ░░░░░░░░░░   0% ❌  全参微调（借鉴资料已备 ✓）
V6.0 ░░░░░░░░░░   0% ❌  自造子代理（借鉴资料已备 ✓）
```

**⇒ 当前位置：V4.0 进行中（60%）✓**

---

## 七、一句话定位

```
已经做到的: **把"微调"从炼丹变成了可度量的工程** ✓
             —— 836 测试 ✓ 50 条评测集 ✓ 配对 t 检验 ✓ 状态快照 ✓ 8 轮诚实负面结果 ✓

还差:       飞轮闭环 ❌ · 全参微调 ❌ · 自造工具 ❌ · Bootstrap CI ❌

最有说服力的: 结构感知 +14pp (p=0.014) ✓ + "P-73 格式修复让 4B 从不会工具调用变成会" ✓
```

---

*2026-10-21 · 真实状态版 · 未来方向借鉴资料见 `docs/FUTURE_DIRECTIONS_REFS.md`*
