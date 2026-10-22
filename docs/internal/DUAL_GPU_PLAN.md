# RTX 3080 10GB 双卡方案（可执行）· 2026-10-19

> 前提：**卡未到** ✓ 本文是**安装前方案** ✓ 卡到后按 checklist 执行 ✓
> 硬件：5060 Ti 16GB（主）+ 3080 10GB（新）= **26GB 显存** · 32GB RAM（WSL 可调 24GB）· NVMe 2.6GB/s · 24 核

---

## 第一部分 · 物理安装（卡到后**先做这 3 项检查** ⚠）

### 1.1 PCIe 插槽（决定跨卡速度 ✓ 最关键）
```
5060 Ti 现在 = Gen5 **x8** ⚠（说明主板把 x16 拆成 x8/x8 ✓ 大概率第 2 槽也是 x8 ✓）

卡到后必查:
  nvidia-smi --query-gpu=index,name,pcie.link.gen.current,pcie.link.width.current --format=csv
预期:
  槽1（5060Ti）: Gen5 x8 ✓
  槽2（3080）  : Gen4 x8 ✓（3080 是 Gen4 卡 ✓）
⇒ 跨卡带宽 ≈ 16 GB/s（Gen4 x8 双方）✓ 可用 ✓
⚠ 若槽2 只有 x4 ⇒ 8 GB/s ⚠ 拆分训练会明显变慢 ⚠ 但仍可跑 ✓
⚠ 若插不上（物理空间）⇒ 见 1.3
```

### 1.2 电源（**最容易翻车** ⚠）
```
功耗预算:
  5060 Ti = 180W
  3080    = 320W ⚠
  CPU+主板+盘 ≈ 150W
  ⇒ 峰值 ≈ 650W ⚠ 建议 PSU ≥ 750W ✓ 金牌 ✓

必查:
  · PSU 铭牌额定功率（≥750W ✓）
  · 8pin PCIe 供电线数量（3080 需 2×8pin ✓）
  · 总瓦数余量（建议 ≤80% 负载 ✓）
⚠ 若 PSU < 650W ⇒ **不要装** ✗ 换电源再说 ✗
```

### 1.3 物理空间 / 散热
```
3080 是 2.5-3 槽厚 ⚠ + 320W 发热 ⚠
必查:
  · 主板 GPU 槽间距（≥3 槽 ✓ 才能装下 ✓）
  · 机箱长度（3080 长 28-32cm ✓）
  · 风道（两张卡之间要有空隙 ✓ 否则 3080 会热到降频 ✗）
⚠ 若装不下 ⇒ 用 **PCIe 延长线**竖装 ✓（注意 x16 转接卡质量 ✓）
```

### 1.4 软件层（WSL 配置 ✓ 5 分钟）
```ini
# C:\Users\A\.wslconfig
[wsl2]
memory=24GB        # 32GB 物理 → 给 WSL 24GB ✓
swap=8GB
processors=20
```
执行 `wsl --shutdown` 重启 ✓

---

## 第二部分 · 四套运行模式（★ 方案核心）

### 模式 A：**双线并行**（推荐默认 ✓ 最高 ROI）
```
┌─────────────────────────────────────────┐
│ 3080 10GB（卡1）      →  vLLM 推理专用     │
│   · Qwen2.5-7B-Instruct-AWQ（5.2GB ✓）    │
│   · gpu_memory_utilization=0.60 → ~6GB ✓  │
│   · port 8000 ✓ 平台正常用 ✓              │
├─────────────────────────────────────────┤
│ 5060 Ti 16GB（卡0）   →  训练/评测专用     │
│   · QLoRA ≤14B（~12GB ✓）                 │
│   · drill 跑批 ✓                          │
│   · 嵌入（bge-m3 2.2GB ✓）                │
└─────────────────────────────────────────┘
收益: 训练时平台【仍可对话】✓ 不用停 vLLM ✓
      这就是"挂载新模型双线并行"的正确姿势 ✓
```
**启动方式**：
```bash
# 3080 跑 vLLM（需给 vllm_manager 加 device 支持 ✓ 见 P-80）
CUDA_VISIBLE_DEVICES=1 bash scripts/vllm_start.sh

# 5060Ti 跑训练
CUDA_VISIBLE_DEVICES=0 bash scripts/train.sh ...
```

### 模式 B：**RAG 专用**（配合 P-74 KB 评估 ✓）
```
3080 10GB → 后端 + 嵌入（bge-m3 2.2GB ✓ bge-reranker 1.2GB ✓）
5060 Ti   → vLLM 7B 推理 + 训练
收益: 嵌入不吃主卡 ✓ RAG 检索质量可独立优化 ✓
```

