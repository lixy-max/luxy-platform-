# 知识库飞轮 · 相关文献与开源项目（2026-10-21）

> **目的**：验证我们的飞轮方案（`docs/KB_FLYWHEEL_PLAN.md`）有没有前人做过，
> 能借什么机制、能避开什么坑。**原则：借机制，不引框架**（`docs/OPEN_SOURCE_REFERENCE.md` 同一原则 ✓）

---

## 一、★ 最相关的 4 篇（几乎就是我们方案的另一版）

### 1. SCAFFOLD: Self-Improving Web Agents via Recursive Parametric Skill Abstraction
🔗 https://export.arxiv.org/pdf/2609.05511

**它做什么**：Web agent 从执行轨迹里**递归抽象出技能**，然后把技能**固化进参数**（parametric），而不是每次检索注入。

**和我们的关系**：**几乎完全一致** ✓✓
```
它的"递归抽象"     ≈ 我们的 skills_extractor（轨迹→做法 ✓ T-1）
它的"parametric"    ≈ 我们的**通道 A**（训练数据固化 ✓）
它的"self-improving"≈ 我们的飞轮闭环 ✓
```
**★ 关键印证**：它明确选 **parametric（参数化）** 而非 **retrieval（检索）** ✗
⇒ 和我们实测的"检索注入 −29.5pp 有害 ✗"**方向一致** ✓✓
⇒ **可借鉴**：递归抽象（技能可再抽象 ✓ 我们目前只做一层 ✗）

### 2. Adaptive Data Flywheel: Applying MAPE Control Loops to AI Agent Improvement
🔗 https://aclanthology.org/2026.eacl-industry.33/

**它做什么**：用 **MAPE 控制环**（Monitor-Analyze-Plan-Execute）驱动数据飞轮，让 agent 持续改进。

**和我们的关系**：**给了飞轮的工程骨架** ✓
```
Monitor  ≈ 我们的"采集 + 指标监控" ✓
Analyze  ≈ 我们的"蒸馏 + 诊断" ✓
Plan     ≈ 我们的"固化方案选择" ✓
Execute  ≈ 我们的"固化 + 验证 + 上线/回滚" ✓
```
**★ 可借鉴**：**MAPE 环**把我们的"4 阶段"做成可监控的控制环 ✓
（我们缺的是 **Monitor 的自动化 ✓** —— 现在靠手工跑 A/B ✗）

### 3. Reinforcement Learning for Self-Improving Agent with Skill Library
🔗 https://huggingface.co/papers/2512.17102

**它做什么**：agent 有**技能库**，用 **RL** 决定何时新增/复用技能。

**和我们的关系**：**给了技能库的准入/淘汰机制** ✓
```
它的"何时新增技能" ≈ 我们的"质量闸门 ✓"
它的"复用决策"     ≈ 我们的"通道 B 工具路由 ✓"
```
**★ 可借鉴**：技能的**准入/淘汰**可以用策略学习 ✓
（我们目前是**规则闸门** ✓ 简单但可解释 ✓ 适合先跑起来 ✓）

### 4. A Comprehensive Survey on Agent Skills: Taxonomy, Techniques, and Applications
🔗 https://ar5iv.labs.arxiv.org/html/2605.07358

**它做什么**：agent 技能的**分类学 + 技术综述**。

**和我们的关系**：**给了分类框架 ✓ 帮我们定位**
```
可借鉴的分类维度：技能来源（自生成/人工）· 形式（参数/检索/代码）· 生命周期（生成/验证/淘汰）
⇒ 我们的做法结构（task_type/trigger/steps/tools_ok/pitfalls）**符合其主流形态** ✓
```

---

## 二、防遗忘 / 增量学习（对应我们的"回滚闸门"✓）

### 5. LoRA Learns Less and Forgets Less
🔗 检索: `LoRA Learns Less and Forgets Less arXiv 2405.09673`

**结论**：LoRA 相比全参微调，**学得少但忘得少** ✓

**和我们的关系**：**支持我们选 LoRA 做固化** ✓
```
⇒ 通道 A 用 LoRA ✓ 而不是全参（T-7 P-86 是"最后做"✓ 有道理 ✓）
⇒ 也解释了为什么 8 轮 LoRA A/B 收益微弱 ✗（学得少 ✓）
```
**★ 可借鉴**：**持续学习用 LoRA ✓**；要突破上限再考虑全参 ✓（对应 T-7 ✓）

### 6. DESIRE: Dynamic Knowledge Consolidation for Rehearsal-Free Continual Learning
🔗 https://huggingface.co/papers/2411.19154

**它做什么**：**无回放**的持续学习 —— 知识动态整合，不存旧数据。

**和我们的关系**：**对应我们的"知识版本化 + 防膨胀"** ✓
```
它避免重放旧数据 ⇒ 我们也避免"KB 无限膨胀"✓（上限 500 条 ✓）
```
**★ 可借鉴**：知识**整合/压缩**机制 ✓（我们的 22 条做法可以再合并成更泛化的 ✓）

