# LoRA 训练数据质量规范（开源依据版）

> 依据三份权威来源 + 实测审计。所有条款**可机器校验** ✓
> 生成时间：2026-10-19 · 用于 v3-2-1 数据重建

---

## 一、格式规范（来源：LLaMA-Factory 官方文档）

**引用**：https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html

官方原文（ShareGPT · Supervised Fine-Tuning）：
> "Note that **human and observation must appear at odd positions**, while **gpt and function must appear at even positions**."
>
> 结构：`conversations: [human, function_call, observation, gpt]`
> `system`: "系统提示词（选填）" ← **独立字段**
> `tools`: "工具描述（选填）" ← **JSON 字符串**

### 硬性条款
| # | 条款 | 校验方法 |
|---|---|---|
| **F1** | `conversations[0].from` 必须是 `human` ✓ | 位置 0（1-based=1 奇数） |
| **F2** | `conversations[1].from` 必须是 `function_call` 或 `gpt` ✓ | 位置 1（2 偶数） |
| **F3** | `conversations[2].from` 必须是 `observation` 或 `human` ✓ | 位置 2（3 奇数） |
| **F4** | 最后一条必须是 `gpt` ✓ | 位置末（偶数） |
| **F5** | `system` **不能**出现在 `conversations` 里 ✗ → 必须是顶层 `system` 字段 ✓ | grep |
| **F6** | `tools` 必须是 **JSON 字符串**（不是对象）✓ | json.loads 后是 list |
| **F7** | `tools` 里的 tool 名必须在 `agent_kb/_tools.json` 白名单内 ✓ | 集合比对 |
| **F8** | `function_call.value` 必须是 `{"name": ..., "arguments": {...}}` ✓ 且 arguments 是 dict ✓ | JSON 解析 |

**⚠ 本次失败的根因（审计已证实）**：
```
旧数据: conversations = [system, human, function_call, observation, gpt]
        → 第1位是 system ✗ 所有角色整体错位 1 格 ✗✗
        → LLaMA-Factory 解析时 human 被当成 system ✗ function_call 被当 human ✗
        → 模型学的是【错位对话】⇒ 训练无效 ✗
```

---

## 二、数量规范（来源：LIMA 论文）

**引用**：LIMA: Less Is More for Alignment, arXiv:2305.11206
> "training LIMA ... on only **1,000 carefully curated** prompts and responses"
> "almost all knowledge in large language models is learned during pretraining, and only **limited instruction tuning data is necessary**"

### 条款
| # | 条款 | 依据 |
|---|---|---|
| **N1** | **≥ 1,000 条**（LIMA 证明 1,000 条高质量足够 ✓） | LIMA |
| **N2** | **≥ 100 个不同任务**（每条任务 ≥ 10 条变体 ✓） | 多样性 |
| **N3** | **不是"少量任务 × 大量重复变体"** ✗（旧数据 40 任务 × 12 = 假 430 条 ✗） | LIMA 强调 curated |
| **N4** | 训练/验证切分 **≥ 90/10** ✓ | 常规 |

---

## 三、质量规范（来源：项目 docs/LORA_DATA_AND_EVAL_GUIDE.md）

**引用**：`docs/LORA_DATA_AND_EVAL_GUIDE.md` §第二部分

官方原文：
> - **② system 要瘦身到 1/5 以内**（目标 ≤400 字符）
> - 判断标准：**system 字数 / 整条样本字数 < 20%**
> - **⑤ 句子多样性**：语义级改写（同义不同词、换语序、换指代）—— ✗ 注意：**不是**加标点/改错字那种变异（那不算多样性）
> - **关键能力占比**：追问/拒答类 **≥15%**
> - **记忆陷阱**：train loss→≈0，eval 崩/塌成一类 ← 数据多样性低

### 条款
| # | 条款 | 阈值 | 校验 |
|---|---|---|---|
| **Q1** | system 字数 / 整条样本字数 | **< 20%** ✓ | 逐条算 |
| **Q2** | system 字符数 | **≤ 400** ✓ | 逐条 |
| **Q3** | 句子多样性（归一化去重后 unique / total） | **≥ 60%** ✓ | 去重比 |
| **Q4** | 追问/澄清/拒答类占比 | **≥ 15%** ✓ | 按 kind 分类 |
| **Q5** | 重复样本 | **0 条** ✓ | 完全重复检测 |
| **Q6** | 同一任务变体上限 | **≤ 8 条** ✓ | 防"假数量" |
| **Q7** | **禁止"加标点/改错字"式变异** ✗ | 编辑距离 > 15% ✓ | 与原文相似度 |
| **Q8** | 工具调用数分布合理（1-3 个/条为主 ✓） | 单工具 ≥ 40% ✓ | 统计 |

---

## 四、训练超参规范（配合数据量）

| # | 条款 | 依据 |
|---|---|---|
| **H1** | **rank ≤ 16** ✓（数据 < 2000 条时不要大 rank ✗） | 防过拟合 |
| **H2** | **epochs ≤ 2** ✓（数据 ≥ 1000 条时 1-2 轮足够 ✓） | LIMA |
| **H3** | 监控 **eval_loss**（不是只看 train_loss ✗） | 防记忆 |
| **H4** | 若 train_loss < 0.05 而 eval_loss 上升 ⇒ **立即停** ✗ | 过拟合信号 |

---

## 五、检查清单（生成后必须全过 ✓）

```
□ F1-F8 格式全过（位置/system 字段/tools 字符串/工具白名单/arguments dict）
□ N1 ≥ 1000 条 · N2 ≥ 100 任务 · N4 90/10 切分
□ Q1 system 占比 < 20%（每条）
□ Q2 system ≤ 400 字符（每条）
□ Q3 句子多样性 ≥ 60%
□ Q4 追问/拒答 ≥ 15%
□ Q5 零重复
□ Q6 同任务 ≤ 8 变体
□ Q7 无"加标点式"假变体
□ Q8 单工具样本 ≥ 40%
□ H1 rank ≤ 16 · H2 epochs ≤ 2 · H3 有 eval_loss
```

---

## 六、引用来源

1. **LLaMA-Factory Data Preparation**（官方）
   https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html
2. **LIMA: Less Is More for Alignment**（NeurIPS 2023）
   https://arxiv.org/abs/2305.11206
3. **项目内规范**：`docs/LORA_DATA_AND_EVAL_GUIDE.md`（§第二部分 数据质量形态）
4. **OpenAI function calling 格式**（sharegpt 的通用实践 ✓）