### 模式 C：**双卡拆分训练**（27B/32B QLoRA · 见第三部分）
```
26GB 合并:
  27B QLoRA ≈ 20GB ✓ 可行
  32B QLoRA ≈ 24GB ⚠ 勉强
代价: 无 NVLink ⇒ 慢 ⚠（预计 0.3-0.6× 单卡）
适用: 数据 1000+ ✓ 且 14B 已证有效 ✓ 时才值得
```

### 模式 D：**双模型并行推理**（T-0 现成模型评测 ✓）
```
3080（卡1）   → 7B-AWQ   port 8000
5060Ti（卡0） → 14B-AWQ（9.4GB ✓）port 8002
收益: 同时跑两个模型做 A/B 对照 ✓✓
      T-0「现成模型 vs 我们 base」评测正好用它 ✓
```

---

## 第三部分 · 模式 C 具体实现（27B 双卡拆分训练）

### 3.1 方案 1：**PyTorch 官方 pipeline parallel**（推荐 ✓ 无额外依赖）
```python
# 27B QLoRA 双卡层拆分（4-bit 权重 ✓）
import torch, torch.distributed as dist
from torch.distributed.pipelining import pipeline, SplitPoint, PipelineStage, Schedule1F1B
from transformers import AutoModelForCausalLM, BitsAndBytesConfig
from peft import LoraConfig, get_peft_model

# ① 4-bit 加载到 CPU（内存 24GB 需能放下 27B 的 4-bit=13.5GB ✓ 可以 ✓）
bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16,
                         bnb_4bit_use_double_quant=True, bnb_4bit_quant_type="nf4")
model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-32B-Instruct", quantization_config=bnb,
                                             device_map="cpu", torch_dtype=torch.float16)

# ② 拆到两卡：32B 有 64 层 ⇒ 卡1(3080 10GB) 0-25 层 · 卡0(5060Ti 16GB) 26-63 层
#    按【显存比例】分：10:16 ⇒ 约 38% : 62%
split = {"model.layers.24": SplitPoint.BEGINNING}
pipe = pipeline(model, mb_args=(), splits=[...], ...)
# ③ 两卡各自 stage（device 显式指定 ✓）
stage0 = PipelineStage(pipe.submod_0, 0, device="cuda:1")   # 3080
stage1 = PipelineStage(pipe.submod_1, 1, device="cuda:0")   # 5060Ti
# ④ 1F1B 调度 + LoRA 只在两卡各挂一部分
```

⚠ **关键难点**（诚实 ✓）：
- ① **QLoRA + PP 组合**很少见 ✗（peft + pipeline 需要手动绑定 ✓ 需调试 ⚠）
- ② **4-bit 量化 + 层拆分**可能不兼容 ✗（bitsandbytes 的 device_map 与 PP 冲突 ⚠）
- ③ **无 NVLink** ⇒ 每步要传 activation ✗ 慢 ⚠
⇒ **预计调试 1-3 天** ⚠ 不保证成功 ✗

### 3.2 方案 2：**RoundPipe**（省事 ✓ 但需实测）
```bash
pip install roundpipe
```
```python
import torch
from roundpipe import RoundPipe, OptimizerCtx
# 官方宣称：24GB 单卡全参 32B / LoRA 到 235B ✓
# 多卡自动线性缩放 ✓ 不改训练循环 ✓
model = torch.nn.Sequential(layer1, layer2, ...)   # 顺序模型 ✓
for data in dataloader:
    loss = model.forward_backward(data)             # 自动跨卡 ✓
    with OptimizerCtx(): optimizer.step(); optimizer.zero_grad()
    model.step()
```
**优点**：自动拆 ✓ 自动 offload ✓ 官方声称 6× 快于 FSDP offload ✓
**风险**：94★ 新项目 ⚠ 27B QLoRA + 双卡异构未验证 ⚠ 需实测
**建议**：**先用小模型（7B）验证它能跑通 ✓** 再上 27B ✓

### 3.3 方案 3：**RoundPipe 纯 offload**（不拆分 ✗ 只用一卡 + 内存/盘 ✓）
```
27B 4-bit = 13.5GB > 16GB? ✗ 不超 ⇒ 单卡 16GB 就够 ✓（~20GB 总 ✗ 超 ✗）
⇒ 需 26GB ✓ 或 offload ✓
⇒ 用 RoundPipe offload: 权重在内存/盘 ✓ 计算时搬入显存 ✓
   32GB RAM ✓ + 2.6GB/s NVMe ✓ ⇒ **可行 ✓** 但慢 ⚠
```

### 3.4 ⚠ 前置条件（**不满足就别做** ✗）
```
✗ 数据 < 1000 条 ⇒ 别训 27B ✗（过拟合 ✓ 且无法证明有效 ✗）
✗ 14B 单卡还没验证出有效结果 ⇒ 别上 27B ✗
✗ T-0~T-5 没走完 ⇒ 别上 ✗
⇒ 顺序：14B 单卡验证 ✓ → 有正结果 ✓ → 才考虑 27B 双卡 ✓
```

