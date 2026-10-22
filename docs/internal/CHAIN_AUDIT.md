# 工作流链条完整性审计（代码级，2026-10-15）

> 审计对象：**「用户需求 → 搭建工作流 → 节点拆任务 → 动态切换 LoRA → 实时知识库学习」** 这条链
> 方法：逐环节在源码里找出实现与调用点（附 `文件:行`），只有**代码里确实被调用**才算"通"。
> 结论速览：**组件几乎全都在 ✓，但有 2 处关键"接线"没接 ✗**（G1 工作流→学习、G2 agent→新建配置）。

---

## 一、链条逐环验证

| # | 环节 | 实现位置（代码证据） | 判定 |
|---|---|---|---|
| 1 | **按用户需求搭建工作流** | agent 工具 `add_workflow_node` / `update_workflow_node` / `delete_workflow_node` **都会落盘**：读 `/workflow/config?name=…` → 改 → `PUT /workflow/config?name=…`（`webui/index3.html` 内三个工具体）；`validate_workflow` → `GET /workflow/validate`；`run_workflow` → `POST /workflow/run` | ⚠ **半通**（能改**当前**配置；**没有"新建/克隆/切换配置"的工具** → G2） |
| 2 | **节点拆分任务** | `NODE_TYPE_SPECS`（`workflow_engine.py:700+`）：`input` / `retrieval` / `process` / `terminal` / `aggregate` / `extract` / `analyze`；语义类型带 `lora_role`、`quality`（grounding/fact_retention）、chunking（3000/500、4 workers）、cache；`apply_type_defaults()`（750）；`build_node_input`（447）+ `render_prompt`（469）支持 `{节点.字段}`；`topological_sort`（481） | ✅ **完整** |
| 3 | **动态切换 LoRA（节点级）** | `LoraRouter`（145）：`ensure()` → `POST /loras/load`（184）；线程锁保护 check-then-load（147）；"已加载"幂等处理（202）；`refresh()` 以服务端为准（151）；`invalidate()` 处理被换出（161）；切换耗时记入 `swaps`（192）；失败抛 `WorkflowPreflightError`（128）而不是 `sys.exit` | ✅ **完整**（缺前置兼容校验 → G3；多 LoRA 调度提示 → G4） |
| 4 | **节点级知识库（读）** | `resolve_kb_name`（571）：节点 `kb_name` → 环境 → `auto-single`/`auto-latest`；运行时 `GET /kb/list`（557）核对存在性、`POST /kb/{name}/query`（1514）注入 prompt（1471-1554）；`top_k` / `min_score` **可按节点配**（1470、1510，字段规范 792/795）；诊断含 `candidates/kept/dropped/best_score` | ✅ **完整** |
| 5 | **实时知识库学习（写回/入库）** | 引擎**只有读**：`GET /kb/list`（557）、`POST /kb/{name}/query`（1514）—— **没有任何入库调用**；`/workflow/run`（`lora_manager.py:1160`）全程不碰 `kb.` / `learn_sink`；学习链路只挂在 `POST /chat/completions`（`lora_manager.py:5701`）与 `POST /reflect/ingest`（4483） | ✗ **没接**（G1） |
| 6 | 产物落盘与可追溯 | `outputs/result_<ts>.json`（`workflow_engine.py:1878`、1914）含 `_meta.traces`；`POST /workflow/export_dataset`（1595）把**某节点真实输入/输出**导出为 sharegpt 样本追加到 `training_data/<dataset>.jsonl`（1617） | ✅ **完整**（这是"按节点取训练数据"的一环 ✓） |

**实际配置已在用这些能力**（`workflow_config.json`，9 节点）：
`input_paper(type=input)` → `kb_context(type=retrieval, kb_name=auto)` →
`extract_method(lora=extract_v2, provider=vllm)` → `extract_dimensions(lora=dimensions_v1)` →
`analyze_method/analyze_data/extract_claims/critique_analysis(provider=openai)` → `finaloutcome(aggregate)`
⇒ **节点级 LoRA + 节点级 provider + auto KB 检索**在同一份配置里都真实使用 ✓

---

## 二、缺口（都有代码级证据）

### G1 ★ 工作流跑完【不进】学习/知识库（"实时知识库学习"没接线）
- 证据：`workflow_engine.py` 对 KB 只有 `GET /kb/list`(557) 与 `POST /kb/{name}/query`(1514)；
  `grep -nE 'add_documents|ingest|learn_sink' workflow_engine.py` → **空**；
  `/workflow/run`(1160) 段内只有 `kb_name` 形参(1164)，没有入库/学习调用。
- 影响：用户说的"实时知识库学习能力"——**组件都有**（`kb.add_documents_to_kb`、`learn_sink.sink_session`、
  `POST /reflect/ingest`），但**没有一条路径把工作流产物喂进去**。工作流跑 100 次，KB 一点不变。

### G2 ★ agent 无法"新建/克隆/切换"工作流配置
- 证据：`grep -oE 'AGENT_TOOLS\.[A-Za-z_]+' ` 中与工作流相关的只有
  `list_workflow_nodes / add_workflow_node / update_workflow_node / delete_workflow_node /
   validate_workflow / run_workflow / list_workflow_configs / list_workflow_results / get_workflow_result`；
  **没有** `create_workflow_config` / `clone_workflow_config` / `use_workflow_config`。
  三个节点工具都硬用 `currentConfigName`（`webui/index3.html` 工具体内）。
