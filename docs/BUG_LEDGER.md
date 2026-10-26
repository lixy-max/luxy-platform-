# Bug 台账（挂起，最后集中修复）

- 约定：后续发现 bug **只登记挂起**，不打断主线；最后统一修复。
- 数据：`data/bug_ledger.jsonl`；本文件由 `bug_ledger.render_doc()` 生成。
- 更新：`POST /bugs` 新增，`POST /bugs/{id}/status` 改状态。

| ID | 严重度 | 状态 | 区域 | 标题 | 发现于 |
|---|---|---|---|---|---|
| B027 | P1 | fixed | repo | 事故：git sparse-checkout 误作用于主仓库，导致 tests/ 等跟踪文件被移除（已恢复） | S8 |
| B002 | P2 | fixed | kb/table | 表格结构化存储只取第一个 sheet；多 sheet/多表关联未做 | S3 |
| B003 | P2 | parked | kb/qa | LLM 兜底路径非数字实体仍可能误差（校验只覆盖数字） | S3 |
| B004 | P2 | fixed | worker | LoRA 工蜂能力可能返回 <tool_call> 文本而非执行结果 | S2 |
| B005 | P2 | fixed | output | 任务表产物仅 markdown，未生成 docx/xlsx 交付格式 | S2 |
| B006 | P2 | fixed | chimera | 外部能力安装仍仅支持纯 wheel/无依赖（P3.2 未做） | P3 |
| B007 | P2 | parked | infra | vLLM 平台托管单实例（主脑/工蜂需脚本并跑） | P0 |
| B010 | P2 | parked | data | 模型磁盘占用约 80GB、KB 含 34 个测试库未清理 | P0 |
| B011 | P2 | parked | cloud | Kimi 云调用 RPM=3，批量任务需排队 | P6 |
| B013 | P2 | parked | distill | 轨迹拆步 v1 按工具调用/轮次计数，语义拆步未做 | S4 |
| B014 | P2 | fixed | distill | 蒸馏工作流 v1 把工具步骤近似为 LLM 模拟（未真实调用工具） | S4 |
| B021 | P2 | wontfix | chimera | Docling 全量依赖过大（torch 2.14 + CUDA13 全家桶），本机直装不可行 | S6 |
| B024 | P2 | parked | model | 模型导入只下载原格式，未做量化转换（评估建议 int4/int8 但未落地） | S7 |
| B029 | P2 | parked | chimera | 代码重建 v1：适配器用模板/可选14B生成，未做真实源码级移植与自动测试集 | S8 |
| B001 | P3 | fixed | frontend | 前端任务表无自动轮询，执行后需手动刷新 | S2 |
| B008 | P3 | parked | security | vLLM 未启用 api-key（仅绑定回环） | P0 |
| B009 | P3 | wontfix | infra | /vllm/start 冷启动 26–28s（未优化） | P7 |
| B012 | P3 | fixed | kb/table | 表格数字解析不支持『待定/区间/单位换算』 | S3 |
| B015 | P3 | fixed | frontend | 前端页面为独立页（未接入 index3 导航） | S2 |
| B016 | P3 | parked | chimera | 奇美拉安装后入口探测靠 introspect 猜测 | P4 |
| B017 | P3 | fixed | distill | 轨迹蒸馏评分口径：覆盖判定只查平台工具文档，DSH 开发工具(pwsh/read)被当新能力 | S4 |
| B018 | P3 | parked | distill | 工作流复用可能过时：沉淀自历史轨迹，执行结果不反映当前环境状态 | S5 |
| B019 | P3 | parked | distill | 命中阈值 0.65 为经验值，未系统调优；embedding 相似度对'同义不同表述'可能漏命中 | S5 |
| B020 | P3 | fixed | governance | 工作流执行不经治理预算（直接调 vLLM，不计入外部能力预算） | S5 |
| B022 | P3 | fixed | chimera | 奇美拉 V2 依赖安装未锁版本/hash 复现：pip 解析到最新版，未来环境漂移风险 | S6 |
| B023 | P3 | parked | chimera | V2 适配器为手写（pdfplumber/docx），新库需人工写 wrapper 才能成为可执行能力 | S6 |
| B025 | P3 | parked | model | 模型部署需停机：主脑14B/工蜂7B 占满两张卡，导入后无法立即起服务 | S7 |
| B026 | P3 | parked | model | 模型下载无断点续传/进度上报 API（长下载只能等，UI 不可见） | S7 |
| B028 | P3 | fixed | chimera | GitHub raw 抓取不稳定（60s 超时）：已加 jsDelivr/GitHub API 镜像回退 | S8 |

