# 顿悟 luxy · 单机模型垂直开发平台

<p>
  <img alt="version" src="https://img.shields.io/badge/version-v4.9.7-D97757?style=flat-square">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-C8B88A?style=flat-square">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="tests" src="https://img.shields.io/badge/tests-828%20passing-7FB069?style=flat-square">
  <a href="https://github.com/lixy-max/luxy-platform-/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/lixy-max/luxy-platform-/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="single machine" src="https://img.shields.io/badge/deploy-single%20machine-1F1E1D?style=flat-square">
</p>

> **把模型变成你想要的样子** —— 而不是让它更聪明。
>
> 一个**单机**的模型垂直开发平台：`下载模型 → 造数据 → QLoRA 训练 → 挂知识库 → Agent/工作流 → 评测`
> 全链路在自己的机器上闭环，数据一行都不出本机。

**当前版本 v4.9.7** · 校验方式 `GET /version` · 变更记录 [CHANGELOG.md](CHANGELOG.md)

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [和别的东西有什么不同](#和别的东西有什么不同)
- [60 秒上手](#60-秒上手)
- [实测数据](#实测数据)
- [架构](#架构)
- [界面](#界面)
- [文档](#文档)
- [开发与贡献](#开发与贡献)
- [Docker 快速开始](#docker-快速开始)
- [合规与许可](#合规与许可)
- [许可](#许可)

---

## 它解决什么问题

通用大模型"知道"很多东西，但**不懂你这行的具体用法**：

| 你的困扰 | 这个平台怎么做 |
|---|---|
| 手里几百份文档，模型读不到 | 建知识库（自动切分 / 向量化 / 检索 / **建成即自动注入**）|
| 模型回答太"通用"，不像你行里人说话 | QLoRA 微调 —— 40 条数据、**200 秒**训出一个领域 LoRA，热切换不中断服务 |
| 模型一本正经地编数字/编出处 | 5 层幻觉检测 + 修复引擎（定位→修复→再验证→失败回滚）+ 上下文接地校验 |
| 缺参数的题它硬算 | **确定性反问**：直接问你要什么，而不是猜一个数字出来 |
| 试过 Dify 那类平台，但只能"用"不能"造" | 这里是**造模型**：数据 → 训练 → 评估 → 挂载，一条链在自己机器上 |

## 和别的东西有什么不同

```
不是 ChatGPT 套壳      —— 底座是你自己的开源权重
不是 Dify/n8n 那类     —— 那些是"编排别人的模型"，这里是"造自己的模型"
不是一键部署 SaaS      —— 单机自托管，数据不出本机
不是训练基础模型       —— 只做领域适配（QLoRA），消费级显卡够用
```

**诚实的边界**：3 个 LoRA 训练样本（n=3）不足以证明普适性；知识库召回在 13 类文档上
实测 94.3%（关键词口径），但对"无标点中文长文"仍需要正确切分参数。详见
[docs/internal/](docs/internal/) 里的实测记录。

## 60 秒上手

```bash
# 1. 依赖
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 启动（后端 :8001 + 前端 :8080）
bash install/start.sh

# 3. 自检（7 项）
bash install/smoke_test.sh

# 4. 打开
#    http://127.0.0.1:8080/index3.html
```

**Docker**（可选）：

```bash
cp .env.example .env        # 填入可选的云 Key
docker compose up -d --build
```

**GPU**（可选，用于本地推理/训练）：装 `vllm==0.11.0`，把模型放进 `models/`，在「底座管理」里启动。
无 GPU 时知识库、文档工具、工作流、规则、仿真全部可用。

## 实测数据

> 全部为本机实测（WSL2 + RTX 5060 Ti / 3060），非估算。

| 项目 | 结果 |
|---|---|
| LoRA 训练 | **3/3 成功** · 最长 **200.3 秒** · loss 2.567 → 1.352 · r=8 · 77 MB |
| LoRA 热切换 | 3 次加载 **vLLM PID 不变**（0.4~0.5 s），同题答案 3/3 不同 |
| 领域对齐度 | 底座 0.182 → 本域 LoRA **0.237（+30%）**，跨域 LoRA 0.182（≈底座）|
| 知识库召回 | 关键词口径 **94.3%**（33/35）· 严格口径 91.4% · 洁净库 **100%**（27/27）|
| 中文长文切分 | 修复前 1677 字 → **1 块**；修复后 → 2 块（1199/679）|
| 无标点长文 | 修复前 2184 字 → 1 块 2280 字；修复后 → **5 块**（最长 500）|
| 隔离验收 | 全新建包 + 空装 → **25/25** 断言通过 |
| 测试 | **828 用例 0 红**（6 skip：旧配置专用）|
| API / 工具 | **136** 个端点 · **68** 个 Agent 工具 |

## 架构

```
┌──────────────────────── 前端 webui/（无构建步骤，解压即用）─────────────────────┐
│  index3.html + pages/*.js  训练 / 知识库 / 工作流 / Agent / 评测 / 设置           │
└───────────────────────────────────┬──────────────────────────────────────────────┘
                                    │ HTTP
┌───────────────────────────────────▼──────────────────────────────────────────────┐
│  后端 lora_manager.py（FastAPI :8001）                                            │
│  ├── 模型管理  vllm_manager · gpu_config（单卡 auto / 双卡 dual）                  │
│  ├── 数据      dataset_toolkit（F1-F8 格式 + Q1-Q8 质量）· data_generator         │
│  ├── 训练      train_lora（QLoRA）· trainer 队列 · 训练前数据体检                 │
│  ├── 知识库    kb.py（切分 / 向量 / 检索）· 自动挂载 · 失效自动清理                │
│  ├── 防错      hallucination_guard（5 层）· hallucination_repair（修复+回滚）      │
│  ├── 确定性    sim_nodes / sim_guard / sim_router（算术·物理·单位·统计）           │
│  ├── Agent     agent_kb（68 工具）· tool_quality · orchestrator（路由/编排）        │
│  ├── 工作流    workflow_engine（DAG · 环检测 · 节点类型契约）                      │
│  └── 数据层    db.py（SQLite）· 配置 settings.json（Key 支持环境变量）             │
└───────────────────────────────────┬──────────────────────────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
        vLLM (:8000)          ChromaDB            浏览器 sidecar (:9224)
        本地推理/训练后热切换   向量索引             Node + playwright-core
```

## 界面

> 截图与 98 秒介绍片见 [docs/](docs/)；介绍片为程序化合成（Remotion），无第三方素材。

| 页面 | 用途 |
|---|---|
| 角色对话 | 带知识库/工具/学习增强的对话（Agent 模式）|
| 模型 & LoRA | 底座下载/切换、LoRA 训练与热挂载 |
| 知识库 | 建库 / 上传 / 重建索引 / 挂载（自动注入）|
| 工作流 | 可视化 DAG 编排、预置示例、数据集导出 |
| 评测 & 反思 | 质量评分、失败归类、能力边界记录 |

## 文档

| 文档 | 内容 |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | 安装、启动、常见问题、版本核对 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模块划分与数据流 |
| [docs/LORA_DATA_AND_EVAL_GUIDE.md](docs/LORA_DATA_AND_EVAL_GUIDE.md) | 训练数据格式、质量规范、评测方法 |
| [docs/DATA_QUALITY_SPEC.md](docs/DATA_QUALITY_SPEC.md) | F1-F8 + Q1-Q8 规则明细 |
| [docs/SIM_NODES.md](docs/SIM_NODES.md) | 确定性计算节点（算术/物理/单位/统计）|
| [docs/TOOL_DEV_SPEC.md](docs/TOOL_DEV_SPEC.md) | 新增 Agent 工具的四步注册法 |
| [docs/DSH_PLUGIN_MANUAL.md](docs/DSH_PLUGIN_MANUAL.md) | 作为插件嵌入 DSH 的调用说明书 |
| [docs/DSH_PLUGIN_INTEGRATION.md](docs/DSH_PLUGIN_INTEGRATION.md) | 接入契约（端点/降级/探活）|
| [CHANGELOG.md](CHANGELOG.md) | 版本变更 |

## 开发与贡献

```bash
# 三道门（CI 同款）
python -m compileall -q .
bash run_tests.sh
bash scripts/check_no_secrets.sh
```

> `make test` / `bash run_tests.sh` 会自动优先使用项目内的 `.venv/bin/python`（若存在），
> 否则用 PATH 上的 `python3`。**跑测试前请先 `pip install -r requirements.txt`**，
> 否则会因缺 fastapi/pydantic 等依赖报错（这是环境问题，不是代码问题）。

见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可

[MIT](LICENSE) © 2026 luxy

## 单卡 / 双卡

```jsonc
// settings.json
{
  "gpu_mode": "auto",        // auto(默认) = 单卡：训练时停 vLLM（与旧版一致 ✓）
                             // dual       = 双卡：训练/推理各占一张 ✓ 训练时平台不停 ✓
  "inference_gpu": null,     // dual 模式必填（例: 1）
  "train_gpu": null          // dual 模式必填（例: 0）
}
```

| 模式 | 卡数 | 行为 |
|---|---|---|
| **auto**（默认 ✓） | 任意 | **单卡**：训练时停 vLLM ✓ 与改动前完全一致 ✓ |
| **auto** | 2+ | 仍单卡 ✓ **不强制双卡** ✗ 训练页只提示"可启用双卡"✓ |
| **dual** | ≥2 | 训练/推理分离 ✓ 训练期间平台不停 ✓ |
| **dual** | 1 | 自动退回单卡 ✓（不报错 ✓ 不崩 ✓）|

> **不假设任何卡号** ✗ 所有卡号都从配置读取 ✓ 换机器/换卡型不用改代码 ✓

## 诚实的边界（先看这个）

| 类型 | 本平台 | 依据 |
|---|---|---|
| **风格/行为专家**（工具调用、固定格式、固定口吻） | ✅ **能做** ✓ 500-2000 条真实数据 ✓ | LIMA 2305.11206 · Biderman 2405.09673 |
| **知识专家**（特定领域知识） | ⚠ 需 1 万-10 万条领域语料 ✓ 建议先用 RAG ✓ | 领域适应研究 |
| **能力专家**（推理/编程能力提升） | ❌ **不做** ✗ 7B LoRA 追能力是**低收益** ✗ | Zhang ICLR 2024（PET 天花板＝底座） |
| **基础模型训练** | ❌ **不做** ✗ 需要千卡级算力 ✗ | — |

> **为什么这样定位**：我们实测三轮 LoRA A/B（v3-2-0/1/2）证明"追总分"无效 ✗，
> 文献也证明 PET 的天花板是底座模型 ✓ ⇒ 转而做 **LoRA 真正擅长的事**：风格、行为、格式 ✓
> 完整复盘：`docs/RETROSPECTIVE_2026-10-19.md` · 文献库：`docs/LITERATURE_REGISTRY.md`

## 目录结构

```
webui/index3.html          前端（单文件，含 Agent 运行时与全部界面）
lora_manager.py            后端（FastAPI，含全部路由与文档工具）
workflow_engine.py         工作流引擎（节点类型/拓扑/分块/缓存）
agent_kb/                  内置 Agent 的知识库（工具目录/套路/坑/同义词）
doc_flows/                 文档流程预设
scripts/                   start / stop / setup / run / 评测 / 打包
tools/                     界面探针与文档任务测试台
tests/                     后端单测 + 前端断言
docs/                      设计与评测文档
outputs/ uploads/ cache/   运行产物（不随仓库分发）
```

---

## 常见问题

| 现象 | 处理 |
|---|---|
| 启动脚本报"找不到 Python" | 脚本会按 `$VENV → 项目 .venv → ~/.venv → 项目 venv → PATH` 顺序探测；确认其中一处存在，或先跑 `bash scripts/setup.sh` |
| 页面能开但模型不可用 | 底座还在异步加载（约 1–3 分钟）；看「🎛️ 底座管理」页或 `tail -f logs/backend.log` |
| 端口被占 | `BACKEND_PORT=8010 FRONTEND_PORT=8090 bash scripts/start.sh` |
| 想不装 GPU 底座先试界面 | `bash scripts/start.sh --no-gpu` |
| 读 E 盘的文档报"文件不存在" | 读工具按设计**不会**全盘扫 E 盘；请求时要带上 `out_dir` |

## 局限

先说清楚做不到的，免得你踩坑后才发现：

- **本地小模型的抽取节点不稳**：4B 级底座上，个别 LoRA 抽取节点会产出很少甚至为空
  （实测出现过"输入 4792 字 → 输出 17 字"）。此时流程会**降级到底座兜底**并在报告里标注
  "缺失输入"，但那一部分的结论请**人工复核**。想稳，可把这些节点切到云端 provider
  （配置里 `provider` 选 openai 等），或换用评测过的 LoRA。
- **生成的文档需要人过一眼**：工具会做格式规范化、结构规整、校对、术语统一，
  但它不是排版软件 —— 复杂版式（多级编号、页眉页脚、交叉引用）请自行检查。
- **数字与大额结论不要直接采用**：模型会算错、会编造看似合理的细节。
  凡是要签字、要付款、要对外发布的数字，请回原始数据核对。
- **本地推理/训练要有显卡**：需要 NVIDIA 显卡且显存够；显存被占满时底座会启动失败
  （先 `bash scripts/stop.sh` 或关掉占显存的进程再试）。纯文档处理不需要显卡。
- **仅在本机可用**：服务只监听本机（127.0.0.1），没有账号体系与鉴权，
  **不要直接暴露到公网**。
- **中文优先**：中文语料的检索与生成效果明显好于其他语言；跨语言场景请自行评估。

## Docker 快速开始

```bash
cp .env.example .env          # 填入可选的云 Key（也可留空）
docker compose up -d --build
# 打开 http://127.0.0.1:8080/index3.html
```

**镜像体积提示**：`requirements.txt` 会连带 `torch` 等依赖，CPU 版镜像约 **3~4 GB**。
若只需要轻量试用，可不装 GPU 相关依赖（`sentence-transformers`/`transformers` 是知识库必需，
`vllm` 不在 requirements 内，需另行安装）。

**GPU 推理**：容器内不打包 vLLM（体积与 CUDA 耦合太强）。推荐：
- 宿主机跑 vLLM（`:8000`），容器里设 `VLLM_CHAT_API=http://host.docker.internal:8000/v1/chat/completions`
- 或让容器使用 GPU（取消 `docker-compose.yml` 里 `deploy.resources` 注释，需 nvidia-container-toolkit）

> ⚠️ 容器以**非 root 用户**（uid 1000）运行；挂载的宿主机目录需可写，否则启动会失败。

## 合规与许可

- 本项目：[MIT](LICENSE)
- 第三方依赖：见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)（含 vLLM / LLaMA-Factory / Playwright / BGE / 前端 vendor）
- 数据与版权口径：见 [DATA_AND_COPYRIGHT.md](DATA_AND_COPYRIGHT.md)
- 仓库**不包含**论文正文、模型权重、用户上传数据；`corrected_papers/` 仅一个格式示例文件，见上文档说明
