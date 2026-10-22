# 易混淆工具对照（按实测错例整理）

## 最快速查（口头说法 -> 工具）

| 用户的口头说法 | 工具 |
|---|---|
| "把 XX **挂上去用**" / "启用 XX LoRA" | `load_lora` |
| "**换成**另一个底座 / 切到 XX 模型" | `switch_model` |
| "**查一下**知识库里关于 XX 的**内容**" / "检索资料" | `query_knowledge_base` |
| "知识库**有哪些**" | `list_knowledge_bases` |
| "**跑个评测**" / "评测一下 XX" | `run_eval` |
| "评测**跑完了吗**" | `get_eval_status` |
| "这次分析**质量怎么样**" / "报告可信吗" | `get_quality_summary` |
| "**有哪些论文**" | `list_workflow_uploads` |
| "**有哪些数据集**" | `list_available_datasets` |
| "把结果**导出**成新表" | `write_sheet` |
| "按部门**汇总**金额" | `group_sum` |
| "**筛出**金额大于 100 的行" | `filter_rows` |
| "给工作流**加**一个节点" | `add_workflow_node` |
| "这个配置**有没有问题**" | `validate_workflow` |
| "看看这张表的**前十行**" | `read_sheet` |
| "看看这张表**有哪些工作表**" | `list_sheets` |


> 这一节直接来自 **42 题工具选择测试的真实错例**：模型把 A 工具选成了 B。
> 遇到下面这些说法时，请照"该选"那一列。

## 1. 模型类：`list_available_models` / `get_settings` / `switch_model` / `load_lora`

| 用户说 | 该选 | 不该选 | 区分点 |
|---|---|---|---|
| "机器上有哪些**底座模型**" / "有哪些模型可选" | `list_available_models` | `get_settings` | 问**有哪些** → 列清单 |
| "当前配置是什么" / "设置里怎么配的" | `get_settings` | `list_available_models` | 问**当前值** → 读配置 |
| "**换成**另一个底座模型" / "切到 14B" | `switch_model` | `get_settings` | **换底座** → switch |
| "把某个 LoRA **挂上去用**" / "启用 LoRA" | `load_lora` | `switch_model` | 换 **LoRA** → load；换**底座** → switch |
| "卸掉当前 LoRA" | `unload_lora` | `load_lora` | 摘掉 → unload |

> 一句话记：**"有哪些"=list**，**"当前是什么"=get**，**"换底座"=switch_model**，**"挂 LoRA"=load_lora**。

## 2. 评测类：`run_eval` / `get_eval_status` / `get_quality_summary`

| 用户说 | 该选 | 区分点 |
|---|---|---|
| "**评测一下** dimensions_v1" / "跑个评测" | `run_eval` | 要**发起**评测 |
| "评测**跑完了吗**" / "评测结果出来没" | `get_eval_status` | 问**进度** |
| "这次分析**质量怎么样**" / "报告可信吗" / "有没有编造" | `get_quality_summary` | 问**工作流产出质量**，不是 LoRA 评测 |

> 注意：`get_quality_summary` 是**工作流节点质量台账**（引用是否可回溯等），
> 与 `run_eval`（LoRA 的逐字段 recall/precision）**完全不是一回事**。

## 3. 知识库：`query_knowledge_base` / `list_knowledge_bases` / `get_quality_summary`

| 用户说 | 该选 |
|---|---|
| "**查一下**知识库里关于 X 的内容" / "检索资料" | `query_knowledge_base` |
| "知识库**有哪些**" | `list_knowledge_bases` |
| "新建知识库" | `create_knowledge_base` |

> "查/检索/找资料" → `query_knowledge_base`；只有"有哪些/列出"才是 list。

## 4. 工作流：`list_workflow_configs` / `list_doc_flows` / `run_workflow` / `run_doc_flow`

| 用户说 | 该选 | 区分点 |
|---|---|---|
| "有哪些**工作流模板/预设**" | `list_workflow_configs` | 论文分析工作流 |
| "有哪些**文档流程**" | `list_doc_flows` | Excel/Word 的步骤脚本 |
| "**跑一下**论文分析" / "开始分析" | `run_workflow` | 真正执行 |
| "跑一下按部门汇总那个**流程**" | `run_doc_flow` | 文档流程执行 |
| "上次分析的**报告**给我看看" | `get_workflow_result` | 取结果（不是历史会话）|
| "看看**历史会话**" | `list_history` | 聊天记录，不是工作流结果 |

## 5. 状态类：`get_workflow_status` / `get_training_status` / `list_running_tasks`

| 用户说 | 该选 |
|---|---|
| "**上次那个任务**跑完了吗"（刚跑过工作流）| `get_workflow_status`（拿不准就先 `list_running_tasks`）|
| "**训练**进度怎么样" | `get_training_status` |
| "现在有哪些任务在跑" | `list_running_tasks` |

## 6. 论文/文件类：`list_workflow_uploads` / `list_uploads` / `list_available_datasets`

