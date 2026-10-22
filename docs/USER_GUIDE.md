# 使用说明书 · Luxy 模型垂直开发平台 v4.9.7

> 推荐使用方式：**DSH 接入**（一个窗口里既聊天、又能操作平台，平台能力自动变成 DSH 工具）
>
> 适用对象：任何想在自己机器上把「私人数据」变成一个「专属模型」并长期使用的人

---

## 目录

| | |
|---|---|
| [一、三种用法怎么选](#一三种用法怎么选推荐-dsh-接入) | [七、界面导览](#七界面导览) |
| [二、环境要求](#二环境要求) | [八、配置说明](#八配置说明) |
| [三、依赖清单](#三依赖清单) | [九、日常运维](#九日常运维) |
| [四、安装 · 推荐路线](#四安装--推荐路线windows--wsl2--dsh) | [十、故障排查](#十故障排查) |
| [五、安装 · 极简路线](#五安装--极简路线无-gpu) | [附录 A 路径速查](#附录-a--路径速查) |
| [六、DSH 接入详解](#六dsh-接入详解推荐重点) | [附录 B–D](#附录-b--命令速查) |

**这份说明怎么用**：只想尽快跑起来 → 直接看第四章（推荐路线）或第五章（无 GPU 极简路线）；想长期用 DSH 当主界面 → 看第六章；出问题 → 直接跳第十章故障排查表。

---

## 一、三种用法怎么选（推荐 DSH 接入）

| 用法 | 推荐度 | 适合谁 | 怎么用 |
|---|---|---|---|
| **① DSH 接入**（侧栏内嵌） | ⭐ **推荐** | 已经在用 DeepSeek Harness，希望**一个窗口**里既聊天又操作平台；想让平台能力变成 DSH 工具被直接调用 | 装 DSH 插件 → DSH 右侧边栏出现「模型垂直开发平台」标签 → 点开即用；同时 33 个平台工具可直接在对话里调用 |
| ② 浏览器网页 | 基础 | 不想装 DSH，只想有个图形界面点一点 | 启动后打开 `http://127.0.0.1:8080/index3.html` |
| ③ 纯 API / 命令行 | 进阶 | 要把平台接进自己的脚本或其它系统 | 直接调后端 136 个 API（`http://127.0.0.1:8001`） |

**为什么推荐 DSH 接入：**

1. 平台界面直接嵌在 DSH 右侧边栏，**不用切窗口**；
2. 平台的文档工具、知识库、LoRA、工作流会作为 DSH 工具自动出现在对话里（后端加了工具，插件下次启动自动就有）；
3. DSH 自带的文件读写 / 终端 / 搜索等工具可以和平台工具混着用；
4. 插件只通过 HTTP 调后端，**不修改平台任何源码**，随时可卸载。

---

## 二、环境要求

### 2.1 硬件

| 项目 | 最低（能用） | 推荐 | 说明 |
|---|---|---|---|
| CPU | 4 核 | 8 核以上 | 文档解析、知识库切分、前端都吃 CPU |
| 内存 | 16 GB | 32 GB | vLLM 加载 7B 模型时内存占用较高 |
| 磁盘 | 20 GB（不含模型） | 60 GB 以上 | 模型另需 5~10 GB；知识库/训练数据会持续增长 |
| GPU 显存 | **0（无卡可用）** | 12 GB / 16 GB 单卡 | 无 GPU：知识库、文档工具、工作流、规则模式**都能用**，只是不跑本地模型<br>12 GB：可跑 Qwen2.5-7B-Instruct-AWQ 推理<br>16 GB+：推理 + 训练都宽松 |

> **实测硬件**：RTX 5060 Ti 16 GB（训练）+ RTX 3060 12 GB（推理），双卡分离模式下训练期间推理不中断。
> 单卡 8 GB 可以跑更小的模型（如 Qwen2.5-3B / Qwen3-4B-AWQ），7B-AWQ 建议 ≥12 GB。

### 2.2 操作系统

| 平台 | 支持 | 说明 |
|---|---|---|
| Windows 11 + WSL2（Ubuntu 24.04） | ✅ 首选 | 本项目长期实测环境；Windows 侧有启动台 `.bat` |
| Ubuntu 22.04 / 24.04（纯 Linux） | ✅ 支持 | 直接用 `scripts/start.sh`，不需要 .bat |
| macOS | ⚠️ 不保证 | vLLM 对 Apple Silicon 支持有限；不跑底座时文档/知识库可用 |
| Windows 原生（不装 WSL） | ❌ 不建议 | 大量脚本是 bash；vLLM 在 Windows 原生安装困难 |

### 2.3 软件版本

| 软件 | 版本要求 | 必需性 | 说明 |
|---|---|---|---|
| Python | **3.10 ~ 3.12** | 必需 | 实测 3.11 / 3.12；`pyproject.toml` 要求 `>=3.10` |
| pip / venv | pip ≥ 23 | 必需 | 建议独立 venv（项目不写死路径，自动探测 `.venv` → `~/.venv`） |
| bash | ≥ 5.0 | 必需 | 启动/停止/训练脚本都是 bash |
| NVIDIA 驱动 + CUDA | CUDA 12.x | GPU 才要 | vLLM 0.11.0 需要匹配的 CUDA；`nvidia-smi` 能出结果即可 |
| vLLM | 0.11.0（实测） | GPU 才要 | 本地推理底座；版本需与 CUDA / torch 匹配 |
| Node.js | ≥ 20（浏览自动化）<br>≥ 22.19（DSH） | 按需 | 「浏览自动化」功能需要 Node 20+；DSH 本身与插件需要 Node ≥ 22.19 |
| pnpm | ≥ 9 | 装 DSH 插件才要 | DSH 的插件管理走 pnpm |
| Git | 任意较新版本 | 可选 | 用 git clone 方式获取代码时需要 |

### 2.4 模型文件

| 模型 | 大小 | 必需性 | 用途 |
|---|---|---|---|
| Qwen2.5-7B-Instruct-AWQ | ≈ 5.2 GB | 跑本地模型时必需 | 主底座（4bit 量化，12 GB 显存可跑） |
| bge-small-zh-v1.5 | ≈ 184 MB | 用知识库时必需 | 中文嵌入模型（CPU 即可，`embedding_device=cpu`） |
| bge-m3 | ≈ 2.2 GB | 可选 | 更强多语言嵌入，显存/内存够时可选 |
| 其它 Qwen 系列 | 0.5B ~ 15 GB | 可选 | 小卡可换 3B/4B；14B 需要更大显存 |

**模型放哪**：默认放项目下的 `models/`，或用环境变量 `MODELS_DIR` 指向别处（如 `/home/<你>/models`）。
本项目实测用的是家目录模型库 `~/models/`，并在 `settings.json` 的 `base_model_path` 里指向具体模型目录。

---

## 三、依赖清单

### 3.1 必装（`requirements.txt`，共 16 项）

| 包 | 版本 | 用途 |
|---|---|---|
| `fastapi` | 0.136.3 | 后端 Web 框架（136 个 API 端点） |
| `uvicorn` | 0.54.0 | ASGI 服务器 |
| `pydantic` | 2.12.3 | 请求/响应模型校验 |
| `python-multipart` | 0.0.32 | **隐式依赖**：文件上传路由需要，缺了后端直接起不来 |
| `requests` | 2.34.2 | 后端之间、vLLM 调用 |
| `chromadb` | 1.5.9 | 本地向量库（知识库索引） |
| `sentence-transformers` | 4.1.0 | 加载 bge 嵌入模型 |
| `transformers` | 4.56.2 | 模型/tokenizer 工具链 |
| `safetensors` | 0.8.0 | 安全张量读写（LoRA 适配器） |
| `python-docx` | 1.2.0 | Word 读写 |
| `openpyxl` | 3.1.5 | Excel 读写 |
| `pypdf` | 6.19.0 | PDF 解析 |
| `pdfplumber` | 0.11.10 | PDF 版面/表格抽取 |
| `pypdfium2` | 5.13.0 | PDF 渲染（转图片） |
| `rapidocr-onnxruntime` | 1.4.4 | 扫描件 OCR（中文） |
| `pillow` | 12.3.0 | 图像处理 |

### 3.2 可选：GPU 推理 / 训练栈

```bash
# 本地推理（底座）
pip install "vllm==0.11.0"

# 训练 LoRA（QLoRA）
pip install "torch==2.8.0+cu128" --index-url https://download.pytorch.org/whl/cu128
pip install peft accelerate datasets bitsandbytes
```

> **不装 GPU 栈会怎样**：平台照常能起，**知识库、文档工具、工作流、规则模式、云端模型**全部可用；
> 只是「底座管理」里本机模型起不来、LoRA 训练页不可用。

### 3.3 可选：浏览自动化（Agent 的浏览器工具）

```bash
cd browser_sidecar
npm install                 # playwright-core 1.64.1（要求 Node ≥ 20）
npx playwright install chromium
node server.mjs             # 默认监听 127.0.0.1:9224
# WSL 内需要访问时：HOST=0.0.0.0 node server.mjs
```

### 3.4 DSH 接入所需

| 组件 | 版本 | 说明 |
|---|---|---|
| DeepSeek Harness（DSH） | 最新版 | 提供 Web GUI 与插件机制；`dsh web` 启动后访问 `127.0.0.1:3080` |
| Node.js | ≥ 22.19 | DSH 与插件 `engines` 要求 |
| pnpm | ≥ 9 | `dsh plugin` 命令依赖 |
| `@deepseek-ai/dsh-tools` | ≥ 0.1.1-rc.2 | DSH 自带，插件 peerDependency |

---

## 四、安装 · 推荐路线（Windows + WSL2 + DSH）

这条路线是长期实测环境，共 9 步，约 30~60 分钟（取决于网速与是否装 GPU 栈）。

### A1. 安装 WSL2 + Ubuntu

```powershell
# Windows PowerShell（管理员）
wsl --install -d Ubuntu-24.04
# 装完重启，首次进入设置 Linux 用户名/密码
```

> **注意**：如果机器上已有多个 WSL 发行版（例如老的 `Ubuntu` 与新的 `Ubuntu-24.04`），
> 启动脚本会**优先选 Ubuntu-24.04**；请确认项目装在你要用的那个发行版里。

### A2. 获取代码

```bash
# 方式一：下载发布包（推荐，最小 1.2 MB）
tar -xzf luxy-platform-4.9.7.tar.gz
cd luxy-platform-4.9.7

# 方式二：git clone
git clone https://github.com/lixy-max/luxy-platform-.git
cd luxy-platform-
```

### A3. 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> **venv 位置**：项目脚本会自动探测 Python，顺序是 `PYTHON` 环境变量 → 项目 `.venv` → `~/.venv` → `python3`。
> 放在项目内或家目录都可以。

### A4.（可选）安装 GPU 推理 / 训练栈

```bash
pip install "vllm==0.11.0"
pip install "torch==2.8.0+cu128" --index-url https://download.pytorch.org/whl/cu128
pip install peft accelerate datasets bitsandbytes
```

### A5. 放置模型

```bash
# 方式一：放进项目 models/
mkdir -p models
# 把 Qwen2.5-7B-Instruct-AWQ 与 bge-small-zh-v1.5 放进去

# 方式二：用环境变量指向家目录模型库
export MODELS_DIR=$HOME/models

# 检查
ls models/     # 或 ls $MODELS_DIR
```

### A6. 启动平台

```bash
bash scripts/start.sh              # 全量启动（后端 8001 + 前端 8080 + 异步拉起 vLLM 8000）
bash scripts/start.sh --no-gpu     # 不起底座（无显卡 / 只想用知识库与文档工具）
bash scripts/start.sh --no-browser # 不尝试打开浏览器
```

Windows 侧也可以直接双击 **启动工作台.bat**（会自动开浏览器；加参数 `nobrowser` 则不打开）。

### A7. 冒烟自检

```bash
bash install/smoke_test.sh         # 7 项检查，期望 7/7
curl http://127.0.0.1:8001/version
curl http://127.0.0.1:8001/health
```

### A8. 安装 DSH 与平台插件

```bash
# 1) 确认 Node / pnpm 版本
node -v        # >= 22.19
pnpm -v        # >= 9

# 2) 启动 DSH（首次会初始化 profile）
dsh web        # 然后浏览器打开 http://127.0.0.1:3080

# 3) 打包插件（★ 必须先打包成 tgz，原因见 6.2）
cd /path/to/dsh-plugin-paper-lora
tar -czf dsh-plugin-paper-lora-0.1.1.tgz package.json src lib README.md cordis.patch.yml

# 4) 安装到当前 profile（一般是 web）
dsh plugin --profile web add ./dsh-plugin-paper-lora-0.1.1.tgz

# 5) 重启 DSH 宿主（插件需要重新导入）
#    Windows: 关掉 dsh 窗口重新执行 dsh web
```

### A9. 验证 DSH 接入成功

1. 刷新 `http://127.0.0.1:3080`
2. 右侧边栏出现标签 **模型垂直开发平台**（表示插件已激活）
3. 在对话里让模型调用 `paper_lora_status`，应返回后端与底座状态

---

## 五、安装 · 极简路线（无 GPU）

只想用「文档工具 + 知识库 + 工作流」，不需要本地大模型，可以省掉全部 GPU 依赖：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 只有 16 个纯 CPU 依赖

bash scripts/start.sh --no-gpu           # 不启动底座
# 或
bash install/start.sh                    # 更轻：只起后端 + 前端

# 打开 http://127.0.0.1:8080/index3.html
```

> **极简模式下能做什么**：上传/解析 Word·Excel·PDF、知识库建索引与检索、工作流编排与运行、
> 规则式问答、把请求转发到你自己的云端模型（在「设置 → 模型与 API」里填 Key）。
> **不能做**：本地底座的对话、LoRA 训练。

---

## 六、DSH 接入详解（推荐重点）

### 6.1 为什么用 DSH 当主界面

- **一个窗口**：左边聊天、右边平台界面（iframe 嵌入），不用来回切浏览器标签。
- **工具即用**：平台的文档工具、知识库、LoRA、工作流全部注册成 DSH 工具，模型在对话里能直接调。
- **零改动**：插件只走 HTTP（`127.0.0.1:8001`），不改平台任何源码；平台升级后插件下次启动自动适配。
- **可逆**：卸载插件不影响平台本身。

### 6.2 安装插件的正确姿势（重要）

> **必须打包成 `.tgz` 再用 `file:` 安装。**
> 不要用 `dsh plugin add <目录>` —— pnpm 会写成 `link:`，
> 那是软链到原地，插件代码不会进 profile 的包树，后续容易出问题。

```bash
# ✅ 正确
cd dsh-plugin-paper-lora
tar -czf dsh-plugin-paper-lora-0.1.1.tgz package.json src lib README.md cordis.patch.yml
dsh plugin --profile web add ./dsh-plugin-paper-lora-0.1.1.tgz

# ❌ 错误
dsh plugin add E:\WSL\dsh-plugin-paper-lora
```

### 6.3 插件提供什么

| 类型 | 名称 / 数量 | 说明 |
|---|---|---|
| 元工具 | `paper_lora_status` | 看平台后端与推理底座是否活着；没起时给排查指引 |
| 元工具 | `paper_lora_tool_list` | 列出平台全部工具与参数提示 |
| 元工具 | `paper_lora_call` | 通用兜底：按后端原始工具名调用任意平台工具 |
| 动态工具 | **33 个** | 从后端 `/docs/tool/list` 自动生成（Word/Excel/PDF 读写、批量处理等），后端加工具插件自动就有 |
| 界面 | 侧栏标签 + 顶栏按钮 | 右侧边栏「模型垂直开发平台」标签，内嵌平台界面；顶栏按钮一键打开 |

### 6.4 怎么用

1. 确认平台后端在跑（`curl http://127.0.0.1:8001/health` 返回 200）。
2. 打开 DSH → 点右侧边栏的 **模型垂直开发平台** 标签（或对话顶栏按钮）。
3. 标签内就是完整平台界面，可直接操作知识库 / 训练 / 工作流。
4. 在对话里说需求，例如：**「把 uploads 里最新的 Word 汇总成 Excel」** —— 模型会自动选平台工具执行。

### 6.5 后端没起时

插件不会崩：`paper_lora_status` 会返回明确的排查指引，侧栏显示「平台后端不可达（:8001）」并给出启动命令。按提示启动平台后刷新即可。

### 6.6 升级 / 卸载插件

```bash
# 升级：改代码后重新打包 + 覆盖安装
tar -czf dsh-plugin-paper-lora-0.1.2.tgz package.json src lib README.md cordis.patch.yml
dsh plugin --profile web add ./dsh-plugin-paper-lora-0.1.2.tgz
# 然后重启 DSH 宿主（插件在新进程里重新导入）

# 卸载（随时可逆）
dsh plugin --profile web remove dsh-plugin-paper-lora
```

> **改了插件代码但界面没变？** 客户端插件在 DSH 宿主启动时导入并打包，**必须重启宿主**
> （关掉 `dsh web` 重新执行），刷新页面不够。

---

## 七、界面导览

| 页面 | 做什么 | 关键操作 |
|---|---|---|
| 🎛️ 底座管理 | 下载/切换底座模型、启动停止 vLLM、看显存与加载进度 | 选模型 → 启动；加载 1~3 分钟 |
| 📚 知识库 | 新建知识库、上传文档（zip/文件夹）、重建索引、挂载 | 上传 → **重建索引（确认 chunks > 0）** → 挂载 |
| 🎯 LoRA 训练 | 上传训练数据（jsonl）、发起 QLoRA 训练、查看 loss 曲线、挂载适配器 | 数据必须是 `conversations` 格式（`messages` 会自动转） |
| 🔀 分析工作流 | 可视化 DAG 编排、运行、看每节点输出 | 出厂带 4 节点示例，空装可跑 |
| 💬 对话 / 角色对话 | 与模型对话；可挂知识库、挂 LoRA、开 Agent 模式（68 工具） | 对话页右上可切模型与适配器 |
| 🧠 学习增强 | 本地答错/答不了时自动升级云端（含敏感词闸门） | 在设置里填云端 Key，并开启 learn |
| 🌐 浏览自动化 | 让 Agent 操作浏览器（13 个工具） | 需先起 sidecar（9224） |
| ⚙️ 系统设置 | 端口、模型路径、GPU 模式、Provider、Key | 改完需要重启后端生效 |

---

## 八、配置说明

### 8.1 settings.json 关键字段

| 字段 | 示例 | 说明 |
|---|---|---|
| `base_model_path` | `/home/<你>/models/Qwen2.5-7B-Instruct-AWQ` | 主底座目录（绝对路径） |
| `lora_dir` | `/home/<你>/loras` | LoRA 适配器存放目录 |
| `venv_python` | `/home/<你>/.venv/bin/python` | 训练/工具用的 Python |
| `vllm_chat_api` | `http://localhost:8000/v1/chat/completions` | vLLM 兼容接口 |
| `lora_manager_api` | `http://localhost:8001` | 后端地址（前端与插件用） |
| `providers.*.api_base` | `https://api.deepseek.com/v1` | 云端 Provider 地址（vllm / ollama / openai） |
| `providers.*.api_key` | （留空，改用环境变量） | **建议不要明文写在这里**，用环境变量注入 |
| `embedding_device` | `cpu` | 嵌入模型设备；`cpu` 省显存，`cuda` 更快 |
| `gpu_mode` | `auto` / `dual` | `auto`=单卡（训练时停 vLLM）；`dual`=双卡分离（训练推理并行） |
| `train_gpu` / `inference_gpu` | `0` / `1` | 仅 `dual` 模式必填 |
| `kb_mounted` | `["kbname", …]` | 当前挂载的知识库列表（对话时自动注入） |

### 8.2 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | 云端 DeepSeek Key（优先生效于 settings 里的明文） |
| `OPENAI_API_KEY` | — | OpenAI 兼容 Provider 的 Key |
| `OLLAMA_API_KEY` | — | Ollama Provider 的 Key |
| `VLLM_API_KEY` | — | vLLM 侧 Key（如启用了） |
| `BACKEND_PORT` | `8001` | 后端端口 |
| `FRONTEND_PORT` | `8080` | 前端端口 |
| `VLLM_PORT` | `8000` | 推理底座端口 |
| `MODELS_DIR` | `./models` | 模型根目录 |
| `PYTHON` | 自动探测 | 显式指定解释器 |
| `FILE_ACCESS_ALLOW` | — | 文件工具额外允许的路径（空格分隔） |
| `EMBEDDING_DEVICE` | `cpu` | 嵌入设备 |

### 8.3 GPU 单卡 / 双卡

| 模式 | 配置 | 行为 |
|---|---|---|
| 单卡（默认） | `gpu_mode=auto` | 训练时自动停 vLLM，训完自动重启 ⇒ 训练期间推理不可用（约 160~200 秒/轮） |
| 双卡分离 | `gpu_mode=dual`<br>`train_gpu=0`<br>`inference_gpu=1` | 训练占 GPU0、推理留 GPU1 ⇒ **训练期间对话不中断**（显存均 ≥12 GB 时） |

### 8.4 端口与多实例

| 端口 | 服务 | 说明 |
|---|---|---|
| `8001` | 平台后端（FastAPI） | 136 个 API；插件也调它 |
| `8080` | 前端静态服务 | `webui/serve.py`（带 no-store，勿用 http.server） |
| `8000` | vLLM 推理底座 | OpenAI 兼容 |
| `9224` | 浏览自动化 sidecar | 可选 |
| `3080` | DSH Web GUI | DSH 自带 |

```bash
# 多实例：复制整个项目目录即可完全隔离（PROJECT_DIR = 脚本所在目录）
cp -r luxy-platform-4.9.7 luxy-run2
BACKEND_PORT=8002 FRONTEND_PORT=8082 bash luxy-run2/scripts/start.sh
# 前端用 ?api= 指后端
# http://127.0.0.1:8082/index3.html?api=http://127.0.0.1:8002
```

---

## 九、日常运维

### 9.1 启动 / 停止

```bash
# Linux / WSL
bash scripts/start.sh            # 全量（含底座，异步加载）
bash scripts/start.sh --no-gpu   # 不启底座
bash install/stop.sh             # 停止（可指定端口）
bash scripts/stop.sh             # 停止（简版）

# Windows
双击 启动工作台.bat               # 自动开浏览器 + 拉起 keeper
双击 停止工作台.bat               # 全部停止

# 只重启后端
pkill -f lora_manager.py && bash install/start.sh
```

### 9.2 keeper（看护进程）

`scripts/keeper.sh` 有两个作用：① 占住 WSL 会话（否则 WSL2 会回收发行版，服务一起死）；
② 每 20 秒检查后端/前端/底座，缺了自动拉起。单实例保护（pid 文件 + `/proc` 校验），重复启动不会起多份。

```bash
pgrep -af "scripts/keeper.sh"      # 确认只有 1 个
tail -f /tmp/workbench_keeper.log  # 看它干了什么
```

### 9.3 日志位置

| 路径 | 内容 |
|---|---|
| `logs/backend.log` | 后端主日志（含 vLLM 拉起过程） |
| `logs/frontend.log` | 前端静态服务日志 |
| `logs/vllm_start.log` | vLLM 启动日志 |
| `/tmp/workbench_keeper.log` | keeper 健康检查与自动拉起记录 |

### 9.4 备份与升级

> **要备份的（数据）**：`data/`（知识库正文）、`chroma_db/`（向量索引）、`loras/`（适配器）、
> `training_data/`（训练集）、`settings.json`。
> **不用备份的（代码）**：其余目录；升级时只替换代码，保留上述数据目录即可。

```bash
# 备份
tar -czf backup-$(date +%F).tar.gz data chroma_db loras training_data settings.json

# 升级（保留数据）
# 1) 停服务  2) 解包新版本到新目录  3) 把数据目录软链或复制过去  4) 启动
```

---

## 十、故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 后端起不来，报 ImportError | 缺 `python-multipart` 等隐式依赖 | `pip install -r requirements.txt` 完整装一遍 |
| 页面打开但功能 undefined | `webui/pages/*.js` 或 `rolechat.js`/`reflect.js` 缺失 | 确认发布包完整；重解包 |
| vLLM 起不来 | 显存不足 / 模型路径错 / CUDA 不匹配 | 调小 `--gpu-memory-utilization`、换小模型、查 `logs/vllm_start.log` |
| 知识库查不到 | 没重建索引 / 没挂载 | 重建索引确认 chunks > 0；挂载后重试 |
| readiness 4/5 | 缺云端 API Key | 正常（可选功能），需要就填 Key |
| 训练期间对话失败 | 单卡模式训练时会停 vLLM | 接受中断，或配 `gpu_mode=dual` 双卡分离 |
| 训练报格式错误 | 数据不是 `conversations` 格式 | 用平台导出/转换；`messages` 上传时会自动转 |
| 浏览自动化灯红 | sidecar 没起 | `curl http://127.0.0.1:9224/health`；`cd browser_sidecar && node server.mjs` |
| DSH 侧栏没出现标签 | 插件没装进当前 profile / 宿主没重启 | `dsh plugin --profile web list` 确认；**重启 DSH 宿主** |
| DSH 工具调用报 8001 不可达 | 平台后端没起 | 先启平台；调 `paper_lora_status` 看指引 |
| 改了插件文案界面没变 | 客户端插件在宿主启动时打包 | 重启 DSH 宿主（刷新页面不够） |
| WSL 过一会儿服务全灭 | WSL2 回收最后一个会话 | 保持 keeper 在跑；或 Windows 保持一个 WSL 终端 |
| 启动台报找不到发行版 | 有多个 WSL 发行版，回退到了老的那个 | 用仓库 `install/windows/` 里的新版 .bat（显式选 Ubuntu-24.04） |
| 中文乱码 | 终端编码 / 脚本编码不匹配 | Windows 启动台用 UTF-8 BOM 版本；Linux 终端 `LANG=zh_CN.UTF-8` |
| 端口被占用 | 旧进程没退 | `ss -lntp \| grep 8001` 找到 pid 后 `kill`；或用 `BACKEND_PORT` 换端口 |

---

## 附录 A · 路径速查

| 路径 | 说明 |
|---|---|
| `lora_manager.py` | 后端主程序（FastAPI，136 端点） |
| `webui/index3.html` | 前端主页面 |
| `webui/serve.py` | 前端静态服务（带 no-store） |
| `scripts/start.sh` · `stop.sh` · `keeper.sh` | 启动 / 停止 / 看护 |
| `install/start.sh` · `stop.sh` · `smoke_test.sh` | 轻量启动 / 停止 / 7 项冒烟 |
| `install/windows/*.bat` | Windows 启动台（随版本走） |
| `docs/` | 公开文档（安装/架构/数据规范/工具开发…） |
| `settings.json` | 配置（不要提交带 Key 的版本） |
| `data/` · `chroma_db/` · `loras/` · `training_data/` | 用户数据（需备份） |

---

## 附录 B · 命令速查

```bash
# 安装
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install "vllm==0.11.0"          # 可选 GPU

# 启动
bash scripts/start.sh
bash scripts/start.sh --no-gpu
bash install/start.sh

# 停止
bash install/stop.sh
pkill -f lora_manager.py

# 自检
bash install/smoke_test.sh
bash run_tests.sh                   # 828 用例
curl 127.0.0.1:8001/version
curl 127.0.0.1:8001/health

# DSH 插件
tar -czf dsh-plugin-paper-lora-0.1.1.tgz package.json src lib README.md cordis.patch.yml
dsh plugin --profile web add ./dsh-plugin-paper-lora-0.1.1.tgz
dsh plugin --profile web remove dsh-plugin-paper-lora

# 诊断
ss -lntp | grep -E '8000|8001|8080'
pgrep -af "scripts/keeper.sh"
tail -f logs/backend.log
nvidia-smi
```

---

## 附录 C · 端口表

| 端口 | 服务 | 默认 | 可改否 |
|---|---|---|---|
| `8001` | 平台后端 | 开 | `BACKEND_PORT` |
| `8080` | 前端 | 开 | `FRONTEND_PORT` |
| `8000` | vLLM 底座 | 有 GPU 时开 | `VLLM_PORT` |
| `9224` | 浏览 sidecar | 可选 | sidecar 内配置 |
| `3080` | DSH Web GUI | DSH 自带 | DSH 配置 |

---

## 附录 D · 版本与许可

| 项目 | 内容 |
|---|---|
| 版本 | v4.9.7（首个开源版） |
| 许可 | MIT |
| 仓库 | https://github.com/lixy-max/luxy-platform- |
| 发布包 | `luxy-platform-4.9.7.tar.gz`（1.2 MB） |
| 依赖许可 | 见 `THIRD_PARTY_LICENSES.md`（vLLM/Playwright 等 Apache-2.0，BGE MIT） |
| 数据与版权 | 见 `DATA_AND_COPYRIGHT.md`；发布包**不含**任何知识库、LoRA、训练数据 |

---

> **说明**：本文档依据 v4.9.7 实际代码与实测环境编写（Python 3.10–3.12 / WSL2 Ubuntu 24.04 / vLLM 0.11.0 / RTX 5060 Ti + 3060）。
> 依赖版本以 `requirements.txt` 为准；如与仓库文档冲突，以 `docs/INSTALL.md` 与 `README.md` 为准。
