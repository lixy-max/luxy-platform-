# 置信度打分器设计（文献驱动 · 2026-10-21）

> **用户要求**: "做，不要凭感觉做……这些是我们平台真正的护城河，**去查资料**" ✓
> **方法**: 用平台自己的 `scripts/academic_search.py` 检索 12 个方向 ✓ 得 **48 篇** ✓
> **本文**: 每一条设计都标注**文献依据** ✓ 不拍脑袋 ✗

---

## 一、文献结论（先看清楚文献说了什么 ✓）

### ★ 结论 1：**模型自评不可靠，必须放弃** ✗
| 文献 | 结论 |
|---|---|
| **Wired for Overconfidence**（arXiv:2604.01457）| LLM"不仅错，而且**自信地错**"✗ 自评置信度系统性偏高 ✗ |
| **On Verbalized Confidence Scores for LLMs**（arXiv:2412.14737）| 口语化置信度**校准性差** ✗ |
| **A Survey of Confidence Estimation and Calibration in LLMs**（NAACL 2023）| 综述：口语化置信度是**最弱**的方法之一 ✗ |

⇒ **设计决定**：**绝不用"模型自评 1-5 分"** ✗
⇒ 我们已有的 `grounding_confidence` 是**外部信号**（检索成功与否）✓ 不是自评 ✓ 可留 ✓

### ★ 结论 2：**内部状态最准，但我们拿不到** ⚠
| 文献 | 结论 |
|---|---|
| **Fully Exploiting LLM Internal States to Enhance Knowledge Boundary Perception**（arXiv:2502.11677）| 用**隐层状态**判断"知道/不知道"效果好 ✓ |
| **Hallucination Detection with Internal Layers**（arXiv:2509.14254）| 内部层特征能检测幻觉 ✓ |

⇒ **设计决定**：vLLM 的 OpenAI 兼容接口**不暴露隐层** ✗ ⇒ **本方案不用内部状态** ⚠
⇒ 但记在"未来可做"里 ✓（换 vLLM 自定义接口 / 或本地 transformers ✓）

### ★ 结论 3：**外部信号集成是可行路线** ✓
| 文献 | 结论 |
|---|---|
| **Multi-Method Ensemble Framework for LLM Hallucination Detection**（2026）| **多方法集成**优于单信号 ✓ |
| **Selective Risk Control for LLM Decision Agents**（2026）| Agent 可用**风险控制**方式做选择性输出 ✓ |
| **DeFA: Dependency-Guided Failure Attribution for LLM Agents**（arXiv:2610.01256）| Agent 失败可**从轨迹归因** ✓（依赖关系分析 ✓）|
| **Distribution-Free Selective Prediction for LLM-RAG Hallucination**（2026）| RAG 幻觉可用**无分布假设的选择性预测** ✓ |
| **Influences on LLM Calibration: Response Agreement**（ACL 2025）| **多次回答一致性**是有效校准信号 ✓ |
| **Confidence Improves Self-Consistency**（arXiv:2502.06233）| 置信度与自一致性**互相增强** ✓ |
| **When LLMs Agree, Are They Right?**（arXiv:2607.08065）| ⚠ **一致性≠正确** ✗ 需审计 ✓ |

⇒ **设计决定**：**多信号集成 ✓ + 自一致性(可选) ✓ + 但要审计"一致≠对"** ✗

### ★ 结论 4：**共形预测给数学保证** ✓✓（最强工具）
| 文献 | 结论 |
|---|---|
| **A Gentle Introduction to Conformal Prediction**（arXiv:2107.07511）| 无分布假设的**覆盖率保证** ✓ |
| **Conformal Prediction with Paraphrase-Aware Scoring for LLM UQ**（2026）| 直接用于 LLM 不确定性量化 ✓ |

⇒ **设计决定**：**用共形预测定阈值** ✓ 而不是拍一个 0.7/0.8 ✗
⇒ 保证：**"低置信组里错误率 ≤ α"** 有**数学保证** ✓✓（不是经验值 ✗）

---

## 二、信号清单（每条都有文献依据 ✓）

### A. 零成本信号（不需要额外 LLM 调用 ✓ 当前就能算 ✓）

