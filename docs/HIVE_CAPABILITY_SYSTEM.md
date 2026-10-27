# 蜂巢能力体系设计：能力基线库 · 云端说明书 · 知识库分层

**我的核心判断：三个问题其实是同一个问题——"能力边界如何被机器可读地表达"。解法 = 结构化能力注册表（唯一事实源）+ 分层知识库（含访问控制）+ 云端能力说明书（Manifest）；小模型不做全能判断，只做"受限选择"；长程/开放任务交云端。** 日期：2026-10-27

## 0. 先给结论（回答你的三个问题）

| 你的问题 | 我的理解/答案 |
|---|---|
| ① 主脑如何识别"已入库/未入库"能力？ | 不靠模型记忆，靠**能力基线库（结构化注册表）+ 三阶段判定**：任务→capability 需求集→注册表查询→有/部分/无 → 结构化缺口（tool/knowledge/model/data/compute）。小模型只做意图/标签抽取与受限选择，**判定由确定性代码完成**。 |
| ② 云端"说明书"长什么样？怎么吸收？ | **Capability Manifest（能力包规格）**：接口契约+运行要求+依赖/环境/许可+测试+回滚；吸收三模式：直接移植 / 兼容借鉴重建 / 模型-LoRA 下载微调；兼容性评分决定走哪条。 |
| ③ 知识库怎么划分、谁能读？ | 分 6 层：L0 能力基线（必读）· L0.5 策略安全（必读）· L1 领域（按任务挂载）· L2 专业细化（仅对应 LoRA/工蜂挂载）· L3 数据型（不 embed，工具+schema+ACL 访问）· L4 云端说明书（缺口时读）。**分层 + 挂载策略 + ACL 是"工蜂只读该读的"关键。** |

## 1. 主脑如何识别能力边界（能力基线库）

### 1.1 能力基线库不是普通知识库，是"结构化注册表"

```
// capability_registry（SQLite/JSON，唯一事实源）
{"cap_id":"lora.json_extract.v2","kind":"lora","status":"active","scope":"local",
 "capability_tags":["json","extract","format"],
 "io":{"input":"text","output":"json"},
 "requires":{"base_model":"qwen2.5-7b","vram_gb":0.2,"tools":[],"kbs":[],"data":[]},
 "evidence":{"eval_set":"qa_json_v2","quality":0.88,"p95_latency_s":1.2,"fail_cases":[...]},
 "limits":{"max_input_tokens":3000,"languages":["zh","en"],"negative":["图像输入"]},
 "mount":{"nodes":["w1"],"auto_load":true,"lru_priority":2},
 "version":"v2","updated_at":"...","source":"trained|transplanted|built"}
```

### 1.2 判定流程（三阶段，LLM 只做中间一步）

```
任务 → [代码/规则] capability 需求抽取（domain → capability → skills）
     → [注册表查询] 匹配 active & 评测达标 & 前置满足的能力
     → [LLM 受限选择] 只在候选表（10~20 条）内选择/组合/给置信度
     → [代码校验] 无候选 → 未入库；候选覆盖不足 → 部分缺口
     → 输出：capability_plan + gap_tags{tool|knowledge|model|data} + 升级建议
```

- **已入库判据**：status=active + 有评测证据 + 前置条件（基座/显存/KB/工具）满足；不是"模型觉得会"。
- **未入库判据**：注册表无匹配 / 匹配但 status≠active / 前置不满足 / 评测未达标。
- **多步任务**：拆成 capability 序列逐个查，缺哪个标哪个（而不是整体转云）。

## 2. 多知识库分层：哪些必读、哪些工蜂读、怎么挂载

