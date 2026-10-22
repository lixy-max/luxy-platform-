# 工具目录（自动生成，请勿手改）

本项目共有 **69** 个 agent 工具，其中 **11** 个属于“会改数据/训练/模型”的危险操作（调用前系统会弹窗让人确认）。

> 本文件由 `scripts/gen_agent_kb.py` 从 `webui/index3.html` 抽取生成。

**参数写法**：下面 `args` 列出的是**可接受的参数名**（同一个参数的别名都列出，任选其一即可）。标 `—` 表示无需参数或参数名不固定，请先调用只读工具确认。

## 工作流：查看与配置

| 工具 | 危险 | 参数 |
|---|---|---|
| `validate_workflow` | 否 | `config`, `config_name`, `name` |

## 工作流：节点编辑

| 工具 | 危险 | 参数 |
|---|---|---|
| `add_workflow_node` | 否 | `description`, `id`, `input_from`, `kb_name`, `lora`, `model_override`, `prompt`, `provider` |
| `update_workflow_node` | 否 | `fields`, `id`, `node`, `node_id`, `patch`, `set` |
| `delete_workflow_node` | ⚠ 是 | — |

## 工作流：运行与结果

| 工具 | 危险 | 参数 |
|---|---|---|
| `run_workflow` | 否 | `config`, `config_name`, `file`, `filename`, `kb`, `kb_name`, `paper`, `paper_name` |
| `get_workflow_status` | 否 | `id`, `taskId`, `task_id` |
| `get_workflow_result` | 否 | `file`, `filename`, `node`, `node_id`, `result_file` |
| `list_workflow_results` | 否 | `limit` |
| `list_workflow_uploads` | 否 | — |

## 训练

| 工具 | 危险 | 参数 |
|---|---|---|
| `start_training` | ⚠ 是 | `adapter_name`, `count`, `dataCount`, `data_count`, `dataset`, `datasetFile`, `dataset_file`, `desc` |
| `get_training_status` | 否 | `id`, `taskId`, `task_id` |
| `list_trainings` | 否 | — |
| `build_dataset` | 否 | `dataset`, `file`, `filename`, `name`, `node`, `node_id`, `result_file` |
| `list_available_datasets` | 否 | — |
| `list_available_models` | 否 | — |

## 评测

| 工具 | 危险 | 参数 |
|---|---|---|
| `run_eval` | 否 | `count`, `dataset`, `dataset_file`, `limit`, `lora`, `lora_name`, `name`, `role` |
| `get_eval_status` | 否 | `id`, `taskId`, `task_id` |

## LoRA 管理

| 工具 | 危险 | 参数 |
|---|---|---|
| `list_loras` | 否 | — |
| `get_loras_registry` | 否 | — |
| `load_lora` | ⚠ 是 | — |
| `unload_lora` | ⚠ 是 | — |

## 知识库

| 工具 | 危险 | 参数 |
|---|---|---|
| `list_knowledge_bases` | 否 | — |
| `create_knowledge_base` | 否 | — |
| `query_knowledge_base` | 否 | — |

## 文档助手：Excel

| 工具 | 危险 | 参数 |
|---|---|---|
| `list_sheets` | 否 | `file`, `filename`, `name` |
| `read_sheet` | 否 | `file`, `filename`, `limit`, `name`, `rows`, `sheet` |
| `filter_rows` | 否 | `col`, `column`, `column_name`, `file`, `filename`, `limit`, `name`, `op` |
| `group_sum` | 否 | `file`, `filename`, `group`, `groupBy`, `group_by`, `limit`, `name`, `sheet` |
| `write_sheet` | ⚠ 是 | `columns`, `data`, `filename`, `header`, `name`, `rows`, `sheet`, `sheet_name` |

## 文档助手：Word

| 工具 | 危险 | 参数 |
|---|---|---|
| `read_docx` | 否 | `dir`, `file`, `filename`, `folder`, `name`, `outDir`, `out_dir` |
| `write_docx` | ⚠ 是 | `content`, `dir`, `filename`, `folder`, `heading`, `lines`, `name`, `outDir` |

## 文档助手：流程

| 工具 | 危险 | 参数 |
|---|---|---|
| `list_doc_flows` | 否 | — |
| `run_doc_flow` | ⚠ 是 | `flow`, `flow_name`, `inputs`, `name`, `vars` |
| `save_doc_flow` | ⚠ 是 | `description`, `flow_name`, `name`, `steps`, `title` |

## 文件与历史

| 工具 | 危险 | 参数 |
|---|---|---|
| `list_uploads` | 否 | — |
| `list_history` | 否 | `limit` |
| `get_history_detail` | 否 | `id`, `taskId`, `task_id` |

## 质量与系统

| 工具 | 危险 | 参数 |
|---|---|---|
| `get_quality_summary` | 否 | `limit`, `node`, `node_id` |
| `get_settings` | 否 | — |
| `get_vllm_status` | 否 | — |
| `switch_model` | 否 | — |
| `list_running_tasks` | 否 | — |

## 其它

| 工具 | 危险 | 参数 |
|---|---|---|
| `academic_search` | 否 | — |
| `browser_click` | 否 | — |
| `browser_fill` | 否 | — |
| `browser_open` | 否 | — |
| `browser_read` | 否 | — |
| `browser_screenshot` | 否 | — |
| `browser_snapshot` | 否 | — |
| `browser_tabs` | 否 | — |
| `browser_wait` | 否 | — |
| `clean_dataset` | ⚠ 是 | — |
| `create_workflow_config` | 否 | `clone_current`, `config`, `config_name`, `fromCurrent`, `from_current`, `name` |
| `export_docx` | 否 | — |
| `list_datasets` | 否 | — |
| `list_sim_nodes` | 否 | `tag` |
| `list_workflow_configs` | 否 | — |
| `list_workflow_nodes` | 否 | — |
| `mount_knowledge_bases` | 否 | — |
| `physics_calc` | 否 | — |
| `read_upload` | 否 | `file`, `filename`, `limit`, `maxChars`, `max_chars`, `name` |
| `rebuild_knowledge_base` | ⚠ 是 | — |
| `replace_in_file` | 否 | — |
| `replace_text` | 否 | — |
| `run_simulation` | 否 | `args`, `node` |
| `set_lines` | 否 | — |
| `set_lines_in_file` | 否 | — |
| `upload_dataset` | ⚠ 是 | — |
| `use_workflow_config` | 否 | `config`, `config_name`, `name` |