| 用户说 | 该选 |
|---|---|
| "有哪些**论文**可以分析" | `list_workflow_uploads` |
| "把**上传的文件**列一下" | `list_uploads` |
| "有哪些**数据集**能用" | `list_available_datasets` |

## 7. Excel：`group_sum` / `write_sheet` / `filter_rows`

| 用户说 | 该选 |
|---|---|
| "按部门**汇总**金额" | `group_sum` |
| "**筛出**金额大于 100 的行" | `filter_rows` |
| "把结果**导出**成新的 Excel" / "存成表" | `write_sheet` |

> 先算后存：**汇总/筛选 ≠ 导出**。用户说要"导出/存成文件"才是 `write_sheet`。

## 8. 复合要求怎么起步

用户一句话里包含多步时，**先做第一步**（通常是只读发现），不要跳到最后一步：

| 复合要求 | 第一步 |
|---|---|
| "把汇总结果导出，再写成 Word" | `group_sum`（先算）→ 之后 `write_sheet` / `write_docx` |
| "从上次结果造训练集然后训练" | `list_workflow_results` → `get_workflow_result` → `build_dataset` → `start_training` |
| "评测一下，不行就换个模型重训" | `get_eval_status` 或 `run_eval`（先看现状）|

---

## 实测补充：最容易判断错的冷门说法（按 178 题评测的 7 个残留错例整理）

| 用户可能这么说 | 该选 | 判断依据（不要被别的词带偏）|
|---|---|---|
| "跑一下按部门汇总**那个流程**" / "跑一下 XX **流程**" | `run_doc_flow` | 句中**有"流程"**且指**已存在**的文档流程 → 不要看"汇总"就选 `group_sum` |
| "帮我**校验**工作流" / "配置**有没有问题**" / "检查一下工作流" | `validate_workflow` | "校验/检查/有没有问题" → **不是** `get_quality_summary`（那是报告质量）|
| "把**节点温度**调低一点" / "改节点提示词" / "改节点参数" | `update_workflow_node` | 出现**节点** + **改/调** → 改节点；只"看节点"才是 `list_workflow_nodes` |
| "**分析进度**怎么样" / "工作流跑到哪了" | `get_workflow_status` | "**分析/工作流** + 进度" → workflow；"**训练**进度"才是 `get_training_status` |
| "之前**训过哪些** LoRA 任务" / "历史训练任务" | `list_trainings` | 出现"**训过/训练任务**" → `list_trainings`；只是"有哪些 LoRA"→ `list_loras` |
| "**测一下**抽取效果" / "测测效果好不好" | `run_eval` | "**测一下/测测**" → 发起评测；`get_quality_summary` 是看**报告**质量 |
| "这张表**长什么样**" / "表里是什么" | `read_sheet` | 想看**内容/前几行** → `read_sheet`；问"**有哪些**工作表/sheet"才是 `list_sheets` |

## 新建/保存流程 vs 执行流程（实测混淆，必须分清）

| 用户怎么说 | 该用哪个 | 不要用 |
|---|---|---|
| **新做一个**文档流程 / **建一个**流程 / **先读表再导出**（在讲"怎么搭"） | `save_doc_flow`（存成流程）；先 `list_doc_flows` 看现有流程 | ✗ 不要用 `read_sheet` —— 用户在**搭流程**，不是在读某张表 |
| **跑一下**那个文档流程 / **执行**流程 / 用流程把表处理了 | `run_doc_flow` | ✗ 不要用 `read_sheet`/`write_sheet` 单步代替整条流程 |
| 这份表里有什么 / 读表头 / 前 10 行 | `read_sheet`（真的是在读某张具体的表时） | — |

判断口诀：句子里出现"**做/建/搭/存/另存**"= 在**建流程** → `save_doc_flow`；
出现"**跑/执行/用流程**"= 在**跑流程** → `run_doc_flow`；
出现"**表里/表头/第几行/这一列**"= 在**读某张表** → `read_sheet`。

## 新增节点 vs 修改节点（实测混淆，必须分清）

| 用户怎么说 | 该用哪个 |
|---|---|
| **新增/加/添加**一个节点、**再来**一个分析节点 | `add_workflow_node` |
| **改/修改/调整/换成**某个节点（的提示词、模型、LoRA） | `update_workflow_node` |
| **删掉/移除**某个节点 | `delete_workflow_node` |

判断口诀：**从无到有 = add**；**已有再变 = update**。
"新做一个流程"里的"做"指的是**流程**，用 `save_doc_flow`，不是 `add_workflow_node`。

## 读取"已上传文件"的正文 → read_upload

用户说"**上传的那个 txt 里写了啥**""**把上传的文件内容读出来**""**这文档说了什么**"时：
先 `list_uploads` 拿到文件名，再 `read_upload(filename)` 取正文，然后基于正文回答。
不要回答"我看不到文件内容"；也不要用 `read_sheet`（那是读 Excel 表格，读不了纯文本）。
