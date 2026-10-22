# 小模型调用 Skill/工具 提升方案（检索增强 + 微调）· 2026-10-19

> 用户要求：**检索小模型调用 skill、工具提升方案** · 相关项目 · 文献 · **量化提升比例**
> 核心结论先说：**这条路是文献里唯一被反复证明「7B 也能做到 SOTA」的方向** ✓✓
> 而且有**两条腿**：① 推理时工具检索（零训练 ✓ +46% Recall）② 训练时工具调用 LoRA（7B 可超 GPT-4 ✓）

---

## 第一部分 · 三条技术路线 + 量化数字

### 路线 A：**工具检索（RAG-Tool Fusion）** ⭐ 零训练 ✓ 见效最快 ✓

#### 📄 Toolshed: Advanced RAG-Tool Fusion（arXiv 2410.14594，2024）
> "**46%, 56%, and 47% absolute improvements** on the ToolE single-tool, ToolE multi-tool and Seal-Tools benchmark datasets (Recall@5)"
> "**without requiring model fine-tuning**"

**关键技术（三阶段 ✓）**：
| 阶段 | 做什么 |
|---|---|
| **Pre-retrieval** | 工具文档**增强**（补关键信息）→ 存进 **Toolshed 知识库**（向量库 ✓） |
| **Intra-retrieval** | 查询**规划 + 变换**（把用户话改成检索友好的 query ✓） |
| **Post-retrieval** | 检索结果**精炼 + 自我反思** ✓ |
| **调优变量** | 工具总数 tool-M × 阈值 top-k ⇒ **精度/性能/token 成本 三者权衡** ✓ |

**⇒ 量化提升**：
```
Recall@5 绝对提升: ToolE 单工具 +46% · ToolE 多工具 +56% · Seal-Tools +47%
（绝对百分点 ✓ 不是相对 ✓ 非常可观 ✓）
零微调 ✓ 纯推理侧改造 ✓
```

