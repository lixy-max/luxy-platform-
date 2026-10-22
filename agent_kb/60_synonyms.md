# 同义词与"流程"判断

## "流程"这个词要按宾语判断

- 宾语是"论文/文章/分析/文献" → 论文工作流：run_workflow / list_workflow_configs / list_workflow_nodes
- 宾语是"文档/Excel/表格/Word/汇总导出" → 文档流程：run_doc_flow / list_doc_flows
- 只说"有哪些流程/流程清单"且未提文档 → 默认论文工作流：list_workflow_configs
## 同义词对照（用户会用不同词说同一件事，按右边选工具）

- 适配器 / adapter / 适配层 = LoRA → list_loras / get_loras_registry / load_lora / unload_lora
- 基座 / 底座 / 基础模型 / base model = 推理底座 → list_available_models / switch_model
- 语料 / 训练数据 / 训练集 = 数据集 → list_available_datasets / build_dataset
- 固化 / 落成 / 存成 / 保存为 ……流程 = save_doc_flow
- 启用 / 应用 / 装载 LoRA = load_lora；撤下 / 停用 = unload_lora
- 校验 / 检查 / 合规 / 有没有写错 = validate_workflow
- 测一测 / 评估 / 跑个指标 = run_eval；进度 / 跑完没 = get_eval_status
- 可靠性 / 有没有编造 / 引用对得上吗 = get_quality_summary
- 开库 / 建库 / 新资料库 = create_knowledge_base；搜 / 找 / 检索内容 = query_knowledge_base
- 标签页 / sheet / 工作表 = list_sheets；表头 / 内容 / 长什么样 = read_sheet
- 存成新表 / 输出 xlsx = write_sheet；输出 word/docx = write_docx
