# SGLang 兼容评测 + Qwen3.8-27B 单卡实测报告

**一句话结论：SGLang 0.5.22 对 Qwen2.5 小模型兼容（LoRA/JSON Schema 均可用），但 Qwen3.8-27B 在单卡 16GB 上 SGLang 走不通；改用 llama.cpp + GGUF Q3_K_M 成功跑起：启动 4~6s、显存 14.35GB、解码 22~28 tok/s、路由 JSON 正确。** 日期：2026-10-27

## 0. 结论速览

| 项目 | 结论 |
|---|---|
| SGLang 0.5.22 安装 | ✅ 隔离 venv 安装成功（CUDA13/torch 2.14.1，11GB） |
| SGLang vs vLLM（1.5B 基准） | 启动 18s vs 22s；JSON Schema 3/3 vs 3/3；LoRA 均正常；短生成吞吐 vLLM 更高（62.9–72.5 vs 11–35.5 tok/s，单次冷启动波动） |
| SGLang 在 5060Ti (sm_120) | ⚠️ DeepGEMM JIT 初始化崩溃（需禁用）；AWQ-INT4 27B 另有两个阻塞：marlin `n=96` tile 不支持、CPU offload 路径 device mismatch |
| Qwen3.8-27B 单卡 16GB | ✅ **llama.cpp GGUF Q3_K_M**：4~6s 启动、14.35/16.3GB 显存、**22~28 tok/s**、路由 JSON 完全正确 |
| 建议 | 平台推理继续 **vLLM 主用**；27B 实验/预研走 **llama.cpp GGUF** 通道；SGLang 作为评测备选，暂不迁移 |

## 1. SGLang 兼容评测

### 1.1 环境与方法

| 项 | 值 |
|---|---|
| 安装方式 | 隔离 venv `/home/luxy/venvs/sglang-eval`（不污染主环境），pip 清华镜像 |
| 版本 | SGLang **0.5.22** / torch **2.14.1+cu130** / transformers 5.19 |
| 测试模型 | Qwen2.5-1.5B-Instruct（同条件对比 vLLM 0.11） |
| 测试项 | 启动耗时、基础对话、JSON Schema 输出（3 次）、LoRA 挂载与请求、吞吐（单次 55–66 tokens） |
| 硬件 | GPU1 RTX 3060 12GB（sm_86） |

### 1.2 结果

| 指标 | SGLang 0.5.22 | vLLM 0.11（基线） |
|---|---|---|
| 启动耗时（base / LoRA） | **18s / 16s** | 22s / 20s |
| JSON Schema 合法率 | **3/3 = 100%** | **3/3 = 100%** |
| LoRA 请求（model=workflow_author） | ✅ 200，正常生成 | ✅ 200，正常生成 |
| 短生成吞吐（单次） | 11.0 / 35.5 tok/s（冷/热波动大） | **72.5 / 62.9 tok/s** |
| OpenAI 兼容 | ✅ /v1/chat/completions | ✅ |

### 1.3 发现的问题（对蜂巢选型关键）

| # | 问题（都有实测日志） | 影响 |
|---|---|---|
| 1 | 5060Ti(sm_120) 上 **DeepGEMM JIT 初始化崩溃**（`deep_gemm::init_jit` → SIGQUIT） | SGLang 在 GPU0 默认起不来；需 `SGLANG_ENABLE_JIT_DEEPGEMM=0` 等规避 |
| 2 | AWQ-INT4 27B（compressed-tensors）加载失败：`gptq_marlin_repack: size_n = 96 is not divisible by tile_n_size = 64` | Qwen3.8 的 96 维投影与 SGLang marlin kernel 不兼容（单卡/双卡均会撞） |
| 3 | `--cpu-offload-gb 6` 加载时报 `Expected all tensors on same device: cuda:0 and cpu`（GemmaNorm 权重加载器） | offload 路径不可用，无法用「少显存+CPU 卸载」兜底 |
| 4 | SGLang GGUF 加载器：`Unknown gguf model_type: qwen3_5_text` | SGLang 暂不支持 Qwen3.8 GGUF |

### 1.4 SGLang 评测结论

- 对 **Qwen2.5 系列（1.5B/7B/14B）**：SGLang 功能可用（LoRA 热挂、JSON Schema 均验证通过），启动更快；
- 对 **Qwen3.8-27B**：SGLang 0.5.22 **当前版本走不通**（3 个独立阻塞，均为版本级兼容问题，非配置问题）；
- 蜂巢推理后端决策：**一期继续 vLLM**（已验证、稳定、Qwen3.8 AWQ 虽也有 marlin 限制但可绕）；SGLang 留作评测/二期备选；27B 单卡实验走 **llama.cpp**（见下节）。

## 2. Qwen3.8-27B 单卡实测

### 2.1 模型事实（官方资料核实）

