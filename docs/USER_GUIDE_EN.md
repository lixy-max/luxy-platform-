# User Guide · Luxy Model Vertical Development Platform v4.9.7

> **Recommended way to use it: DSH integration.** One window for both chatting and operating the platform,
> and the platform's capabilities are exposed to DSH as ordinary tools.
>
> Intended for: anyone who wants to turn their own data into a dedicated model on their own machine — and keep using it.

---

## Table of Contents

| | |
|---|---|
| [1. Choosing a Usage Mode](#1-choosing-a-usage-mode) | [7. UI Tour](#7-ui-tour) |
| [2. Environment Requirements](#2-environment-requirements) | [8. Configuration](#8-configuration) |
| [3. Dependency List](#3-dependency-list) | [9. Day-to-day Operations](#9-day-to-day-operations) |
| [4. Install · Recommended Path](#4-install--recommended-path-windows--wsl2--dsh) | [10. Troubleshooting](#10-troubleshooting) |
| [5. Install · Minimal Path](#5-install--minimal-path-no-gpu) | [Appendix A. Paths](#appendix-a-paths) |
| [6. DSH Integration in Detail](#6-dsh-integration-in-detail) | [Appendix B–D](#appendix-b-command-cheatsheet) |

**How to use this document:** to get running fast, read chapter 4 (recommended) or chapter 5 (no GPU);
if you want DSH as your main UI, read chapter 6; if something breaks, jump straight to chapter 10.

---

## 1. Choosing a Usage Mode

| Mode | Rating | Who it is for | How to use |
|---|---|---|---|
| **① DSH integration** (right sidebar) | ⭐ **Recommended** | People already using DeepSeek Harness who want to chat and operate the platform in **one window**, and to call platform capabilities as DSH tools | Install the DSH plugin → a **Model Vertical Development Platform** tab appears in the right sidebar → open it. The platform's tools are also registered as DSH tools you can invoke from chat |
| ② Web UI | Baseline | No DSH, just want a GUI to click | Open `http://127.0.0.1:8080/index3.html` after starting |
| ③ Raw API / CLI | Advanced | Integrating the platform into your own scripts or systems | Call the backend's 136 API paths (`http://127.0.0.1:8001`) |

**Why DSH integration is recommended:**

1. The platform UI is embedded in DSH's right sidebar — **no window switching**.
2. The platform's document tools, knowledge base, LoRA and workflows are exposed as DSH tools automatically
   (when the backend gains a tool, the plugin picks it up on its next start — the list is never hand-written).
3. DSH's own file / terminal / search tools can be mixed with platform tools.
4. The plugin only talks HTTP to the backend — **it never modifies platform source**, and it can be uninstalled at any time.

---

## 2. Environment Requirements

### 2.1 Hardware

| Item | Minimum (usable) | Recommended | Notes |
|---|---|---|---|
| CPU | 4 cores | 8+ cores | Document parsing, KB chunking and the frontend are all CPU-bound |
| RAM | 16 GB | 32 GB | vLLM loading a 7B model is memory-hungry |
| Disk | 20 GB (excluding models) | 60 GB+ | Models need another 5–10 GB; KB and training data keep growing |
| GPU VRAM | **0 (no GPU needed)** | 12 GB / 16 GB single card | No GPU: knowledge base, document tools, workflows and rule mode **all work**, only local model inference/training is unavailable<br>12 GB: Qwen2.5-7B-Instruct-AWQ inference<br>16 GB+: comfortable for both inference and training |

> **Verified hardware:** RTX 5060 Ti 16 GB (training) + RTX 3060 12 GB (inference); in dual-GPU split mode inference stays up while training runs.
> An 8 GB card can run smaller models (Qwen2.5-3B / Qwen3-4B-AWQ); 7B-AWQ recommends ≥12 GB.

### 2.2 Operating System

| Platform | Support | Notes |
|---|---|---|
| Windows 11 + WSL2 (Ubuntu 24.04) | ✅ Preferred | The long-term verified environment for this project; Windows launcher `.bat` provided |
| Ubuntu 22.04 / 24.04 (native Linux) | ✅ Supported | Use `scripts/start.sh` directly; no `.bat` needed |
| macOS | ⚠️ Not guaranteed | vLLM support on Apple Silicon is limited; document/KB features work without the backend model |
| Native Windows (no WSL) | ❌ Not recommended | Most scripts are bash; vLLM is hard to install natively on Windows |

### 2.3 Software Versions

| Software | Version | Required? | Notes |
|---|---|---|---|
| Python | **3.10 – 3.12** | Required | 3.11 / 3.12 verified; `pyproject.toml` requires `>=3.10` |
| pip / venv | pip ≥ 23 | Required | Use a dedicated venv (`PYTHON` env → `.venv` → `~/.venv` → `python3`, auto-detected) |
| bash | ≥ 5.0 | Required | Start/stop/train scripts are bash |
| NVIDIA driver + CUDA | CUDA 12.x | GPU only | vLLM 0.11.0 needs a matching CUDA; `nvidia-smi` must work |
| vLLM | 0.11.0 (verified) | GPU only | Local inference backend; must match your CUDA / torch |
| Node.js | ≥ 20 (browser automation)<br>≥ 22.19 (DSH) | Optional | Browser automation needs Node 20+; DSH and its plugins need Node ≥ 22.19 |
| pnpm | ≥ 9 | Only for DSH plugins | DSH manages plugins through pnpm |
| Git | any recent version | Optional | Only needed if you fetch the code with `git clone` |

### 2.4 Model Files

| Model | Size | Required? | Purpose |
|---|---|---|---|
| Qwen2.5-7B-Instruct-AWQ | ≈ 5.2 GB | Needed for local inference | Main base model (4-bit AWQ, runs on 12 GB VRAM) |
| bge-small-zh-v1.5 | ≈ 184 MB | Needed for KB | Chinese embedding model (CPU is fine, `embedding_device=cpu`) |
| bge-m3 | ≈ 2.2 GB | Optional | Stronger multilingual embeddings |
| Other Qwen variants | 0.5B – 15 GB | Optional | Smaller cards can use 3B/4B; 14B needs more VRAM |

**Where models go:** by default under the project's `models/`, or point `MODELS_DIR` elsewhere (e.g. `/home/<you>/models`).
The verified setup keeps a model library in the home directory and points `base_model_path` in `settings.json` at the exact model directory.

---

## 3. Dependency List

### 3.1 Required (`requirements.txt`, 16 packages)

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | 0.136.3 | Backend web framework (136 API paths) |
| `uvicorn` | 0.54.0 | ASGI server |
| `pydantic` | 2.12.3 | Request/response validation |
| `python-multipart` | 0.0.32 | **Implicit dependency**: required by upload routes; without it the backend will not start |
| `requests` | 2.34.2 | Inter-service and vLLM calls |
| `chromadb` | 1.5.9 | Local vector store (KB index) |
| `sentence-transformers` | 4.1.0 | Loads bge embedding models |
| `transformers` | 4.56.2 | Model / tokenizer toolchain |
| `safetensors` | 0.8.0 | Safe tensor I/O (LoRA adapters) |
| `python-docx` | 1.2.0 | Word read/write |
| `openpyxl` | 3.1.5 | Excel read/write |
| `pypdf` | 6.19.0 | PDF parsing |
| `pdfplumber` | 0.11.10 | PDF layout / table extraction |
| `pypdfium2` | 5.13.0 | PDF rendering (to images) |
| `rapidocr-onnxruntime` | 1.4.4 | OCR for scanned documents (Chinese) |
| `pillow` | 12.3.0 | Image handling |

### 3.2 Optional: GPU inference / training stack

```bash
# Local inference (base model)
pip install "vllm==0.11.0"

# LoRA training (QLoRA)
pip install "torch==2.8.0+cu128" --index-url https://download.pytorch.org/whl/cu128
pip install peft accelerate datasets bitsandbytes
```

> **Without the GPU stack:** the platform still starts. **Knowledge base, document tools, workflows, rule mode and cloud
> models all work**; only "local base model" inference and the LoRA training page are unavailable.

### 3.3 Optional: browser automation (Agent browser tools)

```bash
cd browser_sidecar
npm install                 # playwright-core 1.64.1 (requires Node >= 20)
npx playwright install chromium
node server.mjs             # listens on 127.0.0.1:9224 by default
# To reach it from inside WSL: HOST=0.0.0.0 node server.mjs
```

### 3.4 Required for DSH integration

| Component | Version | Notes |
|---|---|---|
| DeepSeek Harness (DSH) | Latest | Provides the web GUI and plugin mechanism; `dsh web` serves `127.0.0.1:3080` |
| Node.js | ≥ 22.19 | Required by DSH and the plugin's `engines` |
| pnpm | ≥ 9 | Required by `dsh plugin` |
| `@deepseek-ai/dsh-tools` | ≥ 0.1.1-rc.2 | Ships with DSH; peer dependency of the plugin |

---

## 4. Install · Recommended Path (Windows + WSL2 + DSH)

This is the long-term verified environment. Nine steps, roughly 30–60 minutes depending on network speed and whether you install the GPU stack.

### A1. Install WSL2 + Ubuntu

```powershell
# Windows PowerShell (Administrator)
wsl --install -d Ubuntu-24.04
# Reboot, then set the Linux username/password on first launch
```

> **Note:** if the machine already has several WSL distros (e.g. an old `Ubuntu` and a new `Ubuntu-24.04`),
> the launcher prefers **Ubuntu-24.04**. Make sure the project lives in the distro you intend to use.

### A2. Get the code

```bash
# Option 1: release tarball (recommended, 1.2 MB)
tar -xzf luxy-platform-4.9.7.tar.gz
cd luxy-platform-4.9.7

# Option 2: git clone
git clone https://github.com/lixy-max/luxy-platform-.git
cd luxy-platform-
```

### A3. Create a virtualenv and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> **venv location:** scripts auto-detect Python in this order: `PYTHON` env → project `.venv` → `~/.venv` → `python3`.
> Either location works.

### A4. (Optional) Install the GPU inference / training stack

```bash
pip install "vllm==0.11.0"
pip install "torch==2.8.0+cu128" --index-url https://download.pytorch.org/whl/cu128
pip install peft accelerate datasets bitsandbytes
```

### A5. Put models in place

```bash
# Option 1: into the project's models/
mkdir -p models
# Place Qwen2.5-7B-Instruct-AWQ and bge-small-zh-v1.5 there

# Option 2: point MODELS_DIR at your home model library
export MODELS_DIR=$HOME/models

# Verify
ls models/     # or ls $MODELS_DIR
```

### A6. Start the platform

```bash
bash scripts/start.sh              # full start (backend 8001 + frontend 8080 + async vLLM 8000)
bash scripts/start.sh --no-gpu     # skip the base model (no GPU, or KB/document tools only)
bash scripts/start.sh --no-browser # do not try to open a browser
```

On Windows you can also double-click **启动工作台.bat** (it opens the browser automatically; pass `nobrowser` to skip).

### A7. Smoke test

```bash
bash install/smoke_test.sh         # 7 checks, expect 7/7
curl http://127.0.0.1:8001/version
curl http://127.0.0.1:8001/health
```

### A8. Install DSH and the platform plugin

```bash
# 1) Check Node / pnpm
node -v        # >= 22.19
pnpm -v        # >= 9

# 2) Start DSH (initializes the profile on first run)
dsh web        # then open http://127.0.0.1:3080

# 3) Pack the plugin (★ must be a .tgz — see 6.2)
cd /path/to/dsh-plugin-paper-lora
tar -czf dsh-plugin-paper-lora-0.1.1.tgz package.json src lib README.md cordis.patch.yml

# 4) Install into the current profile (usually "web")
dsh plugin --profile web add ./dsh-plugin-paper-lora-0.1.1.tgz

# 5) Restart the DSH host (the plugin is re-imported on start)
#    Windows: close the dsh window and run `dsh web` again
```

### A9. Verify DSH integration

1. Refresh `http://127.0.0.1:3080`.
2. A **Model Vertical Development Platform** tab appears in the right sidebar (the plugin is active).
3. Ask the model to call `paper_lora_status`; it should return backend and base-model status.

---

## 5. Install · Minimal Path (No GPU)

If you only need document tools, the knowledge base and workflows, you can skip the entire GPU stack:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 16 CPU-only dependencies

bash scripts/start.sh --no-gpu           # do not start the base model
# or
bash install/start.sh                    # even lighter: backend + frontend only

# Open http://127.0.0.1:8080/index3.html
```

> **What works in minimal mode:** upload/parse Word, Excel and PDF; build and query KB indexes; compose and run
> workflows; rule-based Q&A; forward requests to your own cloud model (fill in the key under Settings → Models & API).
> **What does not work:** local base-model chat and LoRA training.

---

## 6. DSH Integration in Detail

### 6.1 Why use DSH as the main UI

- **One window:** chat on the left, the platform UI (embedded iframe) on the right — no browser-tab juggling.
- **Tools ready to use:** document tools, KB, LoRA and workflows are all registered as DSH tools the model can call.
- **Zero intrusion:** the plugin only talks HTTP (`127.0.0.1:8001`) and never modifies platform source; platform upgrades are picked up on the next start.
- **Reversible:** uninstalling the plugin does not affect the platform itself.

### 6.2 The right way to install the plugin (important)

> **Pack it into a `.tgz` and install with `file:`.**
> Do **not** use `dsh plugin add <directory>` — pnpm records it as `link:`, a symlink to the original location;
> the plugin code never lands in the profile's package tree and is easy to break later.

```bash
# ✅ Correct
cd dsh-plugin-paper-lora
tar -czf dsh-plugin-paper-lora-0.1.1.tgz package.json src lib README.md cordis.patch.yml
dsh plugin --profile web add ./dsh-plugin-paper-lora-0.1.1.tgz

# ❌ Wrong
dsh plugin add E:\WSL\dsh-plugin-paper-lora
```

### 6.3 What the plugin provides

| Type | Name / count | Description |
|---|---|---|
| Meta tool | `paper_lora_status` | Checks whether the backend and inference base model are alive; gives troubleshooting guidance when they are not |
| Meta tool | `paper_lora_tool_list` | Lists all platform tools and their parameter hints |
| Meta tool | `paper_lora_call` | Generic fallback: call any platform tool by its backend name |
| Dynamic tools | Generated from the backend | Auto-generated from `/docs/tool/list`, so new backend tools appear automatically (nothing hand-written) |
| UI | Sidebar tab + header button | A **Model Vertical Development Platform** tab in the right sidebar embedding the platform UI; a header button opens it |

### 6.4 How to use it

1. Make sure the backend is running (`curl http://127.0.0.1:8001/health` returns 200).
2. Open DSH → click the **Model Vertical Development Platform** tab on the right (or the header button).
3. The tab contains the full platform UI; operate KB / training / workflows directly.
4. Describe the task in chat, e.g. **"summarise the newest Word file in uploads into an Excel sheet"** — the model picks the right platform tool and runs it.

### 6.5 When the backend is down

The plugin does not crash: `paper_lora_status` returns clear troubleshooting guidance, and the sidebar shows
"platform backend unreachable (:8001)" together with the start command. Start the platform and refresh.

### 6.6 Upgrading / uninstalling

```bash
# Upgrade: repack after edits, then install over the old version
tar -czf dsh-plugin-paper-lora-0.1.2.tgz package.json src lib README.md cordis.patch.yml
dsh plugin --profile web add ./dsh-plugin-paper-lora-0.1.2.tgz
# Then restart the DSH host (the plugin is re-imported in a fresh process)

# Uninstall (reversible at any time)
dsh plugin --profile web remove dsh-plugin-paper-lora
```

> **Changed the plugin but the UI did not change?** Client plugins are imported and bundled when the DSH host starts,
> so you **must restart the host** (close `dsh web` and run it again). Refreshing the page is not enough.

---

## 7. UI Tour

| Page | What it does | Key actions |
|---|---|---|
| 🎛️ Base Model | List/switch base models, start/stop vLLM, watch VRAM and load progress | Pick a model → start; loading takes 1–3 minutes |
| 📚 Knowledge Base | Create KBs, upload documents (zip / folder), rebuild the index, mount | Upload → **rebuild index (confirm chunks > 0)** → mount |
| 🎯 LoRA Training | Upload training data (jsonl), launch QLoRA training, inspect the loss curve, mount adapters | Data must be in `conversations` format (`messages` is converted automatically) |
| 🔀 Analysis Workflow | Visual DAG composition, run, inspect per-node output | Ships with a 4-node example that runs on a clean install |
| 💬 Chat / Role Chat | Talk to the model; mount KB and LoRA; enable Agent mode (many tools) | Switch model/adapter at the top right of the chat page |
| 🧠 Learning Boost | Escalate to the cloud when the local model answers incorrectly | Configure a cloud key and enable `learn` in settings |
| 🌐 Browser Automation | Let the Agent drive a browser | Requires the sidecar (9224) |
| ⚙️ Settings | Ports, model paths, GPU mode, providers, keys | Restart the backend after changes |

---

## 8. Configuration

### 8.1 Key fields in `settings.json`

| Field | Example | Meaning |
|---|---|---|
| `base_model_path` | `/home/<you>/models/Qwen2.5-7B-Instruct-AWQ` | Base model directory (absolute path) |
| `lora_dir` | `/home/<you>/loras` | Where LoRA adapters live |
| `venv_python` | `/home/<you>/.venv/bin/python` | Python used for training/tools |
| `vllm_chat_api` | `http://localhost:8000/v1/chat/completions` | vLLM-compatible endpoint |
| `lora_manager_api` | `http://localhost:8001` | Backend address (used by frontend and plugin) |
| `providers.*.api_base` | `https://api.deepseek.com/v1` | Cloud provider endpoint (vllm / ollama / openai) |
| `providers.*.api_key` | (leave empty, use env vars) | **Do not store plaintext keys here**; inject via environment variables |
| `embedding_device` | `cpu` | Embedding device; `cpu` saves VRAM, `cuda` is faster |
| `gpu_mode` | `auto` / `dual` | `auto` = single GPU (vLLM stopped during training); `dual` = split across two GPUs |
| `train_gpu` / `inference_gpu` | `0` / `1` | Required only in `dual` mode |
| `kb_mounted` | `["kbname", …]` | Currently mounted KBs (injected into chat automatically) |

### 8.2 Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | — | Cloud DeepSeek key (takes priority over plaintext in settings) |
| `OPENAI_API_KEY` | — | Key for OpenAI-compatible providers |
| `OLLAMA_API_KEY` | — | Ollama provider key |
| `VLLM_API_KEY` | — | vLLM-side key (if enabled) |
| `BACKEND_PORT` | `8001` | Backend port |
| `FRONTEND_PORT` | `8080` | Frontend port |
| `VLLM_PORT` | `8000` | Inference backend port |
| `MODELS_DIR` | `./models` | Model root directory |
| `PYTHON` | auto-detect | Explicit interpreter |
| `FILE_ACCESS_ALLOW` | — | Extra paths allowed for file tools (space-separated) |
| `EMBEDDING_DEVICE` | `cpu` | Embedding device |

### 8.3 Single GPU vs. dual GPU

| Mode | Configuration | Behaviour |
|---|---|---|
| Single GPU (default) | `gpu_mode=auto` | vLLM is stopped during training and restarted afterwards ⇒ inference is unavailable while training (about 160–200 s per run) |
| Dual GPU split | `gpu_mode=dual`<br>`train_gpu=0`<br>`inference_gpu=1` | Training on GPU0, inference stays on GPU1 ⇒ **chat keeps working during training** (each GPU ≥12 GB) |

### 8.4 Ports and multiple instances

| Port | Service | Notes |
|---|---|---|
| `8001` | Platform backend (FastAPI) | 136 API paths; the plugin calls it too |
| `8080` | Frontend static server | `webui/serve.py` (sends no-store; do not use `python -m http.server`) |
| `8000` | vLLM inference backend | OpenAI-compatible |
| `9224` | Browser automation sidecar | Optional |
| `3080` | DSH web GUI | Provided by DSH |

```bash
# Multiple instances: copy the whole project directory for full isolation
# (PROJECT_DIR = the directory containing the scripts)
cp -r luxy-platform-4.9.7 luxy-run2
BACKEND_PORT=8002 FRONTEND_PORT=8082 bash luxy-run2/scripts/start.sh
# Point the frontend at the other backend with ?api=
# http://127.0.0.1:8082/index3.html?api=http://127.0.0.1:8002
```

---

## 9. Day-to-day Operations

### 9.1 Start / stop

```bash
# Linux / WSL
bash scripts/start.sh            # full (includes base model, loaded asynchronously)
bash scripts/start.sh --no-gpu   # no base model
bash install/stop.sh             # stop (ports can be specified)
bash scripts/stop.sh             # stop (simple version)

# Windows
Double-click 启动工作台.bat       # opens the browser + starts keeper
Double-click 停止工作台.bat       # stop everything

# Restart the backend only
pkill -f lora_manager.py && bash install/start.sh
```

### 9.2 keeper (supervisor)

`scripts/keeper.sh` does two things: (1) holds the WSL session open (otherwise WSL2 tears down the distro
and takes the services with it); (2) checks backend/frontend/base model every 20 seconds and restarts what is missing.
It is single-instance (pid file + `/proc` check), so repeated launches do not create duplicates.

```bash
pgrep -af "scripts/keeper.sh"      # confirm exactly one is running
tail -f /tmp/workbench_keeper.log  # inspect what it is doing
```

### 9.3 Logs

| Path | Contents |
|---|---|
| `logs/backend.log` | Main backend log (including vLLM startup) |
| `logs/frontend.log` | Frontend static server log |
| `logs/vllm_start.log` | vLLM startup log |
| `/tmp/workbench_keeper.log` | keeper health checks and automatic restarts |

### 9.4 Backup and upgrade

> **Back up (data):** `data/` (KB bodies), `chroma_db/` (vector index), `loras/` (adapters),
> `training_data/` (datasets), `settings.json`.
> **No need to back up (code):** everything else; on upgrade just replace the code and keep the data directories.

```bash
# Backup
tar -czf backup-$(date +%F).tar.gz data chroma_db loras training_data settings.json

# Upgrade (keeping data)
# 1) stop services  2) unpack the new version into a new directory
# 3) symlink/copy the data directories over  4) start
```

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Backend fails to start with ImportError | Missing implicit deps such as `python-multipart` | Reinstall with `pip install -r requirements.txt` |
| Page loads but features are `undefined` | `webui/pages/*.js` or `rolechat.js` / `reflect.js` missing | Verify the release package; re-extract |
| vLLM will not start | Insufficient VRAM / wrong model path / CUDA mismatch | Lower `--gpu-memory-utilization`, use a smaller model, check `logs/vllm_start.log` |
| KB queries return nothing | Index not rebuilt / KB not mounted | Rebuild the index and confirm chunks > 0, then mount |
| readiness 4/5 | Cloud API key missing | Expected (optional feature); add a key if you need it |
| Chat fails during training | Single-GPU mode stops vLLM while training | Accept the interruption, or configure `gpu_mode=dual` |
| Training reports a format error | Data is not in `conversations` format | Export/convert via the platform; `messages` is converted on upload |
| Browser automation light is red | sidecar not running | `curl http://127.0.0.1:9224/health`; `cd browser_sidecar && node server.mjs` |
| No tab appears in DSH | Plugin not installed into the current profile / host not restarted | Check `dsh plugin --profile web list`; **restart the DSH host** |
| DSH tool call reports 8001 unreachable | Platform backend is not running | Start the platform; call `paper_lora_status` for guidance |
| Plugin text changed but UI did not | Client plugins are bundled when the host starts | Restart the DSH host (refreshing is not enough) |
| All services die after a while | WSL2 reclaimed the last session | Keep keeper running, or keep a WSL terminal open |
| Launcher reports no such distro | Multiple WSL distros; it fell back to the old one | Use the updated `.bat` in `install/windows/` (it selects Ubuntu-24.04 explicitly) |
| Chinese text is garbled | Terminal / script encoding mismatch | Use the UTF-8 launcher on Windows; set `LANG=zh_CN.UTF-8` on Linux |
| Port already in use | A stale process is still bound | `ss -lntp \| grep 8001`, kill the pid, or change `BACKEND_PORT` |

---

## Appendix A. Paths

| Path | Description |
|---|---|
| `lora_manager.py` | Backend entry point (FastAPI, 136 paths) |
| `webui/index3.html` | Main frontend page |
| `webui/serve.py` | Frontend static server (no-store) |
| `scripts/start.sh` · `stop.sh` · `keeper.sh` | Start / stop / supervise |
| `install/start.sh` · `stop.sh` · `smoke_test.sh` | Lightweight start / stop / 7-point smoke test |
| `install/windows/*.bat` | Windows launcher (versioned with the repo) |
| `docs/` | Public documentation (install, architecture, data spec, tool dev, …) |
| `settings.json` | Configuration (never commit a copy containing keys) |
| `data/` · `chroma_db/` · `loras/` · `training_data/` | User data (must be backed up) |

---

## Appendix B. Command Cheatsheet

```bash
# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install "vllm==0.11.0"          # optional GPU

# Start
bash scripts/start.sh
bash scripts/start.sh --no-gpu
bash install/start.sh

# Stop
bash install/stop.sh
pkill -f lora_manager.py

# Self-check
bash install/smoke_test.sh
bash run_tests.sh                   # 828 unit tests
curl 127.0.0.1:8001/version
curl 127.0.0.1:8001/health

# DSH plugin
tar -czf dsh-plugin-paper-lora-0.1.1.tgz package.json src lib README.md cordis.patch.yml
dsh plugin --profile web add ./dsh-plugin-paper-lora-0.1.1.tgz
dsh plugin --profile web remove dsh-plugin-paper-lora

# Diagnostics
ss -lntp | grep -E '8000|8001|8080'
pgrep -af "scripts/keeper.sh"
tail -f logs/backend.log
nvidia-smi
```

---

## Appendix C. Ports

| Port | Service | Default | Configurable |
|---|---|---|---|
| `8001` | Platform backend | on | `BACKEND_PORT` |
| `8080` | Frontend | on | `FRONTEND_PORT` |
| `8000` | vLLM base model | on when a GPU is used | `VLLM_PORT` |
| `9224` | Browser sidecar | optional | sidecar config |
| `3080` | DSH web GUI | provided by DSH | DSH config |

---

## Appendix D. Version and License

| Item | Value |
|---|---|
| Version | v4.9.7 (first open-source release) |
| License | MIT |
| Repository | https://github.com/lixy-max/luxy-platform- |
| Release package | `luxy-platform-4.9.7.tar.gz` (1.2 MB) |
| Dependency licenses | See `THIRD_PARTY_LICENSES.md` (vLLM / Playwright Apache-2.0, BGE MIT) |
| Data & copyright | See `DATA_AND_COPYRIGHT.md`; the package contains **no** KB, LoRA or training data |

---

> **Notes:** this document was written against the real v4.9.7 code and the verified environment
> (Python 3.10–3.12 / WSL2 Ubuntu 24.04 / vLLM 0.11.0 / RTX 5060 Ti + 3060).
> Dependency versions follow `requirements.txt`; where this document and the repository disagree,
> `docs/INSTALL.md` and `README.md` win. Only product capabilities are described — local resource counts
> (number of models, LoRA adapters or knowledge bases) depend on what you install yourself and are not product metrics.

---

*Chinese version: [USER_GUIDE.md](USER_GUIDE.md)*
