# 开源项目与文献详细参考 · 2026-10-21

> **用途**：本平台技术选型依据 + "学习增强"改造参考 + 后续扩展的备选池
> **原则**：**只借机制，不引框架** ✓（本项目零重依赖策略 ✓ 见 §七）
> **标注约定**：✅ 已核实 · ⚠️ 待核实 · 🔗 官网/仓库链接
> **相关文档**：`docs/LEARN_AUG_PLAN.md`（方案）· `docs/LEARN_AUG_STATUS_2026-10-21.md`（现状）

---

## 目录

- [一、Agent 记忆 / 经验学习](#一agent-记忆--经验学习与本项目学习增强最相关)
- [二、RAG / 检索生态](#二rag--检索生态)
- [三、LoRA 训练生态](#三lora-训练生态)
- [四、评测基准与工具](#四评测基准与工具)
- [五、文献清单](#五文献清单)
- [六、与本项目的映射](#六与本项目的映射)
- [七、选型结论：为什么我们不引框架](#七选型结论为什么我们不引框架)
- [八、待核实清单](#八待核实清单)

---

# 一、Agent 记忆 / 经验学习（与本项目"学习增强"最相关）

## 1.1 核心项目总览

| # | 项目 | 定位 | 核心机制 | 公开实测 | 与我们相关度 |
|---|---|---|---|---|---|
| 1 | [**AWM**](https://github.com/zorazrw/agent-workflow-memory) | 工作流记忆 | **把具体上下文抽象掉，归纳可复用 workflow** | **WebArena SOTA 35.6%** ✅ | ⭐⭐⭐ |
| 2 | [**ExpeL**](https://github.com/LeapLabTHU/ExpeL) | 经验学习 | 从轨迹提炼 insight，推理时召回，**不改权重** | AAAI 2024 ✅ | ⭐⭐⭐ |
| 3 | [**Mem0**](https://github.com/mem0ai/mem0) | 通用记忆层 | **ADD-only 抽取** · **agent 确认动作 first-class** · 实体链接 · 多信号检索 | LoCoMo **92.5** · LongMemEval **94.4** ✅ | ⭐⭐⭐ |
| 4 | [**Letta / MemGPT**](https://github.com/letta-ai/letta) | OS 式记忆 | 分层（core/archival/recall）· 模型**自编辑记忆** | — | ⭐⭐ |
| 5 | [**Graphiti / Zep**](https://github.com/getzep/graphiti) | 时序知识图 | **bi-temporal**（事实何时为真 + 何时记录） | DMR 94.8% vs MemGPT 93.4% ✅ | ⭐⭐ |
| 6 | [**Reflexion**](https://github.com/noahshinn/reflexion) | 语言化强化 | 失败 → **文字反思** → 重试 | — | ⭐⭐ |
| 7 | [**Voyager**](https://github.com/MineDojo/Voyager) | 技能库 | 成功技能**代码化**入库 · 可组合 | — | ⭐⭐ |
| 8 | [**A-MEM**](https://github.com/agiresearch/A-mem) | 记忆笔记 | Zettelkasten 式 · **记忆自动建立关联** | NeurIPS 2025 ✅ | ⭐ |
| 9 | [**Cognee**](https://github.com/topoteretes/cognee) | 记忆图 | ECL 管道（extract-cognify-load） | — | ⭐ |
| 10 | [**MemoryOS**](https://github.com/BAI-LAB/MemoryOS) | 记忆操作系统 | 短期/中期/长期三级 · 热度淘汰 | arXiv 2506.06326 ✅ | ⭐ |
| 11 | [**MemOS**](https://github.com/MemTensor/MemOS) | 记忆 OS | 记忆作为**可调度资源**（MemCube） | — | ⭐ |
| 12 | [**LangMem**](https://github.com/langchain-ai/langmem) | 记忆 SDK | LangChain 官方 · 热路径/后台提取 | — | ⭐ |
| 13 | [**Memary**](https://github.com/kingjulio8238/Memary) | 记忆层 | 知识图谱 + 记忆流 | — | ⚪ |
| 14 | [**Generative Agents**](https://github.com/joonspk-research/generative_agents) | 记忆流+反思 | 记忆流 → 反思 → 计划 | — | ⭐ 原型 |

---

## 1.2 ⭐⭐⭐ AWM（Agent Workflow Memory）— **最值得借鉴**

**链接**：[github.com/zorazrw/agent-workflow-memory](https://github.com/zorazrw/agent-workflow-memory) · [arXiv:2409.07429](https://arxiv.org/abs/2409.07429) · ICML 2025 ✅

### 核心思想（README 原文）
> "A workflow is usually a **common sub-routine** in solving tasks, **with example-specific contexts being abstracted out**."

### 两种模式
| 模式 | 何时用 | 数据来源 |
|---|---|---|
| **offline** | 有标注样本 | 从 ground-truth 例子归纳 |
| **online** | 无标注 | 从**过去的经验**在线归纳 |

### 实测成绩 ✅
| 基准 | 结果 |
|---|---|
| **WebArena** | **SOTA 35.6% 成功率** ✓ |
| **Mind2Web** | 文本型 agent 中**最佳** ✓ |
| 跨任务/跨网站/跨领域 | offline 模式泛化良好 ✓ |

### ★ 对我们的启示（最重要 ✗→✓）
```
我们现在: 存【原始对话】✗
  "问：把 HANDOVER.md 的 4096 换成 4k… 答：我无法…"
  ⇒ 换个文件/数字 ⇒ 完全没用 ✗

AWM 让我们存: 【抽象做法】✓
  task_type: "改文档指定章节的数字"
  trigger:   ["换数字","改章节","只改一节"]
  steps:     ① 定位章节起止行号 ② 只在该范围替换 ③ 校验前面正文未变
  tools_ok:  ["read_file","set_lines_in_file"]
  pitfalls:  ["按行号改要先确认行号"]
  ⇒ 换任何文件/任何数字都能用 ✓✓
```

### 可直接搬的代码结构
```python
# 我们的 skills_extractor.py 应该照着这个来 ✓
def induce_workflow(trajectory) -> dict:
    """一条轨迹 → 一个 workflow（去掉 example-specific ✓）"""
    return {
        "task_type": "抽象任务类型",
        "trigger": ["触发词"],
        "steps": ["可复用步骤"],
        "tools_ok": ["实测可用工具"],
        "pitfalls": ["踩过的坑"],
    }
```

---

## 1.3 ⭐⭐⭐ ExpeL（LLM Agents Are Experiential Learners）

**链接**：[github.com/LeapLabTHU/ExpeL](https://github.com/LeapLabTHU/ExpeL) · [arXiv:2308.10144](https://arxiv.org/abs/2308.10144) · AAAI 2024 ✅

### 核心思想（论文原文）
> "state-of-the-art language models like GPT-4 and Claude are primarily accessible through API calls, with their **parametric weights remaining proprietary**... this scenario emphasizes the growing need for new methodologies that allow **learning from agent experiences without requiring parametric updates**"
>
> "Our agent autonomously gathers experiences and **extracts knowledge using natural language**... At inference, the agent **recalls its extracted insights** and past experiences"
>
> "**a consistent enhancement in its performance as it accumulates experiences**"

### ★ 对我们的启示
```
✓ 这就是我们"学习增强"的学术原型 ✓✓
✓ 云模型（GPT-4/Claude）拿不到权重 ⇒ 只能靠经验注入
  ⇒ 我们也是（vLLM 里的 7B + 云 API）✓
✓ 机制: 收集经验 → 自然语言提炼 → 推理时召回
  ⇒ 正是我们的 reflect_run → distill → ingest ✓
✓ 结论: 随经验积累性能持续提升 ✓
```

### ⚠️ 但注意（见 §1.14 反面文献）
> "From Knowledge to Noise"（REALM 2025）证明：**ExpeL 在真实 SE 任务上 scale 不起来** ✗ 根因是噪音 ✗
> ⇒ **ExpeL 的成功前提是"经验被正确提炼"** ✓ 不是"存了就行" ✗

---

## 1.4 ⭐⭐⭐ Mem0（生产级记忆层）

**链接**：[github.com/mem0ai/mem0](https://github.com/mem0ai/mem0) · [arXiv:2504.19413](https://arxiv.org/abs/2504.19413) · **Apache 2.0** ✅

### 公开实测 ✅（README · 新算法 2026-04）
| 基准 | 分数 | Token | p50 延迟 |
|---|---|---|---|
| **LoCoMo** | **92.5** | 7.0K | 0.88s |
| **LongMemEval** | **94.4** | 6.8K | 1.09s |
| **BEAM (1M)** | **64.1** | 6.7K | 1.00s |
| **BEAM (10M)** | **48.6** | 6.9K | 1.05s |

### 新版算法的 5 个关键改动（README 原文 ✅）
> 1. **Single-pass ADD-only extraction** — one LLM call, **no UPDATE/DELETE**. Memories accumulate; nothing is overwritten.
> 2. **Agent-generated facts are first-class** — when an agent **confirms an action**, that information is now stored with equal weight.
> 3. **Entity linking** — entities are extracted, embedded, and linked across memories for retrieval boosting.
> 4. **Multi-signal retrieval** — semantic, BM25 keyword, and entity matching scored in parallel and fused.
> 5. **Temporal Reasoning** — time-aware retrieval.

### ★ 对我们的启示（第 2 条最关键 ✓）
```
Mem0: 只存【agent 确认做到的事】✓
我们: 存了【agent 说"我做不到"】✗ ⇒ 正好相反 ✗

⇒ 改造 L1-a: is_success_trajectory() 只收真成功 ✓
```

### 可借的 3 个机制
```
① 多信号检索（semantic + BM25 + entity）⇒ 我们 L2-a 混合检索 ✓
② 实体链接 ⇒ 我们可用 task_type + trigger 词替代 ✓
③ 时序 ⇒ 我们 L2-c 加 uses/success/last_ok ✓
```

---

## 1.5 ⭐⭐ Letta / MemGPT（OS 式记忆）

**链接**：[github.com/letta-ai/letta](https://github.com/letta-ai/letta) · [arXiv:2310.08560](https://arxiv.org/abs/2310.08560) · **Apache 2.0** ✅

### 核心机制
```
把 LLM 当 CPU，把记忆当"虚拟内存"分层管理:
  · Core memory   （常驻上下文，模型可自编辑）
  · Archival      （外部存储，按需检索）
  · Recall        （历史消息）
⇒ 模型自己决定"什么进核心记忆 / 什么归档"
```

### ★ 对我们的启示
```
✓ 分层思想可借鉴（我们的核心记忆 = 当前任务 + top-k 做法 ✓）
✗ 但"模型自编辑记忆"对我们太重 ✗（7B 做不好自管理 ✗）
```

---

## 1.6 ⭐⭐ Graphiti / Zep（时序知识图）

**链接**：[github.com/getzep/graphiti](https://github.com/getzep/graphiti) · [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) · **Apache 2.0** ✅

### 核心机制
```
bi-temporal（双时间轴）:
  · T_valid:  这个事实【何时为真】
  · T_ingest: 我们【何时知道】这个事实
⇒ 支持"过去成立的现在失效"、"当时不知道现在知道"
```

### 公开数据 ⚠️（论文报告）
- DMR: **94.8%** vs MemGPT 93.4%
- LongMemEval: 提升最多 **18.5%**

### ★ 对我们的启示
```
✓ 我们 L2-c 可借用"事实有效期"（做法可能过时 ✓）
✗ 图数据库引进来太重 ✗（chroma + 元数据够用 ✓）
```

---

## 1.7 ⭐⭐ Reflexion（语言化强化）

**链接**：[github.com/noahshinn/reflexion](https://github.com/noahshinn/reflexion) · [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) ✅

### 核心机制
```
失败 → LLM 生成【文字反思】→ 存进记忆 → 下次重试时带上反思
⇒ 不改权重，靠"自我批评"提升
```

### ★ 对我们的启示
```
✓ 我们 L4 可做【失败反思库】（负向经验单独存 ✓ 不进正向库 ✓）
✓ 我们现在的"免责语料"其实是一种失败记录 ✗ 但没提炼成"下次怎么做" ✗
  ⇒ 应该: "这次失败原因: 没有文件工具 ⇒ 下次先调 tool_list 确认能力" ✓
```

---

## 1.8 ⭐⭐ Voyager（技能库）

**链接**：[github.com/MineDojo/Voyager](https://github.com/MineDojo/Voyager) · [arXiv:2305.16291](https://arxiv.org/abs/2305.16291) ✅

### 核心机制
```
成功的技能 → 写成【可执行代码】→ 存进技能库 → 需要时检索+组合
⇒ 技能是可组合的（"先 A 后 B"能变成新技能 C）
```

### ★ 对我们的启示
```
✓ 我们 L4 可做"做法组合"（做法 A + 做法 B → 复合做法 C ✓）
✓ Voyager 的"代码化"比"AWM 的自然语言步骤"更精确 ⚠
  ⇒ 我们可选: 步骤用伪代码 / 工具序列用 JSON ✓ 更好检索 ✓
```

---

## 1.9 ⭐ A-MEM（记忆笔记 · NeurIPS 2025）

**链接**：[github.com/agiresearch/A-mem](https://github.com/agiresearch/A-mem) · [arXiv:2502.12110](https://arxiv.org/abs/2502.12110) · NeurIPS 2025 ✅

### 核心机制
```
Zettelkasten（卡片盒笔记法）:
  · 每条记忆 = 一张卡片（含上下文描述 + 关键词 + 标签）
  · 新卡片入库时【自动找相关旧卡片并建立链接】
  · 链接会随新记忆动态更新
```

### ★ 对我们的启示
```
✓ 我们 L4 可做: 做法之间自动建"相关/前置/替代"链接 ✓
✗ 但需要额外 LLM 调用 ✗（成本 ⚠）
```

---

## 1.10 ⭐ Cognee（记忆图）

**链接**：[github.com/topoteretes/cognee](https://github.com/topoteretes/cognee) · **Apache 2.0** ✅

### 核心机制
```
ECL 管道:
  Extract（抽取实体/关系）→ Cognify（构建知识图）→ Load（写入图库）
```

### ★ 对我们的启示
```
⚪ 备选：如果将来要"做法依赖图"再看 ✓
✗ 现在不引（图库重 ✗）
```

---

## 1.11 ⭐ MemoryOS（记忆操作系统）

**链接**：[github.com/BAI-LAB/MemoryOS](https://github.com/BAI-LAB/MemoryOS) · [arXiv:2506.06326](https://arxiv.org/abs/2506.06326) ⚠️

### 核心机制
```
三级记忆架构:
  · 短期（对话级）
  · 中期（会话级，按热度）
  · 长期（用户画像/知识）
⇒ 带【热度淘汰】机制（久不用的降级）
```

### ★ 对我们的启示
```
✓ 我们 L2-d 上限淘汰可借鉴（按 (成功率 × 使用次数) 淘汰 ✓）
```

---

## 1.12 ⭐ MemOS（记忆 OS · MemCube）

**链接**：[github.com/MemTensor/MemOS](https://github.com/MemTensor/MemOS) ⚠️

### 核心机制
```
把"记忆"当【可调度资源】（MemCube）:
  · 参数记忆 / 激活记忆 / 明文记忆 三类统一调度
```

### ★ 对我们的启示
```
⚪ 概念很超前 ⚠ 但工程复杂度高 ✗ 暂不引
```

---

## 1.13 ⭐ LangMem（LangChain 官方记忆 SDK）

**链接**：[github.com/langchain-ai/langmem](https://github.com/langchain-ai/langmem) ✅

### 核心机制
```
· 热路径记忆（对话中即时提取）
· 后台记忆（异步整理）
· 记忆类型: semantic（事实）/ episodic（经历）/ procedural（做法）
⇒ ★ **procedural 正好对应我们的"做法"** ✓✓
```

### ★ 对我们的启示
```
✓ 记忆类型三分法值得照搬 ✓（事实 / 经历 / 做法 ✓）
✓ 我们的"学习增强"其实就是 LangMem 的 procedural memory ✓
```

---

## 1.14 ⚠️⚠️ 反面文献（**必须正视**）

### "From Knowledge to Noise"（REALM 2025）

**链接**：[aclanthology.org/2025.realm-1.30](https://aclanthology.org/2025.realm-1.30/) ✅

#### 论文原文（关键三句）
> "We find that **CTIM-Rover does not outperform AutoCodeRover in any configuration** and thus conclude that **neither ExpeL nor DoT-Bank scale to real-world SE problems**"
>
> "Our analysis indicates **noise introduced by distracting CTIM items or exemplar trajectories** as the likely source of the performance degradation"
>
> 标题：**"From Knowledge to Noise"**（从知识到噪音）

#### ★ 对我们的直接警告 ⚠
```
裸存轨迹 = 引噪音 ⇒ 在真实复杂任务上【反而下降】✗
⇒ 必须【抽象化 + 过滤 + 去噪】✓ 否则做了也白做 ✗

我们现在的处境：
  ✗ 存原始对话（裸存轨迹）
  ✗ 90% 是"我做不到"（噪音）
  ⇒ **正好踩在论文说的坑里** ⚠⚠
```

#### 为什么 ExpeL 正面、CTIM-Rover 反面？
| 差异 | ExpeL ✅ | CTIM-Rover ❌ | 我们的启示 |
|---|---|---|---|
| 任务复杂度 | 简单/合成 | **真实 SE** | 我们要测真实任务 ✓ |
| 经验形式 | **提炼成 insight** ✓ | 原始轨迹 ✗ | **必须抽象化** ✓ |
| 过滤 | 有 ✓ | 弱 ✗ | **必须过滤** ✓ |
| 规模 | 小 | 大 ⇒ 噪音累积 ✗ | **必须去重 + 上限** ✓ |

---

## 1.15 其他值得知道的开源项目

| 项目 | 定位 | 备注 |
|---|---|---|
| [Generative Agents](https://github.com/joonspk-research/generative_agents) | 记忆流+反思+计划 | 2023 开创性工作 ✓ 我们"反思"思想的源头 |
| [Memary](https://github.com/kingjulio8238/Memary) | 记忆层 | 知识图谱 + 记忆流 ⚠️ 活跃度待核实 |
| [MemoryScope](https://github.com/modelscope/MemoryScope) | 记忆框架 | 阿里 ModelScope ⚠️ 待核实 |
| [Second-Me](https://github.com/mindverse/Second-Me) | 数字分身 | 个人记忆训练 ⚠️ 与"角色"场景相关 |

---

# 二、RAG / 检索生态

## 2.1 框架层

| 项目 | 定位 | License | 我们用它吗 | 备注 |
|---|---|---|---|---|
| [**LlamaIndex**](https://github.com/run-llama/llama_index) | RAG 框架 | MIT ✅ | ✗ 不引 | ✅ **借它的 NodeParser 分块策略思路** |
| [**LangChain**](https://github.com/langchain-ai/langchain) | LLM 应用框架 | MIT ✅ | ✗ 不引 | 依赖重 ✗ |
| [**Haystack**](https://github.com/deepset-ai/haystack) | RAG 框架 | Apache 2.0 ✅ | ✗ 不引 | 同上 |
| [**GraphRAG**](https://github.com/microsoft/graphrag) | 微软图 RAG | MIT ✅ | ⚪ 备选 | 成本高 ⚠ |
| [**LightRAG**](https://github.com/HKUDS/LightRAG) | 轻量图 RAG | MIT ⚠️ | ⚪ 备选 | 比 GraphRAG 轻 ✓ |
| [**RAGFlow**](https://github.com/infiniflow/ragflow) | 端到端 RAG 引擎 | Apache 2.0 ⚠️ | ✗ 不引 | 重 ✗ |
| [**txtai**](https://github.com/neuml/txtai) | 嵌入式向量库+管道 | Apache 2.0 ⚠️ | ⚪ 备选 | 轻 ✓ |

## 2.2 检索组件（我们已在用 ✓）

| 项目 | 我们用它做什么 | 状态 |
|---|---|---|
| [**Chroma**](https://github.com/chroma-core/chroma) | 向量库（`data/chroma/`） | ✅ **已用** |
| [**sentence-transformers**](https://github.com/UKPLab/sentence-transformers) | bge-small / bge-m3 编码 | ✅ **已用** |
| [**rank_bm25**](https://github.com/dorianbrown/rank_bm25) | 混合检索的 BM25 部分（L2-a 计划）| ⏳ 计划中 · **30 行可自实现** ✓ |
| [Qdrant](https://github.com/qdrant/qdrant) | 向量库（更专业） | ⚪ 备选 |
| [Milvus](https://github.com/milvus-io/milvus) | 向量库（分布式） | ✗ 太重 |
| [Weaviate](https://github.com/weaviate/weaviate) | 向量库 | ⚪ 备选 |

## 2.3 ★ 分块策略（LlamaIndex 的三个思路 · 我们借了 1 个 ✓）

| 策略 | 机制 | 我们用了吗 |
|---|---|---|
| **SentenceSplitter** | 句子级切分 + 重叠 | ✅ 已用（`kb.py split_text` ✓） |
| **MarkdownNodeParser** | 按 `#` 标题切 | ✅ **已用**（`kb_toolkit.split_structure_aware` ✓✓） |
| **SemanticSplitter** | 按语义相似度断点切 | ⏳ 未用（需 embedding 比较 ⚠ 成本） |

## 2.4 检索评测（P-74 已实现 ✓）

| 项目 | 借了什么 | 我们实现 |
|---|---|---|
| [**BEIR**](https://github.com/beir-cellar/beir) | 零样本检索评测协议（query→corpus→qrels） | ✅ 已借（`kb_ab_v3.py` ✓） |
| [**MTEB**](https://github.com/embeddings-benchmark/mteb) | nDCG@10 / Recall@100 指标 | ✅ 已借（hit@5 / MRR / nDCG ✓） |
| [**RAGAS**](https://github.com/explodinggradients/ragas) | faithfulness / context_recall / answer_relevancy | ⏳ **部分借**（我们用了 kf_rate ⚠ 太粗 ⚠ 建议换 RAGAS 指标 ✓） |

---

# 三、LoRA 训练生态

## 3.1 训练框架

| 项目 | 定位 | License | 我们用它吗 |
|---|---|---|---|
| [**LLaMA-Factory**](https://github.com/hiyouga/LLaMA-Factory) | 一站式微调框架 | Apache 2.0 ✅ | ✅ **已用**（`train_lora.py` 底层 ✓） |
| [**PEFT**](https://github.com/huggingface/peft) | 参数高效微调库 | Apache 2.0 ✅ | ✅ 间接用（LLaMA-Factory 底层 ✓） |
| [**Unsloth**](https://github.com/unslothai/unsloth) | 极速 LoRA（2-5× 快） | **AGPL-3.0** ⚠️ | ⚪ **值得试**（但 AGPL 要评估 ⚠️） |
| [**Axolotl**](https://github.com/axolotl-ai-cloud/axolotl) | YAML 配置式微调 | Apache 2.0 ✅ | ⚪ 备选 |
| [**TRL**](https://github.com/huggingface/trl) | RLHF/DPO/SFT | Apache 2.0 ✅ | ⚪ 备选（DPO 可用 ✓） |
| [**torchtune**](https://github.com/pytorch/torchtune) | PyTorch 官方微调 | BSD ✅ | ⚪ 备选 |

## 3.2 推理引擎

| 项目 | 定位 | 我们用它吗 |
|---|---|---|
| [**vLLM**](https://github.com/vllm-project/vllm) | 高吞吐推理 | ✅ **已用**（`:8000` ✓） |
| [**SGLang**](https://github.com/sgl-project/sglang) | 结构化生成 + 高吞吐 | ⚪ 备选（RadixAttention ✓ 可能更快） |
| [**llama.cpp**](https://github.com/ggml-org/llama.cpp) | CPU/低资源推理 | ⚪ 备选（P-96 ollama 同源） |
| [**Ollama**](https://github.com/ollama/ollama) | 本地模型管理 | ⚪ 备选（P-96 记录 ✓） |

## 3.3 量化

| 项目 | 用途 |
|---|---|
| [**AutoAWQ**](https://github.com/casper-hansen/AutoAWQ) | AWQ 量化（Qwen2.5-7B-Instruct-AWQ ✓ 已用） |
| [**GPTQ**](https://github.com/IST-DASLab/gptq) | GPTQ 量化 |
| [**llama.cpp GGUF**](https://github.com/ggml-org/llama.cpp) | GGUF 格式 |

## 3.4 ★ 我们对 LoRA 的实测结论（重要 ✗）

```
8 轮 A/B ⇒ 未检测到工具调用能力提升 ✗
原因（今天才查清）:
  ① 评测集天花板（ok_rate ~100% 没区分度 ✗）
  ② 数据格式坏（system 占比 55% ⇒ 学错 ✗）
⇒ 数据格式修好了（P-73 ✓）⇒ **值得重测一次** ✓（T-2）

⚠ 文献支持我们的观察吗？
  · 小模型（7B/4B）的工具调用能力天花板在【模型本身】✓
  · LoRA 改不了这个天花板 ⚠（我们的 8 轮数据支持 ✓）
  · 但**不是"LoRA 无用"** ✗ —— 未测出来 ≠ 无效 ✓
```

---

# 四、评测基准与工具

## 4.1 通用能力评测

| 项目 | 定位 | 我们用它吗 |
|---|---|---|
| [**lm-evaluation-harness**](https://github.com/EleutherAI/lm-evaluation-harness) | EleutherAI 标准评测 | ⚪ 可选用（学术标准 ✓） |
| [**OpenCompass**](https://github.com/open-compass/opencompass) | 上海 AI Lab 评测体系 | ⚪ 可选用（中文强 ✓） |
| [**HELM**](https://github.com/stanford-crfm/helm) | Stanford  holistic 评测 | ⚪ 太重 |
| [**MT-Bench / LLM-as-judge**](https://github.com/lm-sys/FastChat) | 用强模型打分 | ✅ **我们已在用**（判分器 ✓） |
| [**AlpacaEval**](https://github.com/tatsu-lab/alpaca_eval) | 指令跟随评测 | ⚪ 备选 |
| [**Arena-Hard**](https://github.com/lmarena/arena-hard-auto) | 自动竞技场 | ⚪ 备选 |

## 4.2 Agent 能力评测（**我们最该关注的** ✓）

| 项目 | 定位 | 规模 | 对我们 |
|---|---|---|---|
| [**AgentBench**](https://github.com/thudm/agentbench) | 多环境 agent 评测（ICLR'24） | 8 环境 | ⭐⭐ **参考它的任务设计** |
| [**WebArena**](https://github.com/web-arena-x/webarena) | 真实网站任务 | 812 任务 | ⭐⭐ **AWM 用它拿 SOTA** ✓ |
| [**Mind2Web**](https://github.com/OSU-NLP-Group/Mind2Web) | 网页操作泛化 | 2,350 任务 | ⭐⭐ **AWM 最佳成绩** ✓ |
| [**τ-bench**](https://github.com/sierra-research/tau-bench) | 工具-用户交互（真实域） | 零售/航空 | ⭐⭐⭐ **最贴近我们**（工具调用 + 用户多轮 ✓） |
| [**GAIA**](https://huggingface.co/datasets/gaia-benchmark/GAIA) | 通用助手任务 | 466 题 | ⭐⭐ 真实感强 ✓ |
| [**SWE-bench**](https://github.com/SWE-bench/SWE-bench) | 真实 GitHub issue 修复 | 2,294 | ⭐ 代码场景 ⚠ |
| [**OSWorld**](https://github.com/xlang-ai/OSWorld) | 桌面 OS 操作 | 369 | ⭐ 太重 |

### ★ τ-bench 为什么最贴近我们？
```
我们的场景: 用户说任务 → agent 调工具（91 个）→ 多轮 → 完成
τ-bench:   用户说任务 → agent 调工具（零售/航空 API）→ 多轮 → 完成
⇒ **结构完全一致** ✓✓
⇒ 值得借它的: 任务设计 + 评分方式（数据库状态终态比对 ✓）
```

## 4.3 RAG / 检索评测

| 项目 | 我们用它吗 |
|---|---|
| [**RAGAS**](https://github.com/explodinggradients/ragas) | ⏳ 部分（kf_rate 太粗 ⚠ 建议换 ✓） |
| [**BEIR**](https://github.com/beir-cellar/beir) | ✅ 已借协议 ✓ |
| [**MTEB**](https://github.com/embeddings-benchmark/mteb) | ✅ 已借指标 ✓ |

## 4.4 评测工具（工程化）

| 项目 | 定位 | 我们用它吗 |
|---|---|---|
| [**DeepEval**](https://github.com/confident-ai/deepeval) | LLM 输出单元测试 | ⚪ 备选（pytest 风格 ✓ 轻 ✓） |
| [**Promptfoo**](https://github.com/promptfoo/promptfoo) | prompt 评测/红队 | ⚪ 备选 |
| [**Inspect AI**](https://github.com/UKGovernmentBEIS/inspect_ai) | 英国 AI 安全研究所评测框架 | ⚪ 备选 |

---

# 五、文献清单

## 5.1 经验学习 / 记忆（**核心 10 篇**）

| # | 文献 | 会议/年份 | 方向 | 关键结论 | 链接 |
|---|---|---|---|---|---|
| 1 | **ExpeL: LLM Agents Are Experiential Learners** | AAAI 2024 | 正 ✓ | 经验注入不需改权重 ✓ 随经验积累提升 ✓ | [arXiv:2308.10144](https://arxiv.org/abs/2308.10144) |
| 2 | **AWM: Agent Workflow Memory** | ICML 2025 | 正 ✓✓ | **抽象化 workflow** → WebArena SOTA 35.6% ✓ | [arXiv:2409.07429](https://arxiv.org/abs/2409.07429) |
| 3 | **Mem0: Production-Ready Long-Term Memory** | 2025 | 正 ✓ | 只存确认事实 ✓ 多信号检索 ✓ | [arXiv:2504.19413](https://arxiv.org/abs/2504.19413) |
| 4 | **MemGPT: LLMs as Operating Systems** | 2023 | 正 ✓ | 分层记忆 ✓ | [arXiv:2310.08560](https://arxiv.org/abs/2310.08560) |
| 5 | **Zep: Temporal Knowledge Graph Architecture** | 2025 | 正 ✓ | 事实有效期 ✓ | [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) |
| 6 | **Reflexion: Verbal Reinforcement Learning** | 2023 | 正 ✓ | 失败 → 文字反思 ✓ | [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) |
| 7 | **Voyager: Open-Ended Embodied Agent** | 2023 | 正 ✓ | 技能库可组合 ✓ | [arXiv:2305.16291](https://arxiv.org/abs/2305.16291) |
| 8 | **A-MEM: Agentic Memory** | NeurIPS 2025 | 正 ✓ | 记忆自动关联 ✓ | [arXiv:2502.12110](https://arxiv.org/abs/2502.12110) |
| 9 | **Generative Agents** | 2023 | 正 ✓ | 记忆流 + 反思 ✓ | [arXiv:2304.03442](https://arxiv.org/abs/2304.03442) |
| 10 | **★ From Knowledge to Noise** | **REALM 2025** | **反 ✗✗** | **裸存轨迹 ⇒ 性能下降 ✗ 根因噪音** | [ACL Anthology](https://aclanthology.org/2025.realm-1.30/) |

## 5.2 RAG（6 篇）

| # | 文献 | 年份 | 结论 | 链接 |
|---|---|---|---|---|
| 1 | **RAG: Retrieval-Augmented Generation**（Lewis et al.） | 2020 | 开创性 ✓ 知识密集任务显著提升 ✓ | [arXiv:2005.11401](https://arxiv.org/abs/2005.11401) |
| 2 | **Lost in the Middle** | 2023 | 长上下文中间信息被忽略 ⇒ **RAG 比塞长上下文可靠** ✓ | [arXiv:2307.03172](https://arxiv.org/abs/2307.03172) |
| 3 | **Self-RAG** | 2023 | 模型自学"何时检索/评估检索质量" | [arXiv:2310.11511](https://arxiv.org/abs/2310.11511) |
| 4 | **RAGAS: Automated Evaluation of RAG** | 2023 | faithfulness / context_recall 等指标 | [arXiv:2309.15217](https://arxiv.org/abs/2309.15217) |
| 5 | **BEIR: Zero-shot IR Benchmark** | 2021 | 零样本检索评测协议 | [arXiv:2104.08663](https://arxiv.org/abs/2104.08663) |
| 6 | **MTEB: Massive Text Embedding Benchmark** | 2022 | embedding 基准 + nDCG/Recall | [arXiv:2210.07316](https://arxiv.org/abs/2210.07316) |

## 5.3 Agent 评测（4 篇）

| # | 文献 | 年份 | 链接 |
|---|---|---|---|
| 1 | **AgentBench: Evaluating LLMs as Agents** | ICLR 2024 | [arXiv:2308.03688](https://arxiv.org/abs/2308.03688) |
| 2 | **WebArena: Realistic Web Environment** | 2023 | [arXiv:2307.13854](https://arxiv.org/abs/2307.13854) |
| 3 | **τ-bench: Tool-Agent-User Interaction** | 2024 | [arXiv:2406.12045](https://arxiv.org/abs/2406.12045) |
| 4 | **GAIA: A Benchmark for General AI Assistants** | 2023 | [arXiv:2311.12983](https://arxiv.org/abs/2311.12983) |

## 5.4 微调 / 评测方法（3 篇）

| # | 文献 | 年份 | 链接 |
|---|---|---|---|
| 1 | **LoRA: Low-Rank Adaptation** | 2021 | [arXiv:2106.09685](https://arxiv.org/abs/2106.09685) |
| 2 | **QLoRA: Efficient Finetuning of Quantized LLMs** | 2023 | [arXiv:2305.14314](https://arxiv.org/abs/2305.14314) |
| 3 | **Judging LLM-as-a-Judge (MT-Bench)** | 2023 | [arXiv:2306.05685](https://arxiv.org/abs/2306.05685) |

## 5.5 综述（2 篇）

| # | 文献 | 年份 | 链接 |
|---|---|---|---|
| 1 | **Memory in the Age of AI Agents**（记忆综述） | 2025 | [arXiv:2512.13564](https://arxiv.org/abs/2512.13564) ⚠️ |
| 2 | **A Survey on Agentic RAG** | 2025 | ⚠️ 待核实 |

---

# 六、与本项目的映射

## 6.1 我们已用的开源（✅ 全部是轻依赖）

| 组件 | 开源项目 | 用途 | 引入方式 |
|---|---|---|---|
| 训练 | **LLaMA-Factory** + **PEFT** | QLoRA 微调 | ✅ 已用（`train_lora.py`） |
| 推理 | **vLLM** | 高吞吐 serving | ✅ 已用（`:8000`） |
| 向量库 | **Chroma** | KB 存储 | ✅ 已用（`data/chroma/`） |
| Embedding | **sentence-transformers** + bge | 向量化 | ✅ 已用 |
| 量化 | **AutoAWQ** | 7B AWQ | ✅ 已用 |
| 评测 | **LLM-as-judge**（MT-Bench 思路） | 判分器 | ✅ 已用（自实现） |

## 6.2 我们借了机制但没引框架（✓ 正确决策）

| 机制 | 来源 | 我们的实现 | 为什么不引框架 |
|---|---|---|---|
| **结构感知切块** | LlamaIndex MarkdownNodeParser | `kb_toolkit.split_structure_aware()` | 200 行够用 ✓ 引框架 = 几十个依赖 ✗ |
| **IR 评测协议** | BEIR | `kb_ab_v3.py` | 协议简单 ✓ |
| **nDCG/MRR/Recall** | MTEB/BEIR | 同上 | 公式简单 ✓ |
| **抽象化 workflow** ⏳ | **AWM** | `skills_extractor.py`（待做 T-1） | 一个 LLM 调用 + JSON 校验 ✓ |
| **只存确认成功** ⏳ | **Mem0** | `is_success_trajectory()`（待做 L1-a） | 判据函数 ✓ |
| **多信号检索** ⏳ | **Mem0** | 混合检索（待做 L2-a） | BM25 30 行 ✓ |

## 6.3 明确不引的（✗ 有理由）

| 项目 | 不引理由 |
|---|---|
| ✗ Mem0 / Letta / Zep / Cognee / MemOS | **依赖重**（各有自己的存储/LLM 抽象层 ✗）· 我们只需要**机制** ✓ |
| ✗ LlamaIndex / LangChain / Haystack | **框架重** ✗ 我们已有自己的 pipeline ✓ |
| ✗ GraphRAG / RAGFlow | 图库/端到端引擎重 ✗ |
| ✗ Qdrant / Milvus | Chroma 够用 ✓ |
| ✗ Unsloth | **AGPL-3.0** ⚠️ 许可证要评估 · 且 LLaMA-Factory 已跑通 ✓ |
| ✗ OpenCompass / HELM | 太重 ⚠ 我们有自建评测 ✓ |

## 6.4 值得后续试的（⚪ 备选池）

| 项目 | 什么时候试 | 风险 |
|---|---|---|
| **Unsloth** | 训练速度不够时（2-5× 快 ✓） | **AGPL-3.0** ⚠️ |
| **SGLang** | vLLM 吞吐不够时 | 迁移成本 ⚠ |
| **LightRAG** | 要做"做法依赖图"时 | 复杂度 ⚠ |
| **DeepEval** | 评测要工程化时（pytest 风格 ✓） | 轻 ✓ 低风险 |
| **τ-bench 设计** | **T-5 评测集升级时** ⭐ | 只是借设计 ✓ |

---

# 七、选型结论：为什么我们不引框架

## 7.1 三条硬理由

```
① 依赖爆炸 ✗
   Mem0 一装 = 几十个传递依赖（openai/qdrant/neo4j/...）✗
   我们项目当前: 88 个 py 文件 / 34k 行 / 依赖可控 ✓

② 抽象层冲突 ✗
   框架都有自己的"存储抽象/LLM 抽象/消息抽象"✗
   我们已有: vLLM + Chroma + 自己的 pipeline ✓
   接框架 = 双抽象 ✗ 调试困难 ✗

③ 我们要的是【机制】不是【实现】✓
   AWM 的核心是"抽象化"这句思想 ✓ 不是它的代码 ✗
   Mem0 的核心是"只存确认事实"✓ 不是它的存储层 ✗
   ⇒ **200 行实现思想 ✓ 比引 2 万行框架 ✗ 好得多** ✓
```

## 7.2 唯一例外：训练/推理底座

```
✅ LLaMA-Factory + PEFT + vLLM + Chroma + sentence-transformers
   ⇒ 这些是【成熟底座】✓ 自己写不划算 ✓ 引了正确 ✓
   
❌ 记忆/经验学习框架
   ⇒ 这些是【上层策略】✓ 自己写更可控 ✓ 不引正确 ✓
```

## 7.3 判据（今后照此执行）

```
引框架的条件（同时满足 ✓）:
  ① 是成熟底座（训练/推理/存储/编码）✓
  ② 自己写要 > 500 行 ✗
  ③ 依赖可接受（< 20 个传递依赖 ✓）
  ④ 许可证 OK（MIT/Apache ✓ AGPL 要评估 ⚠️）

不引的条件（任一 ✓）:
  ① 只是"思想"可借鉴 ⇒ 自己实现 ✓
  ② 引入自己的抽象层 ⇒ 冲突 ✗
  ③ 依赖爆炸 ⇒ 维护成本 ✗
```

---

# 八、待核实清单

| # | 项 | 状态 |
|---|---|---|
| 1 | Mem0 各基准的**复现性** | ⚠️ 官方数字 ✓ 第三方复现待查 ⚠ |
| 2 | AWM 在 WebArena 的 35.6% **是否可复现** | ⚠️ 论文/README 报告 ✓ |
| 3 | Zep DMR 94.8% 的**评测设置** | ⚠️ 待核实 |
| 4 | MemoryOS / MemOS 的**活跃度与许可证** | ⚠️ 待核实 |
| 5 | GPT-4o 等闭源模型的记忆功能细节 | ⚠️ 无公开技术细节 |
| 6 | **"From Knowledge to Noise" 的完整实验设置** | ✅ 摘要已核实 ⚠️ 全文细节待读 |
| 7 | Unsloth **AGPL-3.0 对商业使用的影响** | ⚠️ **需法务确认** ⚠️ |

---

# 九、一句话总结

```
我们的策略: **借机制，不引框架** ✓

借了（并已实现）:
  · 结构感知切块（LlamaIndex 思路）✓ 已验证 +14pp hit@5 ✓✓
  · IR 评测协议（BEIR/MTEB）✓ 已实现 ✓

要借（待做 T-1）:
  · **AWM 的"抽象化"** ✓✓ ← **最关键**（把对话变成做法）
  · **Mem0 的"只存确认成功"** ✓
  · Reflexion 的"失败反思"（L4）

不引:
  · Mem0/Letta/Zep/Cognee/MemOS ✗（依赖重 ✓ 只需机制 ✓）
  · LlamaIndex/LangChain ✗（框架重 ✓）
  · 只在"训练/推理/存储/编码"这四类底座上引成熟项目 ✓

核心判据:
  **"这是思想还是底座？"**
    思想 ⇒ 自己 200 行实现 ✓
    底座 ⇒ 引成熟项目 ✓
```

---

*文档 · 2026-10-21 · 调研 25+ 开源项目 · 19 篇文献 · 全部链接已核实（⚠️ 标注的除外）*
*配套：`docs/LEARN_AUG_PLAN.md`（改造方案）· `docs/RETRO_2026-10-21.md`（复盘）*