### 7. PILoRA: Prototype Guided Incremental LoRA
🔗 https://dlnext.acm.org/doi/10.1007/978-3-031-73650-6_9

**它做什么**：**增量 LoRA** —— 每轮新知识训一个 LoRA，用原型引导防止互相覆盖。

**和我们的关系**：**对应飞轮的"每轮产出新 LoRA"** ✓
```
我们的方案: 每轮训练新 LoRA → A/B → 通过则替换 ✓
它的问题: 替换会遗忘旧能力 ✗ ⇒ 增量叠加 ✓
```
**★ 可借鉴**：**不替换、而是增量合并**（多 LoRA 叠加 ✓ vLLM 已支持 `--max-loras 4` ✓✓）
⇒ **这是一个具体可落地的改进** ✓（我们目前是"替换"✗ 应改"增量"✓）

---

## 三、我们方案里已引用的（T-1 阶段就用过 ✓）

| 文献 | 用在哪 | 状态 |
|---|---|---|
| **AWM** (Agent Workflow Memory, arXiv:2409.07429) | T-1 抽象化：把具体轨迹抽象成可复用做法 ✓ | ✅ 已用 |
| **Mem0** (arXiv:2504.19413) | 记忆提取/更新 ✓ | ✅ 已用 |
| **From Knowledge to Noise** (REALM 2025 反面) | 检索噪声 ✗ | ✅ 已用 |
| **Reflexion** (arXiv:2303.11366) | 自我反思 ✓ | ⚠ 可借鉴（我们没做"反思"环节 ✗）|
| **ExpeL** (arXiv:2308.10144) | 经验池 ✓ | ⚠ 可借鉴 |
| **Voyager** (arXiv:2305.16291) | 技能库 + 按 embedding 检索 ✓ | ⚠ **反面教材 ✓**（见下）|

### ⚠ ★ Voyager 的重要教训（我们踩的坑文献里写清了 ✓）

**Voyager**（Minecraft agent）的核心是**技能库 + 按 embedding 相似度检索技能** ✓ 效果极好 ✓

**但**：它用的是 **GPT-4 级模型** ✓
```
Voyager: 检索到技能 → GPT-4 能判断"这个技能是不是我要的" ✓ → 用对 ✓
我们:    检索到片段 → **4B 判断不了** ✗ → 被带偏 ✗（−29.5pp ✗）
```
⇒ **★ 这是本次最有价值的文献印证** ✓✓
```
"检索注入"不是错 ✗ 是【有模型能力下限】✓
· GPT-4 级: 检索有效 ✓（Voyager）
· 4B 级:   检索有害 ✗（我们实测 −29.5pp ✗）
⇒ **我们的结论不是"KB 无用"** ✓ 是"**4B 上 KB 不可用**" ✓
⇒ **换 7B+ 后必须重测** ⚠（这是 T-3 报告里"遗留"项的正确解释 ✓）
```

---

## 四、开源项目（可借机制的）

| 项目 | 借什么 | 和我们的关系 |
|---|---|---|
| **DSPy** (stanfordnlp/dspy) | **声明式提示优化**（把 prompt 当参数优化 ✓）| 通道 C 的理论基础 ✓ |
| **TextGrad** (zou-group/textgrad) | 用**文本梯度**优化提示/输出 ✓ | 通道 C 可自动化 ✓ |
| **ProTeGi** (microsoft/LMOps) | 提示自动搜索 ✓ | 通道 C 实现参考 ✓ |
| **Letta / MemGPT** (letta-ai/letta) | 分层记忆管理 ✓ | 对应我们的"知识版本化" ✓ |
| **RAGAS** (explodinggradients/ragas) | **RAG 评测**（忠实度/相关性 ✓）| 补我们的"检索质量"评测 ✓ |
| **τ-bench / AgentBench** | **轨迹级 agent 评测** ✓ | 补我们的"过程"评测 ✓ |
| **AutoGen / CrewAI / MetaGPT** | 多 agent 编排 | ✗ 不借（用户原则: 借机制不引框架 ✗）|
| **LangChain / LlamaIndex** | 全栈框架 | ✗ 不借 ✗（同上）|
| **GraphRAG / LightRAG** | 知识图谱增强检索 | ⚠ 不合适（我们还是"无检索"路线 ✗）|

**★ 最值得借的 3 个**：
```
1. **DSPy/TextGrad** → 通道 C（系统提示固化）自动优化 ✓ 不用手工调 ✓
2. **PILoRA 的增量思想** → 通道 A 改"增量 LoRA 叠加"✓ 不替换 ✓ 防遗忘 ✓
3. **RAGAS 的评测维度** → 补我们的检索质量指标 ✓（虽然我们暂不做检索 ✓ 但 7B 后要用 ✓）
```

---

## 五、我们的方案 vs 文献：三个差异（要诚实面对 ✓）

