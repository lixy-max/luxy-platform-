# README 历史章节存档（v4.9.6 及之前）

> 这些章节在开源封装时从 README 移除：内容已被新 README 覆盖，或属于"研发过程记录"。保留作追溯。

## 这个平台解决什么

### 核心闭环（★ 平台本体的价值）

| 环节 | 本平台怎么做 |
|---|---|
| **模型管理** | 本地模型下载 / 切换 / 量化 ✓ 任意 HuggingFace 模型 ✓ |
| **数据** | 从真实使用轨迹生成 ✓ 清洗 / 提炼 / 质检 ✓ 格式+质量双规范 ✓ |
| **训练** | LoRA（**单卡/双卡可选** ✓ 默认单卡 ✓）· 显存自适应 ✓ |
| **评测** | 5 个工具指标 + 判分器 + A/B + 环境隔离 + t 检验 ✓ |
| **部署** | vLLM 服务 ✓ LoRA 热挂载 ✓ OpenAI 兼容 API ✓ |

### 应用示例（衍生 ✓ 你需要就启用 ✗ 不是平台定位）

| 场景 | 怎么拼 |
|---|---|
| **角色对话** | LoRA（口吻）+ 知识库（该知道的事）✓ **一个应用示例** ✓ |
| **按格式调工具 / 守规范** | 从真实轨迹训 LoRA ✓ 用工具选择准确率等 5 个指标验证 ✓ |
| **算数不出错** | 仿真节点（`math_eval`/`stats`/`ratio`）✓ **禁止心算** ✗ 全链路守卫 ✓ |
| **用完你的文档** | 知识库 + 检索 ✓ 可换 embedding ✓ 有质量报告 ✓ |
| **知道"到底行不行"** | 100 任务演练 + 判分器 + A/B + 环境隔离 + t 检验 ✓ |



## 核心资产

- **评测体系**（开源里罕见 ✓）：`docs/EVAL_PROTOCOL.md` · 32 条评测集 · 5 个工具指标 · 环境快照隔离 · 配对 t 检验
- **数据质量规范**：`docs/DATA_QUALITY_SPEC.md`（F1-F8 格式 + Q1-Q8 质量 + N1-N4 数量 ✓ 可机器校验）
- **工具开发规范**：`docs/TOOL_DEV_SPEC.md`（单一真源 + 5 项一致性检查 ✓）
- **仿真层**：`sim_guard.py`（作用域树 + 递归校验 + 自动修复 ✓ 算数不可信 ⇒ 必须走确定性节点）
- **数据飞轮**：真实轨迹 → 蒸馏 → 知识库 → 训练数据 ✓（`docs/FLYWHEEL_FUSION_REPORT.md`）



## 实测数据（2026-10-19）

- 训练数据：**435 条**真实轨迹（`train_loss 1.1704` ✓ 健康）
- 工具调用：**85 个工具**已注册、schema 完整 ✓ 未知工具错误从 0.16/run 降到 **0** ✓
- 判分器：60 分制机器判分 ✓ 评测协议 v1 ✓ 环境隔离 ✓
- 测试门禁：后端 816+ · 前端 371/371 · JS 语法检查 ✓

---



## 最近重要变更

### 2026-10-19（本轮）

- **算数交给仿真节点**：新增 3 个内置仿真节点（数值表达式 `math_eval` / 描述统计 `stats` / 占比 `ratio`），Agent 要计算时必须调节点、**禁止心算**；对话页有 🧮算一下 / 📊统计分析 / 🔧仿真节点 三个快捷 chip。
- **递归计算守卫**：工作流子节点、嵌套代理、云端接手任意一层只要做过计算，都必须成功走过仿真节点才算合规。
- **任务判分器**：新增 BFCL 风格判分（工具选择 AST 匹配 + 执行验证 + Relevance；机器 60 + 判官 40）。首跑 n=6：arm1s **34.6** / v2r32s **28.2**（60 分制、无判官）。
- **并行工具调用修复**：以前一轮提多个工具只执行第 1 个，现在全部执行并回填；实测执行率 **61% → 100%**。
- **入口更顺手**：访问 `/` 或 `/index.html` 会自动跳到 `/index3.html`，旧书签不再像"前端没同步"。
- **工具目录 52 → 54**（新增 `list_sim_nodes` / `run_simulation`；后端工具 `/docs/tool/list` 共 35 个）。
- **门禁基线**：后端 **816**（原 745）· 前端 371/371 · JS 3/3。

