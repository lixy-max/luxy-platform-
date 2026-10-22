# 文献库（Literature Registry）· 2026-10-19 建

> 配套 `docs/PROJECT_PRINCIPLES.md` 原则一「调研先行」
> 任何新方向开工前必须在这里查到 ≥2 篇依据 ✓

---

## 一、训练与微调（LoRA / SFT）

### L01 · LIMA: Less Is More for Alignment（Meta, NeurIPS 2023）
- **链接**：https://arxiv.org/abs/2305.11206
- **结论**：**1,000 条精挑细选**的数据就能做到很好 ✓ "绝大部分知识在预训练时已学会" ✓
- **对我们的含义**：**质量 > 数量** ✓ 我们实证吻合（278 真实 > 1054 合成 ✓）
- **适用场景**：数据量决策 / 数据质量规范

### L02 · When Scaling Meets LLM Finetuning（Google DeepMind, ICLR 2024）⭐
- **链接**：https://ar5iv.labs.arxiv.org/html/2402.17193
- **结论**：
  1. 微调数据遵循幂律（越多越好 ✓ 但 log 级收益 ⚠）
  2. **增大 LoRA rank 无效** ✗（|α|≪1e-2 甚至反向）
  3. **PET 天花板 = 底座模型** ⚠⚠（不是数据）
  4. few thousands ⇒ LoRA ✓ / million ⇒ 全参 ✓
- **对我们的含义**：**最重要的一篇** ✓ 解释了为什么 7B LoRA 追总分无效 ✗
- **适用场景**：任何"要不要训练/加 rank/加数据"的决策

### L03 · LoRA Learns Less and Forgets Less（Biderman et al., TMLR 2024）
- **链接**：https://arxiv.org/abs/2405.09673
- **结论**：LoRA 在**编程/数学域明显不如全参** ✗ 全参学的 rank 是 LoRA 的 10-100 倍 ✓ LoRA 更不易遗忘 ✓
- **对我们的含义**：LoRA 擅长"小改"✓ 不擅长"大幅提升" ✗
- **适用场景**：方法选型 / 预期管理

### L04 · How Many Examples Do You Need?（IEEE）
- **链接**：https://ieeexplore.ieee.org/document/11655117
- **结论**：PET 的**样本效率标度律** ✓（"需要多少样本"是可建模的）
- **适用场景**：数据量规划

---

## 二、工具调用 / 函数调用（★ 我们最该做的方向）

### L05 · Toolshed: Advanced RAG-Tool Fusion（arXiv 2410.14594）⭐⭐
- **链接**：https://arxiv.org/abs/2410.14594
- **结论**：**工具检索 Recall@5 绝对提升 +46% / +56% / +47%**（三档基准）✓ **零微调** ✓
- **技术**：pre-retrieval（工具文档增强）+ intra-retrieval（查询变换）+ post-retrieval（精炼+反思）
- **对我们的含义**：**最划算的一步** ✓ 我们工具 60+ ✓ 已有 KB ✓ 立刻可做 ✓
- **适用场景**：工具检索 / 工具知识库

### L06 · Repairing Tool Calls Using Post-tool Execution Reflection and RAG（arXiv 2510.17874）⭐⭐
- **链接**：https://arxiv.org/abs/2510.17874
- **结论**：**正确回答率 +36%** ✓ 55% 模型过执行率提升 ✓ **故障排查文档比官方文档 +10%** ✓
- **对我们的含义**：281 个真实错误 ✓ = 现成的 troubleshooting 素材 ✓ 零训练 ✓
- **适用场景**：错误修复 / KB 内容设计（多存"错误→修复"✓）

### L07 · NexusRaven（Nexusflow, EMNLP 2025）
- **链接**：https://aclanthology.org/2025.emnlp-main.1090.pdf
- **结论**：**7B 开源模型在函数调用基准上超过 GPT-4** ✓
- **对我们的含义**：证明 7B 能做好工具调用 ✓（只要专门训练 ✓）

