# 更新日志

本文件记录对外发布版本的可见变化。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## 未发布（main · 2026-10-23）

### 修复
- **UI 对比度事故：知识库「检索评测」结果白字看不清**（用户实测：导入 3 篇 PDF 后跑评测，
  浅蓝底 `#e7f1ff` 上出现白色 40% 透明文字，对比度仅 **1.05:1**，完全不可读）
  - 根因：`--c-text-2` / `--c-text-3` / `--c-surface-2` 三个变量在 `:root`（浅色默认）里被
    写成了**暗色主题专用值**（白色半透明），且 `[data-theme="dark"]` 没有重映射它们
  - 修复：`:root` 改为浅色主题正确值（`--c-text-2:#5a6478` · `--c-text-3:#646a75` ·
    `--c-surface-2:#f1f3f5`），暗色值移入 `--c-dark-*` 调色板并在暗色块用 var() 重映射
  - 影响面：同一变量共 29 处使用（知识库评测/分块预览/检索结果/GPU 卡片/拖拽区/设置页…）
    ⇒ 一次性全部修复，浅色模式对比度 1.05:1 → **4.77:1**（达 WCAG AA 4.5:1）
  - 回归测试：新增 `tests/test_ui_contrast.py`（6 项：解析 CSS 变量 + 计算 WCAG 对比度 +
    暗色重映射断言 + 暗色块禁裸 hex），已做负向验证（改回旧值必失败）
- **前端测试脚手架（`tests/frontend/run.js`）修复：会话用例 TDZ 崩溃 + 3 个过时用例**
  - 崩溃：主脚本 `const API_BASE = window.__LUX_API__ || new URLSearchParams(location.search)...`，
    但脚手架没有 `location` / `__LUX_API__` 桩 ⇒ 主脚本开头抛错、`API_BASE` 永久留在 TDZ
    ⇒ `ensureSession` 报 `Cannot access 'API_BASE' before initialization`、
    harness 在会话断言处崩（`message_count` of undefined）
    → 补浏览器桩：`location` / `__LUX_API__` / `localStorage` / `MutationObserver` 等
  - 引导卡改为 `emptyStateHTML()` 动态渲染后，静态计数用例误报"只有 1 张"
    → 改为调用真实渲染函数计数（同时保留模板计数兜底）
  - T-3c（2026-10-21）起 KB 预注入默认关闭 + 相关性门控 ⇒ t41 用例没开开关、hits 无 score 必挂
    → 补"默认关闭"断言；显式开启 `ds_kb_inject` 并给 hits 带 `score`；注入标记同步为
    `[参考资料 · 来自知识库「…」]` 新模板
  - 脚手架主脚本/Agent 模块加载失败时打印真实 stack（原来静默，排障困难）
  - 结果：前端 `378/378 passed`（修复前：会话断言处崩溃，测试跑不完）

## v4.9.7（2026-10-22）· 首个开源封装版

### 新增（开源工程化）
- **CI**：`.github/workflows/ci.yml` 五道门
  ① 全量 `compileall`（覆盖根目录 27 个模块，修「只查 2 个文件漏网」的历史问题）
  ② 逐模块 `import` 冒烟（能抓 NameError / 缺 import）
  ③ `run_tests.sh`（828 用例）
  ④ `scripts/check_no_secrets.sh` 密钥扫描
  ⑤ 卫生检查（运行产物目录必须空、不得有 .pyc）
- **容器化**：`Dockerfile`（python:3.11-slim · healthcheck · CPU 可跑）+ `docker-compose.yml`
  （端口只绑 127.0.0.1、数据卷、密钥走环境变量）+ `.dockerignore`
- **贡献体系**：`CONTRIBUTING.md`（三道门 + 代码约定）、`CODE_OF_CONDUCT.md`、
  `SECURITY.md`（含"设计边界"与可接受风险说明）、`.github/ISSUE_TEMPLATE/*`、PR 模板
