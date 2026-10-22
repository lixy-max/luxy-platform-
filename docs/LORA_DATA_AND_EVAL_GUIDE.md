# LoRA 训练：数据形态 · 评测标准 · 训练原理

> 用途：调整数据、设计评测、后续重训时照这份走。
> 来源：① 我查过的一手文献（末尾附链接）② 本项目 2026-10-14/15 的实测（含一次失败的语义 LoRA）。
> 记号：**【实证】**=有一手来源；**【我们的实测】**=本项目真实数据；**【开放】**=证据薄、待我们自测。

---

## 0. 一句话背景（为什么要写这份）

我们昨晚训了 `semantic_intent_v1`（1322 条 → 2 epochs，QLoRA），结果是：
训练 loss 从 2.56 掉到 **0.0003**，而留出集上 **工具名正确率 25%、intent_type 命中 0/12（全塌成一类）**。
⇒ 这不是"学得好"，是**把可逐字背诵的标签背下来了**。这份文档就是把"为什么会这样、数据该长什么样、该怎么评"讲清楚。

---

# 第一部分 · LoRA 训练原理（够用的那部分）

## 1.1 LoRA 在学什么
冻结原权重 W，只训练一对低秩矩阵 A、B，前向变成 `W + BA`；**rank 决定"能给模型加多少新东西"**。
- `lora_rank` = 容量旋钮；`lora_alpha` 通常取 2×rank（本项目训练脚本就是 rank 32 / alpha 64）
- 训练只更新 A/B ⇒ 可训练参数极少、显存小、对原能力破坏小