### 2026-10-18（上一轮）

- **工作流产物可回流知识库**（G1，可选）：在节点上开 `kb_write`（默认关），运行完把清洗后的要点写进指定库。
- **Agent 能新建/切换工作流配置**（G2）；**发布默认配置只读**保护 —— 误删/误改会被拒绝并提示先克隆。
- **Agent 本地失败/超时自动云接手**（A1），带"是否真的动手"质量闸门；云接手轨迹写入演练库 `test_use_agent`。
- **学习链修好**：云接手后知识库能自动积累（P-1：0 → 30 块）；清洗不再按长度误丢短任务（P-2，真实语料保留 22/23）。
- **训练更稳**：并发训练第二个直接被 409 拒 + 文件锁（P-3）；数据超上下文比例 >10% 拒绝开训（P-4/P-7）；
  梯度检查点 + 启动前清 `VLLM::EngineCore` 孤儿（P-5）；启动时对账"假 running"（P-11）；vLLM 启动互斥（P-12）。
- **新接口**：`POST /gpu/cleanup`（清孤儿 + 显存兜底）；`POST /training/start` 支持 `lora_rank` / `lora_epochs`。
- **Agent 更省上下文**：system 提示 9253 → **269** 字符（P-6）；工具定义按名去重 **92 → 73**（重复 0，P-19）。
- 问题台账见 `docs/PLATFORM_ISSUES.md`（P-1~P-26）；演练/评估见 `docs/DRILL_*.md`、`docs/PROJECT_REVIEW.md` 与 `docs/EVAL_P19_P22.md`；
  数据与评测标准见 `docs/LORA_DATA_AND_EVAL_GUIDE.md`。



## 一键安装

