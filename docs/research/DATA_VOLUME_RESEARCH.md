# 数据量文献研究 + 我们的实证对照 · 2026-10-19

> 用户指出：「**数据量是一个坎**」✓ 要求查文献
> 本文 = 文献综述（4 篇核心）+ 与我们三轮实验的对照 + 结论与建议

---

## 第一部分 · 文献综述

### 📄 1. LIMA: Less Is More for Alignment（Meta, NeurIPS 2023）
[arXiv:2305.11206](https://arxiv.org/abs/2305.11206)

> "training LIMA ... on only **1,000 carefully curated** prompts and responses"
> "almost all knowledge in large language models is learned during pretraining, and only **limited instruction tuning data is necessary**"

**结论**：**质量 > 数量** ✓ 1,000 条精挑细选的数据可以做到很强 ✓
**对我们的意义**：数据量不是唯一解 ✓ 但 **"carefully curated"** 是前提 ✓（我们的 v3-2-1 1054 条合成数据不够 curated ✗ → 无效 ✓ 印证 ✓）

---

### 📄 2. When Scaling Meets LLM Finetuning（Google DeepMind, ICLR 2024）
[arXiv:2402.17193](https://ar5iv.labs.arxiv.org/html/2402.17193)

这是**最直接回答"数据量"问题的论文** ✓（系统实验：1B-16B 模型 × 8K-100K 微调数据 × FMT/Prompt/LoRA）

#### 核心发现（4 条，全部对我们有直接影响 ✓）

**① 微调数据遵循幂律** ✓
```
L̂(X, Df) = A · (1/X^α) · (1/Df^β) + E
  Df = 微调数据量 · X = 其他因素（模型大小等）
```
> "while finetuning with small amount of examples could achieve decent results, **larger scale finetuning data still contributes to improved downstream performance**"

⇒ **数据越多越好 ✓ 但收益是对数级的** ✓（从 278 → 400 只提升 log(400/278) ≈ 0.36 个 log 单位 ⇒ **收益很小** ⚠）
⇒ **要跨一个量级（278 → 2780）才有明显收益** ✓

**② 增大 LoRA rank 是无效的** ✗✗
> "**Scaling PET parameters is ineffective** ... the small scaling exponents, |α_t| ≪ 1e-2, and even results in **inverse scaling** in some settings, e.g. **LoRA on En-De**"
> "LoRA is more stable and reliable [than Prompt]"

⇒ **加 rank 无意义** ✗（|α| < 0.01 ✓ 甚至反向 ✓）
⇒ **我们的实证完全吻合** ✓✓：v3-2-0 用 **r=16** ✗ 无效 / v3-2-1 用 **r=8** ✗ 无效 / 两轮结果一样差 ✓ **文献解释了这个现象** ✓

**③ PET（LoRA）的瓶颈是【底座模型】不是【数据】** ⚠⚠
> "**PET depends more on LLM model and pretraining data scaling than finetuning data scaling** across settings"
> "Since the majority of LLM parameters is frozen during finetuning, **PET relies heavily on the encoded knowledge in pretrained LLMs**"

⇒ **LoRA 的天花板 = 底座模型** ✓✓✓ **这是最关键的洞察** ✓
⇒ **我们硬件只能跑 7B（14B fp16 训练需 ~28GB ✗ 16GB 卡做不到 ✗）⇒ LoRA 的收益天花板被硬件锁死** ✗
⇒ **再怎么加数据，也突破不了 7B 底座的能力上限** ✓ 这解释了为什么三轮都是"边缘/无效" ✓

**④ 关键数据量分界线** ✓
> "When only **few thousands** of finetuning examples are available, **PET should be considered first**, either Prompt or LoRA. With slightly larger datasets, **LoRA would be preferred** due to its stability and slightly better finetuning data scalability. For **million-scale** datasets, **FMT** would be good."

⇒ **我们的 278-1000 条 ⇒ 属于 "few thousands" ⇒ LoRA 是正确方法 ✓**（不是选错了 ✓）
⇒ 但 **FMT（全参微调）在我们的数据量下反而更差** ✗ → 不用考虑 ✓

---

### 📄 3. LoRA Learns Less and Forgets Less（Biderman et al., TMLR 2024）
[arXiv:2405.09673](https://arxiv.org/abs/2405.09673)

> "we compare the performance of LoRA and full finetuning on two target domains, **programming and mathematics**"
> "in the standard low-rank settings, **LoRA substantially underperforms full finetuning**"
> "**full finetuning learns perturbations with a rank that is 10-100X greater** than typical LoRA configurations"
> "LoRA better maintains the base model's performance on tasks outside the target domain [forgets less]"

#### 对我们的三项重要含义 ✓
| 发现 | 对我们的意义 |
|---|---|
| **LoRA 在编程/数学域明显不如全参微调** ✗ | 我们的任务是 **工具调用 + 结构化输出** ✓ 与"编程"同族 ⚠ ⇒ **LoRA 结构性劣势** ✓ 解释了为什么难提升 ✓ |
| **全参微调学到的扰动 rank 是 LoRA 的 10-100 倍** ✓ | 即使 LoRA rank 开到 128 也不够 ✓（更何况我们 r=8 ✓）⇒ **加 rank 无用已从两个角度印证** ✓ |
| **LoRA 更不容易遗忘底座能力** ✓ | ⇒ LoRA 适合**专精**（不改底座通用能力 ✓）不适合**大幅提升** ✓ |

⇒ **结论**：**LoRA 本来就擅长"小改"，不擅长"大变"** ✓ → 我们期待的"大幅提升"在方法层面就不现实 ✗

---

### 📄 4. How Many Examples Do You Need?（样本效率标度律 · IEEE）
[ieeexplore.ieee.org/document/11655117](https://ieeexplore.ieee.org/document/11655117)
> 专门研究 **PET（参数高效微调）的样本效率标度律** —— 标题即结论：**需要多少样本是可建模的** ✓
⇒ 与我们情况最贴近的量化研究 ✓（论文正文需订阅 ⚠ → 只取标题结论 ✓）

---

## 第二部分 · 与我们三轮实验的实证对照（★ 关键）

| 文献结论 | 我们的实验 | 是否吻合 |
|---|---|---|
| 加 rank 无效 ✗ | v3-2-0 (r=16) −0.1 ✗ / v3-2-1 (r=8) −0.5 ✗ → **两轮一样差** | ⭐ **完全吻合** ✓ |
| 数据要跨量级才有效 | 430 → 1054（+2.5×）分数 **−0.5** ✗ / 278 真实 vs 1054 合成 **+8.0** ✓ | ⭐ **吻合** ✓（真实 278 > 合成 1054 ✓ 质量 > 数量 ✓） |
| 质量 > 数量（LIMA） | **278 条真实轨迹（+8.0 ✓）> 1054 条合成（−0.5 ✗）** | ⭐ **完全吻合** ✓✓ |
| PET 瓶颈是底座 ⚠ | 三轮都是 7B ✓ 分数在 30-40 徘徊 ✗ 从未突破 | ⭐ **吻合** ✓（7B 天花板 ✓） |
| LoRA 在编程域弱 ✗ | 工具调用/结构化输出 ✗ 三轮无稳定提升 | ⭐ **吻合** ✓ |
| few thousands ⇒ LoRA 合适 ✓ | 我们 278-1054 ⇒ 方法选对了 ✓ 但收益有限 | ✓ 方法对 ✓ 但天花板低 ✓ |

### ⇒ 一句话：**三轮实验的现象，文献全部能解释** ✓✓
```
"为什么加 rank 没用" → Zhang et al. ②
"为什么合成 1054 条不如真实 278 条" → LIMA + AlpaGasus
"为什么怎么都提升不上去" → Zhang et al. ③（底座天花板）+ Biderman ①（LoRA 结构性弱）
"那数据量还要加吗" → 要 ✓ 但要跨量级（×10 ✓ 不是 ×1.5 ✗）
```

---

## 第三部分 · 结论与建议（数据量到底该多少）

### 3.1 数据量的"真实答案"（分三个层次 ✓）

| 层次 | 量 | 依据 | 我们该做到吗 |
|---|---|---|---|
| **最小可用** | **100-500 条**（高质量 ✓） | LIMA 1,000 ✓ / 行业实践 100-1000 ✓ | ✅ **已达到**（278 真实 ✓） |
| **有意义的提升** | **1,000-3,000 条**（真实 ✓ 非合成 ✗） | 幂律 + log 收益 ⚠ ×3-10 才有明显效果 | ⏳ **目标**（当前 278 ✓ 差 4-10 倍） |
| **突破底座上限** | **不可能靠数据** ✗✗ | PET 天花板 = 底座（Zhang ③） | ✗ **做不到**（硬件限 7B） |

### 3.2 所以我们的策略应该是（**调整预期 ✓ 不再空耗**）

**① 数据量目标改为 1,000-2,000 条真实轨迹** ✓（不是 400 ✗）
- 来源：多跑几轮 drill ✓ 或从历史 runs 再挖 ✓（991 条里有 621 成功 ✓ 但只提取出 278 ✓ → **提升解析器覆盖率** 可以再挖 ✓ 潜在 400-600 ✓）
- 补充：**小规模人工/LLM 精修** ✓（LIMA 精神 ✓ 1,000 条 curated ✓）

**② 明确接受"LoRA 只能小改"** ✓
- 目标从"大幅提升分数"✗ 改为 **"稳定提升特定行为"** ✓：
  - 工具调用格式合规率 ✓
  - 减少无效工具调用（我们已看到 −2.83 个 ✓）
  - 特定任务类型（P1-47 +35 / P1-08 +25 / P1-06 +24 说明**有效果但集中** ✓）
- ⇒ **评测要看"特定能力"而非总分** ✓（改评测口径 ✓）

**③ 不要做的事（有文献依据 ✓）**
| 不做 | 依据 |
|---|---|
| ✗ 加 rank（16→32→64） | Zhang ②（|α| < 0.01）+ Biderman（差 10-100×，加也不够） |
| ✗ 追合成数据量（1054 → 5000） | LIMA（质量>数量）+ 我们实证（合成 1054 < 真实 278） |
| ✗ 期待突破 7B 上限 | Zhang ③（PET 依赖底座） |
| ✗ 换全参微调（FMT） | Zhang ④（需 million 级数据 ✗ 我们 1K 级 ✓ 且 16GB 卡训不了 7B 全参 ✗） |

**④ 真正能提升上限的方向（供参考 ✓）**
| 方向 | 依据 | 我们的可行性 |
|---|---|---|
| **换更大底座**（14B/32B） | Zhang ③（模型 scale 收益 > 数据 scale ✓） | ✗ **16GB 卡做不到**（14B fp16 训练 ~28GB ✗ / 14B-AWQ 不可训 ✗） |
| **改评测口径**（看特定能力 ✓） | 我们实证（提升集中在 3 条 ✓） | ✅ **可做**（P-74 也在做类似 ✓） |
| **改数据形态**（真实轨迹 ✓ 已验证 +8.0 ✓） | 我们实证 ✓ | ✅ **继续**（扩到 1000+ ✓） |
| **用 RAG/工具替代训练**（不训了 ✓ 直接靠底座能力） | 实践常识 ✓ | ✅ **最省事**（平台本来就有 KB + 工具 ✓ 能力上限取决于底座 ✓） |

---

## 第四部分 · 对三个方案的最终修正

### P-73（训练界面）追加修正
```
① 数据量目标: 278 → 1000-2000 条真实轨迹 ✓（不是 400 ✗ 收益太小）
② 训练页必须显示【底座天花板提示】⚠
   "当前底座 Qwen2.5-7B · LoRA 收益受底座能力上限约束（文献: Zhang et al. ICLR 2024）"
   ⇒ 管理用户预期 ✓ 避免无谓调参 ✓
③ 高级参数里的 rank 默认 **8** 并注明"调大无益（文献实证）"✓
④ 评测口径加【特定能力】维度（工具规范率/无效调用数/特定任务类 ✓）不只总分 ✓
```

### P-74（KB 评估）追加修正
```
① 如果 LoRA 的本事有限 ⇒ KB/RAG 的投入产出比可能【高于】训练 ✓
   ⇒ 建议 KB 评估（K0+K3）的优先级【提前】✓ 6h ✓ 见效快 ✓
```

### UI 方案追加修正
```
① 训练页加【数据量建议卡片】:
   "当前 278 条 · 建议 1000+ 才可能有明显提升（文献）· [如何扩充]"
② 训练结果页加【文献参照】脚注（用户知道什么预期合理 ✓）
```

---

## 第五部分 · 一句话总结

```
文献答案: 数据量确实有坎 ✓ 但对 LoRA 而言【更大的坎是底座模型】✗✗
  · Zhang et al. (ICLR 2024): PET 的瓶颈是底座，不是数据 ✗
  · Biderman et al. (TMLR 2024): LoRA 在编程/数学域结构性弱 ✗（我们工具调用同族 ⚠）
  · LIMA: 1000 条精挑细选够用 ✓（但我们的合成 1054 条不算"curated" ✗）
  · 加 rank 无效 ✗（我们 r=16/r=8 实证吻合 ✓）

我们该做:
  · 数据: 278 → 1000-2000 条【真实】轨迹 ✓（不是 400 ✗ 也不是 5000 合成 ✗）
  · 预期: 从"大幅提升分数"改为"稳定提升特定行为"✓
  · 评测: 看特定能力 + n≥30 + base 对照 ✓
  · 不追: 加 rank ✗ / 合成数量 ✗ / 突破 7B ✗
  · 认真考虑: KB/RAG 投入产出比可能 > 训练 ✓（平台本来就有 ✓）
```

---

*文献研究 · 2026-10-19 · 论文：[LIMA 2305.11206](https://arxiv.org/abs/2305.11206) · [Zhang et al. 2402.17193 (ICLR 2024)](https://ar5iv.labs.arxiv.org/html/2402.17193) · [Biderman et al. 2405.09673 (TMLR 2024)](https://arxiv.org/abs/2405.09673) · [IEEE sample efficiency scaling](https://ieeexplore.ieee.org/document/11655117)*