| 项 | 值 |
|---|---|
| 官方仓库 | Qwen/Qwen3.8-27B（ModelScope/HF，[README](https://hf-mirror.com/Qwen/Qwen3.8-27B/raw/main/README.md)） |
| 参数/结构 | 27B 稠密 + 视觉编码器，64 层，原生 262K 上下文（YaRN 可到 1M） |
| License | **Apache-2.0** ✅ 可商用 |
| 权重形态 | BF16 约 **55.6GB**；官方 FP8 约 27GB；社区 AWQ-INT4 约 17.7~21GB；GGUF Q3_K_M **13.5GB** / Q4_K_M 16.8GB |
| 框架支持 | 官方推荐 SGLang / vLLM / TokenSpeed 最新版；实测 llama.cpp 也可（GGUF） |

### 2.2 单卡 16GB 尝试矩阵

| 方案 | 结果 | 数值 |
|---|---|---|
| AWQ-INT4 文本版（17.65GB，SGLang 无 offload） | ❌ marlin tile 限制 | 加载即失败（n=96 非 64 倍数） |
| AWQ-INT4 + `--cpu-offload-gb 6`（SGLang） | ❌ device mismatch | offload 路径报 cuda:0 与 cpu 混用 |
| GGUF Q3_K_M + SGLang `--quantization gguf` | ❌ 架构不支持 | Unknown gguf model_type: qwen3_5_text |
| **GGUF Q3_K_M + llama.cpp（-ngl 99）** | ✅ **成功** | **启动 4~6s；显存 14,350/16,311 MiB；解码 22~28 tok/s；prompt eval 44~90 tok/s** |
| GGUF + llama.cpp 但 CUDA 未加载（CPU 回退） | ⚠️ 能跑但慢 | 3.1 tok/s（对比 GPU 27.6 → **8.9×**） |

### 2.3 可用性验证（单卡 GPU）

```
# 启动（llama.cpp b11913 CUDA 13.4）
LD_LIBRARY_PATH=/home/luxy/llama.cpp/llama-b11913:/home/luxy/llama.cpp/cudart-llama-b11913-bin-ubuntu-cuda-13.4-x64:$LD_LIBRARY_PATH \
/home/luxy/llama.cpp/llama-b11913/llama-server \
  -m /home/luxy/models/Qwen3.8-27B-GGUF/Qwen3.8-27B.Q3_K_M.gguf \
  -a qwen38-27b -ngl 99 -c 4096 --host 127.0.0.1 --port 8016 --jinja

# 实测（同一进程内多次）
非thinking 自我介绍 : 27 tokens / 1.22s = 22.1 tok/s，中文正常
thinking 模式       : 123 tokens / 5.08s = 24.2 tok/s，reasoning 正常
路由 JSON 测试      : target="sim", confidence=0.95,
  reason="请求涉及高并发压测及脏数据注入，属于破坏性/高风险操作，必须在隔离的模拟环境中执行…"
→ 与蜂巢 Sim 路由规则完全一致 ✅
```

### 2.4 27B 单卡结论与建议

- **16GB 单卡可以跑 Qwen3.8-27B**，但只能用 **GGUF Q3_K_M（13.5GB）** 且是 llama.cpp 运行时；解码约 **22~28 tok/s**，做「中枢路由决策」这类短输出任务够用（一次路由 JSON ~2.9s / 66 tokens）；
- **Q4_K_M（16.8GB）** 需 ~1-2GB CPU offload，未实测；质量更好但速度会降；
- **AWQ/BF16/FP8** 在 16GB 单卡不可行（权重 > 显存）；双卡 TP2（16+12=28GB）可试但异构卡效率低；
- 生产建议：**中枢继续用 14B AWQ（vLLM）**；27B 作为「实验性中枢/大任务专家」走 llama.cpp GGUF 通道，与 S9 计划中的 vLLM 主链路隔离，不进主流程。

## 3. 对蜂巢计划的影响（更新计划图后端标注）

| 模块 | 决策 |
|---|---|
| 平台推理 | **vLLM 0.11 主用**（Qwen2.5-14B/7B 已验证；Qwen3.8 需 vLLM 新版才支持，暂不升级） |
| SGLang | 评测完成：小模型可用、Qwen3.8 走不通 → **二期备选**，不迁移 |
| 27B 实验通道 | **llama.cpp + GGUF**（单卡 16GB 已验证 22~28 tok/s）；作为"中枢升级预研" |
| S18 启动 | 中枢三路由继续用 14B AWQ；Prompt/JSON Schema 设计与后端无关，可直接落地 |

## 4. 复现与产物

| 产物 | 路径 |
|---|---|
| SGLang 评测脚本 | `scripts/eval_sglang_compat.py` |
| SGLang 结果 | `data/sglang_compat_eval.json` |
| 27B 单卡（llama.cpp）脚本 | `scripts/eval_qwen38_llamacpp.py` |
| 27B 单卡结果 | `data/qwen38_llamacpp_eval.json`（速率）/ `data/qwen38_llamacpp_quality.json`（可用性） |
| GGUF 模型 | `/home/luxy/models/Qwen3.8-27B-GGUF/Qwen3.8-27B.Q3_K_M.gguf`（13.5GB） |
| llama.cpp | `/home/luxy/llama.cpp/llama-b11913`（CUDA 13.4 预编译） |
| AWQ 权重（备用） | `/home/luxy/models/Qwen3.8-27B-AWQ-INT4-text`（17.65GB，SGLang 路径受阻，留作 TP2） |

## 5. 诚实边界

- SGLang 对比 vLLM 的吞吐是**单次短生成**（55–66 tokens），样本小、冷启动影响大，不能作为稳定基准；要严谨需压测（多并发/多轮）。
- 27B 单卡只测了 **Q3_K_M**；Q2_K/Q4_K_M/Q5 未测；Q3 量化质量低于 BF16，仅适合"路由 JSON/短决策"这类任务，长文生成质量需另评。
- llama.cpp 的 22~28 tok/s 是**单请求串行**；并发会显著下降，未测。
- SGLang 的问题都是**版本级兼容**（DeepGEMM/marlin/GGUF map），后续版本可能修复，需持续跟踪。
- 27B 是**多模态**模型，本次只验证文本；图像/视频输入未接（mmproj 未用）。
- AWQ-INT4 17.65GB 在 16GB 单卡上仍无解（需 TP2 或 offload，而 SGLang offload 路径当前坏）。