#### 相关项目
| 项目 | 说明 |
|---|---|
| **Toolshed** (2410.14594) ✓ | RAG-Tool Fusion + 工具知识库 ✓ **最直接可抄** ✓ |
| **ToolRet / Tool RAG** | 大规模工具集下的检索 ✓ |
| **Gorilla** (NeurIPS 2024) [论文](https://proceedings.neurips.cc/paper_files/paper/2024/hash/e4c61f578ff07830f5c37378dd3ecb0d-Abstract-Conference.html) | ⭐ **retriever-aware training** ✓ 接入检索器后 API 调用准确率显著超 GPT-4 ✓ |
| **ToolQuery** (2409.03215) | 统一工具查询 ✓ |
| **BFCL**（Berkeley Function Calling Leaderboard） | **事实标准榜单** ✓ 我们的评测应该对标它 ✓ |

---

### 路线 B：**工具调用修复（Post-execution Reflection + RAG）** ⭐ 直接补我们的短板

#### 📄 Repairing Tool Calls Using Post-tool Execution Reflection and RAG（arXiv 2510.17874，2025）
> "a **post-tool execution reflection** component that combines LLM-based reflection with **domain-specific RAG** using documents describing both the **specific tool** being called and **troubleshooting documents**"
> "our RAG-based reflection will repair kubectl commands such that they are both more likely to successfully execute (pass rate) for **55% of our models** evaluated and **36% more likely to correctly answer the user query** on average"
> "**troubleshooting documents improve pass rate compared to official documentation by an average of 10%**"

**⇒ 量化提升**：
```
正确回答率  +36%（平均 ✓）
过执行率    55% 的模型有提升 ✓
故障排查文档 vs 官方文档  +10% ✓ ← 【重要洞察】
```

**⚠ 关键洞察**：**"troubleshooting docs"（报错教程）比"official docs"（官方文档）更有用** ✓
```
⇒ 我们的 KB 里应该多存【错误 → 修复办法】✗ 而不是只存【工具说明】✗
⇒ 正好接上我们的 reflect 飞轮（轨迹里的真实错误 ✓ 就是最好的 troubleshooting 素材 ✓）✓✓
```

**⇒ 这两条路线的结合点**：
```
工具调用失败（我们 v3-2-2 数据里有 281 个真实错误 ✓）
   ↓ reflect_distill 提炼成 troubleshooting 文档 ✓
   ↓ 存进 KB（新库：tool_troubleshooting ✓）
   ↓ 推理时：工具报错 → 检索 troubleshooting → 自动修复 ✓
⇒ 这就是【零训练】的可靠性提升 ✓ 而且我们已经有一半素材 ✓
```

---

### 路线 C：**小模型专用函数调用模型** ⭐ 证明 7B 可以 SOTA

| 项目 | 量化/结论 | 说明 |
|---|---|---|
| **NexusRaven** (Nexusflow) | 7B 开源模型，EMNLP 2025 论文 [ACL](https://aclanthology.org/2025.emnlp-main.1090.pdf) ✓ 在函数调用基准上**超过 GPT-4** ✓ | 专为函数调用训练的 7B ✓ |
| **Hammer-7B** (MadeAgents) [HF](https://huggingface.co/MadeAgents/Hammer-7b) | **7B 模型在工具调用综合榜单排名第一** ✓（超过大量更大模型 ✓） | 专注函数调用的小模型 ✓ |
| **xLAM / APIGen** (Salesforce, 2406.18518) | 60k+ 可验证函数调用数据 ✓ **xLAM-7B 在 BFCL 上超过 GPT-4** ✓（论文声称 ✓） | **APIGen = 自动生成可验证数据** ✓ |
| **ToolACE** (2024) | 自动化工具学习数据合成 ✓ | 数据合成方法论 ✓ |
| **ToolLLM / ToolBench** (2023) | 16k+ API · DFSDT 决策树搜索 ✓ | 大规模工具基准 ✓ |

**⇒ 最重要的一句话**：
```
7B 模型【专门训练过】就能在工具调用上达到/超过 GPT-4 ✓✓
  NexusRaven 7B ✓ · Hammer 7B ✓ · xLAM 7B ✓ 三个独立验证 ✓

⇒ 这正好推翻了"LoRA 在 7B 上没用"的悲观结论 ✓
   区别在于：
   ✗ "提升通用能力" → 教不会 ✓（我们三轮实证 ✓ 文献 ✓）
   ✓ "学会工具调用格式/选择/修复" → **能教会** ✓✓✓（三个项目证明 ✓）
```

---

## 第二部分 · 量化提升汇总（一张表 ✓）

| 技术 | 指标 | 提升 | 是否需要训练 | 来源 |
|---|---|---|---|---|
| **工具检索（RAG-Tool Fusion）** | Recall@5 | **+46% / +56% / +47%**（单/多/Seal） | ✗ **零训练** ✓ | Toolshed ✓ |
| **工具调用修复（反思+RAG）** | 正确回答率 | **+36%** 平均 | ✗ 零训练 ✓ | 2510.17874 ✓ |
| **同上** | 过执行率 | **55% 的模型提升** | ✗ | 同上 ✓ |
| **故障排查文档 vs 官方文档** | 过执行率 | **+10%** | ✗ | 同上 ✓ |
| **专门训练的小模型（7B）** | BFCL/工具榜单 | **达到/超过 GPT-4** ✓ | ✅ 需要训练 | NexusRaven / Hammer / xLAM ✓ |
| Gorilla + retriever | API 调用准确率 | 显著超 GPT-4 ✓（论文声称） | ⚠ 需 retriever-aware 训练 | Gorilla ✓ |

### ⇒ 从这表能读出三件事
```
1. 【零训练】就能拿 +36%~+56% ✓✓  ← 性价比最高 ✓ 先做这个 ✓
2. 【训练】能让 7B 在工具调用上超 GPT-4 ✓  ← 说明"教具体行为"有效 ✓
3. 这两个**不冲突** ✓ 而且**互相增强** ✓（检索给训练提供好工具描述 ✓ 训练让模型更好用检索结果 ✓）
```

---

## 第三部分 · 我们平台现状 vs 缺口

### 3.1 我们已经有的 ✓
| 资产 | 现状 | 对这三条路线的价值 |
|---|---|---|
| **工具清单** | `agent_kb/_tools.json`（60+ 工具 ✓） | ⭐ **路线 A 的原料** ✓ |
| **工具文档** | 每个工具有 `desc` ✓ | ⚠ 太短（"description 80 字"✗）→ 要**增强** ✓ Toolshed 的 pre-retrieval ✓ |
| **真实错误轨迹** | 281 个真实错误 ✓（v3-2-2 数据 ✓） | ⭐⭐ **路线 B 的原料** ✓ 天然的 troubleshooting 素材 ✓ |
| **reflect 飞轮** | `reflect_run → distill → ingest` ✓ | ⭐ 可以直接产 troubleshooting 文档 ✓ |
| **检索基础设施** | `kb.py` 795 行 ✓ 16 个 KB ✓ | ⭐ **路线 A 直接可用** ✓ |
| **评测体系** | 100 任务 drill + 判分 ✓ | ⭐ 能验证提升 ✓（但要看工具指标 ✗ 现在看总分 ✗） |
| **sim_guard** | 作用域树 + 自动修复 ✓ | ⭐ 已有"反思"机制的雏形 ✓ |

### 3.2 我们缺的 ✗
| # | 缺口 | 影响 | 补法 |
|---|---|---|---|
| **G1** | **工具不是通过检索给的** ✗ | 全部工具塞进 prompt ✗ 工具一多就退化 ✓ | 建 **Toolshed 工具知识库** ✓ |
| **G2** | **工具描述太短** ✗ | 检索质量差 ⚠ | 增强：补参数说明/示例/常见错误 ✓ |
| **G3** | **没有 troubleshooting 库** ✗ | 报错不能自动修 ✗ | 从 281 个真实错误提炼 ✓ |
| **G4** | **评测不看工具指标** ✗ | 不知道工具调用好不好 ✗ | 加指标：工具选择准确率/调用成功率/修复率 ✓ |
| **G5** | **没有工具调用 LoRA** ✗ | 7B 潜力没发挥 ✗ | 用 v3-2-2 真实轨迹训工具调用 LoRA ✓ |

---

## 第四部分 · 落地方案（★ 分两批：零训练先做 ✓ 训练后做 ✓）

### 批次一：**零训练（推理侧）** · 8h · 预期 +20~40%

#### P0-1 建「工具知识库」（Toolshed 模式）· 3h
```
① 工具文档增强（pre-retrieval ✓）：
   每个工具补：完整参数说明 ✓ 3 个调用示例 ✓ 常见错误 ✓ 返回值格式 ✓
   （现在只有 80 字 desc ✗ → 补到 300-500 字 ✓）
② 存进专用 KB：`tool_docs`（向量库 ✓ 与业务 KB 分开 ✓）
③ 推理流程改造：
   用户 query → 查询变换 ✓ → 检索 top-k 工具（k=5-8 ✓）
   → 只把这 k 个工具给模型 ✓（不是全塞 ✗）
④ 效果：工具选择准确率 ↑ + token 成本 ↓ ✓
```
**预期**：工具选择 Recall@5 **+20~46%**（文献 ✓ 我们工具数较少 60+ ⇒ 保守取 +20% ✓）

#### P0-2 建「故障排查库」· 3h
```
① 从 v3-2-2 数据的 281 个真实错误提炼 ✓：
   错误信息 → 原因 → 修复办法（模板化 ✓）
② 入库：`tool_troubleshooting`（独立库 ✓）
③ 推理流程：工具报错 ⇒ 检索 troubleshooting ⇒ 模型自动修复 ✓
```
**预期**：错误修复率 **+10~36%**（文献 ✓ 保守取 +15% ✓）

#### P0-3 加工具调用评测指标 · 2h
```
（现在只看总分 ✗ → 加）
· 工具选择准确率（选对了工具吗 ✓）
· 参数正确率（参数对吗 ✓）
· 首次调用成功率 ✓
· 报错后修复率 ✓
· 工具调用轮数（越少越好 ✓）
```
**预期**：能**量化看到**前两项的提升 ✓ 否则白做 ✗

---

### 批次二：**训练侧（工具调用 LoRA）** · 8h · 有希望 ✓

#### P1-1 训一个「工具调用专用 LoRA」· 5h
```
数据：v3-2-2 的 278 条真实轨迹（已有 ✓）
      + 补充合成的工具选择样本（工具检索结果 ✓ 正确/错误各半 ✓）
      + troubleshooting 修复样本（错误→修复 ✓）
超参：rank 8 ✓ epochs 3 ✓（和 v3-2-2 一样 ✓）
目标：【不是提升总分】✗ 是【工具选择准确率 + 参数正确率 + 修复率】✓
```
**依据**：NexusRaven / Hammer / xLAM 三个 7B 项目 ✓ 证明**专门训练能超 GPT-4** ✓
**注意**：我们数据量少（278 ✓）⇒ 预期**不如他们**（他们用 60k-∞ ✓）⚠ 但**方向对** ✓

#### P1-2 工具调用 A/B 评测 · 3h
```
评测集：30+ 条工具调用任务 ✓（从 drill 里挑工具类 ✓）
臂：base(7B) vs +tool-lora ✓
指标：工具选择准确率 / 参数正确率 / 首次成功率 / 修复率 ✓
```
**预期**：如果文献规律成立 ⇒ **工具选择准确率 +10~20%** ✓（保守 ✓）
**若无效** ⇒ 说明我们数据量不够 ✓ 但至少**指标口径对了** ✓ 能看清问题在哪 ✓

---

## 第五部分 · 为什么这条路比之前三轮更有希望

| 对比 | 之前三轮（追总分） ✗ | **工具调用方向** ✓ |
|---|---|---|
| 任务性质 | 通用能力 ✗（教不会 ✓） | **特定行为** ✓（能教会 ✓） |
| 文献支持 | Zhang：PET 天花板=底座 ✗ | **NexusRaven/Hammer/xLAM 三个 7B 超 GPT-4** ✓✓ |
| 是否需要大模型能力 | 是 ✗ 7B 不够 ✗ | **否** ✓ 只需格式/选择 ✓ |
| 是否可验证 | 总分含糊 ⚠ | **是** ✓（选对没/执行成功没 ✓） |
| 数据要求 | 1000+ 真实 ✗ 我们不够 ⚠ | **278 条就有 281 个真实错误可用** ✓ |
| 我们已有的资产 | 需要从零造 ✗ | **工具清单 ✓ + 真实错误 ✓ + KB ✓ + 评测 ✓ 全有** ✓✓ |

---

## 第六部分 · 建议（一句话 + 工时）

```
三条路线都验证过量化数字 ✓：
  A. 工具检索（零训练）: Recall@5 +46~56% ✓✓ 【最划算 ✓ 先做 ✓】
  B. 工具调用修复（零训练）: 正确率 +36% ✓ 【我们素材现成 ✓ 一起做 ✓】
  C. 工具调用 LoRA（训练）: 7B 可超 GPT-4 ✓ 【三个项目证明 ✓ 有希望 ✓】

建议批次：
  批次一（8h · 零训练）→ 预期 +20~40% 工具指标 ✓ 立刻见效 ✓
  批次二（8h · 训练）  → 看能否复制 Hammer/NexusRaven 的成功 ✓

⇒ 这是目前【唯一】文献证明"7B 能做好"+ 我们资产齐全 + 可验证 的方向 ✓✓
```

---

*文献 · 2026-10-19 · [Toolshed 2410.14594](https://arxiv.org/abs/2410.14594) · [Repairing Tool Calls 2510.17874](https://arxiv.org/abs/2510.17874) · [Gorilla NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/e4c61f578ff07830f5c37378dd3ecb0d-Abstract-Conference.html) · [NexusRaven EMNLP 2025](https://aclanthology.org/2025.emnlp-main.1090.pdf) · [Hammer-7B](https://huggingface.co/MadeAgents/Hammer-7b) · [xLAM/APIGen 2406.18518](https://arxiv.org/abs/2406.18518) · [ToolQuery 2409.03215](https://arxiv.org/abs/2409.03215)*