- **工程配置**：`pyproject.toml`（ruff 规则：优先查语法/未定义名类）、`.editorconfig`、
  `.env.example`（密钥只走环境变量）
- **README 重写为开源版**：徽章 + 目录 + 差异化定位 + 60 秒上手 + 实测数据 + ASCII 架构图 + 文档索引

### 整理
- **docs 分层**：公开文档保留 9 份（INSTALL / ARCHITECTURE / 数据与评测 / 质量规范 /
  仿真节点 / 工具开发 / 项目原则 / DSH 两份说明书）；研发笔记归 `docs/research/`（13 份）；
  事故台账 / 验收记录 / 复盘 / 旧版本发布说明归 `docs/internal/`（122 份）

### 兼容性
- 对外行为与 v4.9.6 一致（仅文档/工程化，无功能变更）

## v4.9.6（2026-10-22）

### 修（依据外部复查确认的 7 项）
- **P0 `learn_sink.py` 等 6 个脚本 NameError**：v4.9.5 路径可移植化批量替换时
  `from pathlib import Path` 文件里用了 `pathlib.Path(...)` 却没补 `import pathlib`
  ⇒ `import learn_sink` 直接 NameError，而它被 `_learn_handoff` 阶段3 包在 try/except 里
  ⇒ **经验入库静默失效、用户无感知**。全仓扫描 6 个文件（learn_sink / gen_reflect_tasks /
  build_drill_dataset / build_drill_dataset_v2 / cloud_fix / cloud_fix_experience）全部补齐
- **P1 `install/stop.sh` 多实例误杀**：原来有 5 条宽泛 `pkill -f`，从副本目录执行会连主实例一起杀
  ⇒ 重写为「必须显式端口 或 `--all` 二次确认」，且只杀确认属于 luxy 的进程
- **P1 版本不可追溯**：v4.9.3/4/5 无 commit、CHANGELOG 无记录、INSTALL 头停在 v4.9.2 ⇒ 本次补齐
- **P1 CI 语法门漏网**：原来只查 2 个 py ⇒ 新增全量 `compileall` + 逐模块 `import` 冒烟（27/27）
- **P2 `/orchestrate/capabilities` 数字错**：读项目内 `./loras` 而非 `settings.lora_dir`
  ⇒ 改按 settings 真源
- **P2 `/loras/load` 重复加载 400**：前端页面加载会连发两次，第二次 vLLM 回 already-loaded
  ⇒ 400 且透传原始报文。改为先查 `/loras/loaded` 幂等返回 + already-loaded 兜底（200）
- **P2 前端 CSS 变量缺失**：`--c-surface-2 / --c-text-2 / --c-text-3` 被引用但未定义
  ⇒ 样式静默失效；6 处裸 hex 改为变量
- **P2 `dataset_toolkit.py` Q1 短样本误杀**：`system="你是助手。"`(5 字) / 样本 10 字
  ⇒ 占比 50% > 20% ⇒ 合格数据被拒 ✗（模块 L587 早有短样本豁免，校验器漏了）
  ⇒ 加 system < 60 字不看占比；并让 `TRAIN_ALLOW_SYSTEM_HEAVY=1` 真正生效
- **包卫生**：去掉 35 个 `.pyc` + 2 个 `__pycache__` + 私人产物 docx；
  补 `Makefile` / `scripts/run.sh` / `settings.example.json`（README 与说明书引用了但包内没有）