| 信号 | 怎么算 | 文献依据 |
|---|---|---|
| `tool_ok_rate` | `len(exec_ok_tools) / max(len(exec_tools),1)` | DeFA（失败归因 ✓）|
| `retrieval_hit` | 检索结果的关键实体命中率 ✓ | Selective Prediction for RAG ✓ |
| `grounding_conf` | `high=1.0 / medium=0.5 / low=0.0` ✓ | RAG 幻觉检测 ✓ |
| `n_tools_anomaly` | 工具数是否异常多（>5 ✓）或 0 ✓ | Over-Privileged Tool Selection ✓ |
| `dup_guard_rate` | `dup_guard_hits / max(iter,1)` | DeFA（循环检测 ✓）|
| `escalated` | 是否升级到云端（=1 表示本地不行 ✓）| Selective Risk Control ✓ |
| `finished` | 任务是否正常结束 ✓ | Agent 失败归因 ✓ |
| `answer_len_z` | 答案长度相对同档均值 z-score | Ensemble ✓ |

### B. 可选信号（要额外成本 ⚠）

| 信号 | 成本 | 文献依据 |
|---|---|---|
| `self_consistency` | **N× LLM 调用** ✗ | ACL 2025 Response Agreement ✓ |
| `internal_states` | 需改 vLLM 接口 ✗ | 2502.11677 ✓ |
| `nli_consistency` | 需小 NLI 模型 ⚠ | RAG faithfulness ✓ |

⇒ **第一版只做 A 组** ✓（零成本 ✓ 立刻可验证 ✓）
⇒ B 组留作 V2 ✓

---

## 三、方法：**共形预测** ✓（核心设计 ✓）

### 为什么不用"简单加权求和 + 阈值" ✗
```
加权求和的权重是**拍的** ✗ 阈值也是**拍的** ✗
⇒ 无法保证"低置信组错误率 ≤ α" ✗
⇒ 换个模型/换批数据就得重新调 ✗
```

### 共形预测怎么做 ✓
```
① 校准集: (x_i, y_i)  i=1..n
   x_i = 8 维信号向量 ✓
   y_i = 是否答对（V2 评测的 pass/fail ✓）
   ⇒ 我们已有数据: V2 评测 76 跑次 × 2 臂 + 587 条历史轨迹 ✓

② 拟合打分器 s(x) ⇒ 预测"答对概率" ✓
   ⇒ **逻辑回归** ✓（8 维特征 ✓ 样本小也能拟合 ✓ 可解释 ✓）
   ✗ 不用神经网络 ✗（样本 <1000 ✗ 会过拟合 ✗）

③ 共形分位数 ✓
   对校准集算 nonconformity: α_i = 1 - p_i（答对概率的补 ✓）
   取 q = ceil((n+1)(1-α))/n 分位数 ✓
   ⇒ 测试时: 若 1 - p(x) > q ⇒ **判为"低置信" ⇒ 拒答/升级** ✓

④ 保证（文献 2107.07511 定理 ✓）
   P(错误且被接受) ≤ α ✓  **无分布假设** ✓
   ⇒ 这是**数学保证** ✓ 不是经验值 ✓✓
```

### 三档输出 ✓
| 置信度 | 阈值来源 | 动作 |
|---|---|---|
| **高**（接受）| 1−p ≤ q_α 且 p ≥ 0.8 | 正常答 + 引用 ✓ |
| **中**（谨慎）| q_α < 1−p ≤ q_β | 答 + 标"未验证"⚠ + 建议核实 ✓ |
| **低**（拒答）| 1−p > q_β | 拒答 / 升级到云端 / 要求澄清 ✓ |

⚠ **α / β 由用户定** ✓（如 α=0.10 错 10% 内可接受 · β=0.30 错 30% 内标警告 ✓）
⇒ **不用我们拍** ✓ 是**明确的业务选择** ✓

---

## 四、评估方法（文献标准 ✓）

| 指标 | 定义 | 文献依据 |
|---|---|---|
| **ECE**（期望校准误差）| 置信度与实际准确率的偏差 ✓ | Survey 2023 ✓ |
| **AUROC** | 置信度区分对/错的能力 ✓ | Survey 2023 ✓ |
| **Selective accuracy @ coverage** | 在给定覆盖率下的准确率 ✓ | Selective Prediction 文献 ✓★ |
| **Risk@coverage** | 同上但看错误率 ✓ | Selective Risk Control 2026 ✓ |
| **覆盖率** | 能接受的比例 ✓ | 共形预测 ✓ |