- 而"新建/另存"**前端有**：`prompt('新配置文件名…')` → `PUT /workflow/config?name=<新名>`（2224-2245）；
  后端 `PUT /workflow/config`（`lora_manager.py:1121`）也支持。
- 影响：agent 只能"改当前打开的那份"，无法"从一句需求新建一套工作流"。**这是链条第 1 环缺的最后一步。**

### G3 节点级 LoRA 无前置兼容校验（运行时才炸）
- 证据：`LoraRouter.ensure()` 失败时只回 `加载失败: <后端 detail>`（205）；`WorkflowPreflightError`
  的注释本身就写了"底座是小模型、工作流引用不兼容 LoRA"时踩过（135）。
- 现状：`/loras` 已带 `compatible` 标志、`registry.json` 有 `hidden_size` → **信息是现成的，只是没人前置用**。
- 影响：切了底座（4B/2560）后跑旧工作流，要在节点加载时才 400；应**预检阶段一次性列出**并给建议。

### G4 多 LoRA 节点的切换成本无提示
- 证据：vLLM 启动参数 `--max-loras 2 --max-cpu-loras 4`（`vllm_manager.py` 命令行）；
  `LoraRouter.swaps` 只记录耗时（192），没有"配置里有几个不同 LoRA 会不会抖动"的判断。
- 影响：节点绑 3+ 个不同 LoRA 时反复换入换出，单节点耗时增加数秒，用户看不出原因。

### G5（G1 的细化）节点输出没有"写回 KB"的选项
- 证据：`NODE_FIELD_SPECS`（766+）里有 `kb_name/top_k/min_score`（读），**没有** `kb_write` 之类字段。
- 影响：即使想"这个节点的产出沉淀进某库"，也只能手工另走接口。

---

## 三、改进方案（按优先级，含验收）

### P0-A 把工作流接进学习闭环（治 G1 + G5）
1. **节点级写回字段**：`NODE_FIELD_SPECS` 加 `kb_write`：
   `{"mode": "off|append|replace", "target": "<kb 名>", "only_if_ok": true}`（默认 off，向后兼容）。
2. **运行后入库**：`/workflow/run` 收尾处（结果已落盘、`_meta.traces` 已写）调用一条新管道：
   - 取每个 `kb_write != off` 的节点 trace（输入 + 输出）；
   - 走**清洗**（复用 `learn_sink.clean_one` 的规则：去绝对路径、去重复、丢弃不完整）；
   - 汇聚成一份 Markdown（**经验型短条目**，参考 A4）→ `kb.add_documents_to_kb(target, [...])`。
3. **目标库分流**（已确立的规矩）：默认目标库用**新建的 `workflow_learned`**，
   **绝不写 `agent_use`**（它是工具调用经验库，混入工作流产物会"进得去查不到"）。
4. **验收**：跑一次工作流 → 目标库条目/块数增长；用"这次结论是什么"类问法能检索到；
   `agent_use` 指纹**不变**。

### P0-B 补 agent 的"新建/克隆/切换工作流"工具（治 G2）
1. 前端加两个 agent 工具（形状照现有节点工具）：
   - `create_workflow_config(name, from_current=true)` → `PUT /workflow/config?name=<name>`（克隆当前）
   - `use_workflow_config(name)` → 设置 `currentConfigName` 并刷新面板
2. 两个都标 `dangerous: false`（只影响配置文件，不动数据）；
   命名校验复用前端已有规则（必须以 `workflow_config` 开头 + `.json` 结尾，2228-2232）。
3. **验收**：对 agent 说"按这个需求搭一套工作流" → 观察它：新建配置 → 逐个 `add_workflow_node` →
   `validate_workflow` → `run_workflow`，且**不动原有配置**。

### P1-A LoRA 前置兼容校验（治 G3）
- 在 `validate_workflow` 与运行预检里加一步：对每个节点的 `lora` 查 `/loras`（或 registry）
  的 `compatible` / `hidden_size`，与当前底座比对；
- 不兼容就**一次性列全**（节点名 + 该 LoRA 期望 hidden_size + 当前底座 hidden_size + 建议），
  而不是等节点跑到一半 400。
- **验收**：底座切到 Qwen3-4B 时校验旧工作流 → 立刻给出"这 2 个节点的 LoRA 不兼容"。

### P1-B LoRA 调度提示（治 G4）
- 统计一份配置里**不同 LoRA 的个数**；超过 vLLM `--max-loras` 就提示
  "本配置 X 个不同 LoRA > 显存可驻留 2 个，预计发生 Y 次换入换出，预计增加 Z 秒"，
  并建议把同 LoRA 的节点相邻执行。
- **验收**：3 LoRA 的配置在校验阶段给出该提示。

### P2 学习产物按来源分流（治 G1 的副产品）
- 对话类（答题质量）→ `qa_standards`（或项目规范文件）；工具调用类 → `agent_use`；
  工作流产物 → `workflow_learned`。三者检索语义不同，混库必然"进得去查不到"（已实测 5/5 落空）。

---

## 四、一句话结论

> **"理论上所有环节都是现成的" —— 基本成立 ✓**：节点拆分、节点级 LoRA 动态切换、节点级 KB 检索、
> 产物落盘与按节点导出训练数据，全都在源码里**真实接线并已被现有配置使用**。
> 缺的是 **两处接线**：**① 工作流产物没有回流到学习/知识库（G1）**；
> **② agent 不能新建/切换工作流配置（G2）**；外加两处健壮性（G3 兼容预检、G4 切换提示）。
> 按 P0-A / P0-B 补完，这条链才真正"端到端闭合"。
