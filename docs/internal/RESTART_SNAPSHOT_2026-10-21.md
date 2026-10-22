# 重启前状态快照（2026-10-21）

> **生成时间**: 2026-10-21 15:27:44
> **git HEAD**: `097ed5e` · 未提交: **0**
> **用途**: 重启后照这个恢复 ✓

---

## 一、⚠ 重启会杀掉的东西（需要重起 ✓）

### 1. 三个服务（**无自启** ✗ 必须手动起）
```bash
# ① vLLM（3060 · 端口 8000）
cd /home/luxy/paper_lora_project
CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1 \
VLLM_USE_FLASHINFER_SAMPLER=0 VLLM_ATTENTION_BACKEND=FLASH_ATTN \
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 \
nohup /home/luxy/.venv/bin/python -m vllm.entrypoints.openai.api_server \
  --model /home/luxy/models/Qwen2.5-7B-Instruct-AWQ \
  --port 8000 --max-model-len 8192 --gpu-memory-utilization 0.90 \
  --enable-lora --max-loras 4 --max-lora-rank 32 --max-num-seqs 8 \
  --enforce-eager --enable-auto-tool-choice --tool-call-parser hermes \
  > /tmp/vllm.log 2>&1 &

# ② 后端（8001）
nohup /home/luxy/.venv/bin/python lora_manager.py > /tmp/backend.log 2>&1 &

# ③ 前端（8080）
nohup /home/luxy/.venv/bin/python webui/serve.py > /tmp/frontend.log 2>&1 &
```

### 2. ⚠ 正在跑的评测会被杀掉
```
task:  干净泛化评测（easy + mid）
tag:   **lora_p16_gen2**
状态:  跑到 3/46 左右 ✗ 会被中断
数据:  已完成的记录在 `runs_v2.jsonl` ✓ **不会丢** ✓
⇒ 重启后**需要重跑** ✓（用同一 tag 继续 ✓ 或新 tag ✓）
```

### 3. LoRA 挂载会丢（vLLM 重启后要重挂）
```bash
for L in p16_7b_v2_20261021_134604 flywheel_round1_20261021_150646; do
  curl -s -X POST http://127.0.0.1:8001/loras/load \
    -H 'Content-Type: application/json' \
    -d "{\"name\":\"$L\",\"lora_name\":\"$L\",\"path\":\"/home/luxy/loras/$L\"}"
done
```

---

## 二、✅ 不会丢的（安全 ✓ 都在磁盘上）

### 三个 LoRA（全部已注册 ✓）
```
/home/luxy/loras/
  ├── p16_7b_v2_20261021_134604/           ← 435 条 · loss 1.2993 · **4b 档 100%** ✓
  ├── secretary_7b_20261021_140709/        ← 同上（重复品 ⚠）
  └── flywheel_round1_20261021_150646/     ← 30 条飞轮数据 · loss 2.9021 ✓
```

### 数据（全部在磁盘 ✓）
```
E:\WSL\_check\eval_v2\out\
  ├── runs_v2.jsonl                 ← 全部评测记录 ✓
  ├── lora_p16_gen2.log             ← 干净评测日志（会断 ✗）
  ├── lora_p16_general.log          ← 污染评测（9 条 ✗ 弃用）
  ├── final_7b_kb0.log              ← 7B 定论臂A ✓
  ├── final_7b_kb1v3.log            ← 7B 定论臂B ✓
  └── runs_v2.jsonl.bak_*           ← 备份 ✓
```

### 文档（全部已提交 ✓）
```
docs/
  ├── RELEASE_V4.md                              V4 发布说明 ✓
  ├── LORA_TRAINING_SUCCESS_2026-10-21.md        LoRA 训练报告 ✓
  ├── FLYWHEEL_LOOP_VALIDATION_2026-10-21.md     飞轮闭环验证 ✓
  ├── VIDEO_SCRIPT_LUXY_2026-10-21.md            视频剧本（数据版）✓
  ├── 7B_DEFINITIVE_2026-10-21.md                7B 定论 ✓
  ├── CONFIDENCE_SCORER_DESIGN.md                置信度设计 ✓
  ├── GROUNDING_FIRST_2026-10-21.md              Grounding ✓
  ├── AGI_PLUGIN_ARCHITECTURE.md                 AGI 外挂 ✓
  ├── METAAGENT_HONESTY_REVIEW.md                MetaAgent 诚实审查 ✓
  ├── PRIORITY_PLAN_2026-10-21.md                优先级 ✓
  ├── V6_EXECUTION_PLAN.md                       V6 计划 ✓
  ├── RETROSPECTIVE_2026-10-21.md                复盘 ✓
  └── PLATFORM_ISSUES.md                         台账（含核实报告 ✓）
```

### Remotion 视频项目（**在 Windows 侧** ✓ 不受 WSL 重启影响 ⚠）
```
E:\WSL\remotion-demo\
  ├── src\index.ts · LuxyRoot.tsx · LuxyVideo.tsx   ← 5 幕 · 5400 帧 ✓
  ├── out\act3-core.png                            ← 渲染验证 ✓
  └── node_modules\（219MB ✓ 已装 ✓）
```

---

## 三、★ 重启后第一件事（按顺序 ✓）

```bash
# ① 检查磁盘/服务
nvidia-smi                          # 两张卡应都在（5060 16G / 3060 12G）
curl -s http://127.0.0.1:8000/v1/models   # vLLM 起来了吗
curl -s http://127.0.0.1:8001/health     # 后端起来了吗
curl -s http://127.0.0.1:8080/index3.html # 前端起来了吗

# ② 若没起来 ⇒ 按上面 §一 的三条命令起

# ③ 重挂 LoRA
# ④ 继续跑评测（tag lora_p16_gen2，会用已有记录去重吗？⚠ 跑批器不去重 ✗ 会重跑 ✓）
cd /mnt/e/WSL/_check/drill
node eval_v2_run.mjs --tier easy --repeats 2 --lora p16_7b_v2_20261021_134604 \
  --kb off --kb-inject off --tag lora_p16_gen2 --timeout-min 3
node eval_v2_run.mjs --tier mid  --repeats 2 --lora p16_7b_v2_20261021_134604 \
  --kb off --kb-inject off --tag lora_p16_gen2 --timeout-min 3
```

---

## 四、⚠ 重启前的注意事项

```
① **不要** 手动 kill 服务（重启会全杀 ✓ 没必要 ✗）
② **不要** 清空 `/tmp`（训练日志在那里 ✓）
③ **确认 git 已提交**（当前 0 条未提交 ✓）
④ Remotion 在 E: 盘 ✓ WSL 重启不影响 ✓
⑤ runs_v2.jsonl 在 E: 盘 ✓ 不会丢 ✓
```

---

## 五、重启后的待办（按优先级 ✓）

| # | 任务 | 说明 |
|---|---|---|
| **1** | 起三个服务 | 见 §一 ✓ |
| **2** | 重挂 LoRA | 见 §一 ✓ |
| **3** | **重跑泛化评测**（easy + mid） | tag `lora_p16_gen2` ✓ 这次不碰训练 ✓ |
| 4 | 渲染 Remotion 视频 | 评测完再渲 ✓（避免抢 CPU ✗）|
| 5 | 飞轮数据扩到 ≥200 条 | 30 条不够 ⚠ |
| 6 | 飞轮数据加 function_call | 才能教工具调用 ✓ |

---

*2026-10-21 · 重启前快照 · 照 §一 恢复即可 ✓*