---

## 第四部分 · 平台代码要改的地方（P-80/P-81）

### P-80：vLLM 启动支持指定 GPU ✓（小改 30 分钟）
```python
# lora_manager.py vllm_manager.start_vllm(...) 增加 device 参数
env["CUDA_VISIBLE_DEVICES"] = str(device)   # 新增 ✓
# .vllm.args.json 增加 "device": 1
```

### P-81：训练脚本支持指定 GPU ✓（小改 30 分钟）
```bash
# scripts/train_lora.py 读环境变量 ✓
DEVICE=${TRAIN_DEVICE:-0}
export CUDA_VISIBLE_DEVICES=$DEVICE
```

### P-82：双卡状态面板（前端 ✓ 可选）
```
底座管理页显示: 卡0（5060Ti）: 2.1/16GB · 卡1（3080）: 6.0/10GB
```

---

## 第五部分 · 落地 checklist（卡到后按顺序 ✓）

### 第 0 步：装卡前（10 分钟）
```
☐ 查 PSU 额定功率 ≥ 750W ✓（不足 ⇒ 停 ✗）
☐ 查 8pin PCIe 线 ×2 ✓
☐ 查槽间距 ≥3 槽 ✓
☐ 改 .wslconfig（memory=24GB ✓）
```

### 第 1 步：装卡 + 启动验证（30 分钟）
```
☐ 断电插卡 ✓ 接供电 ✓ 开机 ✓
☐ WSL: nvidia-smi -L                         # 应看到 2 张卡
☐ nvidia-smi --query-gpu=index,name,memory.total,pcie.link.gen.current,pcie.link.width.current --format=csv
☐ 记录: 卡0 = 5060Ti x8 Gen5 · 卡1 = 3080 x? Gen4
☐ 跑 3 分钟 furmark/训练压测 ✓ 看温度和功耗 ✓
```

### 第 2 步：模式 A 上线（1 小时）
```
☐ 加 P-80（vLLM 指定卡 ✓）
☐ 3080 起 vLLM: CUDA_VISIBLE_DEVICES=1
☐ 验证 :8000 = 200 ✓ 平台对话正常 ✓
☐ 5060Ti 跑一次 14B QLoRA 训练 ✓（验证双线并行 ✓）
☐ 训练期间看平台对话是否正常 ✓（这才是双线并行的价值 ✓）
```

### 第 3 步：模式 D 验证（T-0 用 ✓ 1 小时）
```
☐ 5060Ti 起 14B-AWQ（port 8002 ✓）
☐ 两模型同时跑 ✓
☐ 为 T-0「现成模型评测」做准备 ✓
```

### 第 4 步（只在前 3 步都成功 + 数据够时 ✓）
```
☐ 模式 C 实验：先 RoundPipe 7B 单卡验证 ✓
☐ 再 27B 双卡 ✓（1-3 天调试 ⚠）
```

---

## 第六部分 · 风险与止损

| 风险 | 概率 | 止损 |
|---|---|---|
| **PSU 不够** ✗ | 中 | 装前必查 ✓ 不够就停 ✗ |
| **槽位只有 x4** ⚠ | 中 | 可接受 ✓ 拆分训练慢但能跑 ✓ |
| **装不下/过热** ⚠ | 中 | 延长线竖装 ✓ 或只装一张 ✓ |
| **WSL 只认一张卡** ⚠ | 低 | 更新驱动 ✓ 或 WSL2 GPU-PV ✓ |
| **RoundPipe 不通** ⚠ | 中 | 退回 PyTorch PP ✓ 或放弃拆分 ✓ |
| **27B 训了没用** ✗ | **高** ⚠ | **前置条件拦住** ✓ 14B 没正结果不动 27B ✓ |

---

## 第七部分 · 一句话总结

```
✅ 3080 的最优用法【不是拆分层训练】✗ 是【模式 A 双线并行】✓
   3080 推理 ✓ + 5060Ti 训练 ✓ ⇒ 训练时平台不停 ✓ 这才是最大收益
⚠ 拆分层训练（模式 C）技术可行 ✓ 但:
   · 只对 27B+ 有意义 ✓
   · 无 NVLink ⇒ 慢 ⚠
   · 前置条件：14B 已证有效 + 数据 1000+ ✓
⇒ 卡到后：装卡检查 → 模式 A 上线 → 模式 D 验证 → 才考虑模式 C
```

---

*方案 · 2026-10-19 · 依据：`docs/GPU_3080_FEASIBILITY.md`（修正版 ✓）+ [RoundPipe](https://github.com/thustorage/RoundPipe) + [PyTorch Pipeline Parallelism](https://docs.pytorch.org/docs/main/distributed/pipelining.html) + 平台代码实证（`vllm_manager.start_vllm` 无 device 参数 ✗ 需加 P-80）*
