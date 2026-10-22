# 六项需求：只读核查报告 + 调整方案

> 核查方式：只读代码/数据（未改任何逻辑）。基线：后端 617 测试 / 前端 150 断言。

## 0. 结论速览

| # | 需求 | 核查结论 | 建议顺序 | 预估 |
|---|---|---|---|---|
| 1 | UI：设置工具栏到左下角、会话历史改下拉 | **已实施** | ✅ 完成 | — |
| 2 | agent 能否全量调用工具模块 | **不能：23 个工具 / 70 条路由（约 33%）**，缺口可分类 | 先补关键缺口 | 2-4h |
| 3 | 调用工具时不跳转、改为对话内小窗口显示进程 | 工具处理器**有 UI 副作用**（会改下拉/切页），消息结构**已带** tool_status/args/result | 第二批 | 3-4h |
| 4 | agent 挂知识库 / 挂专用 LoRA 能否提升工具调用准确度与速度 | **KB：可行且便宜**（机制已存在）；**LoRA：当前无轨迹数据**（仅 1 会话 / 2 条 tool_call） | KB 先做，LoRA 待攒数据 | KB 2h；LoRA 需先造数据 |
| 5 | PDF 上传支持识图/扫描文本 | 回退链已能**检测**扫描版，但**没有 OCR 引擎**；`pypdfium2` 已装可直接栅格化 | 第三批（价值高） | 3-5h |
| 6 | 新增"文档 agent 流程"（Excel/Word），与工作流并列 | 架构可行；需新模块 + 新工具集 + 新页面 | 最后（新架构） | 1-2 天 |

---

## 1. #1 UI（已实施）

- **会话历史改下拉式**：把 M3a 的列表换成 `<select id="chat-session-select" class="session-dropdown">`
  （复用原本被隐藏的那个 select，`refreshSessionList` 无需改；`#session-list` 保留但隐藏，
  `renderSessionList()` 仍可用 —— 未删代码，便于回退）
- **设置工具栏移到左栏底部**：`✏️ 重命名 / 🗑️ 删除 / ⚙️ 参数 / 🧹 清空` 移到左栏 `.col-foot`，
  用 `margin-top:auto` 固定到底部；中栏 toolbar 里的重复按钮移除（保留"折叠左栏"）
- 验收：后端 617 OK、前端 150/150

---

## 2. #2 agent 工具覆盖核查（只读）

**结论：不能全量调用。** agent 工具在前端声明（`AGENT_TOOLS`，index3.html:4375 起），
现有 **23 个**：

```
list_available_models  switch_model        list_loras          load_lora
unload_lora            list_knowledge_bases query_knowledge_base create_knowledge_base
list_workflow_nodes    add_workflow_node   list_workflow_uploads run_workflow
get_workflow_status    update_workflow_node build_dataset       run_eval
get_quality_summary    get_loras_registry  delete_workflow_node list_available_datasets
list_trainings         get_training_status start_training
```

后端有 **70 条路由**，未覆盖的 **47 条**，按性质分四类：

| 类别 | 例子 | 是否有意排除 | 建议 |
|---|---|---|---|
| **结果读取**（对 agent 最有用） | `GET /history`、`/history/{id}`、`/workflow/result/{f}`、`/workflow/list`、`GET /eval/status/{id}`、`/eval/list` | 否，是缺口 | **优先补** |
| **上传/入库** | `POST /kb/{n}/upload_zip`、`/kb/{n}/ingest_folder`、`/kb/{n}/rebuild`、`/training/upload_dataset`、`POST /models/upload` | 否 | **优先补**（#6 也依赖） |
| **配置读写** | `GET/PUT /settings`、`/providers`、`/workflow/config`、`/workflow/config/import|export`、`/workflow/validate` | 部分（危险） | 读接口可补，写接口加确认 |
| **危险操作** | `POST /vllm/start|stop|switch`、`DELETE /loras/{n}`、`DELETE /kb/{n}`、`DELETE /history/{id}` | **建议排除** | 保持不给 agent（或 `dangerous: true` + 二次确认） |

**注意**：`run_eval` 已存在但 **`/eval/status` 没被包** → agent **能起评测却读不到结果** ✗，
这是最典型的一个"半覆盖"缺口。

---

## 3. #3 工具调用不再跳转 + 对话内进度小窗

**现状**：工具处理器里混了 UI 副作用。例证：`AGENT_TOOLS.list_available_models.handler`
会**重建对话页的模型下拉**（`sel.innerHTML=''` 后重填）；`switch_model` 也会改下拉；
工作流类工具会触发 `loadWorkflowConfig()` 等页面动作 —— 所以"调用工具 = 界面跳一下"。

**改法（两步，风险低）**
1. **把 handler 变纯函数**：只 `fetch` + 返回数据；把"刷新界面"的动作交给 UI 层
   （例如 `list_available_models` 返回列表即可，是否刷新下拉由调用方决定）
2. **对话内进度小窗**：消息结构**已经**带 `tool_status / tool_args / tool_result`
   （`chat_messages` 表字段已确认），所以只需**渲染层**加一个可折叠卡片：
   工具名 + 运行中转圈 + 参数摘要 + 结果摘要（长结果折叠、可"查看详情"）
   —— 数据链路是通的，不改存储、不改 agent 循环

---