### 安全
- **API Key 支持环境变量注入**：新增 `_apply_env_api_keys()`，优先级
  `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `OLLAMA_API_KEY` / `VLLM_API_KEY` > settings.json
  ⇒ 下次不必再填回明文 json

### 测试
- **828 用例全绿（0 红 / 6 skip）**，此前为 20 红
  - `test_train_preflight` 夹具改为新格式（顶层 `system` + conversations 以 human 开头）
  - 旧 9 节点预设相关用例改为 skip 并注明原因

## [未发布] — 2026-10-19（practical-2 作业由 agent 完成 + P-49/P-50）

这一批是**平台 agent 端到端完成真实作业**（VLE 下载的课程作业 → 5 问答 → Word），以及过程中暴露并修复的两个平台缺陷。

### 修复
- **P-49** `write_docx(table)` / `write_sheet(rows)` 传 JSON 字符串时被**静默丢弃却返回成功** ⇒ 实测 Q5 追踪表整张丢失（段落数/标题都正常，不核对发现不了）。唯一出口做 JSON 字符串容错解析；新增 2 个回归测试（后端 **826 → 828**）。
- **P-50** 前端 `marked` 依赖 jsdelivr CDN，弱网时 `marked is not defined` ⇒ **agent 回合直接崩**。主脚本块新增 `renderMarkdown()` 降级（无 marked 时等宽纯文本，内容不丢）+ 3 处调用点替换；前端断言 **371 → 372**。

### 新增
- **交付物**：`E:\zy.work\practical-2-answers.docx` —— 由平台内置 agent（云 DeepSeek）经 `read_docx` → `run_simulation` → `write_docx` 生成；98 段 / 7 标题 / 20×7 真 Word 表 / 诚实 AI Declaration。
- `docs/PRACTICAL2_AGENT_RUN.md`：端到端运行记录（4 次尝试、API 层工具链证据、产物级自动验收、诚实声明）。

### 记录（未修）
- **P-48** 计算纪律诱导逐项 `run_simulation` ⇒ 24 轮迭代耗尽 ⇒ 进入**无工具的云接手**并输出"无法完成"。本轮用任务提示（算术合并、≤3 次仿真、优先 write_docx）规避并成功；建议下轮平台修 `MAX_ITER` 40~48 + 系统提示算术合并 + escalation 如实告知"迭代耗尽"。

## [未发布] — 2026-10-19（续接：agent 文档链路 P-45~P-47）

这一批是**让内置 agent 真的能读写工作台之外的文件**，并补上"长文不截断 / 云模型可主动选"两个 agent 模式缺口。

### 修复
- **P-45** 文档工具读写不对称：`read_docx`/`read_sheet` 后端实现支持 `out_dir`，但工具元数据未声明 ⇒ schema 里没有该参数 ⇒ agent 永远读不到 `uploads/` 之外的文件（典型：浏览器自动化下载到 `E:\zy.work\from_agent` 的 docx 读不回 ✗）。已在 `DOC_TOOL_META` 补 `out_dir`。
- **P-45b** 前端 `AGENT_TOOLS.read_docx`/`write_docx` 是旧手写 handler，把 `out_dir` 丢掉 ⇒ 产物只落项目 `outputs/`、写不到 E 盘 ✗。改为走通用分发 `POST /docs/tool`（服务端 `_ft_*` 已有 out_dir 与"产物真实存在"校验）；并让 `scripts/gen_agent_kb.py` 认识 `callDocTool` 分发模式（否则生成物把 `write_docx` 判成 GET，既有守卫会红 ✓）。
- **P-46** agent 模式 `nativeToolCall()` 写死 `max_tokens: 2048` ⇒ 页面「max tokens」设置对 agent 无效 ⇒ 整篇答案一次写会被截断、工具参数 JSON 损坏 ✗。改为跟随页面设置（256~8192）。
- **P-47** 主对话模型下拉原本只渲染 LoRA+底座，`/chat/models` 里的 `openai:deepseek-chat` 不可选 ⇒ 主对话 agent 无法主动选云模型 ✗。下拉补「☁️ 云模型」分组（**设计取舍待用户确认**，可回滚）。

### 文档
- `docs/PLATFORM_ISSUES.md`：新增 P-45/P-45b/P-46/P-47；**补录 P-32~P-44**（13 个浏览器自动化修复此前只在提交信息/代码注释里，未入台账 ✗）。
- `docs/OPEN_TASKS.md`：J 组台账补 P-22~P-47 状态行。
- `HANDOVER.md` / `NEXT_PROMPT.md`：本轮收尾交接（作业本体未开始、4 个待定决策、下一轮驱动方式、复盘教训）。

## [未发布] — 2026-10-19

这一批是**确定性计算 + 可判分**：把计算从模型脑子里搬进仿真节点（禁止心算），补上 BFCL 风格判分器，并确认 P-22/P-24 两项平台修复的实际效果。

### 修复
- 并行工具调用被静默丢弃（P-22）：一轮提 N 个只执行第 1 个 ✗ ⇒ 全部执行 + 画卡 + 结果回填；执行率 **61% → 100%**（arm1s 33/33 · v2r32s 4/4）。
- 度量口径缺陷（M-1~M-5）：只数 ✅ 漏失败卡 / `.slice(-6000)` 截头 / 名字正则无边界 / "提出"当"执行" ⇒ 旧口径**低估 11 倍**（33 次执行数成 3 次），改为从 DOM 卡片分层统计。
- 学习链语料闸门（P-23）：免责模板且无工具计划 ⇒ 只落盘做证据不入库；按问句去重（实测重复 60%）；`agent_use` 未被污染（74 块 / 10 条目）。
- 演练任务文档缺陷（P-24）：P-8 默认配置只读导致 P1-01/05/06/07 必败 ⇒ 补"先 `create_workflow_config` 克隆"前置；写操作 **2/6 → 4/6**。
- `run_simulation` 参数类型（422）：`args` 声明为 object + 后端容错 `dict|str`；schema 参数类型改为按后端声明的 `prm.type` 判断。
- 静态服务入口：`/` 原返回目录列表、`/index.html` 原 404 ⇒ 统一 302 到 `/index3.html`（旧书签不再误判"前端没同步"）。
- 发布闸门（P-26）：预设工作流数量只看已入库文件（演练产物落盘不该卡发布）。

### 新增
- **全局仿真节点**（`sim_nodes.py`）：3 个内置节点 `math_eval` / `stats` / `ratio` + 插件目录 `sim_plugins/`（`def register(reg)` 启动自动加载）。
- **递归计算守卫**（`sim_guard.py`）：任意子代理层级（工作流节点/嵌套/云端）都必须**成功**用过仿真节点才算合规（只看"尝试过"会被参数失败绕过）。
- 工具：`list_sim_nodes` / `run_simulation`（agent 工具目录 52 → **54**；后端工具 `/docs/tool/list` 共 35 个）。
- 前端 3 个快捷 chip：🧮算一下 / 📊统计分析 / 🔧仿真节点。
- **演练判分器**（`scripts/drill_score.py`）：BFCL 风格（AST 工具匹配 + 执行验证 + Relevance）· 机器 60 + 判官 40；单测 23 项。
- 测试：`test_sim_nodes` 29 · `test_sim_guard` 19 · `test_drill_score` 23 ⇒ 后端用例 **745 → 816**。

### 文档
- `docs/PROJECT_REVIEW.md`：三层目标现状 + **20 条未完成清单** + 最大测量缺口（从未测过任务成功率）+ 结论硬度表。
- `docs/LORA_TRAINING_RESEARCH.md`：LoRA 训练实证调研（官方文档 + 论文 + 本地代码交叉验证，带来源 URL）。
- `docs/SIM_NODES.md`：仿真节点三层强制机制 + 3 个内置节点 + 插件写法 + 递归守卫。
- `docs/EVAL_P19_P22.md`：对照评估最终版（P-19 去重 / P-22 并行 / 度量修正三组实测）。
- `docs/DRILL_SCORES_ARM1S.md`、`docs/DRILL_SCORES_V2R32S.md`：两臂判分报告（BFCL 60 分制 · n=6 · 无判官）。
- `docs/P20_UI_TOOLCARD_REPORT.md`：P-20 工具卡渲染/计数口径调查（真正渲染 bug 只有一处：只取 `tool_calls[0]`）。
- `docs/KB_HEALTH_REPORT.md`：知识库健康审计 + 学习链产物质量（`test_use_agent` 有用:退化 = 3:14）。
- `docs/DRILL_TASKS_REVIEW.md`：100 条演练任务质量复核（20/50 条验收点不可判分，已给改法）。
- 主文档陈旧数字修正：后端 **745 → 816**、工具目录 **52 → 54**（`HANDOVER.md` / `README.md`）。

## [未发布] — 2026-10-18（上一批，保留追溯）

这一批是**平台自愈 + 实战演练**：把演练暴露的平台自身缺陷（P-1~P-21）修掉，并用真页面跑批逐条验收。

### 修复
- 学习增强提炼恒为空模板（P-1）：改为 `learn_sink` 自产条目 ⇒ `test_use_agent` 从 0 到 1 条目 / 30 块。
- 清洗按长度丢短任务（P-2）：改按内容判碎片，阈值 25 → 8；真实语料保留 22/23（95.7%）。
- 并发训练崩 CUDA（P-3）：`/training/start` 单飞检查（第二个 409）+ `train_lora.py` 进程级 flock。
- 截断数据照样开训（P-4/P-7）：训练前置体检，超 cutoff 比例 >10% 拒绝并给建议，产出 `data_report.json`。
- 7B QLoRA 无梯度检查点 + 显存孤儿（P-5）：加 `--gradient_checkpointing true`，vLLM 启动前清 `VLLM::EngineCore` 孤儿。
- Agent system 提示冗长（P-6）：9253 字符 → 269 字符（工具定义已由原生 schema 提供）。
- 危险操作能改发布默认配置（P-8）：`workflow_config.json` 只读，写类工具拒绝并要求先克隆。
- 云接手轨迹缺动作计划（P-9）：要求先给 PLAN，后端解析并存 `planned_tools`。
- 训练任务"假 running"（P-11）：后端启动时按进程对账；vLLM 启动互相踩（P-12）：加锁 + 已在加载则直接返回状态。
- reflect 超时丢已产出答案（P-13）：超时/中断前打捞 final（`final_salvaged` + 单测）。
- 经 API 传 `lora_rank` 不生效（P-17）：超参注入挪到 env 最后一次重置之后。
- 工具定义重复（P-19）：schema 按名去重 92 → 73（重复 0），空参数兜底不再压过真实描述。
- 训练-推理格式结论修正（P-16/P-18）：三层验证证明格式本来就对齐（原假设被推翻，详见 `docs/P18_FORMAT_VERIFICATION.md`）。

### 新增
- 工作流产物回流知识库（G1）：`workflow_learn.py` + 引擎节点字段 `kb_write`（默认 `off`，向后兼容）。
- Agent 新建/切换工作流配置工具（G2）：`create_workflow_config` / `use_workflow_config`。
- Agent 模式本地失败/超时自动云接手 + 质量闸门（A1）；演练库 `test_use_agent`（与 `agent_use` 严格分开）。
- `POST /gpu/cleanup`：清理无对应 entrypoints 的 `VLLM::EngineCore` 孤儿 + 显存兜底。
- 训练接口支持显式 `lora_rank` / `lora_epochs`。
- 工具目录统一为 **52** 个 agent 工具（其中 8 个危险操作），并加"目录 ↔ schema"一致性测试（P-10）。

### 文档
- `docs/PLATFORM_ISSUES.md`：P-1~P-21 平台问题台账（现象/证据/根因/修法/验收）。
- `docs/DRILL_PLAN_2026-10-15.md`、`docs/DRILL_TASKS_PART1.md`、`docs/DRILL_TASKS_PART2.md`：演练方案 + 50/50 条任务。
- `docs/DRILL_PART1_RESULTS.md`、`docs/DRILL_FINDINGS.md`、`docs/DRILL_EVALUATION_2026-10-15.md`：part1 结果与实测发现。
- `docs/DRILL_H5_EVALUATION.md`、`docs/DRILL_H5V2_EVALUATION.md`、`docs/DRILL_H5_COMPARISON.md`：H5 / H5-v2 评估与三臂对比。
- `docs/P18_FORMAT_VERIFICATION.md`：P-18 三层格式对齐验证（含真实页面抓包）。
- `docs/LORA_DATA_AND_EVAL_GUIDE.md`：数据形态 · 评测标准 · 训练原理。
- `docs/CHAIN_AUDIT.md`、`docs/TRAIN_PLAN_SEMANTIC.md`：工作流链条审计 · 语义训练方案。
- 门禁基线更新：后端 **699 → 745** · 前端 **371/371** · JS 语法 **3/3** · `make lint` 绿。

## [未发布] — 2026-10-13（上一批，保留追溯）

这一轮的主线是**让本地模型也能真正用起来**：把"对话页的 Agent 模式跑在本地底座上"打通，
并顺手清掉一批"看见过但没跑过"的底层缺陷。

### 新增
- **本地模型的原生工具调用**（本版最重要的修复，见下）：对话页 Agent 模式选
  「（不挂 LoRA · 用当前底座）」或任意本地 LoRA，现在能真正调用 80+ 个工具。
- `scripts/check_local_agent.sh`：本地 Agent 路径冒烟测试（静态 + 进程 + 端到端 + 门禁）。
- 知识库**向量模型可自选**：知识库页新增「🧬 向量模型」区，可切换内置模型或填外部模型目录，
  换完自动重建、维度随之调整；每个库各记自己的模型（`meta.json`），互不干扰。
- 学习增强：把 headless profile 的"环境铁律 + 调用预算"写进**系统提示层**
  （`docs/dsh/headless-cordis.patch.yml`，附安装与验证命令）。
- 蒸馏防呆：轨迹少于 `MIN_TRAJ`（默认 20）时**拒绝重写** `agent_kb/90_learned.md`
  并拒绝入库 —— 防止空数据覆盖成果。

### 修复
- **本地 Agent 四处底层缺陷**（选本地模型根本用不了；详见 `docs/FAILURE_CASES.md` 第八组）：
  `/chat/tools` 本地分支引用不存在的 `VLLM_PORT`（必 500）；
  vLLM 启动未带 `--enable-auto-tool-choice` / `--tool-call-parser hermes`；
  前端对本地的"文本协议"导致工具调用丢失；`buildToolsSchema` 漏掉全部 agent 工具。
- 前端 `API_BASE` 与 `learn.js` / `rolechat.js` 的 `localhost` → `127.0.0.1`
  （浏览器可能解析成 IPv6 `::1`，表现为大量 `ERR_CONNECTION_REFUSED`、时通时不通）。
- 「（不挂 LoRA · 用当前底座）」选项 `value` 为空串导致"点了发送没反应" → 改为 `vllm:base`。
- `kb.delete_by_source` 引用不存在的集合名辅助函数 → `NameError`；改为 `kb_<name>`。
- 知识库重塑/入库的若干问题：`90_learned.md` 曾被空蒸馏覆盖（已用归档轨迹精确还原：
  7762 字符 / 251 行，并入库回 `agent_use`）；`meta.embedding` 曾按全局环境变量记录，
  与库实际模型不符。

### 文档
- `README.md`：Agent 一节讲清**本地/云都能跑**及"本地要能调工具"的前提；
  自检一节加 `check_local_agent.sh`。
- `docs/FAILURE_CASES.md`：新增第八、九组（L1~L5 + 三条流程规矩），案例数 25 → 29。

## [v0.2.0] — 2026-10-11

这一版的主线是**可交付性与防错**：把"给别人用"这条路走通，并堵掉一类"静默产出垃圾"的缺陷。

### 新增
- **DSH 插件（服务端半）**：把工作台能力作为 53 个工具暴露给任意 AI 对话 ——
  文档 33 个（Word/Excel/PDF 读写编辑）、控制类 17 个（LoRA / 训练 / 底座 / 工作流 / 知识库）、元工具 3 个。
  插件只经 HTTP 调用后端，不修改工作台任何源码。
- **DSH 插件（客户端半）**：在 DSH 对话的**右侧边栏**加一个「论文工作台」标签页，
  内嵌工作台完整界面（实测 575×653 可见可用）；另提供对话顶栏按钮作为入口。
- **采购自动化文档流程**预设示例（`doc_flows/doc_flow_procurement.json`）：
  建采购台账 → 按供应商汇总 → 筛待交付 → 导出明细，4 步 142ms 全绿，确定性工具、不占显卡。
- `scripts/publish.sh`：一键发布同步（打包 → 重解安装目录 → 同步快照 → 体检）。
- `scripts/check_sync.sh`：按内容哈希比对发布包/安装目录/快照与 HEAD，支持 `--fix`。
- 自动启动：可选的计划任务，登录即启动工作台并保持 WSL 常驻。

### 修复
- **拒绝把"一整坨 JSON"当正文写进 Word**（`write_docx` / `write_docx_new` / `export_docx`）：
  实测 agent 把工作流结果 `result_*.json` 导成 Word，产出 507 段 JSON、标题只有 `{`，
  却返回 `exported: true` ✗。现在命中特征即 400 拒绝并给出可照做的替代做法。
  （详见 `docs/FAILURE_CASES.md` 案例 I1）
- `requirements.txt` 补 `python-multipart`：干净环境安装后后端曾**整个模块导入失败**
  （只有"从零装"才复现），并加 CI `fresh-install` 作业守住。
- `workflow_engine` 的预检失败改用异常（原先 `sys.exit` 会直接杀掉进程），
  后端返回 400 + 错误码 + 详情。
- 发布包会混入名字异常的垃圾文件：`make_release.sh` 加文件名门禁，`git rm --cached`
  加 `core.quotePath=false` 以正确处理非 ASCII 文件名。

### 变更
- 前端"运行流程"使用的是**内存草稿**（不强制先保存）：满意后请点「💾 保存流程」才会落盘，
  刷新页面草稿即失。发布包内含 5 个可直接复用的文档流程。

### 已知局限
- 本地小模型（4B）上的部分 LoRA 抽取节点产出偏少，论文分析流程中个别字段可能为空，
  此时流程会"降级到底座兜底"并在报告里标注；对关键结论请人工复核。
  详见 `docs/FAILURE_CASES.md` 与 `README.md` 的常见问题。
- 需要本地推理/训练时须有 NVIDIA 显卡与足够显存；显存被占满时底座会启动失败。

## [v0.1.0] — 2026-10-08

首个可对外分发的版本。

### 新增
- 一键安装：`install.sh`（含 `--check` / `--minimal`，幂等，绝不覆盖已有 `settings.json`）；
  Windows 侧 `一键安装.bat` / `启动工作台.bat` / `停止工作台.bat` / `WSL保持运行.bat`。
- 文档编辑流水线：Word/Excel/PDF 读写编辑、校对、术语统一、润色、翻译、结构规整、逻辑核查、精简与扩写。
- 论文分析工作流引擎：可视化编排、节点注册表、预检、LoRA 挂载与切换、云端 provider 路由。
- 知识库（语义检索 + 多库候选 + 命中诊断）、可复跑的文档流程（编辑工作流）。
- LoRA 训练与推理底座管理（vLLM）。
- 测试基线：后端 699 项、前端 370 项、JS 语法门禁 2 项；CI 同时跑 `fresh-install`。
- 文档：`README.md`、`docs/ARCHITECTURE.md`、`docs/BASELINE.md`、`docs/EVAL_PROTOCOL.md`、
  `docs/FAILURE_CASES.md`（46 个真实失败案例）。
