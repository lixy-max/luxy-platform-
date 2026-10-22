# 云边架构 · 第 1 步：平台编排接口（2026-10-21）

> **基线**: 5e79f0b
> **前提**: 平台作为 **DSH 插件** ✓（DSH 能调平台 API ✓）
> **本步做的是**: 平台对 DSH 暴露「我有什么能力 / 这个任务该怎么做」✓

---

## 一、为什么做这一步（问题诊断 ✓）

```
核查结果（2026-10-21）:

① 云边架构: ❌ **没打通** —— 只有「本地→云」的应急升级 ✓
               **「云→平台→本地」的编排完全没有** ✗
               平台没有 /orchestrate /route /dispatch 任何编排接口 ✗

② 任务路由: ⚠ 工具路由 ✓（92→26/23 省 70%）
            **模型路由没有** ✗

③ 训练:     ✅ 全链路打通 ✓✓
④ 知识库:   ⚠ 有 API ✓ 自动挂载没有 ✗
⑤ 自我认知: ⚠ 能力有 ✓ **置信度打分器未接入对话流程** ✗
⑥ DSH:      ✅ 平台**是 DSH 插件** ✓（用户确认 ✓）
```

**⇒ 第 1 步的目标：给 DSH 一个「平台能力发现 + 任务规划」接口** ✓

---

## 二、交付物

### 1. 新模块 `orchestrator.py`（328 行 ✓）

| 功能 | 实现 | 验证 |
|---|---|---|
| **能力清单** | `capabilities()` | ✅ tools=92 · sim=6 · lora=41 · kb=29 |
| **模型路由** | `route_model(task, strategy)` | ✅ 论文→cloud · 训练→local ✓ |
| **执行计划** | `build_plan(task)` | ✅ 6 场景全对 ✓ |
| **置信度信号** | `confidence_signals(task, plan)` | ✅ 4 个外部信号 ✓ |
| **主入口** | `orchestrate(task, context, strategy)` | ✅ **11/11 测试通过** ✓ |

### 2. 新接口（`lora_manager.py`）

```
POST /orchestrate
  body: {task, context?, strategy?}
  ⇒ {ok, route:{model,why}, plan:[{step,action,target,params,reason}],
      confidence:{level,why,signals}, capabilities:{...}, warnings:[...]}

GET /orchestrate/capabilities
  ⇒ {ok, capabilities:{tools, sim_nodes, models, kb, lora, features}}
```

---

## 三、决策逻辑（**全部基于已有验证过的组件** ✓）

### 模型路由规则

| 任务特征 | 路由 | 理由 |
|---|---|---|
| 训练/微调（非查询类）| **local_7b** | 数据不出机器 ✓ 且要 GPU ✓ |
| 学术/最新/论文/复杂推理 | **cloud** | 云端更强 ✓ |
| 平台操作/查询（列出/当前/状态）| **local_7b** | 本地够用 ✓ 数据不外传 ✓ |
| 无明显特征 | **local_7b** | 默认安全 ✓ |
| `strategy=local_only` / `cloud_first` | 强制 | 调用方决定 ✓ |

### 执行计划步骤

| 触发 | action | target | 依据 |
|---|---|---|---|
| 论文/研究/最新 | `retrieve` | `academic_search` | ✅ Grounding-First ✓ 实测可用 ✓ |
| 知识库/文档 | `kb_query` | `query_knowledge_base` | ✅ KB 清洗已验证 ✓ |
| 列出/查/状态/工具 | `call_tool` | **top-8 工具名** | ✅ tool_retriever ✓ 省 70% token ✓ |
| 计算/求和/平均 | `simulate` | `run_simulation` | ✅ 6 节点 41/41 ✓ 禁止心算 ✓ |
| 训练/微调（真训练）| `train` | `train_lora.py` | ✅ 全链路验证 ✓ |
| 无特殊需求 | `direct_answer` | None | — |

**⚠ 关键设计: 搜索/routing 都是复用**已验证**的组件 ✓ 不是新写的逻辑 ✗**

---

## 四、置信度信号（**外部可验证 ✓ 不是模型自评 ✗**）

