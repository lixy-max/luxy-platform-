# practical-2 作业：平台 agent 端到端运行记录（2026-10-19 凌晨）

> 目的：证明 `E:\zy.work\practical-2-answers.docx` **由平台内置 agent 通过工具链生成**（不是脚本代写），
> 并记录过程中暴露的 3 个平台问题（P-48 / P-49 / P-50）。

## 1. 任务与运行方式

| 项 | 值 |
|---|---|
| 任务 | 读 `E:\zy.work\from_agent\practical-2.docx`（PSP Practical 2 · Perfect Number）→ 完成 5 问 → Word 存 `E:\zy.work\` |
| 执行者 | 平台**主对话 Agent**（页面 `http://127.0.0.1:8080/index3.html#/chat`，Agent 模式） |
| 模型 | `openai:deepseek-chat`（云 DeepSeek，经工作台 `/chat/tools` 原生 function calling） |
| 驱动 | `E:\WSL\_check\drill\run_p2.mjs`（Playwright 跑手：**只做用户在页面上会做的事** —— 选模型/开 Agent/点发送/点"危险工具确认"；实际读写文件全部由 agent 调工具完成） |
| max_tokens | 8192（P-46 修复后页面设置对 agent 生效） |
| 工具 | 88 个（含 `read_docx` / `run_simulation` / `write_docx`） |

## 2. 运行历史（4 次，全部记录在 `E:\WSL\_check\r17\out\`）

| # | 时间 | 结果 | 原因 |
|---|---|---|---|
| 1 | 16:48 | ❌ 未出文件 | agent 为满足"计算纪律"**逐个**调 `run_simulation`（20%1…20%19、28%1…），**24 轮迭代耗尽**；随后自动"云接手"无工具（P-15）⇒ 最终回"无法完成"（**P-48**） |
| 2 | 16:50 | ✅ 出文件但**缺 Q5 表格** | `table` 被当作 JSON 字符串传入，后端只认 list ⇒ **静默丢表**却返回成功（**P-49**） |
| 3 | 16:53 | ❌ 未出文件 | 页面 `marked` 从 jsdelivr CDN 加载失败 ⇒ `marked is not defined` ⇒ agent 回合崩溃（**P-50**） |
| 4 | 16:54 | ✅ **最终成功** | P-49/P-50 已修；任务提示加迭代预算约束（算术合并、`run_simulation≤3`、优先 `write_docx`） |

## 3. 最终成功运行的 API 层证据（run 4 · `p2_2026-10-18T16-54-57-509Z.json`）

```
第 1 轮  tool_calls=[read_docx]                      ← 读 E:\zy.work\from_agent\practical-2.docx（out_dir 参数生效 ✓）
第 2 轮  tool_calls=[run_simulation, run_simulation] ← 复算 20 与 28 的因子和
第 3 轮  tool_calls=[run_simulation]
第 4 轮  tool_calls=[write_docx]                     ← 首次写（agent 自查发现缺 AI Declaration）
         ← 危险工具确认弹窗 → 点确认（页面上的"用户确认"）
第 5 轮  tool_calls=[write_docx]                     ← 重写（补齐 AI Declaration）
         ← 确认弹窗 → 点确认
第 6 轮  tool_calls=[] content_len=1308              ← 最终汇报
```
工具卡状态（页面 DOM 直读）：`read_docx:ok · run_simulation:ok ×3 · write_docx:ok ×2`。

## 4. 产物与验收（`E:\zy.work\practical-2-answers.docx`）

- 40,962 bytes · **98 段** · **7 个标题**（标题 + 5 问 + AI Declaration）· **1 张 Word 表格（20 行 × 7 列）**
- ✅ Header：Module Name / Module Code **CHC4008** / Assessment Title / Student Number（占位 `YOUR-STUDENT-NUMBER`，待用户填）
- ✅ Q1：20 的真因子 1,2,4,5,10 → 和 22 → 非完美 → 输出 `0 22`（逐项除法 + 解释正确）
- ✅ Q2：自造输入 **28** → 真因子 1,2,4,7,14 → 和 28 → 完美 → 输出 `1 28`
- ✅ Q3：8 个子问题（含可选的平方根优化说明）
- ✅ Q4：伪代码两版（Version A `d<n`，与追踪表一致；Version B 平方根优化，边界 `d<n`/`other<>d` 正确）
- ✅ Q5：**真 Word 表格**，按题目列头填 20 行；逐行复算：最终 sum=28、Perfect=True、Output=`1 28` ✓
- ✅ AI Declaration：**诚实**声明使用了 AI（DeepSeek Chat，经 LoRA 工作台内置 agent）+ prompt 摘要 + 受影响部分（全部 5 问）

自动验收脚本：`E:\WSL\_check\r17\finalize_docx.sh`（header/AI 声明/关键答案/追踪表逐行复算全部通过）。

## 5. 诚实声明（不美化）

- ✅ 文档**不是脚本生成的**：脚本只负责点页面按钮；`read_docx`/`run_simulation`/`write_docx` 全部由页面 agent 发起（上方 API 记录为证）。
- ✅ **没有复用**任何旧产物（`uploads/cb067d75_practical-2-answers.docx`、`E:\zy.work\_lead_manual_backup\` 均未作为答案来源）。
- ⚠️ 任务提示里给了**迭代预算约束**（"run_simulation 最多 2~3 次、优先 write_docx"）——这是用户侧的合理指令（第一次跑正是因为没这条而失败），不属于代写答案。
- ⚠️ agent 第一次写出的版本缺 AI Declaration（92 段），第二次重写补齐（98 段）；我删掉了中间版，只保留完整版。
- ⚠️ 学号未知 ⇒ header 与提交文件名仍是占位符，需用户自己填（提交要求 `{StudentNumber}-Practical2.pdf`）。

## 6. 本轮暴露并修复的平台问题

| 编号 | 问题 | 状态 |
|---|---|---|
| P-48 | 计算纪律导致逐项仿真调用 ⇒ 24 轮迭代耗尽 ⇒ 无工具云接手 ⇒ 输出"无法完成" | ⏳ 已用任务提示规避；平台侧修复建议见台账 |
| P-49 | `write_docx(table)` / `write_sheet(rows)` 传 JSON 字符串 ⇒ 静默丢数据却返回成功 | ✅ 已修（后端容错 + 2 项回归测试） |
| P-50 | `marked` 走 CDN，弱网 undefined ⇒ agent 回合崩（`marked is not defined`） | ✅ 已修（renderMarkdown 降级 + 前端断言） |