| 层 | 内容 | 主脑 | 工蜂 | 云端 | 读取策略 |
|---|---|---|---|---|---|
| L0 能力基线 | 能力注册表/评测/负边界 | 读（代码查询） | 只读自身能力 | 只读摘要 | **必读**（每次路由） |
| L0.5 策略安全 | 权限/预算/数据不出机/风险规则 | **必读** | 不读 | 仅摘要 | **必读**（硬规则） |
| L1 领域知识 | 采购/合同/论文/农业等领域库 | 读摘要 | 按任务挂载 | 按需摘要 | 任务域必读 |
| L2 专业细化 | 某 LoRA 对应专业知识 | 不读原文 | **仅本 LoRA 挂载** | 不读 | 条件挂载 |
| L3 数据型 | 供应商清单/本地 Excel/DB/API | 只读 schema | 经工具读（ACL） | 不读原始数据 | 工具按需，不 embed |
| L4 云端说明书 | 架构/依赖/环境/许可/评测 | 读（升级评估） | 不读 | 读写 | 缺口时读 |

**读取顺序**：L0+L0.5（≤1.5k token，代码注入）→ L1（top-k 3~5）→ L2（按激活 LoRA）→ L3（工具调用）→ L4（仅升级判断）。**必读**：L0/L0.5；L1 无则视为知识缺口；L2/L3 按能力挂载策略；L4 可选。

### 2.1 能力细化到 LoRA/节点/知识库挂载

```
{"capability":"json_extract",
 "deployments":[{"lora":"json_extract","node":"w1","status":"active","vram_gb":0.2}],
 "kbs":{"must":[],"optional":["format_style"]},
 "data_sources":[],
 "tools":["write_sheet"],
 "triggers":{"tags":["json","结构化","抽取"],"domains":["采购","报表"]}}
```

- **能力→LoRA**：capability_tags + io + base 家族 + 评测分数 → 候选 LoRA；节点是运行时，能力可部署到多节点。
- **能力→节点**：节点注册表（GPU/显存/基座/槽位/LoRA 列表）→ 调度器确定性匹配。
- **能力→KB**：每个 LoRA/任务配 kbs_must/kbs_optional + data_sources；挂载由代码执行，LLM 不决定"读什么库"。

## 3. 小模型能否胜任？——分层职责，而不是让它全能

| 职责 | 由谁承担 | 为什么 |
|---|---|---|
| 意图/能力标签抽取、候选受限选择、置信度、澄清 | 14B 主脑（LLM） | 语言理解是 LLM 强项 |
| 注册表查询、能力匹配、KB 挂载、ACL、预算、步数、回滚 | **确定性控制面（代码）** | 可验证、可审计、不幻觉 |
| 长程多步规划/开放任务/实时外部信息 | 云端（Assist 或 Orchestrator） | 本地小模型不可靠 |
| 能力制造/评估/部署 | 训练与热加载流水线（已有） | S21/S22 已验证 |

### 3.1 什么时候必须交云端（明确阈值）

| 模式 | 触发条件 | 云端职责 |
|---|---|---|
| Cloud Assist（子任务求助） | 缺口=1 且可描述；依赖步数≤2；上下文≤8K；本地置信度≥0.6 | 解决子任务，返回答案+轨迹；本地继续 |
| Cloud Orchestrator（云端代理主脑） | 依赖步数>3；缺口≥2；需实时外部数据；上下文>12K；本地置信度<0.6；连续失败≥2；长程多工具任务 | 云端做计划/执行，本地只提供工具与数据访问（审批/边界） |
| 本地直做 | 能力命中+前置满足+步数≤3+风险低 | 不需要云端 |

阈值可配置并记录决策依据（审计 → 后续训练"路由 LoRA"）。

## 4. 云端能力说明书（Capability Manifest）

### 4.1 一份 Manifest 包含什么

```
{"manifest_version":"1.0","capability_id":"tool.supplier_compare.v1",
 "name":"供应商比价工具","type":"tool|model|lora|workflow|knowledge_pack",
 "source":{"provider":"github","repo":"...","license":"Apache-2.0"},
 "interface":{"protocol":"openapi|mcp|http|python","spec_url":"...","io_schema":{...}},
 "runtime":{"os":"linux","python":"3.12","deps":["pandas"],"gpu":{"vram_gb":0,"arch":[]},"disk_gb":1},
 "env":{"vars":[],"secrets":[]},
 "data":{"needs":["supplier_list.xlsx"],"sensitivity":"medium"},
 "tests":{"smoke":["...","..."],"eval":{"metric":"accuracy","threshold":0.8}},
 "fallback":{"strategy":"cloud|disable|base"},
 "compat":{"vllm":">=0.11","docker":true}}
```

