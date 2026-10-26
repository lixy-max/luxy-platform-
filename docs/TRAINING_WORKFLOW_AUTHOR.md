# 工作流作者 LoRA（S17）

> 让**本地小模型学会自己搭建工作流**：训练数据、训练脚本、评估与部署说明。

## 1. 数据

| 文件 | 说明 |
|---|---|
| `training_data/workflow_authoring/workflow_author_v2_all.jsonl` | 12 条「任务 → workflow JSON」样本（DeepSeek 生成） |
| `..._v2_train.jsonl` / `..._v2_eval.jsonl` | 10 train / 2 eval |
| 样本格式 | sharegpt：`system`（工作流架构师约束）+ `user`（短任务）+ `assistant`（workflow JSON） |
| 来源 | 云端 DeepSeek 生成（target_model_requested=deepseek4.1flash1017 → 实际 deepseek-flash） |

生成脚本：`scripts/gen_authoring_data.py`

## 2. 训练

```bash
# 先停 7B 工蜂释放 GPU1（约 10GB）
CUDA_VISIBLE_DEVICES=1 /home/luxy/.venv/bin/python scripts/train_workflow_author_lora.py
```

- 基座：`/home/luxy/models/Qwen2.5-1.5B-Instruct`
- LoRA：r=16 / alpha=32 / target 全线性层 / 14 epochs / lr 2e-4 / fp16
- 实测：**42 steps / 61.2s / loss 0.4150 / adapter 73.9MB**
- 产物：`/home/luxy/loras/workflow_author_v1/`（已登记 `loras/registry.json`，role=workflow_author，compatible=false 因 1.5B 与 7B/14B 不兼容）

## 3. 评估（scripts/eval_workflow_author.py）

同一批 4 个任务、system 提示、贪心解码、max_new_tokens=1400：

| 指标 | 基座 Qwen2.5-1.5B | + workflow_author_v1 |
|---|---|---|
| JSON 合法率 | 100%（system 提示约束） | **75%** |
| **workflow_engine lint 通过率** | **0%** | **75%** |
| 2 样本 structure_ok（训练同构评估） | 50% | **100%** |

> 基座能"输出 JSON"但**结构不符合工作流 schema**（节点类型/字段乱）；LoRA 后 3/4 任务
> 直接产出可被 `workflow_engine.lint_config` 接受的工作流（input→process/tool→aggregate，含 write_sheet rows/header）。

## 4. 部署/使用

1. **独立 vLLM（推荐先验证）**：
   ```bash
   CUDA_VISIBLE_DEVICES=1 vllm serve /home/luxy/models/Qwen2.5-1.5B-Instruct \
     --enable-lora --lora-modules workflow_author=/home/luxy/loras/workflow_author_v1 \
     --host 127.0.0.1 --port 8003 --max-model-len 4096
   ```
   请求 `model=workflow_author`，system 用 `scripts/eval_workflow_author.py` 中的约束。
2. **接平台**：把生成的 workflow JSON 交给 `workflow_engine`（lint → 落盘 → 执行）；
   目前 1.5B 模型与主脑 14B/工蜂 7B 不兼容，需单独节点或另训 7B 版本。
3. **评测复现**：`CUDA_VISIBLE_DEVICES=1 python scripts/eval_workflow_author.py` → `data/workflow_author_eval.json`

## 5. 已知局限

- 训练样本只有 10 条（云端生成），泛化有限：4 任务评估 75%，未覆盖任务类型可能失败。
- 1.5B 小模型容量有限，复杂工作流（多 tool 链）成功率会下降。
- 训练/推理必须使用同款 system 提示（否则结构合法率显著下降）。
- 与平台 7B/14B 服务不兼容；要上线需按 7B 基座重训（脚本可直接改 BASE 路径）。
