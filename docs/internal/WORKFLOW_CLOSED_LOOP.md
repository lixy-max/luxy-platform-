# 平台闭环：工作流 ←→ Agent ←→ 每节点 LoRA

> 这份文档是对我此前方向的修正。核心结论：
> **这个平台的三个任务（搭工作流 / 训 LoRA / 评测）本就是同一个闭环，
> 而闭环的执行者应该是内置 Agent。** 我此前把它们当成三套独立工具在优化，
> 结果是"零件都在，但只有人能连线"。
> 日期：2026-10-06 ｜ 基线：后端 425 测试通过 / 提交 `3cf6dcc`

---

## 1. 闭环的准确表述

```
  自然语言需求
        │
        ▼
 ① 搭工作流 ──→ ② 试跑 ──→ ③ 定位弱节点（指标 + 人工）
        ▲                          │
        │                          ▼
 ⑧ 质量台账 ── ⑦ 绑定回节点 ←── ⑥ 节点级评测 ←── ⑤ 训练 ←── ④ 从节点取数据
   (role,LoRA)→指标                                    (节点级数据集)
```

关键点在于：**每一个箭头都应该是 Agent 能调用的工具**，而不是只有人能在 UI 上点。
用户要的是"用自然语言说'这个节点方法抽取不准，帮我训一个' → Agent 完成 ④⑤⑥⑦"。

**"高精度"的落点在 ⑥⑧**：每个节点的模型（LoRA）有**可查的质量指标**，
选它进工作流时有据可依，而不是靠感觉。

---

## 2. 代码级缺口（今天断在哪）

### 2.1 Agent 现在有 15 个工具，闭环需要的动作**三个全缺**

| 已有 | 缺失（闭环必需） |
|---|---|
| `add_workflow_node` / `delete_workflow_node` | ❌ **`update_workflow_node`** —— 没有它就无法"把刚训好的 LoRA 换到现有节点上"（这是闭环的核心动作） |
| `start_training` / `list_trainings` / `get_training_status` | ❌ **`run_workflow`** —— Agent 完全无法触发运行（只有 UI 按钮） |
| `list_loras` / `load_lora` / `unload_lora` | ❌ **`run_eval` / `build_dataset`** —— 评测与数据集构建全是 CLI 脚本，Agent 够不到 |
| `list_available_datasets` | ❌ **`bind_lora_to_node`**（可由 `update_workflow_node` 覆盖） |

**证据**：`AGENT_TOOLS` 的 15 个键全部列在 `index3.html`，没有一个是运行/评测/改节点。

### 2.2 三个"最后一公里"的具体障碍

| 障碍 | 具体表现 | 影响 |
|---|---|---|
| **Agent 无法提供文件** | 唯一的运行入口是 `POST /workflow/run`（multipart 上传），工具的 JSON 协议传不了文件 | 即使加了 `run_workflow` 工具也无从下手；需要一个"按已上传论文运行"的端点 |
| **数据集来源与节点无关** | `start_training` 只支持 `task_desc`（合成数据）或 `dataset_file`（人工准备）；`build_extractive_dataset.py` 按**论文**切片，不按**节点** | "训各个节点的模型"缺输入：拿不到"这个节点的真实任务数据" |
| **指标不落账** | `eval_lora.py` / `spot_check.py` 的结果只写成 `outputs/eval_*.json`、`spotcheck_*.json` | 选 LoRA 时无据可依；preflight 只能查"装了没/兼容不兼容"，查不了"好不好" |

### 2.3 已经具备、可以直接复用的资产

| 资产 | 位置 | 在闭环里的角色 |
|---|---|---|
| 分块抽取 + 缓存 + 并发 | `workflow_engine.py`（`chunk_*`、`cache`） | ④ 的数据来源（节点真实输入/输出）与 ② 的运行成本控制 |
| 抽取式数据集构建 | `build_extractive_dataset.py` | ④ 的模板：改成"按节点 trace 生成"即可 |
| 评测与诚实性检查 | `eval_lora.py`（含三重泄漏检查）、`spot_check.py` | ⑥ 的指标实现 |
| LoRA 注册与兼容检查 | `GET /loras`（含 `compatible`）、`check_lora_compatibility` | ⑧ 台账的底座 |
| 节点类型契约 + LoRA 角色 | `NODE_TYPE_SPECS`（我刚加的 `lora_role`） | ⑦ 绑定的锚点：角色 → LoRA |
| 失败/降级/分块/换入换出元数据 | `_meta.*` | ③ 定位弱节点的数据基础 |

---

## 3. 闭环 MVP 设计（每个环节要做什么）

