# 已知坑与真实错例（Pitfalls）

> 全部来自本项目**真实踩过的坑**。这些是最省时间的部分：知道坑在哪，就不用重踩。

## 1. 名字类参数必须来自 `list_*`（最高频错误）

**症状**：工具返回"文件不存在 / 未知配置 / 找不到数据集"。

**原因**：模型凭"看起来合理"的名字去调用（例如自己造 `workflow_config_v2`、
`paper.pdf`、`test_kb`）。

**正确做法**：任何名字参数都先 `list_*`：
工作流 → `list_workflow_configs`；论文/文件 → `list_workflow_uploads` / `list_uploads`；
数据集 → `list_available_datasets`；LoRA → `list_loras`；KB → `list_knowledge_bases`；
结果 → `list_workflow_results`；节点 → `list_workflow_nodes`。

**真实错例**（工具选择测试，20 题里错 2 题，其中一题就是这类）：
用户说"用那份**上个月**的数据集训练"，模型直接编了一个数据集名去调 `start_training`，
正确做法是**先 `list_available_datasets`**，再告诉用户"没有叫这个名字的，可选的是……"。

## 2. 工作流是异步的，不要立刻要结果

`run_workflow` 只返回 `task_id`。紧接着调 `get_workflow_result` 通常拿到"还没跑完"。
正确顺序：`run_workflow` → `get_workflow_status`（轮询）→ `get_workflow_result`。

## 3. 危险工具要先解释、再调用

标 ⚠ 的工具会改数据（删节点、覆盖模型绑定、启动训练、卸载 LoRA）。
**先一句话说明影响**，再发调用；系统还会弹窗让人二次确认。

真实教训：`analyze_method` 节点上曾绑过一个**测试产物 LoRA**，导致分析结果跑题。
所以：**改绑定/训练前先看质量与注册表**（`get_quality_summary` / `get_loras_registry`）。

## 4. Excel 的列名不能猜

`group_sum` / `filter_rows` 的列名必须来自 `read_sheet` 返回的 `header`。
真实情况：表里是「部门 / 地区 / 金额 / 数量」，模型却传了 `"销售额"` → 报"找不到列"。

**参数别名可以宽松，业务含义不能宽松**：目录里给的别名（如 `sum_column`/`sum`）任选都行，
但**列名本身**必须和表头一致。

## 5. `write_sheet` 的 rows 是"字典列表"时要带 columns

`group_sum` 的返回是 `[{"group":...,"sum":...,"count":...,"avg":...}, ...]`。
直接喂给 `write_sheet` 会写出 4 列且与表头不匹配。
正确：`"columns": ["group","sum"]`，并让 `header` 与之对应。

> 这是**真实事故**：种子文档流程最初就是漏了 `columns`，一跑就失败。

## 6. 文档工具只在 `uploads/` 内工作

文件名不能带路径（`/`、`\`、`..` 一律拒绝），且只接受受支持的扩展名
（Excel：`.xlsx/.xlsm`；Word：`.docx`）。要处理别处的文件，先上传。

## 7. 拿不到数据 ≠ 工具坏了

多数"失败"其实是**前置条件没满足**：
- 没有上传任何文件 → 先 `list_uploads` 看，可能是空的
- 推理底座没启动（`:8000` 无响应）→ 先 `get_vllm_status`
- 任务还在跑 → 先 `list_running_tasks`

**先诊断再重试**，不要换个参数反复撞。

## 8. 不要编造工具名

工具名是**固定的 44 个**（见 `10_tool_catalog.md`）。
不要发明 `search_paper`、`download_model`、`fix_error` 这类不存在的工具。
没有对应工具时，直接告诉用户"我没有这个能力，可用的相关工具是……"。

## 文档工具的坑（2026-10 扩充测试发现）

### 读写必须对称：解析出路径后不要再"只传文件名"
`_resolve_input_file` 负责在 uploads / outputs / 指定 out_dir / 项目内找文件。
曾经的写法是"解析出完整路径 -> 调用 read_docx_text(src.name)"，而 read_docx_text
内部只认 uploads/，等于把刚解析出的目录又丢掉，表现为 404（错误信息还会退回它自己的措辞，
让人误以为补丁没生效）。
**正确做法**：拿到解析后的路径就直接读它（python-docx / openpyxl），不要再二次转手。

### 打桩（stub）必须忠实于真实实现的校验
测试台为了快速自检会把需要模型的工具打桩。第一版桩"无论输入什么都返回文本"，
于是"空文本应报错"这类负向用例被桩放过去，报成 FAIL —— 是假问题。
**正确做法**：桩里保留与真实实现相同的入参校验（空输入照样抛 400）。

### create/write 系列遇到重名不会覆盖，会另存一份
实测 write_docx 生成 `e2e_result_r2.docx` 已存在时，新产物为 `e2e_result_r2_2.docx`。
好处是不丢数据；代价是反复跑会堆文件。需要覆盖时请显式删除或改用新名字。

### 长文用例的输入长度不要"估算"
分块相关的断言（chunks >= 2）依赖输入真的超过 chunk_chars（默认 2500）。
我连续三次把测试输入估算短了（1300 / 4028 / 4588 字），导致断言误报、甚至 assert 中止整轮。
**正确做法**：用 while 循环拼到明确超过阈值（例如 7000 字），并留下自检 assert。

### out_dir 写入限制
只允许写到【项目目录】或【E 盘】；拒绝 `..`、`/etc`、`C:\Windows`、`/root`。
支持 Windows 风格 "E:\目录"（自动转 /mnt/e/...）。这是为了防止误写系统目录。

### 读工具不会全盘扫描 E 盘
read_docx / read_sheet 按 uploads -> outputs -> 指定 out_dir -> 项目内 的顺序找，
**不会**递归扫描整个 E 盘（太慢且有风险）。因此读 E 盘的产物时**必须传 out_dir**。