```
参考: docs/CONFIDENCE_SCORER_DESIGN.md ✓（共形预测 · ECE 0.084 · AUROC 0.976）

信号 4 个:
  ① has_grounding_step      —— 有检索步骤 ⇒ 有外部依据 ✓
  ② has_deterministic_compute —— 计算走仿真 ⇒ 不是心算 ✓
  ③ tool_candidates         —— 候选工具数 ✓
  ④ platform_covered        —— 平台能力是否覆盖 ✓

⇒ level: high / medium / low
⚠ 当前**最高只给 medium** ✗（诚实: 因为还没接入对话流程 ✗）
```

---

## 五、⚠ 诚实边界（**必须写清** ✗）

```
✅ 已做:
   · 平台暴露编排接口 ✓ DSH 可调 ✓
   · 模型路由规则 ✓ 6 场景验证 ✓
   · 工具推荐（top-8）✓
   · 置信度外部信号 ✓
   · 能力清单诚实标注未实现项 ✓

❌ 没做:
   · **编排结果的实际执行** ✗（只出计划 ✗ DSH 或前端来执行 ✓）
   · 置信度**接入对话流程** ✗（features.confidence_in_chat=False ✓）
   · **云端自动触发本地训练** ✗（需 DSH 侧调 /training/start ✓）
   · 多步计划的**依赖管理/回滚** ✗
   · 计划质量评估 ✗（无法证明计划"对" ✓ 只能证明"符合规则" ✓）
```

---

## 六、测试结果（**11/11 通过** ✓）

```
✓ Bug1: "列出LoRA" 不应有 train 步
✓ 训练任务 route 应是 local_7b
✓ 训练任务 why 应提到"训练"
✓ 训练任务应有 train 步
✓ 计算应走 simulate
✓ 论文应 route cloud
✓ 论文应有 retrieve
✓ 寒暄应 direct_answer
✓ caps.tools == 92
✓ caps.sim_nodes == 6
✓ caps.features.confidence_in_chat == False（诚实 ✓）

6 个场景实测:
  列出平台所有 LoRA  → local_7b · [call_tool]  ✓
  训练一个工具调用 LoRA → local_7b · [call_tool, **train**]  ✓
  帮我算一下 (1.5+2.5)*3 → local_7b · [**simulate**]  ✓
  最近大模型置信度校准的论文 → **cloud** · [**retrieve**]  ✓
  知识库里有什么文档 → local_7b · [**kb_query**]  ✓
  你好 → local_7b · [direct_answer]  ✓
```

---

## 七、⚠ 修复的 3 个 bug（写代码时引入 ✗）

| Bug | 现象 | 修法 |
|---|---|---|
| 1 | "列出所有 LoRA" 被误判为训练任务 ✗ | 加 `_is_train_task()` ✓ 查询动词+LoRA ⇒ 不是训练 ✓ |
| 2 | 训练任务 route 理由是"无明显特征" ✗ | 训练任务单独分支 ✓ "必须本地 ✗ 数据不出机器" ✓ |
| 3 | `_tool_docs.json` 路径写了两遍 ✗ | 提 `TOOL_DOCS` 常量 ✓ |

---

## 八、⚠ 待生效

```
lora_manager.py 已改 ✓ 语法通过 ✓
⚠ **需重启 backend** ✗
⇒ 但评测在跑（lora_p16_gen3 ✗）⇒ **等评测完再重启** ✓
```

---

## 九、下一步（云边架构第 2 步）

| # | 任务 | 说明 | 工时 |
|---|---|---|---|
| **2** | **DSH 侧接 `/orchestrate`** | DSH 调平台拿计划 ✓ 然后执行 ✓ | 2h |
| 3 | 置信度接入对话流程 | 低置信 ⇒ 拒答/升级 ✓ | 2h |
| 4 | 云端触发本地训练 | DSH 调 `/training/start` ✓ | 1h |
| 5 | KB 自动挂载 | 按任务自动建/查库 ✓ | 1h |
| 6 | 计划质量评估 | 需先有执行记录 ✗ | 2h |

---

*2026-10-21 · 云边架构第 1 步完成 ✓ · **只出计划不执行** ✓ · 诚实边界明确 ✗*
