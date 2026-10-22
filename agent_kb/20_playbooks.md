# 常用操作套路（Playbooks）

> 每条套路都是**真实跑通过**的顺序。小模型照抄这个顺序，成功率明显更高。
> 规律只有一句：**只读发现 → 明确参数 → 危险操作（确认）→ 读结果**。

---

## P1 分析一篇论文（最常用）

```
1) list_workflow_configs          # 看有哪些预设（本项目只保留两个正式预设）
2) list_workflow_uploads          # 看有哪些论文文件可选（拿真实文件名）
3) run_workflow                   # ⚠
   args: {"config_name": "<第1步拿到的>", "file": "<第2步拿到的>"}
4) get_workflow_status            # 轮询任务状态（用第3步返回的 task_id）
5) get_workflow_result            # 拿结果（或 list_workflow_results 先列出结果文件）
```

**要点**
- `run_workflow` 是唯一"跑起来"的入口；配置名与文件名都必须来自第 1、2 步。
- 第 3 步之后**不要**立刻要结果 —— 工作流是异步的，先看状态。

---

## P2 训练一个 LoRA

```
1) list_available_datasets        # 拿数据集文件名
2) list_available_models          # 拿底座模型路径
3) start_training                 # ⚠
   args: {"lora_name": "<新名字>", "dataset_file": "<第1步拿到的>",
          "base_model": "<第2步拿到的>", "data_count": <条数>}
4) get_training_status            # 轮询（用 task_id）
```

**要点**
- `data_count` 用于自动推导 rank/epochs；不确定就先看数据集条数（`build_dataset` 之前的历史步骤或问你）。
- 训练会**占用 GPU**、期间推理服务不可用；调用前必须提醒用户。
- 训练完成后常需要 `switch_model` / `load_lora`（都是 ⚠）。

---

## P3 从工作流节点数据造训练集

```
1) list_workflow_results          # 找最近的成功结果
2) get_workflow_result            # 看结果里有哪些节点（拿 node_id）
3) get_quality_summary            # ⚠ 强烈建议：先看质量，质量差就别拿去训
4) build_dataset                  # ⚠
   args: {"result_file": "<结果文件>", "node_id": "<节点>"}
5) list_available_datasets        # 确认新数据集已生成
```

**要点**：**第 3 步不能省** —— 用过质量差的数据训出的 LoRA 会污染生产线（本项目有真实事故记录）。

---

## P4 Excel 处理（文档助手）

```
1) list_uploads                   # 拿 .xlsx 文件名
2) read_sheet                     # 先"看懂"：表头 + 前几行（rows 默认 10）
3) group_sum / filter_rows        # 计算（列名必须来自第 2 步看到的表头）
4) write_sheet                    # ⚠ 导出为新文件（args 里可用 columns 挑选字段）
```

**要点**
- 第 2 步是关键：**列名不能猜**，必须用第 2 步返回的 `header` 里的名字。
- 文档工具**只在 uploads/ 内工作**；文件名不能带路径。
- `write_sheet` 只生成**新文件**，绝不覆盖原件（同名会自动加序号）。

## P4b 分组汇总后导出（真实跑通的例子）

```
group_sum  {"filename": "sales.xlsx", "group_by": "部门", "sum_column": "金额"}
→ 返回 groups: [{"group":"市场","sum":250,"count":2,"avg":125}, ...]

write_sheet {"name": "按部门汇总", "header": ["部门","汇总金额"],
             "rows": "<上一步的 groups>", "columns": ["group","sum"]}
```
> `rows` 是"字典列表"时**必须**用 `columns` 指定要哪几个字段，否则写出来的列会不对。

---

## P5 跑"文档流程"（可复跑模板）

```
1) list_doc_flows                 # 看有哪些流程（含步数）
2) run_doc_flow                   # ⚠
   args: {"name": "<流程文件名>", "inputs": {"file": "<上传的文件名>"}}
```
返回里带 `trace`（每步工具、耗时、是否成功）—— 汇报时把每步结果说出来。

---

## P6 管理知识库

```
1) list_knowledge_bases
2) query_knowledge_base           # 检索
3) create_knowledge_base          # ⚠ 新建（之后用界面上传文档入库）
```

**要点**：答用户问题时，如果 KB 里检索到了相关片段，**优先引用 KB 内容**并说明来源，
不要用通用知识替代项目内资料。

---

## P7 排错（服务/模型层）

```
1) get_vllm_status                # 推理底座是否在跑
2) list_running_tasks             # 有没有正在跑的任务
3) get_settings / get_quality_summary
```

---

## 补充：实测中最容易选错的四句话（务必按此执行）

1. **"把汇总结果导出成新的 Excel" / "…存成文件" / "…导出"**
   → 是 `write_sheet`（**导出=写文件**），**不是** `group_sum`。
   "汇总""筛选"只是**计算**；出现"导出/存成/写成文件"才是**写**。
   （同理 Word：出现"写成一份 Word"→ `write_docx`。）

2. **"跑一下按部门汇总那个流程" / "跑一下……那个流程"**
   → 是 `run_doc_flow`（**文档流程**，步骤脚本），**不是** `group_sum`。
   句子里有"**流程**"两个字，且用户说的是**已存在的某个流程** → `run_doc_flow`；
   不确定流程名时先 `list_doc_flows`。

3. **"把 dimensions_v1 挂上去用" / "启用某个 LoRA" / "挂载 LoRA"**
   → 是 `load_lora`。**不要**选 `switch_model`（那是换**底座模型**），
   也**不要**先 `list_loras` —— 用户已经给了明确的 LoRA 名。

4. **"帮我查一下知识库里关于 XX 的内容" / "检索一下资料"**
   → 是 `query_knowledge_base`。**不要**选 `get_quality_summary`
   （那是看**分析报告质量**，与知识库无关）。