**Windows 双击 `一键安装.bat`**（桌面 / `E:\WSL\` / 仓库根目录各一份），或在 WSL 里：

```bash
bash scripts/install.sh              # 六步：环境检测 → 依赖安装 → 配置生成 → 运行目录 → 模型检查 → 自检
bash scripts/install.sh --check      # 只检测环境，不安装任何东西（安全，随时可跑）
bash scripts/install.sh --minimal    # 只装核心依赖（不装 chromadb 等较重的包）
WORKBENCH_VENV=~/myenv bash scripts/install.sh   # 指定 venv 位置
```

它会自动检测并报告：WSL/系统、Python 版本、GPU 与驱动、磁盘空间、PyPI 可达性、端口占用；
然后建 venv、按 `requirements.txt` 装依赖、从 `settings.example.json` **生成本地化配置**
（**已存在则不覆盖**）、确保运行目录、检查底座模型是否就绪，最后跑一遍单元测试自检。

> 不装底座模型也能用：界面、知识库、文档工具、以及**云模型**（选 `openai:...`）都可用。



## 快速开始

### 一键启动（推荐）

**Windows 双击 `启动工作台.bat`**（在 `E:\WSL\` 或仓库根目录下各有一份），
或在 WSL 里：

```bash
bash scripts/start.sh              # vLLM 底座 + 后端 + 前端（幂等，秒级返回）
bash scripts/start.sh --no-gpu     # 不起底座（仅 KB / 规则模式）
bash scripts/start.sh --no-browser # 不自动打开浏览器
```

启动后打开：<http://127.0.0.1:8080/index3.html>

> **底座是异步加载的**：启动脚本提交请求后**立即返回**，不阻塞等待。
> 进度看网页「🎛️ 底座管理」页，或 `tail -f logs/backend.log`。

### 停止

```bash
bash scripts/stop.sh            # 停前端与后端（保留底座）
bash scripts/stop.sh --all      # 连底座一起停
bash scripts/stop.sh --dry-run  # 只显示会停哪些进程（安全自检）
```

或双击 `停止工作台.bat`。

### 首次安装

```bash
bash scripts/setup.sh     # 建 venv、装依赖、预建运行目录
```

模型从**魔搭社区**下载：「🎛️ 底座管理」页的模型下载框支持直接填 ModelScope 仓库名，
带进度百分比与已下载列表/删除。

---



## 界面导览

| 页面 | 做什么 |
|---|---|
| 💬 **对话** | 聊天；右上角可切 **🤖 Agent 模式**（自然语言操作全站）|
| 🧩 **分析工作流** | 论文分析流水线的可视化编辑与运行（节点/连线/参数/运行可视化）|
| 📝 **编辑工作流** | **文档编辑流水线**：导入 → 规范化 → 分段 → 校对 → 润色 → 精简 → 逻辑核查 → 术语统一 → 翻译 → 导出 Word |
| 📚 **知识库** | 语义检索知识库（含 agent 工具调用手册）|
| 🚀 **LoRA 训练** | 从运行结果构造数据集 → 训练 → 评测 → 挂到节点上 |
| 🧱 **LoRA 管理** | 列出/加载/卸载 LoRA 到 vLLM |
| 🎛️ **底座管理** | 启停 vLLM、下载模型、就绪检查 |
| 📜 **历史** · ⚙️ **系统设置** | 运行历史 · 端口/密钥/路径等配置 |

---



## 常见用法

### 用 Agent 做事（自然语言）

在对话页切到 🤖 Agent 模式，然后直接说，例如：

```
新建一个 word 在 E 盘，标题是「项目汇总」，按小节补全内容
给这个 word 加一页目录，并修正格式
列出所有知识库，每个多少块
看一下最近有哪些训练任务，成功失败各几个
在 agent_use 里检索「工具调用要注意什么」，把来源文件名列出来
```

Agent 会自己选择工具（`create_docx` / `add_toc` / `format_fix` / `list_knowledge_bases` /
`query_knowledge_base` / `list_trainings` … 共 **80+ 个**），并把结果整理成回答；
生成文件的 Windows 路径会直接告诉你。产物默认落在项目 `outputs/`，
也可以在参数里指定 `out_dir`（支持 **E 盘**）。

#### 用哪个模型跑 Agent（本地 / 云都行）

| 选择器里的项 | 走哪 | 说明 |
|---|---|---|
| `（不挂 LoRA · 用当前底座）` | **本地 vLLM** | 本地底座直接调工具 |
| `vllm:lora:<名称>` | **本地 vLLM + LoRA** | 挂了 LoRA 也一样能调工具 |
| `openai:<模型名>` | **云端 API** | 在「系统设置」里配好 api_key |

前端对三者都走**原生 function-calling**（`POST /chat/tools`），不再依赖
"让模型输出 `<tool_call>` 标记再解析"的文本协议 —— 本地 Qwen 经常不打那个标记，
会让工具调用整体丢失。

> **本地要能调工具，vLLM 必须带两个参数**：
> `--enable-auto-tool-choice` 与 `--tool-call-parser hermes`
> （由 `vllm_manager.py` 自动带上；缺了它们，带 `tools` 的请求会被 vLLM 直接拒绝 400）
>
> 一条命令自检：`bash scripts/check_local_agent.sh`

### 跑分析工作流

1. 「🧩 分析工作流」页 → 左上选配置 → 「📎 选择论文」上传
2. 「▶️ 运行」→ 日志与结果分栏显示；节点上会显示 ✅/❌ 与耗时
3. 结果自动入库，可到「📜 历史」查看，或用它构造训练样本

### 训练一个 LoRA 并挂到流程里

「🚀 LoRA 训练」→ 选择运行结果构造数据集 → 训练 → 评测 →
「🧩 分析工作流」里把节点换成新 LoRA（Agent 里就是 `update_workflow_node`）。

---



## 文档编辑流水线（编辑工作流页）

节点库按**一般文档编辑流程**组织，从导入到导出共 11 个阶段：

```
📥 导入文档 → 🧹 格式规范化 → ✂️ 分段与标题 → ✅ 校对纠错 → 🖋️ 润色改写
   → 📉 精简摘要 → 🔍 逻辑核查 → 📚 术语统一 → ➕ 扩写补写 / 🌐 整篇翻译 → 📤 导出 Word
