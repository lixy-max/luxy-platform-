# 知识库飞轮方案（2026-10-21）

> **一句话**：让平台「用一次 → 强一分」，知识从**轨迹**自动流向**能力**，每轮必须 A/B 验证，不改进就回滚。

---

## 一、为什么需要飞轮（问题陈述）

### 现状：知识是**死的**
```
· learned_skills KB: 22 条做法（T-1 手工跑一次 ✓ 之后不再更新 ✗）
· 每次对话产生的轨迹: 只记录 ✗ 不回流 ✗
· 学到的东西: 不进入模型能力 ✗
⇒ 平台不会因为「用得更多」而变强 ✗
```

### 关键约束（★ 今晚实测得出 · 决定了方案形态）
```
✗ **检索式 KB 注入有害** −29.5pp（T-3 组合 A/B 实测 ✗）
   原因: 4B 无法区分"参考资料"与"任务要求" ✗ 注入什么都会带偏 ✗
   ⇒ **飞轮绝不能靠"每次塞知识进上下文"** ✗

✓ **训练数据固化有效**（T-2 实测 ✓）
   LoRA 让 4B 的 tool_calls 从 **0 → 1** ✓ 质变 ✓
   ⇒ **知识必须变成"能力"（权重/路由/提示），不是"上下文"** ✓
```

---