### 4.2 三种吸收模式（不是"完全复制"或"随便抄"）

| 模式 | 适用条件 | 本地动作 | 对应已有零件 |
|---|---|---|---|
| ① 直接移植（copy & run） | 接口/运行时/许可/安全都兼容 | 沙箱拉取→装依赖→smoke test→注册能力基线库 | Docker/OpenAPI 工具、vLLM 兼容模型 |
| ② 兼容借鉴重建（re-implement） | 接口不兼容但逻辑可借鉴 | 生成适配层/重写工作流→测试→注册 | `code_rebuild.py`、S15 云端搭工作流 |
| ③ 模型/LoRA 下载 + 微调/蒸馏 | 需要模型能力，本地有算力或可蒸馏 | 下载→基座适配/量化→评测→热加载 | `model_import.py`、S21 训练、S22 热加载 |

**兼容性评分**（接口 30 / 运行时 25 / 许可 15 / 安全 15 / 性能 15）：≥75 直接移植；50~75 借鉴重建；<50 放弃或改用训练。云端教师/代理产出的"新手脚"必须按 Manifest 输出**能力包提案**，本地 importer 解析→沙箱→门禁→注册。

## 5. 数据型知识库（供应商清单）怎么给 agent 读

- **不 embedding 原始数据**（避免泄露/过期/不可控），而是"数据能力注册"：data_source manifest（类型/路径/schema/owner/新鲜度/访问策略）。
- 主脑只读 **schema 摘要**（列名/类型/可用性）做计划；工蜂通过工具（read_sheet/SQL/HTTP）按 ACL 读取；结果写回/导出。
- 例：采购清单 → 主脑知道 supplier_list.xlsx 有 供应商/单价/交期 → 计划 read_sheet→比价→write_sheet；原始数据不进 prompt。
- L3 数据库需要：schema 注册、任务级 ACL、脱敏、只读/写分离、访问审计（当前未实现，是 S24 重点）。

## 6. 落地顺序（S24 先做最小闭环）

| 步骤 | 交付 | 验收 |
|---|---|---|
| S24a | capability_registry.py（SQLite：schema/status/evidence/mount/ACL 字段） | 注册 20 个现有能力；查询/缺口判定可复现 |
| S24b | KB 分层与挂载（L0/L0.5 注入、L1/L2 检索、L3 数据源注册、L4 manifest） | 10 个任务：必读/挂载/工蜂隔离正确 |
| S24c | Capability Manifest 解析 + 兼容评分 + 沙箱占位 | 1 个 Manifest 走通：解析→评分→沙箱 smoke→注册 |
| S24d | decide 输出 capability_plan + gap_tags；云端升级阈值（Assist/Orchestrator） | 20 个任务：已入库/缺口/升级判定准确率≥80% |
| S24e | 端到端验收 + 报告 | 全链路可复现，架构自检 +1 |

## 7. 诚实边界（这些目前只是设计，不是已实现）

- 能力基线库/Manifest importer/KB 分层 ACL 目前都是**设计稿**；现有实现只有临时知识库+能力路由标签。
- 14B 不能可靠做开放式多库路由；必须"确定性控制面 + 受限 LLM + 云端兜底"，不要指望小模型全能。
- 能力注册表需要持续维护（评测回填/退役），否则会腐烂；这是长期运营成本。
- 数据型 KB 的 ACL/脱敏/审计未实现；L3 当前只有 schema 设计。
- 云端 Orchestrator 模式涉及数据边界/成本/审批，未实现；需要治理先于自动化。
- 多 KB 挂载的 token 预算/检索质量需评测（否则"挂载越多越差"）。