### L08 · Hammer-7B（MadeAgents）
- **链接**：https://huggingface.co/MadeAgents/Hammer-7b
- **结论**：**7B 在工具调用综合榜单排名第一** ✓（超过大量更大模型）
- **对我们的含义**：同上 + **可直接下载试** ✓（原则二：先找不训 ✓）

### L09 · xLAM / APIGen（Salesforce, arXiv 2406.18518）
- **链接**：https://arxiv.org/abs/2406.18518
- **结论**：60k+ **可验证**函数调用数据 ✓ **xLAM-7B 在 BFCL 上超 GPT-4** ✓
- **对我们的含义**：数据合成方法论 ✓ + 7B 可达 SOTA ✓

### L10 · Gorilla: LLM Connected with Massive APIs（NeurIPS 2024）
- **链接**：https://proceedings.neurips.cc/paper_files/paper/2024/hash/e4c61f578ff07830f5c37378dd3ecb0d-Abstract-Conference.html
- **结论**：**retriever-aware training** ✓ 接检索器后 API 调用准确率显著超 GPT-4 ✓
- **对我们的含义**：检索 + 训练结合 ✓

---

## 三、知识库 / RAG / 经验学习

### L11 · ExpeL: LLM Agents Are Experiential Learners（AAAI 2024）✓ 正面
- **链接**：https://arxiv.org/abs/2308.10144
- **结论**：**云模型拿不到权重 ⇒ 靠经验注入 KB 学习** ✓ "随经验积累持续提升" ✓
- **对我们的含义**：**"云模型自动入库"的学术原型** ✓ 我们的 reflect 飞轮方向对 ✓
- **适用场景**：学习增强 / 数据飞轮

### L12 · CTIM-Rover: From Knowledge to Noise（REALM 2025）✗ 反面
- **链接**：https://aclanthology.org/2025.realm-1.30/
- **结论**：真实复杂任务上**经验库不提升反下降** ✗ 根因 = **裸存轨迹的噪音** ✗
- **对我们的含义**：**必须蒸馏 + 过滤 + 分库** ✗ 裸存轨迹有害 ✗
- **适用场景**：自动入库的质量控制

### L13 · RAG: Retrieval-Augmented Generation（Lewis et al. 2020）
- **链接**：https://arxiv.org/abs/2005.11401
- **结论**：RAG 在知识密集型任务上**显著提升事实准确性** ✓（多篇基准 +10~30 分）
- **适用场景**：KB 价值论证

### L14 · Lost in the Middle（Liu et al. 2023）
- **链接**：https://arxiv.org/abs/2307.03172
- **结论**：长上下文中间信息会被忽略 ✗ ⇒ **RAG 比塞长上下文可靠** ✓
- **适用场景**：KB vs 长上下文选择

### L15 · The Power of Noise（Cuconasu et al. 2024）
- **链接**：https://arxiv.org/abs/2401.14887
- **结论**：一篇干扰文档就能降低准确率 ✗（但 2025 元分析指出被实验设置放大 ⚠）
- **适用场景**：检索质量 / 噪音控制

---

## 四、速查表（按问题找文献）

| 你想问 | 查哪篇 |
|---|---|
| 数据要多少才够？ | L01（1000 精选）· L02（幂律）· L04 |
| 要不要加 rank？ | **L02（无效 ✗）** · L03 |
| 7B 训练有没有意义？ | **L02（天花板=底座 ✗）** |
| 什么方向 7B 能做好？ | **L07/L08/L09（工具调用 ✓）** |
| 怎么不训练就提升？ | **L05（工具检索 +46%）** · L06（修复 +36%） |
| 经验自动入库有没有用？ | **L11（有效 ✓）** · **L12（裸存=噪音 ✗）** |
| KB 有多少价值？ | L13（+10~30）· L14 |
| 怎么评 RAG？ | L05（Recall@k）· L06（pass rate）· RAGAS |

---

*建立于 2026-10-19 · 当前 15 篇 · 后续新增必须注明结论 + 对我们的含义 + 适用场景*
