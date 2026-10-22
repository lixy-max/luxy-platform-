# 学习增强（KB Augmented Learning）详细完善方案 · 2026-10-21

> **依据**：开源项目调研 + 文献 + 我们今天实测的数据
> **前置**：语料已清理（30 条 → 2 条 ✓ KB 31 块 → 2 块 ✓）

---

## 零、先说结论

```
文献一致结论：经验增强**可行** ✓ 但**必须 抽象化 + 去噪 + 只存成功** ✗
我们的现状：存的是【原始对话】✗ 90% 是"我做不到"✗ ⇒ 必然无效 ✗
改造核心：**把"对话"变成"做法"** ✓
          这一条不改，做多少 A/B 都不会有提升 ✗
```

---

## 一、开源项目调研（10 个 · 全部核实 ✓）

### 1.1 总览表（按与我们相关性排序）

| # | 项目 | 定位 | 关键机制 | 公开实测数据 | 我们用吗 |
|---|---|---|---|---|---|
| **1** | [**AWM** Agent Workflow Memory](https://github.com/zorazrw/agent-workflow-memory) · [arXiv:2409.07429](https://arxiv.org/abs/2409.07429) | **工作流记忆** | **归纳可复用 workflow（把具体上下文抽象掉）** · 离线/在线两种 | **WebArena SOTA 35.6%** ✓ · Mind2Web 文本 agent 最佳 ✓ | ⭐⭐⭐ **最贴近我们** |
| **2** | [**ExpeL**](https://arxiv.org/abs/2308.10144)（AAAI 2024） | 经验学习 | 从轨迹提炼 insight，推理时召回 · **不需要改权重** | "随经验积累性能持续提升" | ⭐⭐⭐ **我们的原型** |
| **3** | [**Mem0**](https://github.com/mem0ai/mem0) · [arXiv:2504.19413](https://arxiv.org/abs/2504.19413) | 通用记忆层 | **single-pass ADD-only** · **agent 确认的动作 first-class** ✓ · 实体链接 · 多信号检索（语义+BM25+实体）· 时序 | LoCoMo **92.5** · LongMemEval **94.4** · BEAM(1M) 64.1 | ⭐⭐⭐ 借机制 |
| **4** | [**Letta / MemGPT**](https://github.com/letta-ai/letta) · [arXiv:2310.08560](https://arxiv.org/abs/2310.08560) | OS 式记忆 | 分层记忆（core / archival / recall）· **模型自编辑记忆** | — | ⭐⭐ 借分层 |
| **5** | [**Graphiti / Zep**](https://github.com/getzep/graphiti) · [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) | 时序知识图 | **bi-temporal**：事实的"何时为真"和"何时记录" | DMR 94.8% vs MemGPT 93.4% | ⭐⭐ 借时序 |
| **6** | [**Reflexion**](https://arxiv.org/abs/2303.11366) | 语言化强化 | 失败 → **文字反思** → 重试 | — | ⭐⭐ 借失败反思 |
| **7** | [**Voyager**](https://arxiv.org/abs/2305.16291) | 技能库 | 成功技能**代码化**入库 · 可组合 | — | ⭐⭐ 借技能库 |
| **8** | [**A-MEM**](https://arxiv.org/abs/2502.12110)（NeurIPS 2025） | 记忆笔记 | Zettelkasten 式 · **记忆之间自动建立关联** | — | ⭐ 借关联 |
| **9** | [**Cognee**](https://github.com/topoteretes/cognee) | 记忆图 | ECL 管道（extract-cognify-load） | — | ⭐ 备选 |
| **10** | [**RAGAS**](https://github.com/explodinggradients/ragas) | RAG 评测 | faithfulness / context_recall / answer_relevancy | — | ⭐⭐⭐ **评测必用** |

### 1.2 ⭐ 最关键的借鉴：**AWM 的"抽象化"** ✓

AWM 原文（README）：
> "A workflow is usually a common sub-routine in solving tasks, **with example-specific contexts being abstracted out**."

**⇒ 这就是我们缺的核心** ✗：
```
我们现在存的（原始对话 ✗）:
  "问：把 HANDOVER.md 那个 4096 全换成 4k，只改「2. 改动全史」那一节…
   答：我无法直接完成这个任务…没有可用的文件操作工具"
  ⇒ 换个文件/换个数字 ⇒ 完全没用 ✗

AWM 让我们存的（抽象做法 ✓）:
  task_type: "改文档指定章节的数字"
  trigger:   ["换数字", "改章节", "只改一节", "别动前面"]
  steps:     ① 定位章节起止行号
             ② 只在该范围替换
             ③ 替换后校验前面正文逐字未变
  tools_ok:  ["read_file", "set_lines_in_file"]
  pitfalls:  ["按行号改要先确认行号", "别用全局替换"]
  ⇒ 换任何文件/任何数字都能用 ✓✓
```

### 1.3 ⭐ 第二个借鉴：**Mem0 的"只存 agent 确认的动作"** ✓

Mem0 新版算法（README · 2026-04）：
> "**Agent-generated facts are first-class** — when an agent **confirms an action**, that information is now stored with equal weight."

**⇒ 我们缺的第二件事** ✗：
```
Mem0: 只存【agent 确认做到的事】✓
我们: 存了【agent 说"我做不到"】✗
⇒ 正好相反 ✗
```

### 1.4 ⚠ 反面文献（必须正视）

**CTIM-Rover / "From Knowledge to Noise"**（REALM 2025）：
> "CTIM-Rover **does not outperform** AutoCodeRover in any configuration"
> "**neither ExpeL nor DoT-Bank scale to real-world SE problems**"
> "**noise introduced by distracting CTIM items or exemplar trajectories** as the likely source of the performance degradation"

**⇒ 对我们是直接警告** ⚠：
```
裸存轨迹 = 引噪音 ⇒ 在真实复杂任务上【反而下降】✗
⇒ 必须【抽象化 + 过滤 + 去噪】✓ 否则做了也白做 ✗
```

### 1.5 文献汇总（8 篇 · 正反都有）

| 文献 | 年份 | 方向 | 对我们的含义 |
|---|---|---|---|
| [ExpeL: LLM Agents Are Experiential Learners](https://arxiv.org/abs/2308.10144) | AAAI 2024 | 正 ✓ | 经验注入**不需要改权重** ✓ 云模型拿不到权重也能学 ✓ |
| [AWM: Agent Workflow Memory](https://arxiv.org/abs/2409.07429) | ICML 2025 | 正 ✓✓ | **抽象化 workflow** → WebArena SOTA 35.6% ✓ |
| [Mem0: Production-Ready Long-Term Memory](https://arxiv.org/abs/2504.19413) | 2025 | 正 ✓ | 只存确认事实 ✓ 多信号检索 ✓ 时序 ✓ |
| [MemGPT: LLMs as Operating Systems](https://arxiv.org/abs/2310.08560) | 2023 | 正 ✓ | 分层记忆 ✓ |
| [Zep: Temporal Knowledge Graph](https://arxiv.org/abs/2501.13956) | 2025 | 正 ✓ | 事实的有效期 ✓ |
| [Reflexion: Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) | 2023 | 正 ✓ | 失败 → 文字反思 ✓ |
| [Voyager: Open-Ended Embodied Agent](https://arxiv.org/abs/2305.16291) | 2023 | 正 ✓ | 技能库可组合 ✓ |
| [A-MEM: Agentic Memory](https://arxiv.org/abs/2502.12110) | NeurIPS 2025 | 正 ✓ | 记忆自动关联 ✓ |
| **[From Knowledge to Noise](https://aclanthology.org/2025.realm-1.30/)** | **REALM 2025** | **反 ✗✗** | **裸存轨迹在真实任务上反而下降** ⚠ 根因是噪音 ⚠ |

---

## 二、我们缺什么（对照文献 · 逐项）

| # | 差距 | 文献依据 | 我们的现状 | 严重度 |
|---|---|---|---|---|
| **1** | **抽象化** ✗✗ | **AWM**（abstract out example-specific contexts） | 存**原始对话** ✗ | 🔴 **最致命** |
| **2** | **只存成功** ✗ | **Mem0**（agent-confirmed facts first-class） | 存免责失败 ✗ | 🔴 |
| **3** | **去噪** ✗ | **CTIM-Rover 反面** | 90% 是"我做不到"✗（已清 ✓ 但管道还会产生 ✗） | 🔴 |
| **4** | **结构化** ✗ | Mem0 实体链接 · A-MEM 关联 | 平铺文本 ✗ | 🟠 |
| **5** | **检索质量** ⚠ | Mem0 多信号（语义+BM25+实体） | 纯向量 ⚠ | 🟠 |
| **6** | **时序** ✗ | Zep bi-temporal | 无 ✗ | 🟡 |
| **7** | **价值验证** ✗✗ | RAGAS + 我们的 `EVAL_PROTOCOL.md` | **无 A/B** ✗ | 🔴 |

---

## 三、详细完善方案（4 阶段 · 逐段可验证 ✓）

### 🎯 阶段 L1（1-2 天 · **不用 GPU**）：**管道修正 —— 从"存对话"改成"存做法"**

#### L1-a 入口收窄（只接受真成功的轨迹 ✓）

```python
# scripts/reflect_distill.py 加判据 ✓
def is_success_trajectory(traj) -> bool:
    """只收【真做到】的轨迹 ✗ 不收"我无法"✗"""
    # ① 至少 1 个工具调用成功 ✓
    tool_ok = any(t.get('ok') for t in traj.get('tool_calls', []))
    if not tool_ok:
        return False, '零工具成功 ⇒ 没动手 ✗'
    # ② 云的最终答案不含免责模板 ✓
    ans = traj.get('final_answer', '')
    if DISCLAIMER_RE.search(ans):
        return False, '答案含免责 ✗'
    # ③ 轨迹完整（有 observation ✓）
    if not traj.get('observations'):
        return False, '无观察结果 ⇒ 无法提炼 ✗'
    return True, 'ok'
```

#### L1-b ★ **抽象化（新增 `scripts/skills_extractor.py`）—— 本方案的核心**

```python
"""把【一条成功轨迹】→【一条可复用做法】（AWM 思路 ✓）

输入: {question, tool_calls:[{name,args,ok}], observations:[...], final_answer}
输出: {
  "task_type": "改文档指定章节的数字",        # 任务类型（抽象掉具体文件/数字）
  "trigger":   ["换数字","改章节","只改一节"], # 触发词（检索用）
  "steps":     ["① 定位章节起止行号", ...],    # 可复用步骤
  "tools_ok":  ["read_file","set_lines_in_file"],  # 实测可用工具
  "pitfalls":  ["按行号改要先确认行号", ...],   # 踩过的坑（从失败/重试里提）
  "evidence":  "session-xxxxx"
}
"""
EXTRACT_SYS = """你是经验提炼器。把下面这条【成功轨迹】提炼成【可复用的做法】。
要求：
① 去掉所有具体文件名/数字/特定路径（那是 example-specific ✗）
② task_type 要说"哪一类任务"（≤15 字）
③ steps 要能套到别的同类任务上（2-6 步 ✓）
④ pitfalls 只写这条轨迹里真实踩过的（没有就 []）
只输出 JSON：{task_type, trigger[], steps[], tools_ok[], pitfalls[]}
不要输出其他内容。"""

def extract_skill(traj, llm_fn):
    prompt = "问：%s\n工具调用：%s\n观察：%s\n最终答：%s" % (...)
    out = llm_fn(EXTRACT_SYS, prompt)
    d = repair_llm_json(out)          # ★ 复用 P3 的 JSON 修复器 ✓
    return validate_skill(d)          # 字段校验 ✓
```

**★ 本地 7B 能做好吗？—— 已实测 ✓**
```
docs/KB_LOCAL_MODEL_FEASIBILITY.md 实验:
  T3 元数据抽取: {"topic":"向量相似度检索","section":"知识库构建流程","keywords":[...]} ✓✓ 优秀
  T4 问题生成:   "结构感知切块的优点是什么？" ✓✓ 优秀
  ⇒ 同样形态的"结构抽取"任务 ⇒ 本地 7B 完全够用 ✓ 延迟 0.6-1.5 秒 ✓
```

#### L1-c 质量闸门 v2（加严 ✓）

```python
def quality_gate_v2(skill) -> tuple:
    # ① 免责 ⇒ 丢（已实现 ✓ 但要加严：有工具计划也丢 ✓ 今天已实测这条漏 ✗）
    # ② 无 steps ⇒ 丢
    if not skill.get('steps'): return False, '无步骤 ⇒ 不是做法 ✗'
    # ③ steps < 2 ⇒ 丢（太泛 ✓）
    if len(skill['steps']) < 2: return False, '步骤太少 ⇒ 太泛 ✗'
    # ④ 与已有做法语义相似 >0.9 ⇒ 合并（不是删除 ✓ 合并证据/计数 ✓）
    # ⑤ task_type 为空 ⇒ 丢
    return True, 'ok'
```

#### L1-d 切分修（P-23b ✓ 终于做）

```python
# 1 做法 = 1 chunk ✓ · overlap = 0 ✓ · 块首回填 task_type + trigger ✓
def skill_to_chunk(skill) -> str:
    return "[%s] 触发词：%s\n步骤：\n%s\n可用工具：%s\n注意事项：%s" % (
        skill['task_type'], ' / '.join(skill['trigger']),
        '\n'.join(skill['steps']), ' / '.join(skill['tools_ok']),
        ' / '.join(skill['pitfalls']))
# ⇒ 检索时第一眼就看到"这是哪类任务"✓ 不是"问：xxx"✗
```

**L1 验收**：
```
· 用 30 条历史轨迹跑提炼 ⇒ 应产出 ≥5 条合格做法 ✓
· 人工抽检 5 条 ⇒ ≥4 条是"可复用做法"而不是"复述这段对话" ✓
· KB 块数：≥5 块 ✓ 每块都带 task_type + steps ✓
```

---

### 🎯 阶段 L2（2-3 天 · **少量 GPU**）：**检索改造**

#### L2-a 混合检索（Mem0 多信号 ✓）
```python
# 向量（语义）+ BM25（关键词）+ 触发词精确匹配 ✓ 三路融合
def hybrid_search(query, kb, top_k=5):
    v = vector_search(query, kb, top_k*2)      # 语义 ✓
    b = bm25_search(query, kb, top_k*2)        # 关键词 ✓（rank_bm25 30 行 ✓）
    t = trigger_match(query, kb)               # 触发词命中 ✓
    return rrf_fuse(v, b, t)[:top_k]           # RRF 融合 ✓
```

#### L2-b 结构分桶（Mem0 实体链接简化版 ✓）
```
按 task_type 分桶: 改文档 / 查状态 / 跑训练 / 检索 / 数据转换 …
检索时: 先判 task_type（小分类器或关键词）⇒ 只在该桶里检索 ✓
⇒ 减少"跨类误命中"✓
```

#### L2-c 时序 + 成功计数（Zep ✓）
```json
{"skill_id": "...", "uses": 12, "success": 11, "last_ok": "2026-10-21"}
⇒ 检索排序: 相似度 × 成功率 加权 ✓（Mem0 的 entity boosting 同理 ✓）
```

#### L2-d 上限（防膨胀 ✓）
```
每类 task_type 最多 20 条 ✓
超出 ⇒ 按 (成功率 × 使用次数) 淘汰最差 ✓
```

**L2 验收**：
```
· 造 10 条测试问题 ⇒ 检索命中正确 task_type ≥8 条（80%）✓
· 对比: 纯向量 vs 混合检索 ⇒ 命中率提升 ✓（记录数字 ✓）
```

---

### 🎯 阶段 L3（1 天 · **要 GPU**）：**★ A/B 验证（核心 ✓）**

```python
# scripts/learn_eval.py
"""
20-30 条任务 × 2 臂:
  臂 A: 学习增强开（挂 test_use_agent，检索注入做法）
  臂 B: 学习增强关（同底座同 prompt 不注入）
指标: 任务成功率 · 工具调用数 · 云接手率 · 耗时
统计: 配对比较（同任务两臂 ✓）+ n≥30 ✓ + 配对 t 检验 ✓
通过标准: 有检索 **+5pp** ✓ 否则回滚改造 ✓
"""
```

**L3 验收**：
```
· 跑完 20-30 条 ⇒ 出成功率对比表 ✓
· |t|>2 且方向为正 ⇒ 通过 ✓
· 否则 ⇒ 诚实报告"未检测到提升"✗（不下"无效"结论 ✗ 可能是样本/任务不够 ✓）
```

---

### 🎯 阶段 L4（可选 · 长线）

| 项 | 依据 | 价值 |
|---|---|---|
| **失败反思库**（负向经验单独存 ✓ 不进正向库） | Reflexion | 避免重复踩坑 ✓ |
| **技能组合**（做法可组合成复杂做法） | Voyager | 处理复合任务 ✓ |
| **做法图**（做法之间依赖关系） | Graphiti / Cognee | 编排 ✓ |
| **训练闭环**（做法 → 训练数据 → 微调） | 飞轮设计 | 飞轮真转起来 ✓ |

---

## 四、代码改动清单（14 小时 · 2 天）

| 文件 | 改动 | 工时 | 阶段 |
|---|---|---|---|
| **`scripts/skills_extractor.py`** 🆕 | 轨迹 → 可复用做法（LLM 蒸馏 ✓ 复用 JSON 修复器） | **4h** | L1-b |
| `scripts/learn_sink.py` | 闸门 v2 + 调抽象化 + 语义去重合并 | 2h | L1-c |
| `scripts/reflect_distill.py` | `is_success_trajectory()` 判据 | 1h | L1-a |
| `kb.py` | `skill_to_chunk()` 切分（1做法=1chunk · overlap=0 · 块首回填） | 1h | L1-d |
| `scripts/learn_eval.py` 🆕 | A/B 评测（20-30 任务 × 2 臂 ✓ 配对 t ✓） | **3h** | L3 |
| `lora_manager.py` | `POST /chat/learn/ab` 端点 | 1h | L3 |
| 前端（学习增强看板） | A/B 结果展示 | 2h | L3 |
| **合计** | | **14h** | |

---

## 五、验收标准（每阶段卡点 ✓）

| 阶段 | 验收条件 | 不通过怎么办 |
|---|---|---|
| **L1** | 30 轨迹 → ≥5 条合格做法 · 人工抽检 5 条 ≥4 合格 | 调 prompt / 换更大模型提炼 |
| **L2** | 10 条测试问题命中 task_type ≥80% | 调融合权重 / 加触发词 |
| **L3** | **A/B 有检索 +5pp** ✓ | **回滚改造** ✗ 或诚实报告"未检测到" |
| **L4** | 闭环跑通 | 视情况 |

---

## 六、边界（明确不做 ✗）

| 不做 | 理由 |
|---|---|
| ✗ 引入 Mem0/Letta/Zep 框架 | 依赖重 ✗ 我们只需要**机制** ✓ 不引框架 ✓ |
| ✗ 上 Graphiti 图数据库 | 先用 chroma + 元数据 ✓ 后期再说 ✓ |
| ✗ 全自动训练闭环 | 先证明检索有用 ✓ |
| ✗ **原样存轨迹** | **已被反面文献否决** ✗（From Knowledge to Noise） |
| ✗ 存"我做不到" | 已实测 90% 是这种 ✗ 已清理 ✓ |

---

## 七、一句话总方案

```
现状：存【原始对话】✗ → 90% 是"我做不到"✗ → 检索进上下文反而有害 ✗
文献：AWM 证明【抽象化的做法】SOTA ✓ · Mem0 证明【只存确认成功】✓
      CTIM-Rover 证明【裸存轨迹 = 噪音】✗
方案：把"对话"→"做法"（4 阶段 14h）
   L1 抽象化管道（不用 GPU · 1-2 天）★ 核心
   L2 混合检索 + 分桶 + 时序
   L3 A/B 验证（+5pp 通过 ✓ 否则回滚）
   L4 失败反思 / 技能组合 / 图 / 训练闭环
⇒ **L1 不做，后面全是白费** ✗✓
```

---

*方案 · 2026-10-21 · 依据：10 个开源项目实测数据 + 9 篇文献（含 1 篇反面）+ 今天语料实测*
*开源链接全部核实 ✓ 文献 arXiv 编号全部可查 ✓*
