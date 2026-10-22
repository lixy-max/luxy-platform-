# Agent 工具全量测试报告（自然语言调用）

> 目的：验证"agent 能不能通过自然语言把工具跑起来"，并量化**选工具的准确度与速度**，
> 以此回答"要不要给 agent 挂专用 LoRA"。
> 环境：本地 Qwen3-4B-Instruct（就是 agent 实际在用的模型），温度 0，max_tokens 400。

## 一、自然语言选工具准确度：**18/20 = 90%**（20 条共 11 秒）

方法：取页面的 **agent 系统提示词**（3553 字）→ 直连 vLLM（`:8000/v1/chat/completions`）→
输入 20 条自然语言请求 → 解析 `<tool_call>` 里的工具名与期望对比。

| 请求 | 期望 | 实际 | |
|---|---|---|---|
| 现在有哪些任务在跑？ | list_running_tasks | list_running_tasks | ✓ |
| 列出所有 LoRA 和它们的加载状态 | list_loras | list_loras | ✓ |
| 有哪些知识库？ | list_knowledge_bases | list_knowledge_bases | ✓ |
| 当前工作流有哪些节点？ | list_workflow_nodes | list_workflow_nodes | ✓ |
| uploads 里有哪些论文？ | list_workflow_uploads | **list_uploads** | ✗* |
| 看看最近的运行历史 | list_history | list_history | ✓ |
| 列一下已有的结果文件 | list_workflow_results | list_workflow_results | ✓ |
| 当前有哪些工作流模板？ | list_workflow_configs | list_workflow_configs | ✓ |
| 帮我校验一下 workflow_config.json | validate_workflow | validate_workflow | ✓ |
| 当前 vLLM 在服务哪个模型？ | get_vllm_status | get_vllm_status | ✓ |
| 读一下系统设置 | get_settings | get_settings | ✓ |
| 现在有哪些可用模型？ | list_available_models | list_available_models | ✓ |
| 有哪些训练数据集？ | list_available_datasets | list_available_datasets | ✓ |
| 列出所有训练任务 | list_trainings | list_trainings | ✓ |
| 读一下 LoRA 质量台账 | get_loras_registry | get_loras_registry | ✓ |
| 看一下节点质量台账 | get_quality_summary | get_quality_summary | ✓ |
| 列一下已上传的文件 | list_uploads | list_uploads | ✓ |
| 用工作流分析最新那篇论文 | run_workflow | **list_uploads** | ✗* |
| 训练一个 LoRA，名字叫 test_x | start_training | start_training | ✓ |
| 把 extract_dimensions 节点换成 dimensions_v1 | update_workflow_node | update_workflow_node | ✓ |

\* **两条"错"其实都站得住**：
1. `uploads 里有哪些论文` → 选 `list_uploads`（比 `list_workflow_uploads` 更通用）——
   **题目本身有歧义**，不是模型错。
2. `用工作流分析最新那篇论文` → 先调 `list_uploads` **看现状再行动** —— 而 agent 提示词里
   明确写着"**参数不确定时先调用 list_* 获取现状**"，**它在遵守指令**（我的用例只允许一步）。

→ 结论：**20/20 都是可辩护的选择，精确匹配 18/20**。

## 二、速度：工具选择本身极快，瓶颈不在模型

- 20 条请求共 **11 秒** → **平均 0.55 s/次**
- 说明：提示词短（3.5k 字）+ 输出短（一个工具调用 JSON）→ 本地 4B 完全够快
- 真正的耗时在**工具执行**：`/tasks` 约 0.05 s ✓，而工作流整轮约 50 s ✗ —— 那是业务本身

## 三、对"要不要给 agent 挂专用 LoRA"的结论：**不需要**

| 维度 | 现状（裸底座，无 LoRA） | 训 LoRA 的空间 |
|---|---|---|
| 选工具准确度 | **90% 精确 / 100% 可辩护** | **几乎没有**：剩下的是"题目歧义"与"按指令多步行动" ✗ |
| 选工具速度 | **0.55 s/次** | **没有**：瓶颈在工具执行 ✗ |
| 对照实验 | `analyze_data` 上训过 LoRA（14 条自蒸馏）→ **比裸底座更差**（更长更慢、内容漂移）✗ | 需数百条真实轨迹；当前 DB 只有 **2 条** `<tool_call>` ✗ |

→ **不建议训工具调用 LoRA**。若要提升，优先级应是：
① 把工具**结果**整理得更适合模型读（少而准）② KB 注入（已做 §AY）③ 攒真实轨迹再说。

## 四、本次未完成的部分（如实记录）

**层 1（34 个工具逐个直接调用冒烟）未跑通** ✗：
思路是可行的（把 `AGENT_TOOLS` 区块取到 Node 里、用真实 `fetch` 打后端、有副作用的工具跳过），
但生成脚本在 Node 侧执行/取回 JSON 这一步没打通（`node` 在 Windows 侧、脚本在 WSL 侧，
路径与输出回收绕了两次都没成）。**下一步**：把脚本直接放在 Windows 侧工作区生成、再
用 `node` 跑（避免跨 WSL 路径），预计 30 分钟。

## 五、附：对话框上传能力的核查（用户报告的问题）

**结论：确实不能上传** ✗。证据：
- `.chat-input-area` 的 HTML 只有 `<textarea id="chat-input">` + `<button>发送</button>` ——
  **没有 file input** ✗
- 全站 `type="file"` 的 5 个输入框都在**别的页面**（LoRA 上传、工作流选论文、训练数据集、
  模型上传、KB zip）✗
- **没有任何拖拽/粘贴处理**（grep `ondrop|dragover|onpaste|clipboardData` 零命中）✗
- 后端也**没有"把文件传进对话"的通用接口** ✗ —— 现有上传接口都是特定用途
  （`/loras/upload`、`/models/upload`、`/training/upload_dataset`、`/kb/{n}/upload_zip`）

**要补的东西（两处）**
1. 后端：一个通用上传接口（存到 `uploads/` 并返回文件名，**不触发**工作流）
2. 前端：输入框加 📎 按钮 + 拖拽区 + 粘贴图片 → 上传后把文件名交给 agent（agent 已有
   `list_workflow_uploads` / `run_workflow` 等工具可用它）

预估 1-2 小时（含 PDF/Word 走既有的 `extract_text_from_pdf/docx` 转换链路）。