| 项 | 文献主流 | 我们 | 差异原因 |
|---|---|---|---|
| **固化形态** | SCAFFOLD/Voyager: 参数化 + 检索混合 ✓ | **只参数化**（通道 A）✗ | 4B 上检索有害 ✗ 已实测 ✓ |
| **模型能力** | GPT-4 级 ✓ | 4B ✓ | **能力下限是我们的最大约束** ✗ |
| **验证自动化** | MAPE 控制环 ✓ | **手工 A/B** ✗ | 工程欠债 ✓ 期 3 补 ✓ |

### ★ 我们的两个"别人没做"的点（可能是贡献 ✓）

```
1. **量化了"检索注入"的负面效应** ✓
   −29.5pp（base −34pp / LoRA −25pp）✓ 系统性 ✗
   文献多说"检索有效"✓（但都在强模型上 ✗）
   ⇒ 我们的 n=5×4臂 数据是小模型上的**反面实证** ✓

2. **"4B 无收益区间"的三层事实** ✓
   极简 87%≈93% ✗ · 中等 67%vs63% ✗ · 复杂 都 0% ✗
   ⇒ 小模型的 LoRA 收益**没有可测区间** ✗ ← 这是工程上很重要的边界知识 ✓
```

---

## 六、对方案的具体修改建议（基于文献 ✓）

| # | 改什么 | 依据 | 优先级 |
|---|---|---|---|
| **1** | 通道 A 改 **增量 LoRA 叠加**（不替换 ✗）| PILoRA ✓ + LoRA 少遗忘 ✓ + vLLM 支持 max-loras 4 ✓ | **高** ★ |
| **2** | 加 **MAPE 控制环**（Monitor/Analyze/Plan/Execute ✓）| Adaptive Data Flywheel ✓ | **高** ★ |
| **3** | 加 **递归抽象**（做法可再抽象成元做法 ✓）| SCAFFOLD ✓ | 中 |
| **4** | 通道 C 用 **DSPy/TextGrad** 自动优化（不手工 ✗）| DSPy/TextGrad ✓ | 中 |
| **5** | 加 **Reflexion/ExpeL 式"反思"环节**（从失败中提炼 ✓）| Reflexion/ExpeL ✓ | 中 |
| **6** | 评测补 **RAGAS 维度**（7B 后开检索时用 ✓）| RAGAS ✓ | 低（现在不做检索 ✓）|
| **7** | 技能分类对齐 **Survey 框架**（便于对比 ✓）| Survey 2605.07358 ✓ | 低 |

---

## 七、结论

```
✅ **我们的方案方向正确** ✓
   · 参数化固化（SCAFFOLD ✓ 同路线 ✓）
   · 飞轮闭环（MAPE ✓ 同思路 ✓）
   · 质量闸门（RL 技能库 ✓ 同需求 ✓）
   · LoRA 而非全参（少遗忘 ✓ 有文献支持 ✓）

✅ **我们的实测发现被文献印证** ✓
   · 检索注入在弱模型上有害 ✗（Voyager 用 GPT-4 才有效 ✓）
   · 这解释了为什么"4B + KB"做不起来 ✓

⚠ **我们的工程欠账**（文献提醒 ✗）
   · 验证手工 ✗ 要自动化（MAPE ✓）
   · 固化是"替换"✗ 要"增量"✓（PILoRA ✓）
   · 只做一层抽象 ✗ 可递归 ✓（SCAFFOLD ✓）

⚠ **我们的根本约束**（文献也有 ✓）
   · 模型能力下限 ✗（4B 不足 ✓ ⇒ 7B/更大是唯一出路 ✓）
   · 这也是 T-2/T-3/T-4B 全部结论的底层解释 ✓
```

---

## 八、参考链接汇总

**文献**
1. SCAFFOLD: Recursive Parametric Skill Abstraction — https://export.arxiv.org/pdf/2609.05511
2. Adaptive Data Flywheel: MAPE Control Loops — https://aclanthology.org/2026.eacl-industry.33/
3. RL for Self-Improving Agent with Skill Library — https://huggingface.co/papers/2512.17102
4. Survey on Agent Skills — https://ar5iv.labs.arxiv.org/html/2605.07358
5. DESIRE: Dynamic Knowledge Consolidation — https://huggingface.co/papers/2411.19154
6. PILoRA: Prototype Guided Incremental LoRA — https://dlnext.acm.org/doi/10.1007/978-3-031-73650-6_9
7. AWM (arXiv:2409.07429) · Mem0 (arXiv:2504.19413) · Reflexion (arXiv:2303.11366) · ExpeL (arXiv:2308.10144) · Voyager (arXiv:2305.16291) · LoRA Learns Less and Forgets Less (arXiv:2405.09673)

**开源**
- DSPy — https://github.com/stanfordnlp/dspy
- TextGrad — https://github.com/zou-group/textgrad
- Letta/MemGPT — https://github.com/letta-ai/letta
- RAGAS — https://github.com/explodinggradients/ragas

---

*2026-10-21 · 文献调研 · 结论: **方向与 SCAFFOLD/MAPE 一致 ✓** · 我们的"检索有害"发现被 Voyager(强模型) vs 我们(4B) 的对比解释 ✓ · 三个可落地改进: 增量LoRA / MAPE自动监控 / 递归抽象*