## 1.2 ★ 秩容量墙（LoRA 特有，最容易被误读成"没学会"）
**【实证】** [LoRA Learns Less and Forgets Less](https://arxiv.org/abs/2405.09673)（Biderman 等，TMLR 2024）：
> "in the standard low-rank settings, **LoRA substantially underperforms full finetuning** …
> full finetuning learns perturbations with a **rank that is 10-100X greater than typical LoRA configurations**"

⇒ 若任务需要"高秩更新"，低 rank 会**卡在容量墙**，训多久都不跳。
**判据（很重要）**：把 rank 从 32 提到 128 立刻变好 ⇒ **是容量不够**，不是"还没顿悟"。
**【我们的实测】** 我们用 rank 32 训了 24 类结构化输出任务 —— 这个 rank 很可能偏低（24 类 × 多字段 ⇒ 需要更多容量）。

## 1.3 数据量 ↔ 优化量（真正的"顿悟"条件）
**【实证】** [Grokking](https://arxiv.org/abs/2201.02177)（Power 等，OpenAI 2022）：
> "generalization performance from random chance level to **perfect generalization** … can happen
> **well past the point of overfitting**"；"**smaller datasets require increasing amounts of optimization**"
- 顿悟签名 = **训练 loss 早已很低 + 测试指标长期不动（平台期）+ 之后突然跃升**
- 典型配方 = 小数据集 + 海量步数 + 正则（一般要 weight decay）+ 任务有可学的通用算法

**反过来说**：我们只跑 2 epochs，**从没给过平台期任何时间** ⇒ 谈不上顿悟；0.0003 是记忆。

## 1.4 "涌现"的争议（直接决定我们怎么评测）
**【实证】** [Are Emergent Abilities a Mirage?](https://arxiv.org/abs/2304.15004)（Schaeffer 等, 2023）：
> "emergent abilities appear due to the researcher's choice of **metric** … **nonlinear or discontinuous
> metrics produce apparent emergent abilities**, whereas **linear or continuous metrics produce smooth,
> continuous predictable changes**"

⇒ **两条硬纪律**：
1. 评测别用"整条输出对/错"这种**不连续**指标（我们上一版正是 ✗）⇒ 会掩盖渐进进步、也会造出**假涌现**；
2. 要看"有没有在变好"，必须看**连续指标的趋势曲线**。

## 1.5 微调能移动"涌现点"（对我们是好消息）
**【实证】** [Predicting Emergent Capabilities by Finetuning](https://arxiv.org/abs/2411.16035)（Snell 等, 2024）：
> "finetuning LLMs on a given task can **shift the point in scaling at which emergence occurs towards
> less capable models** … finetune with **varying amounts of data** and fit … '**emergence laws**'"

⇒ 同一个能力，**微调能让它更早出现**，而**数据量是可调的旋钮**。
⇒ 含义：与其等"参数够大"，不如**把数据量/质量做对**（这也是我们该走的路）。

## 1.6 显存与超参（本项目实测口径）
**【我们的实测】**
- 基座 `Qwen2.5-7B-Instruct`(FP16, 15GB) + QLoRA 4bit + `cutoff_len 2048` + `batch 1 × grad_accum 8` ⇒ 16GB 卡**可训**（权重≈4GB + 激活）
- `train_lora.py` **明确拒绝 AWQ/GPTQ/FP8 基座**（量化底座不能训 LoRA，只能推理）
- 训练前必须停 vLLM（它常驻 ~15GB）；训完自动拉起
- 1322 条 × 2 epochs = 298 step 用了 **64 分钟**（约 12.9 s/step）

## 1.7 三种"没学会"的签名（先分清再改）
| 签名 | 现象 | 诊断 | 处方 |
|---|---|---|---|
| **记忆** | train loss→≈0，eval 崩/塌成一类 | 标签可逐字抄 + 数据多样性低 | 改标签形态（第二、三部分） |
| **容量墙** | train loss 降不动，加 rank 明显变好 | rank 太小 | 提 rank（32→128→256） |
| **顿悟未到** | train loss 低，eval 平台期后**突跳** | 数据小 + 步数不够 + 无正则 | 加步数 + weight decay + 连续指标盯曲线 |
| （对照）**正常** | 两条曲线一起平滑上升/下降 | 没问题 | 扩数据、微调超参 |

---

# 第二部分 · 数据质量形态（SFT/LoRA 数据该长什么样）

## 2.1 一句话原则
> **模型学的是"输入 → 输出"的映射 ⇒ 标签必须"不可抄、可验证、有区分度"。**
> 如果标签能靠复制输入或背模板得到，训练就只会得到记忆。

## 2.2 我们踩过的 7 个坑（反例清单，务必对照）
| # | 坑 | 我们的实测证据 | 后果 |
|---|---|---|---|
| 1 | **system 每条几乎相同且极长** | system 占整条样本 **88% 的 token**（2107 字符/条 × 594 条） | 算力花在抄模板；schema 被背下来 |
| 2 | **标签含大段可逐字抄的散文** | goal/constraints 是我写死的长句 | loss 秒降到 0.0003（记忆） |
| 3 | **句子多样性不足** | 594 条归一化后只有 **195 个不同句子**（平均复制 3 次），24 类 ⇒ 每类约 8 个句子 | 覆盖不了类别边界 |
| 4 | **类别严重失衡** | `query_knowledge_base` 占 **55%** | 模式坍塌（12/12 全预测成同一类） |
| 5 | **关键能力样本太少** | `should_ask=true` 仅 **6%** | 学不会"信息不足先追问" |
| 6 | **环境是编的** | 快照是我造的 4 个假变体 | grounding 学不实（模型学不到"看现状说话"） |
| 7 | **字段名/名字手工映射** | 例：`kb_rebuild → create_knowledge_base`（语义就错了） | 教了错的工具语义 |

## 2.3 正例形态（推荐）
**① 标签要"动作型"，不要"叙事型"**（对 agent 真正有用，且难背）：
```jsonc
// ✗ 叙事型（可逐字抄）
{"intent_type":"kb_ingest","goal":"把学习成果写入 agent_use",
 "constraints":["目标库必须是 agent_use","不得写入其它库","该库是重要资产，禁止破坏/覆盖"], "plan":[...]}

// ✓ 动作型（短、结构化、可逐字段校验）
{"intent":"kb_ingest","target":{"kb":"agent_use"},"ask":false,"plan":["list_knowledge_bases","ingest_kb"]}
```
- `constraints` 这类"规则"应搬进 **system 提示**（对所有样本通用）✗ 不要放进每条标签 ✓
- `goal` 若保留，应短（≤20 字）且**不允许与输入同词复制**

**② system 要瘦身到 1/5 以内**（目标 ≤400 字符）：
- 只留：角色一句 + 输出 schema + 必要的枚举
- 环境快照按需注入：只在任务**真的依赖现状**时给（而不是每条都塞）
- 判断标准：**system 字数 / 整条样本字数 < 20%**

**③ 每条样本必须"字段级可验证"**：
- 枚举字段（intent）∈ 白名单
- 名字字段（tool/kb/lora/dataset）必须**真实存在**（生成时用实时清单校验 ✓ 我们已做）
- 数值/数量类字段可自动断言

**④ 数据配额建议（起步值，可调）**
| 维度 | 建议 | 理由 |
|---|---|---|
| 每类样本数 | **≥60**（且各类差距 ≤3×） | 防模式坍塌 |
| 不同句子数 | **≥ 每类 20 个真句**（不是标点变异） | 学语义边界 |
| 关键能力占比 | 追问/拒答类 **≥15%** | 这是最想要的能力 |
| 负例/边界 | **≥20%**（不存在名字、歧义指代、超范围请求） | 防瞎猜 |
| 长度分布 | 短句(≤15字) ≥30% · 长句(≥40字) ≥20% | 覆盖真实口气 |
| 多要求连发 | ≥10% | 用户常一句多事 |

**⑤ 句子多样性从哪来（按性价比排序）**
1. **真实语料**（最值钱）：真实对话/工单/你自己的原话 ⇒ 我们已有 `data/user_voice/seeds.jsonl`（23 条 ✓ 太薄，要扩到 150+）
2. **语义级改写**（可用云大模型）：同义不同词、换语序、换指代方式 —— ✗ 注意：**不是**加标点/改错字那种变异（那不算多样性）
3. **模板组合**：用真实资源名生成（我们已做，但要**限类别配额** ✗ 否则又失衡）

## 2.4 切分与泄漏（我们踩过）
- **训练/留出必须分层**：按 `intent_type` 每类留 2~3 条 ✓（我们原来"每 6 条种子留 1"⇒ 抽到整族同一句话，12 条几乎一样 ✗）
- **同族变异不能跨集**：同一句子的 6 个变异要么全进训练要么全进留出（否则留出集被"见过"✗）
- **答案泄漏检测**：如果标签字段能在输入里原样找到 ⇒ 标记可疑（可自动跑一遍）

## 2.5 数据自检清单（可脚本化，建议进 `make`）
```
□ system 字数 / 整条样本 < 20%
□ 标签里不含长散文（每个字段 ≤ 30 字，除明确的"解释类"字段）
□ 每类样本数 ≥60，最大/最小 ≤3
□ 不同句子数 / 样本数 ≥ 0.5
□ 留出集按类分层，覆盖全部类别
□ 每个 tool/kb/lora/dataset 名都存在于真实清单
□ 追问类 ≥15%、负例 ≥20%
□ 同族不跨集；无近重复（如 3-gram 重合 > 0.9 的样本对）
```

---

# 第三部分 · 评测标准

## 3.1 第一原则：**用连续指标**（文献教训 + 我们的错误）
**【实证/纪律】** 不连续指标 ⇒ 假涌现 + 掩盖进步。所以：
- ✗ 不要只报"整条 JSON 是否完全正确"
- ✓ 报**字段级**与**序列级**的连续分，并给出**逐类**结果与**趋势**

## 3.2 指标矩阵（建议固定为评测模板）
| 指标 | 定义 | 为什么 | 阈值（起步） |
|---|---|---|---|
| **JSON 合法率** | 可解析且含必需键 | 工程可用性前提 | ≥95% |
| **枚举准确率** | intent_type 正确 | 基础分类 | ≥85% |
| **逐类准确率** | 每类单独算（并报最小类） | 抓"塌成一类" | 最小类 ≥60% |
| **name 准确率** | targets 里的名字真实且正确 | 治瞎猜 | ≥90% |
| **工具序列编辑距离** | pred 与 gold 的 `plan.tool` 序列的归一化编辑距离（越小越好） | 部分正确也给分 ✓ | ≤0.3 |
| **plan 顺序正确率** | "只读在写操作之前"是否成立 | agent 安全纪律 | ≥90% |
| **追问混淆矩阵** | 应问/不应问 × 问了/没问（TP/FP/TN/FN） | 核心能力，二值但要看四格 | 召回 ≥80%，误问 ≤10% |
| **多要求覆盖率** | 一句话多任务时，plan 覆盖了几个子任务 | 治"只做第一件" | ≥80% |
| **幻觉率** | 编造了快照里不存在的名字 | 最危险的一类错 | ≤5% |

## 3.3 评测流程（可复现）
1. **固定留出集**（分层、且与训练同族隔离 ✓）
2. **temperature=0**（可复现）+ 固定 seed
3. **同一条样本跑 1 次**（若模型不稳定，再报 3 次的方差）
4. **同时报基座（不挂 LoRA）的分数** ⇒ 才能证明 LoRA **真的带来增益**（我们上一版就没这个对照 ✗）
5. **逐条留明细**（哪条错、错在哪字段）✗ 不要只给一个总数

## 3.4 训练过程中的评测（想谈"顿悟/相变"必须有）
- 每 **N=20 步** 在留出集上评测一次，落盘成 JSONL ✓
- 画三条曲线：`train_loss` / `eval_连续总分` / `eval_逐类最低分`
- 判定规则（写死，别靠感觉）：
```
出现"平台期 + 突跳"且【无 weight decay 的对照组没有】 ⇒ 支持 grokking ✓
提 rank 立刻变好                                   ⇒ 容量墙 ✗（不是顿悟）
两条曲线平滑同向                                   ⇒ 正常学习 ✓（别等顿悟，去修数据）
train↓ 而 eval↓（或塌成一类）                        ⇒ 记忆/失衡 ✗（去修标签与配额）
```

## 3.5 达标之后才算"能用"
```
① 全部指标过阈值（3.2 表）
② 相对基座有显著增益（同一留出集，LoRA vs 不挂 LoRA）
③ 界面端到端 3 条真实原话实测通过（挂新 LoRA → 是否先 list、该问就问、别瞎猜）
④ 无回归：原来能做的任务不掉（LoRA 的"忘得少"是优点 ✓ 但仍要测）
```

---

# 第四部分 · 落到本项目：下一步改动清单

## 4.1 数据生成器（`scripts/gen_semantic_dataset.py`）
```
□ 标签改"动作型"：删 goal/constraints 长句；只留 intent/target/ask/plan
□ system 瘦身到 ≤400 字符（schema 精简；环境按需注入）
□ 真实句子扩到 ≥150 条（扩 seeds.jsonl；再用云做语义级改写，不是标点变异）
□ 类别配额：每类 ≤120 且 ≥60；失衡就补少数类，别只砍多数类
□ 追问类提到 ≥15%，负例 ≥20%
□ 环境用【真实快照】（从 /kb/list、/loras、workflow_config.json 取当期真值）
□ 修掉工具名手工映射的语义错配（对照 /docs/tool/list 与 AGENT_TOOLS 真实语义）
□ 切分改【按类分层】+ 同族不跨集
□ 生成时跑 2.5 的 8 条自检，不达标就拒绝产出
```

## 4.2 评测脚本（`scripts/eval_semantic_intent.py`）
```
□ 二值改为连续（字段级 F1 + 工具序列编辑距离 + 逐类）
□ 加【基座对照】（不挂 LoRA 同跑一遍）
□ 输出逐条明细 + 逐类汇总 + 追问混淆矩阵
□ 支持 --steps-eval：训练中定时评测，落 JSONL 供画曲线
```

## 4.3 训练配置（`train_lora.py` / `scripts/train_semantic.sh`）
```
□ 加 --weight_decay（grokking 配方之一；默认现在没显式给）
□ rank 扫描 {8, 32, 128}（判"容量墙 vs 顿悟"必须的对照）
□ epochs 放到 5~30（配合每 20 步评测；别再用 2 epochs 下结论）
□ --eval_steps 固定值（不要只靠 epoch 级 eval）
□ 记录：训练时的真实 s/step、显存峰值（便于估下一轮时间）
```

## 4.4 便宜可跑的"相变小实验"（1~2 小时/组）
```
矩阵: 数据量 {200, 500} × epochs {5, 30, 80} × weight_decay {0, 0.1} × rank {8, 32, 128}
观测: 每 20 步评测（连续指标）→ 三条曲线
产出: 一张判定表（记忆 / 容量墙 / 顿悟 / 正常），据此决定"提 rank"还是"扩数据"还是"改标签"
```

---

## 附录 A · 参考文献（我实际查过的一手来源）
1. Power et al., **Grokking: Generalization Beyond Overfitting on Small Algorithmic Datasets**, 2022 — https://arxiv.org/abs/2201.02177
2. Schaeffer et al., **Are Emergent Abilities of Large Language Models a Mirage?**, 2023 — https://arxiv.org/abs/2304.15004
3. Snell et al., **Predicting Emergent Capabilities by Finetuning**, 2024 — https://arxiv.org/abs/2411.16035
4. Biderman et al., **LoRA Learns Less and Forgets Less**, TMLR 2024 — https://arxiv.org/abs/2405.09673

> **【开放】**：关于"LoRA 训练中是否存在 grokking 式相变"，我没有找到可信的一手实证
> （搜索里出现过几个 arXiv 编号在时间上不成立的链接，我不采信也未引用）⇒ 这个问题只能靠 4.4 的自测回答。

## 附录 B · 本项目实测数据（可复查）
| 项 | 数值 | 出处 |
|---|---|---|
| 数据集规模 | 1322 训练 / 52 留出（首版）；546 / 48（均衡版） | `training_data/*_report.md` |
| system 占比 | **88%** 的 token | `training_output/review_all.tsv` |
| 不同句子数 | 594 条 → **195 个** | 同上（归一化统计） |
| 类别失衡 | `query_knowledge_base` **55%**（首版） | 生成报告 |
| 训练结果 | loss 2.56 → **0.0003**；2 epochs / 298 step / **64 分钟** | `training_output/semantic_intent_v1/trainer_log.jsonl` |
| 留出集评测 | JSON 100% ✓ / 工具名 **25%** ✗ / intent **0/12** ✗ | `training_output/semantic_intent_v1_eval_report.md` |
| 目标输出重复 | 594 条 → **234 种**（平均用 2.5 次） | 同上 |

## 附录 C · 术语
- **SFT**：监督微调；**QLoRA**：4bit 量化底座上的 LoRA
- **rank / alpha**：容量与缩放；**target modules**：给哪些层加 LoRA
- **grokking（顿悟）**：过拟合很久之后测试性能突然跃升
- **模式坍塌**：模型把所有输入都预测成同一类
- **泄漏**：留出集与训练集同族/同句，导致指标虚高
- **连续指标**：能反映"部分正确"的分数（如字段级 F1），与"整条对错"相对
