# 训练记录 · exercise-v3-2-0（第 0 轮）

- **训练时间**: 2026-10-19 14:44 → 14:54（**9 分 46 秒** ✓）
- **状态**: ✅ 成功（exit 0 ✓ 147/147 步 ✓ 3 epochs ✓）

## 配置（实测 ✓）
| 项 | 值 |
|---|---|
| 底座 | `/home/luxy/models/Qwen3-4B-Instruct`（fp16 ✓） |
| 方法 | QLoRA 4-bit（LLaMA-Factory ✓） |
| **rank / alpha** | **16 / 32**（脚本固定 ✓ 不是我原计划的 32/64 ⚠） |
| dropout | 0.05 |
| lora_dropout | 0.05 |
| target_modules | q/k/v/o_proj + gate/up/down_proj ✓ |
| 学习率 | 1e-4 · cosine · warmup 0.1 |
| batch | 1 × grad_accum 8（有效 8 ✓） |
| epochs | 3 |
| cutoff_len | **3072**（实测 avg 250 token ✓ 超 cutoff **0 条** ✓） |
| bf16 + gradient_checkpointing | ✓ |

## 数据（实测 ✓）
- 训练集: `training_data/exercise_book_v3_2_0_v2.jsonl` = **430 条**
- 来源: 40 条人工精标样本 × 12 问法变体 ✓
- `data_report.json`: tokens avg **250** / p95 271 / max 283 · 超 cutoff **0** · verdict **pass** ✓
- ⚠ **system_token_ratio = 0.6506（65%）** —— 高于 GUIDE 的「system <20%」建议 ✗
  （但前置闸门只按超 cutoff 判 ✓ 没拦这一项 ⚠）

## 结果（实测 ✓）
| 指标 | 值 |
|---|---|
| 起始 loss | 6.6287 |
| **最终 train_loss** | **0.8126**（avg）· 末步 0.0008 ⚠ |
| grad_norm（末步） | 0.0358 |
| 训练时长 | 9:46 |
| adapter 大小 | **126.1 MB**（132,187,888 B ✓） |
| 产出 | `/home/luxy/loras/exercise-v3-2-0/` ✓ |
| checkpoint | 49 / 98 / 147 ✓ |

## ⚠ 三个必须记录的问题

### 问题 1 🔴 **底座不匹配（LoRA 暂时用不了 ✗）**
- **训练底座**: `Qwen3-4B-Instruct`（4B ✓ qwen3 ✓）
- **平台服务底座**: `Qwen2.5-7B-Instruct-AWQ`（7B ✓ qwen2 ✓）
- ⇒ **不同家族 + 不同尺寸** ⇒ **LoRA 无法挂到当前 vLLM** ✗
- **解法**：
  - A. 把 vLLM 切到 `Qwen3-4B-AWQ`（2.5G ✓ 同族 ✓ 能挂 ✓）→ 跑 A/B ✓（~40 分钟）
  - B. 用 7B 重训（~2.5h ✓）→ 直接可用 ✓
  - C. 先记录不验证 ✓

### 问题 2 ⚠ **过拟合风险（loss 0.0008 极低）**
- 430 条 × 3 epochs ⇒ loss 从 6.63 掉到 **0.0008** ✗
- 训练集 40 个任务 × 12 问法 ✓ 高度同质 ✓ ⇒ **大概率记忆训练集** ⚠
- **只能靠 A/B 判断泛化** ✓（这正是评估体系存在的意义 ✓）

### 问题 3 ⚠ **system 占比 65%（训练信号弱）**
- system prompt 占了样本 65% 的 token ✗ ⇒ 模型主要在学"system 长什么样" ✗ 而不是"工具怎么调" ⚠
- **改法**：缩短 system（去纪律条款 ✓ 只留工具清单 ✓）→ system 占比降到 ~20% ✓
- 这是 **v3-2-1 的第 1 优先改动** ✓

## 结论
```
✅ 训练管线跑通（9:46 · QLoRA · 显存峰值 6.1G ✓）
✅ adapter 产出正确（126MB · rank16 ✓）
⚠ 但【还没证明有效】✗ —— loss 低 ≠ 分数高 ✗
⚠ 数据 3 个问题（底座不匹配/过拟合/system 占比）需下一轮修
```