★ **核心验收标准**（可测 ✓ 不靠感觉 ✓）
```
✅ selective accuracy 曲线**单调**（置信度越高越准 ✓）
✅ 低置信组错误率 **≤ α**（共形保证 ✓ 实测应满足 ✓）
✅ 相比"全部接受"的基线准确率**提升** ✓
```

---

## 五、实现计划（分 4 步 ✓ 每步可验证 ✓）

| 步 | 内容 | 工时 | 验证 |
|---|---|---|---|
| **1** | 从 `runs_v2.jsonl` + 轨迹抽 8 维特征 ✓ | 1h | 特征能算出来 ✓ |
| **2** | 拟合逻辑回归 + 共形分位数 ✓ | 1h | ECE/AUROC 有值 ✓ |
| **3** | 三档阈值 + 输出 JSON 接口 ✓ | 1h | 给定特征返回置信度 ✓ |
| **4** | **在 V2 评测集上验证** ✓ | 1h | selective accuracy 单调 ✓ |

**总计约 4 小时** ✓ **零 GPU** ✓（纯 Python + sklearn ✓）

---

## 六、⚠ 诚实边界（必须写清 ✗）

### 能做的 ✓
```
✅ 零成本信号集成 ✓（不增加 LLM 调用 ✓）
✅ 共形预测的**数学保证** ✓（覆盖率 ✓ 不是经验值 ✓）
✅ 可解释 ✓（8 维特征 ✓ 逻辑回归系数可看 ✓）
✅ 可验证 ✓（ECE/AUROC/selective accuracy ✓）
✅ 可回滚 ✓（不满意就关 ✓ 不影响主流程 ✓）
```

### 不能做的 ✗
```
✗ **保证"高置信就一定对"** ✗ —— 只能保证"错误率 ≤ α"✓（数学上限 ✓ 不是零错 ✗）
✗ 处理**从未见过的任务类型** ✗ —— 共形预测需要校准集覆盖相似分布 ⚠
✗ 取代人工 ✓ —— 只是**辅助判断** ✓
✗ 用内部状态（vLLM 接口不暴露 ✗）—— 未来可做 ⚠
✗ **"自一致性=正确"** ✗ —— 文献 2607.08065 明确审计过 ⚠ 一致性可能是"一致的错误" ✗
```

### 数据依赖风险 ⚠
```
⚠ 校准集只有 ~76 跑次 + 587 轨迹 ✓
   ⇒ 共形预测在 n<100 时覆盖率保证**偏保守**⚠（区间宽 ✗）
   ⇒ 建议: **n ≥ 200** 才上线 ✓ 现在先做**离线验证** ✓
```

---

## 七、参考文献（48 篇全文见 `/mnt/e/WSL/_check/lit/all.json` ✓）

**核心 11 篇**（按用途分组 ✓）:

**① 共形预测（方法核心 ✓）**
- A Gentle Introduction to Conformal Prediction（arXiv:2107.07511）★
- Conformal Prediction with Paraphrase-Aware Scoring for LLM UQ（2026）

**② 选择性预测 / 风险控制（决策层 ✓）**
- Distribution-Free Selective Prediction for LLM-RAG Hallucination（2026）★
- Selective Risk Control for LLM Decision Agents under Uncertainty（2026）★

**③ Agent 失败归因（信号来源 ✓）**
- DeFA: Dependency-Guided Failure Attribution for LLM Agents（arXiv:2610.01256）★

**④ 幻觉检测（多信号 ✓）**
- Multi-Method Ensemble Framework for LLM Hallucination Detection（2026）★
- Hallucination Detection with Internal Layers（arXiv:2509.14254）
- Probabilistic distances-based hallucination detection in LLMs with RAG（arXiv:2506.09886）

**⑤ 校准与置信度（理论 ✓）**
- A Survey of Confidence Estimation and Calibration in LLMs（NAACL 2023）★
- Wired for Overconfidence（arXiv:2604.01457）★ ← 为什么不用自评 ✗
- On Verbalized Confidence Scores for LLMs（arXiv:2412.14737）

**⑥ 自一致性（可选信号 ✓）**
- Influences on LLM Calibration: Response Agreement（ACL 2025）★
- When LLMs Agree, Are They Right?（arXiv:2607.08065）★ ← 审计"一致≠对" ✗

---

*2026-10-21 · **文献驱动设计** ✓ · 每条设计有出处 ✓ · 先做离线验证再上线 ✓*