## 详细说明

### B027 · 事故：git sparse-checkout 误作用于主仓库，导致 tests/ 等跟踪文件被移除（已恢复）
- 区域：repo ｜ 严重度：P1 ｜ 状态：fixed
- 发现于：S8 ｜ 登记：2026-10-26T21:57:43
- 详情：S8 探测 RAGFlow 克隆时，克隆失败后在 data/chimera/repos 子目录执行 git sparse-checkout set rag LICENSE NOTICE，因该目录在主仓库工作区内，sparse-checkout 被应用到主仓库，tests/ 等 6 个路径被移除。已通过 git sparse-checkout disable 恢复（494 跟踪文件 0 缺失），未提交改动备份后回填。教训：在仓库子目录做 git 操作前先确认 GIT_DIR；脚本需 set -e 并在 cd 失败时中止。

### B002 · 表格结构化存储只取第一个 sheet；多 sheet/多表关联未做
- 区域：kb/table ｜ 严重度：P2 ｜ 状态：fixed
- 发现于：S3 ｜ 登记：2026-10-26T21:03:48
- 详情：table_store.register_table 使用 parsed['sheets'][0]；多工作表/跨表检索需扩展

### B003 · LLM 兜底路径非数字实体仍可能误差（校验只覆盖数字）
- 区域：kb/qa ｜ 严重度：P2 ｜ 状态：parked
- 发现于：S3 ｜ 登记：2026-10-26T21:03:48
- 详情：table_qa.check_numbers 只校验数字；名称/实体错位无自动校验；建议后续加行级引用强制

### B004 · LoRA 工蜂能力可能返回 <tool_call> 文本而非执行结果
- 区域：worker ｜ 严重度：P2 ｜ 状态：fixed
- 发现于：S2 ｜ 登记：2026-10-26T21:03:48
- 详情：7B worker 开启 tool calling 时会输出 tool_call 文本；swarm 未解析该格式

### B005 · 任务表产物仅 markdown，未生成 docx/xlsx 交付格式
- 区域：output ｜ 严重度：P2 ｜ 状态：fixed
- 发现于：S2 ｜ 登记：2026-10-26T21:03:48
- 详情：artifact 目前是 .md；招标/采购场景需要 Word/Excel 导出（可接 python-docx/openpyxl）

### B006 · 外部能力安装仍仅支持纯 wheel/无依赖（P3.2 未做）
- 区域：chimera ｜ 严重度：P2 ｜ 状态：fixed
- 发现于：P3 ｜ 登记：2026-10-26T21:03:48
- 详情：sandbox_installer 限制纯 wheel 无依赖；selenium/crawlee 等装不上；需依赖锁定/venv + 兼容矩阵

### B007 · vLLM 平台托管单实例（主脑/工蜂需脚本并跑）
- 区域：infra ｜ 严重度：P2 ｜ 状态：parked
- 发现于：P0 ｜ 登记：2026-10-26T21:03:48
- 详情：vllm_manager PID 文件单实例；多实例端口/进程管理未平台化

### B010 · 模型磁盘占用约 80GB、KB 含 34 个测试库未清理
- 区域：data ｜ 严重度：P2 ｜ 状态：parked
- 发现于：P0 ｜ 登记：2026-10-26T21:03:48
- 详情：Gemma-27B/Qwen3-14B 等无用模型、测试 KB 较多；需确认后清理

### B011 · Kimi 云调用 RPM=3，批量任务需排队
- 区域：cloud ｜ 严重度：P2 ｜ 状态：parked
- 发现于：P6 ｜ 登记：2026-10-26T21:03:48
- 详情：cloud_bridge 已退避；云审/升级高并发需多 key 或中转