## 二、飞轮结构（4 阶段闭环）

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│   ① 采集 Collect                                             │
│   每次对话/工具调用 → 轨迹                                    │
│   data/trajectories/*.jsonl  ← 已有 ✓                        │
│   scripts/learn_sink.py      ← 已有 ✓                        │
│                    ↓                                         │
│   ② 蒸馏 Distill                                             │
│   轨迹 → 结构化知识（task_type / trigger / steps / tools_ok / pitfalls）│
│   scripts/skills_extractor.py ← 已有 ✓（30 轨迹 → 22 做法 ✓）│
│                    ↓                                         │
│   ③ 固化 Solidify  ★★★ 关键步骤                               │
│   三条通道（见第三节）                                        │
│                    ↓                                         │
│   ④ 验证 Validate  ★ 铁律                                     │
│   V2 评测 A/B → 通过才进主库 ✓ 不通过回滚 ✗                  │
│   tools/eval_v2_run.mjs + scripts/eval_v2_score.py ← 已有 ✓  │
│                    ↓                                         │
│   回到 ①（知识变强 ⇒ 用得更好 ⇒ 更多高质量轨迹）              │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、★ 固化通道（三条 · 按证据强度排序）

### 通道 A：训练数据固化（**已验证有效 ✓ 首选**）

```
做法（skills.jsonl）→ 生成 sharegpt 训练样本 → QLoRA 训练 → V2 A/B → 通过则替换 LoRA
```
| 项 | 值 |
|---|---|
| 证据 | ✅ T-2 实测: tool_calls **0→1** ✓ 质变 ✓ |
| 产出 | `data/flywheel/train_round<N>.jsonl` + 新 LoRA |
| 成本 | GPU 训练 ~10-20min/轮 ⚠ |
| 判定 | V2 命中率 ≥ 当前版本 −2pp ⇒ 通过 ✓ |
| 适用 | **4B 唯一可行通道** ✓ |

### 通道 B：工具路由固化（**不用 GPU ✓ 低成本**）

```
做法里的 tools_ok → 扩充 T-4 的【域关键词表】→ 动态工具检索更准
```
| 项 | 值 |
|---|---|
| 证据 | ✅ T-4 离线验证覆盖率 **100%** ✓（30/30 ✓） |
| 产出 | 更新的 `TOOL_DYN_DOMAINS` 关键词 ✓ |
| 成本 | 零 GPU ✓ 秒级 ✓ |
| 判定 | 离线覆盖率不下降 ✓ + V2 命中率不降 ✓ |
| 适用 | 两者都行 ✓ |

### 通道 C：系统提示固化（**少而精 ✓ 不是检索注入 ✗**）

```
高频 + 高置信的做法（A/B 验证过 ≥2 次）→ 手工审核 → 写进 buildAgentSystemPrompt
```
| 项 | 值 |
|---|---|
| 证据 | ⚠ 未验证（需 A/B ✓） |
| 产出 | `webui/index3.html::buildAgentSystemPrompt()` 的固定片段 ✓ |
| 成本 | 零 GPU ✓ 但要人工审核 ✓ |
| 判定 | **必须 A/B 验证** ✓ 不通过就撤回 ✗ |
| 限制 | **只放"纪律"类**（如"必须先调工具再回答" ✓）**绝不放"知识"类** ✗（我们已证明知识注入有害 ✗） |

### ✗ 明确不做：检索式上下文注入
```
路径: 每次对话 → 查 KB → 拼片段 → 塞 prompt
理由: **T-3 实测 −29.5pp** ✗ 4B 分不清参考与任务 ✗
⇒ 飞轮不采用 ✗（除非换 7B 并重新验证 ⚠）
```

---

## 四、质量闸门（5 道 · 每道都会丢弃知识）

```
① 入口闸门（采什么）
   只收【真成功】轨迹 ✓
   · 有真工具调用（非 provider 占位 ✓）
   · final 非空 ✓
   · 无免责声明 ✓
   实测: 149 轨迹 → **110 通过（74%）** ✓

② 蒸馏闸门（提炼什么）
   只留【可复用做法】✓
   · steps ≥ 2 ✓
   · 无具体文件名/绝对路径 ✓（要抽象 ✓）
   · 无免责 ✓
   实测: 自测 9/9 ✓

③ 合并闸门（去重）
   task_type 相同 **且工具集有交集** 才合并 ✓
   （实测坑: 只按 task_type 会串味 ✗）

④ ★ 验证闸门（最重要 ✓）
   新知识 **必须** 经 V2 A/B 验证才能进主库 ✓
   · 配对对比 n≥30 ✓
   · |t| > 2 显著 ✓
   · 不显著变差 ⇒ 不进 ✗

⑤ 回滚闸门
   上线后监控: 任一档掉 >5pp ⇒ **自动回滚** ✓
   版本化: data/flywheel/versions/<ts>/ ✓ 可随时回
```

---

## 五、实现计划（分 3 期）

### 期 1：飞轮骨架（不用 GPU · 6h）
```
新增 scripts/flywheel.py:
  · collect(days=7)      → 收轨迹（trajectories + learn_sink）✓
  · distill()            → 调 skills_extractor ✓
  · dedupe()             → 调 dedupe_skills ✓
  · solidify(track)      → 按通道产出（train/routing/prompt）✓
  · validate(arm)        → 调 eval_v2_run + eval_v2_score ✓
  · promote_or_rollback()→ 通过则更新主库 ✓ 否则回滚 ✗
  · status()             → 飞轮状态报告 ✓

新增 data/flywheel/:
  · state.json           → 当前轮次 / 版本 / 指标历史 ✓
  · versions/<ts>/       → 每个版本的做法快照 ✓
  · train_round<N>.jsonl → 训练数据 ✓

新增 scripts/flywheel_weekly.sh:
  · 采集 → 蒸馏 → 固化(路由) → 验证 → 报告 ✓
```

### 期 2：通道 A 实装（要 GPU ⚠）
```
· 做法 → sharegpt 训练样本生成器（use skills' steps as function_call ✓）
· 自动训练（复用 train_lora.py ✓）
· V2 A/B 验证 ✓
· 通过则替换当前 LoRA ✓
⏳ 等 7B 底座或用户不用 5060
```

### 期 3：自动化 + 监控
```
· cron 每周跑 ✓
· 飞轮看板（UI 页面 ✓ 复用现有 UI 风格 ✓）
· 告警: 指标掉 >5pp ⇒ 通知 + 自动回滚 ✓
```

---

## 六、飞轮指标（怎么知道它在转）

| 指标 | 定义 | 目标 |
|---|---|---|
| **飞轮转速** | 每周新增有效做法数 ✓ | ≥3 条/周 |
| **知识质量** | 做法 A/B 通过率 ✓ | ≥60% |
| **能力增益** | V2 分数周环比 ✓ | >0（不要求每周 ✓ 但半年要有趋势 ✓）|
| **遗忘检测** | 老任务是否退化 ✓ | 掉 <5pp |
| **知识库健康** | 做法总数 / 平均 steps / 工具覆盖 ✓ | 无膨胀（上限 500 条 ✓）|

---

## 七、风险与对策（诚实 ✓）

| 风险 | 严重度 | 对策 |
|---|---|---|
| **幻觉知识**（LLM 蒸馏出错 ✗）| 高 | 人工抽检 10% + A/B 验证 ✓ |
| **知识膨胀**（KB 无限长 ✗）| 中 | 上限 500 条 + LRU 淘汰 ✓ |
| **自我污染**（错误固化 ✗）| 高 | 版本化 + 一键回滚 ✓ |
| **过拟合评测集** ✗ | 高 | **评测集轮换**（V2 → V3 每季度 ✓）+ 留出集 ✓ |
| **4B 无收益区间**（今晚已证明 ✗）| 高 | **通道 A 只在 7B 上有意义** ⚠ 4B 只做路由固化 ✓ |
| **飞轮转但不变强**（最可能 ✓）| 高 | **每轮必须 A/B** ✓ 不显著就不上线 ✗ 宁可慢 ✓ |

---

## 八、与现有资产的关系（不重造轮子 ✓）

| 已有 | 用途 | 状态 |
|---|---|---|
| `scripts/skills_extractor.py` | 蒸馏 ✓ | ✅ T-1 |
| `scripts/learn_sink.py` | 采集 ✓ | ✅ 已有 |
| `tools/eval_v2_run.mjs` | 验证跑批 ✓ | ✅ T-5 |
| `scripts/eval_v2_score.py` | 验证判分 ✓ | ✅ T-5 |
| `scripts/tool_retriever.py` | 通道路由 ✓ | ✅ T-4 |
| `docs/EVAL_SET_V2.json` | 评测集 ✓ | ✅ 50 条 |
| `data/learning/skills/skills.jsonl` | 做法库 ✓ | ✅ 22 条 |
| **新增** `scripts/flywheel.py` | 编排器 | ⏳ 期 1 |
| **新增** `data/flywheel/` | 状态/版本 | ⏳ 期 1 |

---

## 九、诚实的预期

### 能做的 ✓
```
✅ 自动化"轨迹→知识"的管道（T-1 已手工跑通 ✓ 现在改成自动 ✓）
✅ 让工具路由随使用变准 ✓（通道 B ✓ 零 GPU ✓ 有离线验证 ✓）
✅ 知识版本化 + 可回滚 ✓（工程可靠性 ✓）
✅ 建立"每轮必验证"的纪律 ✓（这是最有价值的 ✓）
```

### 不能保证的 ✗
```
✗ **"平台越用越强"** ✗ —— 今晚数据说明 4B 上没有可检测的收益区间 ✗
✗ 通道 A 在 4B 上只能"从不会到会"✓ 不能再往上 ✗
⚠ 7B 上有希望（未验证 ⚠ 要 GPU）
```

### 最诚实的说法
```
飞轮的真正价值 = **把"知识不丢失 + 可验证 + 可回滚"的工程纪律建立起来** ✓
不是 = "自动变强" ✗（那需要 7B + 更多数据 + 更长时间 ✓）
```

---

*方案 · 2026-10-21 · 依据: T-1(蒸馏 ✓) T-2(训练固化 0→1 ✓) T-3(KB注入 −29.5pp ✗) T-4(路由 100% ✓) T-5(评测 ✓)*

---

## 十、★ 文献修订（2026-10-21 晚 · 依据 `docs/KB_FLYWHEEL_REFERENCES.md`）

调研后吸收 3 个改进，方案形态因此调整：

### 改进 ①：通道 A 改「增量 LoRA 叠加」✗ 不替换
```
原方案: 每轮训练新 LoRA → A/B → 通过则【替换】旧 LoRA ✗
文献:   **PILoRA**（Prototype Guided Incremental LoRA）+ **LoRA Learns Less and Forgets Less**
问题: 替换会遗忘旧能力 ✗
改法: **增量叠加** ✓ 每轮新 LoRA 保留 ✓ vLLM 已支持 `--max-loras 4` ✓
       当前 vLLM 启动参数已带 `--max-loras 4` ✓ 可直接叠 ✓
```
**落地**: `flywheel.py solidify --track train` 产出新训练数据 ✓
训练后用 `--lora-modules` 叠加（不删除旧的 ✓）

### 改进 ②：加 **MAPE 控制环**（自动化 Monitor）
```
原方案: 4 阶段（采集/蒸馏/固化/验证）· **全靠手工触发** ✗
文献:   **Adaptive Data Flywheel**（EACL 2026）用 MAPE 环驱动 ✓
       Monitor（监控）→ Analyze（分析）→ Plan（计划）→ Execute（执行）
改法: 把 4 阶段映射到 MAPE ✓ 并加**自动监控**：
       · Monitor: 每轮后记录指标（V2 分数 / 做法数 / 工具覆盖）✓
       · Analyze: 对比上轮，找下降的档 ✓
       · Plan: 选通道（A/B/C）+ 阈值 ✓
       · Execute: 固化 + 验证 + 晋级/回滚 ✓
```
**落地**: `flywheel.py state.json` 已有 `metrics_history` ✓
期 3 加 cron 自动跑 ✓

### 改进 ③：加**递归抽象**（做法 → 元做法）
```
原方案: 只做**一层**抽象（轨迹 → 做法）✗
文献:   **SCAFFOLD**（arXiv 2609.05511）**递归**参数化技能抽象 ✓
问题: 22 条做法里有重复模式 ✗ 可再合并 ✓
改法: 对 22 条做法再做一次抽象 → 6-8 条"元做法"✓（如"平台状态核查类"✓）
```
**落地**: `flywheel.py distill --round 2` 待加 ✓（期 1.5）

---

## 十一、★ 文献给的关键印证（解释全部负结果 ✓）

### Voyager 的教训（最重要 ✓）
```
Voyager（Minecraft）: 技能库 + embedding 检索 ✓ **效果极好** ✓
  但用 **GPT-4 级模型** ✓ 能判断"检索到的是不是我要的" ✓

我们: 4B 判断不了 ✗ → 被带偏 ✗（−29.5pp ✗）

⇒ **"检索注入"不是错 ✗ 是【有模型能力下限】** ✓✓
⇒ 我们的结论不是"KB 无用" ✓ 是"**4B 上 KB 不可用**" ✓
⇒ **换 7B+ 后必须重测** ⚠ ← 这是衔接第 3 步的核心任务 ✓
```

### 我们的两个可能贡献（文献没有 ✓）
```
① 量化"检索注入"负面效应: **−29.5pp**（base −34 / LoRA −25）小模型反面实证 ✓
② "4B 无收益区间"三层事实: 极简 87%≈93% · 中等 67%vs63% · 复杂 都 0% ✓
```

---

## 十二、期 1 已实现（2026-10-21 晚 ✓）

### `scripts/flywheel.py`（约 480 行）
| 命令 | 对应 MAPE | 作用 |
|---|---|---|
| `status` | — | 飞轮状态（轮次/版本/资产）✓ |
| `collect` | **Monitor** | 收轨迹（8 文件 · 587 条 ✓） |
| `distill` | **Analyze** | 调 skills_extractor ✓ |
| `solidify --track routing/train/prompt` | **Plan+Execute** | 3 条通道 ✓ |
| `validate` | Evaluate | 给跑批命令（WSL 不能驱动 Playwright ✗）|
| `score --tag X` | Evaluate | 判分 ✓ |
| `promote --version vNNN` | 晋级 | 版本快照 ✓ |
| `rollback` | 回滚 | 上一版本 ✓ |
| `run --track routing` | 一轮 | 全自动（除验证 ✓）|

### 首轮实测 ✓
```
① 采集: 8 个轨迹文件 · 587 条 ✓ 做法库 22 条 ✓
② 蒸馏: 规则模式 → 1 条（预期 ✗ 规则模式不抽象 ✓ 要 --use-llm）
③ 固化(通道B): 域覆盖 {kb:5, training:13, other:4, workflow:3, file:2} ✓
   产出 data/flywheel/routing_suggestion.json ✓
④ 指引下一步（验证命令 + 判分 + 晋级）✓
```

### 已知限制（诚实 ✗）
```
✗ WSL 不能驱动 Playwright ⇒ validate 只能给命令 ✓ 需在 Windows PowerShell 跑 ✗
✗ 规则模式蒸馏质量低 ✗（1 条 vs LLM 模式 22 条 ✗）⇒ 正式轮次用 --use-llm ✓ 要 vLLM ✓
✗ 自动监控（Monitor）还没做 ✗ 期 3 ✓
```

---

## 十三、衔接第 3 步：7B 验证（唯一能验证"检索是否有用"的机会 ⚠）

### 为什么要 7B
```
文献: 检索在 GPT-4 级有效 ✓ 在我们的 4B 无效 ✗（Voyager vs 我们）
⇒ 唯一没测过的中间点 = **7B** ⚠
⇒ 如果 7B 上检索也不起作用 ⇒ 结论"检索对本地小模型无用"✓（更强 ✓）
⇒ 如果 7B 上检索有用 ⇒ 我们的飞轮通道 C 可以启用 ✓
```

### 前置条件（阻塞 ⚠）
```
⚠ 7B QLoRA 需 ~10-11GB ⇒ **3060 12GB 会 OOM** ✗
⇒ 必须用 **5060 Ti 16GB** ⇒ **要用户不玩游戏**
```

### 步骤（等 GPU 空闲 ✓）
```bash
# ① 训 7B LoRA（用 5060，4060 太慢）
BASE_MODEL=/home/luxy/models/Qwen2.5-7B-Instruct-AWQ   python train_lora.py --dataset <flywheel 产出的训练数据> --lora_name fw_7b_round1

# ② 启 vLLM（7B + 新 LoRA + 旧 LoRA 叠加 ✓ 增量 ✓）
CUDA_VISIBLE_DEVICES=0 vllm serve /home/luxy/models/Qwen2.5-7B-Instruct   --enable-lora --max-loras 4 --max-lora-rank 32   --tool-call-parser hermes --enable-auto-tool-choice

# ③ 跑 4 臂 A/B（base/LoRA × KB关/开）
cd E:/WSL/_check/drill
node eval_v2_run.mjs --tier 4b --kb off --tag 7b_b_kb0
node eval_v2_run.mjs --tier 4b --kb agent_use --tag 7b_b_kb1
node eval_v2_run.mjs --tier 4b --kb off --lora fw_7b_round1 --tag 7b_l_kb0
node eval_v2_run.mjs --tier 4b --kb agent_use --lora fw_7b_round1 --tag 7b_l_kb1

# ④ 判分对比
python scripts/flywheel.py score --tag 7b_b_kb0   # 等
```

### 判定
```
✅ 7B 上 KB 开 > KB 关（≥+10pp）⇒ **检索路线复活** ✓ ⇒ 飞轮通道 C 可启用 ✓
❌ 7B 上 KB 开 ≤ KB 关 ⇒ **检索对本地模型全线无用** ✓ ⇒ 飞轮只走 A+B ✓（结论更强 ✓）
```

---

*文献修订 · 2026-10-21 晚 · 3 改进: 增量LoRA ✓ MAPE环 ✓ 递归抽象 ✓ · 期1 已实现 `flywheel.py` ✓ · 衔接: 7B 验证（等 5060 空闲 ⚠）*