```

- 只有 `format_fix` 是**确定性规则**（快、可复现）；其余需要本地模型。
- **从 0 创建**：`create_docx`（标题 + 小节）、`create_sheet`（表头 + 数据行）、
  `add_toc`（插入目录页，**幂等**：已有目录不会重复添加）。
- **输出目录安全**：`out_dir` 只允许项目目录或 E 盘，拒绝 `..`、`/etc`、`C:\Windows`、`/root`。
- **长文本自动分块**：translate / polish / proofread / condense / terminology /
  structure_fix 都按段落边界分块（默认 2500 字符/块，`chunk_chars` 可调），
  返回里带 `chunks` 字段。
- **一键打开结果**：运行完点「📂 打开结果」，资源管理器会定位到生成的文件。

---



## 测试与自检

```bash
make test            # 后端单元测试（816）
make test-frontend   # 前端断言（371，需在 Windows 侧用 node 跑）
make doc-test        # 文档处理任务测试台（37 用例/轮，真实模型，约 15 分钟）
make repro           # 云端 token 对照实验（读 outputs/ 真实运行记录）
make secrets         # 发布前密钥扫描闸门
make release         # 打包发布 zip（打包后自动跑 secrets）
make ui-check        # 界面自检（需 Windows 侧 node + 本机 Edge）
bash scripts/check_local_agent.sh   # ★ 本地 Agent 路径冒烟测试（推荐改动后先跑这个）
```

`check_local_agent.sh` 专门盯**本地 Agent** 这条链：vLLM 有没有带 tool-calling 参数、
`/chat/tools` 在本地底座上能不能真的返回 `tool_calls`、前端有没有把 agent 工具
放进原生 schema、`API_BASE` 是不是 127.0.0.1…… 每一条都对应一个**真实踩过的坑**
（见 `docs/FAILURE_CASES.md` 第八组）。

界面自检用 `playwright-core` 驱动**系统已装的 Edge**（不下载浏览器），
真的点按钮、拖拽、断言元素，并全程捕获 `console.error` / `pageerror` / HTTP 4xx+。
用法与首轮结果见 `tools/README-ui-testing.md`。

---



## 架构与设计文档

| 文档 | 内容 |
|---|---|
| `docs/BASELINE.md` | 基准数据与对照表（所有数字现测）|
| `docs/EVAL_PROTOCOL.md` | 评测口径：测什么、怎么判分、噪声多大、什么算证据 |
| `docs/FAILURE_CASES.md` | **失败案例集**（24 个真实失败：现象/复现/根因/修法/防复发）|
| `docs/DSH_PLUGIN_PORTING_ASSESSMENT.md` | 把 DSH 插件移植到本项目 agent 的可行性评估 |
| `HANDOVER.md` | 开发全过程与决策记录（含 §Z 受控 A/B 实验）|
| `WORKFLOW_ROADMAP.md` | 工作流后续方向 |

---



## 许可与合规

- 本项目以 **MIT** 许可发布（见 `LICENSE`）。
- 第三方依赖清单与许可证见 `THIRD_PARTY_LICENSES.md`。
- 数据与版权注意事项见 `DATA_AND_COPYRIGHT.md`。
- 发布前会跑密钥扫描闸门（`make secrets`）。

---