## 4. #4 知识库 / 专用 LoRA 对 agent 工具调用的作用评估

### 4.1 知识库：**可行、便宜、建议做**
机制已存在：`#chat-kb-select` + `GET/POST /kb/{n}/query` + 工具 `query_knowledge_base`。
差别在**注入方式**：
- 普通对话：发送前检索 KB，把片段注入 system prompt（已有）
- **agent 模式：目前不自动注入** ✗，只能靠模型自己想起去调 `query_knowledge_base`
→ 改法：agent 模式也做一次 KB 预检索注入（照抄对话的实现，约 20-30 行）
→ 预期：模型不必"猜"该查什么，**少一轮工具调用**、参数更准

### 4.2 工具调用专用 LoRA：**当前做不了，缺数据**
实测 `data/workbench.db`：`chat_sessions` **1** 行、`chat_messages` **8** 行、
其中含 `<tool_call>` 的 **2** 条 ✗ —— 训一个工具调用 LoRA 至少要**数百条**轨迹。
两条可行路径：
- **A. 攒真实使用**（零成本，但要时间）：先把工具调用用起来，慢慢积累
- **B. 合成轨迹**（一次性成本）：写一批脚本化任务，让**云端**（更强的老师）产出
  正确的 `<tool_call>` 序列 → 蒸馏到本地 4B。这样才有"教正确的调用"的可能
> 结论：**先做 4.1（KB 注入）**；4.2 等 A 积够数据，或你同意走 B（要花云端 token 造数据）。

---

## 5. #5 PDF 识图 / 扫描文本（价值高，建议早做）

**现状**：`lora_manager.py:258` 起是回退链 `pdfplumber → pypdf → pdftotext`；
`x_tolerance=1.5, y_tolerance=3`；**已能识别**"0 页有可提取文字（可能是扫描版 PDF）" ✓

**缺什么**：没有任何 OCR 引擎（`pip list` 无 pytesseract/paddleocr/easyocr ✗）。
已装：`pdfplumber / pypdf / pypdfium2 / pillow / opencv-python-headless` ✓

**方案**
1. **栅格化**：用已装的 `pypdfium2` 把"无文字层"的页渲染成图（无需新依赖）
2. **OCR**：装 **`rapidocr-onnxruntime`**（纯 pip、无系统依赖、中英文都支持、CPU 可跑）
   —— 若效果不够再退到 `easyocr`（torch 已装，但模型更重）
3. **接线**：在回退链里加"方案 C：逐页渲染 + OCR"，**只在 A/B 都拿不到文字时触发**
   （避免对可提取 PDF 白跑 OCR，那会很慢）
4. **顺带修一个老问题**：现在 `extract_text()` 对**双栏排版**会把两栏交错（造成参考数据
   16% 碎片）。可改用 `extract_words()` + 按 x 坐标聚类分栏 —— 这项会让**所有**抽取质量变好
5. 需要下载 OCR 模型（首次运行联网，约 10-50 MB）

---

## 6. #6 新架构：文档 agent 流程（Excel / Word）

**定位**：与工作流模块**并列**的第二条流水线 —— 不分析论文，而是**处理办公文档**。
建议叫「文档助手 / 文档流程」，与「论文分析工作流」共享底座与 LoRA 机制。

**设计要点**
1. **两个层次**（与现有架构一致，便于复用）
   - **Agent 工具流程**：自然语言驱动，工具做原子操作（读表/筛选/汇总/写回）
   - **可保存的"文档流程"**：像 `workflow_config.json` 一样可存模板、可复跑（阶段二再做）
2. **工具集（首批 10-12 个）**
   - Excel：`read_sheet`（概览/表头/前 N 行）、`filter_rows`、`group_sum`、
     `write_sheet`（新文件，不覆盖原件）、`list_sheets`
   - Word：`read_docx`（按标题分段）、`extract_tables`、`write_docx`（模板填充）、
     `replace_text`
   - 通用：`list_docs`、`convert`（docx→txt 便于检索）
3. **依赖**：`openpyxl` / `python-docx`（都需新装，纯 pip）
4. **安全**：**只写新文件**，永不覆盖原文件；所有写操作进"待确认"队列（复用 danger/确认机制）
5. **前端**：工具二级页新增「📄 文档助手」，含上传区 + 任务列表 + 结果下载
6. **与 agent 的关系**：这些工具**注册进同一个 `AGENT_TOOLS`** → 对话里也能直接说
   "把这份表按部门汇总" → 自然接入 #2/#3 的改进
7. **分期**：P1 只读（读表/读 Word/检索）→ P2 写回（新文件）→ P3 可保存的文档流程模板

**为什么这个顺序**：#6 依赖 #2（工具覆盖）与 #3（对话内反馈）的机制，
先把那两块理顺，新模块就是"照着加工具 + 加页面"，而不是再造一套。

---

## 7. 建议执行顺序

```
#1 已完成
→ #5 OCR / PDF（修数据源，后面所有评测与抽取都受益）
→ #2 补关键工具缺口（尤其 /eval/status、/history、上传类）
→ #3 工具纯化 + 对话内进度卡片
→ #4.1 KB 注入 agent
→ #6 文档助手（新架构，P1 只读）
→ #4.2 工具调用 LoRA（等轨迹数据，或走云端合成）
```