### B013 · 轨迹拆步 v1 按工具调用/轮次计数，语义拆步未做
- 区域：distill ｜ 严重度：P2 ｜ 状态：parked
- 发现于：S4 ｜ 登记：2026-10-26T21:03:48
- 详情：decompose 以 tool_call/文本轮为原子步；同一工具内的多语义步骤未拆；后续可上语义拆步

### B014 · 蒸馏工作流 v1 把工具步骤近似为 LLM 模拟（未真实调用工具）
- 区域：distill ｜ 严重度：P2 ｜ 状态：fixed
- 发现于：S4 ｜ 登记：2026-10-26T21:03:48
- 详情：workflow_engine 无 tool 节点类型；v1 用 process 节点近似；需扩展 tool 节点或 MCP

### B021 · Docling 全量依赖过大（torch 2.14 + CUDA13 全家桶），本机直装不可行
- 区域：chimera ｜ 严重度：P2 ｜ 状态：wontfix
- 发现于：S6 ｜ 登记：2026-10-26T21:27:30
- 详情：S1 判定 docling '直装'，但 S6 pip dry-run 显示将安装 ~120 包（含 torch 2.14.1、nvidia-cuda-* 13.x、transformers、opencv），体积数 GB；仅 70MB 的 pdfplumber 安装耗时 412s。结论：Docling 需云端或后续换轻量替代/CPU-only 精简版，不纳入本机直装。

### B024 · 模型导入只下载原格式，未做量化转换（评估建议 int4/int8 但未落地）
- 区域：model ｜ 严重度：P2 ｜ 状态：parked
- 发现于：S7 ｜ 登记：2026-10-26T21:36:36
- 详情：model_import 评估会给出 fp16/int8/int4 选型建议，但 import_model 只下载原始 safetensors；若 fp16 放不下需先 AWQ/GPTQ 量化（或选现成量化版仓库），否则无法按评估结论部署。

### B029 · 代码重建 v1：适配器用模板/可选14B生成，未做真实源码级移植与自动测试集
- 区域：chimera ｜ 严重度：P2 ｜ 状态：parked
- 发现于：S8 ｜ 登记：2026-10-26T21:57:43
- 详情：当前只借逻辑、本地独立重写最小实现（模板）；LLM 生成适配器为可选且未强制验证；无自动测试生成/回归。

### B001 · 前端任务表无自动轮询，执行后需手动刷新
- 区域：frontend ｜ 严重度：P3 ｜ 状态：fixed
- 发现于：S2 ｜ 登记：2026-10-26T21:03:48
- 详情：lora_manager 执行 async 后前端未轮询；可加 2s 轮询或 SSE

### B008 · vLLM 未启用 api-key（仅绑定回环）
- 区域：security ｜ 严重度：P3 ｜ 状态：parked
- 发现于：P0 ｜ 登记：2026-10-26T21:03:48
- 详情：vllm 启动参数无 --api-key；若改 0.0.0.0 有风险；回环下风险低

### B009 · /vllm/start 冷启动 26–28s（未优化）
- 区域：infra ｜ 严重度：P3 ｜ 状态：wontfix
- 发现于：P7 ｜ 登记：2026-10-26T21:03:48
- 详情：模型加载耗时；可预加载/常驻策略；stop 已优化到 3.5s

### B012 · 表格数字解析不支持『待定/区间/单位换算』
- 区域：kb/table ｜ 严重度：P3 ｜ 状态：fixed
- 发现于：S3 ｜ 登记：2026-10-26T21:03:48
- 详情：table_store.num 仅解析普通数值；区间价格/单位（元/套）未处理

### B015 · 前端页面为独立页（未接入 index3 导航）
- 区域：frontend ｜ 严重度：P3 ｜ 状态：fixed
- 发现于：S2 ｜ 登记：2026-10-26T21:03:48
- 详情：luxy-routes.html 独立；按用户确认先独立，后续可加导航入口

### B016 · 奇美拉安装后入口探测靠 introspect 猜测
- 区域：chimera ｜ 严重度：P3 ｜ 状态：parked
- 发现于：P4 ｜ 登记：2026-10-26T21:03:48
- 详情：复杂库需人工补 entry；可加候选函数测试/README 解析