| # | 环节 | 具体动作 | 依赖 |
|---|---|---|---|
| ① | 搭工作流 | `add_workflow_node`（已有）+ **`update_workflow_node`**（新增） | — |
| ② | 试跑 | **`POST /workflow/run_saved {config_name, paper_name}`** + Agent 工具 `run_workflow(paper, config?)` | 后端新端点 |
| ③ | 定位弱节点 | 节点级指标：`key_fact_retention`（数字/缩写回查）、耗时、分块失败数、降级原因 | 已有 `_meta`，需算"节点输出可溯源率" |
| ④ | 从节点取数据 | **节点 trace**：把每个节点的 `{输入, 输出, 模型/LoRA, 提示词, 时间戳}` 落盘 → `POST /workflow/runs/{id}/export_dataset?node=e` 生成 sharegpt | ③ 的 trace 要先落盘 |
| ⑤ | 节点级训练 | Agent 工具 `start_training`（已有）+ 允许 `dataset_file` 指向 ④ 的产物 | ④ |
| ⑥ | 节点级评测 | 把 `eval_lora.py` / `spot_check.py` 包成 `POST /eval/start` + `GET /eval/status/{id}`；Agent 工具 `run_eval(lora, dataset)` | 脚本已在 |
| ⑦ | 绑定回节点 | `update_workflow_node(id, {lora: ...})`；按 `lora_role` 校验（extractor 角色才允许填 extract 类型） | ① |
| ⑧ | 质量台账 | `loras/registry.json`：`{lora: {base, role, dataset, metrics{similarity, key_fact_retention, n}, 时间}}`；preflight 读取 → 无指标或指标过低时给警告 | ⑥ |

---

## 4. 可行性判断

| # | 环节 | 可行性 | 成本 | 主要风险 |
|---|---|---|---|---|
| ① `update_workflow_node` | **高** | **极低** | 无 —— 照 `add_workflow_node` 写即可 |
| ② 按已上传论文运行 | **高** | 低 | 无 —— 后端把 `uploads/` 里的文件喂给现有 run 逻辑 |
| ③ 节点级可溯源率 | **高** | 低 | 指标对"创造性"节点（分析/报告）不适用，只能用于抽取类 → 需按 `lora_role` 决定是否计算 |
| ④ 节点 trace 落盘 + 导出 | **高** | 中 | trace 会让结果 JSON 变大（长输入要截断或另存）；需注意隐私/体积 |
| ⑤ 用节点数据训练 | **高** | 低 | ④ 的数据量可能很小（一次运行只有 1 条），需要多篇论文累积 |
| ⑥ 评测包成 API | **高** | 中 | 评测要跑真实推理（本地几十秒～几分钟），需要 task + 轮询（训练已有同款模式可抄） |
| ⑦ 绑定 + 角色校验 | **高** | 低 | 需要给每个 LoRA 标角色（可在 ④⑤ 时自动写入台账） |
| ⑧ 质量台账 + preflight 接入 | **高** | 中 | 指标口径要统一（similarity 只适合有参考的场景）；台账要能手工修正 |

**结论：八项全部可行且没有硬约束阻挡**（不像"多 LoRA 真并行"受 570 MiB 显存限制）。
瓶颈是**工作量**，不是可行性。

---

## 5. 建议顺序（按"解锁闭环"的先后）

### 第一刀：打通 Agent 的三个缺失动作（最小可用闭环）
1. **`update_workflow_node`** —— 闭环的核心动作（换 LoRA/改提示词）
2. **`POST /workflow/run_saved` + `run_workflow` 工具** —— Agent 能跑
3. **`POST /eval/start` + `GET /eval/status` + `run_eval` 工具** —— Agent 能评
4. **`POST /workflow/runs/{id}/export_dataset` + `build_dataset` 工具** —— Agent 能取数据
5. **`loras/registry.json` 台账 + preflight 读取** —— Agent 选 LoRA 有据

做完这五件，**"自然语言 → 搭工作流 → 跑 → 评 → 训 → 换 → 再评"就能全在对话里完成**。

### 第二刀：让精度可累积
6. 节点 trace → 数据集：跨多次运行累积（同一节点、多篇论文）
7. 评测结果进台账 + 在节点编辑处显示"这个 LoRA 的历史指标"
8. `verify` 任务节点（报告数字回查抽取结果）—— 让"精度"在运行时就可见

### 第三刀：模板与规模化
9. 模板 manifest（声明所需 LoRA + 角色 + 指标门槛），选中模板时自动预检
10. X2' 前端按注册表渲染编辑器（纯重构）

---

## 6. 一句话

我此前把"评测/数据集/训练"做成了**三套 CLI 工具**，
而你描述的是一个**由 Agent 驱动的单一闭环**。
零件基本齐了（分块、缓存、评测、抽取式数据集、类型契约、LoRA 兼容检查都在），
缺的是把它们**接成环**的那几根线 —— 而第一刀的五件事都是低成本、高确定性的。

**这也解释了为什么"高精度"一直没落地**：没有 ⑥⑧（节点级评测 + 质量台账），
选哪个 LoRA 只能靠感觉；而我做的评测脚本又只有人能跑。