### B017 · 轨迹蒸馏评分口径：覆盖判定只查平台工具文档，DSH 开发工具(pwsh/read)被当新能力
- 区域：distill ｜ 严重度：P3 ｜ 状态：fixed
- 发现于：S4 ｜ 登记：2026-10-26T21:05:45
- 详情：covered 仅检查 agent_kb/_tool_docs.json；历史轨迹大量使用 DSH 开发工具（pwsh/read/grep），不在平台工具文档 → novelty=1.0、评分偏高（587 条中 500 条 high）。后续应区分『平台能力覆盖』与『开发工具轨迹』，或按工具归属分类评分。

### B018 · 工作流复用可能过时：沉淀自历史轨迹，执行结果不反映当前环境状态
- 区域：distill ｜ 严重度：P3 ｜ 状态：parked
- 发现于：S5 ｜ 登记：2026-10-26T21:15:11
- 详情：命中即执行会直接跑旧轨迹蒸馏出的工作流；输入/工具状态可能已变化（如 LoRA 数量），结果需标注来源与时间。

### B019 · 命中阈值 0.65 为经验值，未系统调优；embedding 相似度对'同义不同表述'可能漏命中
- 区域：distill ｜ 严重度：P3 ｜ 状态：parked
- 发现于：S5 ｜ 登记：2026-10-26T21:15:11
- 详情：search_workflows 阈值硬编码 env 默认 0.65；建议用路由命中率/误命中率做一次标定。

### B020 · 工作流执行不经治理预算（直接调 vLLM，不计入外部能力预算）
- 区域：governance ｜ 严重度：P3 ｜ 状态：fixed
- 发现于：S5 ｜ 登记：2026-10-26T21:15:11
- 详情：S5 快路径/任务表 workflow 项调用 workflow_engine→vLLM；governance 执行预算只统计 external 能力。

### B022 · 奇美拉 V2 依赖安装未锁版本/hash 复现：pip 解析到最新版，未来环境漂移风险
- 区域：chimera ｜ 严重度：P3 ｜ 状态：fixed
- 发现于：S6 ｜ 登记：2026-10-26T21:27:30
- 详情：install_with_deps 记录 pip --report 的版本+sha256（可审计），但未把解析结果固化为 requirements.lock；重装可能升级依赖导致行为变化。建议支持导出 lock 并重装校验。

### B023 · V2 适配器为手写（pdfplumber/docx），新库需人工写 wrapper 才能成为可执行能力
- 区域：chimera ｜ 严重度：P3 ｜ 状态：parked
- 发现于：S6 ｜ 登记：2026-10-26T21:27:30
- 详情：自动适配需要 LLM 生成 wrapper + 测试；当前是为试点手写 2 个适配器。

### B025 · 模型部署需停机：主脑14B/工蜂7B 占满两张卡，导入后无法立即起服务
- 区域：model ｜ 严重度：P3 ｜ 状态：parked
- 发现于：S7 ｜ 登记：2026-10-26T21:36:36
- 详情：评估已返回 deploy_now=false（当前空闲 2.2GB）；部署需要停掉现有 vLLM 服务或排队切换。

### B026 · 模型下载无断点续传/进度上报 API（长下载只能等，UI 不可见）
- 区域：model ｜ 严重度：P3 ｜ 状态：parked
- 发现于：S7 ｜ 登记：2026-10-26T21:36:36
- 详情：ModelScope snapshot_download 自带重试但进度只在进程输出；大模型（14GB+）下载不可视、不可取消。

### B028 · GitHub raw 抓取不稳定（60s 超时）：已加 jsDelivr/GitHub API 镜像回退
- 区域：chimera ｜ 严重度：P3 ｜ 状态：fixed
- 发现于：S8 ｜ 登记：2026-10-26T21:57:43
- 详情：S8 实测 raw.githubusercontent 单文件 60s 超时；重建管线已改为 raw→jsDelivr→GitHub API 三级回退（jsDelivr 实测 0.9s）。仍建议加本地缓存/离线包，避免每次重建依赖网络。