# P-20 调查报告：为什么"API 层有工具调用，但页面没渲染工具卡"

> 只读调查，未改任何代码/服务/知识库；唯一写入文件为本报告。
> 数据：`training_output/drill_part1_runs.jsonl`（132 行，被评估任务持续写入，本次只读快照）
> 代码：`webui/index3.html`（8748 行）· `E:\WSL\_check\drill\run.mjs`（148 行，评估跑手）
> 时间：2026-10-18 08:19–08:26（arm1p 批，20 条）

---

## 0. 结论（一句话）

**页面其实"画了"，没画全：**
1. **真正的渲染 bug 只有一处** —— `webui/index3.html:8421` 的 `const _tc = d.tool_calls[0]`：模型在一轮里并行提出 N 个工具调用时，**只执行并只画第 1 个，其余静默丢弃**（无卡片、无执行、无结果回填）。arm1p 批里 **5 次调用被丢（P1-01 缺 3 张、P1-03 缺 2 张）**。
2. **"P1-01 页面一张卡都没有"是测量错误，不是渲染错误** —— 页面明明有 **3 张 ❌ 失败卡**（`create_workflow_config` 3 次全失败，含完整错误原文）。因为 `run.mjs:129` 的工具计数正则**只数 ✅**，失败卡一律不算 ⇒ `ui_tools=[]`。
3. 另有 2 个测量口径缺陷：`run.mjs:123` 的 `.slice(-6000)`（截**头**，长会话丢最早的卡）和 `run.mjs:129` 正则无词边界（把耗时数字粘进工具名、把答案正文里的 `✅/❌` 列表符号当成卡片）。

**对"用户以为它没动手"的纠正**：P1-01 用户看到的是 3 张红色失败卡，不是空白——用户知道它动了、也看到它失败了。真正的信任风险是**卡片说失败、正文却像在交付**（见 §5），而不是"悄悄动手"。

---

## 1. 先校准口径（原始数据与任务描述有出入）

| 项 | 任务描述 | 实测 |
|---|---|---|
| 字段名 | `api_tools` / `ui_tools` 每条都有 | **只有 22/132 条有**（行 111–132）；其余是旧版跑手写的记录，只有 `tool_names`/`tool_calls`/`tool_mentions` |
| 为何只有 22 条 | — | `run.mjs:56` 注释：API 层计数是 **2026-10-18 才加的**（P0-2），旧记录无此字段 |
| `msgs` 截断 | "只截了 -6000 字符" | ✓ 确认：`run.mjs:123` `(await page.locator('#chat-messages').innerText()).slice(-6000)`，**保留尾部、砍掉头部** |
| lead 的批 | 20 条 | **arm1p 20 条 = 行 111–130**；行 131–132 是 v2r32p 两条（vLLM 掉线，api=0），不计入 |

lead 的三个数字已 1:1 复现：**API 层有工具 6/20 = 30%**、**旧口径 ui_tools 有工具 5/20 = 25%**、P1-01 的 api 恰好是那 6 个名字。

### 逐条对账（arm1p 20 条）

"卡片"的判据不是关键词，而是**页面真实结构**：`图标 + 工具名 + 紧跟耗时`。`appendToolCard`（`index3.html:6844`）渲染成 `❌\ncreate_workflow_config\n0.0s\n参数…`，所以用 `(✅|❌)\s*(名)\s*(\d+\.\d+s)` 锚定。

| id | API 提出 | 真实卡片 | ✅ | ❌ | 旧口径 ui_tools | 差额 | 说明 |
|---|---|---|---|---|---|---|---|
| **P1-01** | **6** | **3** | 0 | **3** | **0** | **3 丢** | 3 张 ❌ 卡，旧口径全漏 |
| P1-03 | 5 | 3 | 3 | 0 | 2 | **2 丢** | 2 张被 8421 丢掉 |
| P1-13 | 3 | 1 | 1 | 0 | 1 | 2? | **msgs=5994 被截头**，无法判定 |
| P1-17 | 1 | 1 | 1 | 0 | 1 | 0 | ✓ |
| P1-18 | 1 | 1 | 1 | 0 | 1 | 0 | ✓ |
| P1-20 | 2 | 2 | 2 | 0 | 2 | 0 | 旧口径把名字写成 `list_available_datasets0`/`start_training0`（脏名） |
| 其余 14 条 | 0 | 0 | 0 | 0 | 0 | 0 | 模型没调工具（只说不做，另属 P-1x 类问题） |
| **合计** | **18** | **11** | 8 | 3 | 9 | **5 明确丢弃** | 执行率 11/18 = 61% |

**关键判据（让"少卡"无法用其它原因解释）**：`index3.html:8498` 对**每一个**进入执行的调用都**无条件**先 `appendToolCard(..., 'running', ...)`，卡片从不被删除或清空（只有 `renderAgentSession` 会重建，且仅用于历史恢复）。因此：

> **卡片数 == 真正执行的调用数**。
> 6 次 API 提出 − 3 张卡 = **3 次从未执行**。

再用回合数交叉验证（每个 assistant 气泡一个 🤖）：P1-01 有 **4 个气泡**（3 个工具回合 + 1 个收尾），3 个工具回合却对应 6 次 API 调用 ⇒ 至少有一轮提了 ≥2 个调用而只执行了第 1 个。精确匹配：

- P1-01：轮次 `[create,write_sheet,run_doc_flow,read_sheet]` → `[create]` → `[create]` = **6 提出 / 3 执行**，卡片 3 张全是 `create_workflow_config` ✓
- P1-03：轮次 `[list,validate]` → `[validate,validate]` → `[validate]` = **5 提出 / 3 执行**，卡片 `list,validate,validate` ✓

---

## 2. 根因 1（真 bug）：并行工具调用只取第 1 个

**文件:行 → `webui/index3.html:8420-8430`**（agent 主循环，`sendAgentTurn()` 内）

```js
8420:  if (d.tool_calls && d.tool_calls.length) {
8421:      const _tc = d.tool_calls[0];        // ★ 只取第 1 个，其余丢弃
8422:      call = { name: _tc.name, args: _tc.args || {},
8423:               __id: _tc.id || 'call_0',
8426:               __native: { id: _tc.id || 'call_0', type: 'function', ... } };
8429:      bubble.finalize(acc || ('正在调用工具 ' + call.name + '…'));
8430:  }
```

- `nativeToolCall()` 返回的是 `/chat/tools` 的**完整响应体**（`index3.html` 中该函数把 `d` 原样 `return d`），`d.tool_calls` 是模型一次给出的**全部**并行调用；vLLM 已开 `--enable-tool-choice --tool-call-parser hermes`，支持一轮多调用。
- 循环体只把 `d.tool_calls[0]` 包成 `call`，后面 `appendToolCard`/`tool.handler`/`pushToolResult` 全围绕这一个 `call`。**其余 N-1 个既没有卡片，也没有执行，也没有回填结果。**
- 更隐蔽的是 `pushToolResult`（`index3.html` 约 8330 行）：它只 push `tool_calls: [call.__native]`（1 个）+ 1 条 tool 结果。所以**模型自己也永远不知道它那 N-1 个调用被吞了**——下一轮它看到的"历史"是自洽的，于是不会补发，也不会据此改策略。
- 代码里 8407 行的注释写着「实测 `/chat/tools` + `model=vllm:base` → `tool_calls=1` ✓」——这是**用单轮单调用的一次样本归纳出的假设**，本批数据证明该假设不成立（P1-01 一轮 4 个）。

**后果（按严重度）**
1. 用户看到的卡片少于模型真实动作 → 与 API 记录对不上（本次 P-20 现象）。
2. 被丢弃的调用若含**写操作**（P1-01 丢了 `write_sheet`/`run_doc_flow`；P1-03 丢了 2 次 `validate_workflow`），会让"是否动手"的判断整体失真。
3. 模型缺了 N-1 个工具结果，却以为一切正常 → 继续往下编（P1-01 最后直接手写 Python 假执行，见 §5）。
4. `agentTurnTools`（质量闸门，8517–8522 行用于判断"只说不做/只读查询"）也只记录执行过的那个 → **闸门判断同样失真**。

---

## 3. 根因 2/3/4（测量 bug）：旧 `ui_tools` 口径会漏、会脏、会误

评估跑手 `E:\WSL\_check\drill\run.mjs`：

| # | 位置 | 代码 | 后果 | 本次证据 |
|---|---|---|---|---|
| ② | `run.mjs:129` | `/✅\s*([a-z_][a-z0-9_]*)/g` **只数 ✅** | **❌ 失败卡全部不算** → "页面没工具卡" | P1-01：3 张 ❌ 卡 → `ui_tools=[]`；这是 lead 看到的"UI 卡 = []"的唯一来源 |
| ③ | `run.mjs:123` | `innerText().slice(-6000)` **保留尾部** | 长会话**丢掉最早**的卡 → 少算 | P1-13：msgs=5994（正好撞上限），开头是半截 JSON（`reated_at": ""`），最早的 `list_history` 卡被砍 |
| ④ | `run.mjs:129` | 名字后**无边界** | 耗时数字粘进名字；正文符号被当卡 | P1-20 得到 `list_available_datasets0`/`start_training0`（应为 `..._datasets`/`start_training`）；`ui_tools` 里还混进 `epoch`——它来自答案正文的列表符号 `❌ epoch 过多 → 过拟合` |
| — | `run.mjs:61-67` | `page.on('response', async r => { await r.json(); apiCalls.push(...) })` | 异步 push，**理论上可能与 126 行读取竞态**（本批未观察到净影响，22 条里 api 与卡片对账一致） | 仅作提示，非本次主因 |

> ②③④ 合起来正是 lead 观察到的"API 30% vs UI 25%"的全部差额来源——**其中没有一条是渲染失败**。逐条验证：arm1p 里"API 有工具但页面 0 卡片"的样本 = **0/20**；而真实有卡片的记录 = 6/20，与 API 6/20 **完全对齐**。

---

## 4. 结论修正：影响面比原判断小，但性质更明确

| 原判断 | 实测修正 |
|---|---|
| "UI 没渲染工具卡" | 渲染了。arm1p 20/20 条里，凡是执行过的调用都有卡；**0 条"执行了但没画"** |
| "P1-01 页面工具卡 = []" | 页面有 3 张 ❌ 卡（含完整错误原文）。`[]` 是 `ui_tools` 这个**指标**为空，不是 DOM 为空 |
| "用户以为它没动手 ✗" | P1-01 用户看得到 3 张失败卡。真实风险是 **卡片（失败）与正文（像在交付）矛盾** |
| "评估系统性低估 ✓" | **半对**：记录级"有没有动手"没有低估（6/20 = 6/20）；**动作数量**低估（18 次提出 vs 11 次执行，执行率 61%）；且 `api_tools` 是**"提出"**不是**"执行"**，直接拿它当"做了多少事"会**高估** |
| `ok:true` | P1-01 明确失败却 `ok=true`——`ok` 只表示"跑完没崩"，不能当任务成功判据 |

---

## 5. 附带的真实问题（不是渲染，但解释了"信任受损"）

P1-01 的 3 张卡全是 ❌（创建 workflow 配置连续 3 次被后端拒绝：文件名不合法 → 要 `workflow_config*.json` → 又不合法），**但最终答复转头手写了一段 Python 代码**（`def edit_paper(paper): ... tools.proofread(...)`），并以"运行上述代码后…请您自行运行代码查看"收尾——**正文读起来像交付，卡片写着全失败**。这才是用户"信任受损"的直接来源，属"编造执行/只说不做"类，与 P-20 的渲染无关，但有因果关联：如果第 1 轮那 3 个被丢弃的调用（`write_sheet`/`run_doc_flow`/`read_sheet`）真的执行了，模型可能就有真实结果可用，不必去编。

---

## 6. 修复建议（3 条，均未实施）

1. **【必须】`webui/index3.html:8420-8430`：把 `d.tool_calls[0]` 改成遍历 `d.tool_calls`。**
   对每个调用依次 `appendToolCard` → 执行 → 收集结果；并把**整批** `__native` 回填为一条 `assistant.tool_calls: [...]`、每个结果各一条 `role:'tool'`（`pushToolResult` 需支持数组），全部执行完再进下一轮。否则并行调用永远丢、模型永远拿不到结果、`agentTurnTools` 闸门永远失真。
2. **【必须】`_check/drill/run.mjs:129`：`ui_tools` 改为从 DOM 直读，不要用文本正则。**
   `page.$$eval('#chat-messages .agent-tool-card', els => els.map(e => ({ name: e.querySelector('.head span')?.textContent, status: e.classList.contains('error') ? 'error' : e.classList.contains('ok') ? 'ok' : 'running' })))`
   一次同时修掉 ②（漏 ❌）、③（截断）、④（脏名 + 正文误判）四类误差——DOM 是权威，不受 `innerText` 截断与正文符号影响。附带把记录字段从 `ui_tools` 扩成 `ui_cards:[{name,status}]`，成败可分别统计。
3. **【建议】`_check/drill/run.mjs:123`：`msgs` 去掉 `.slice(-6000)` 或改为"存全量长度 + 卡片数组 + 文案尾部"。**
   本次 P1-13 就因此无法判定（5994 字符撞上限）。若为控文件大小必须截断，也应**保留头部**或**单独存 `ui_cards`**，不能让"截断"变成"丢证据"。

---

## 7. 复现方式

```bash
# 1) 逐条对账（API 提出 / 页面真实卡片 / 旧口径 / 差额）
wsl.exe -d Ubuntu-24.04 -- /home/luxy/.venv/bin/python /mnt/e/WSL/_check/p20_analyze2.py
# 2) 回合数 × 卡片数 交叉验证
wsl.exe -d Ubuntu-24.04 -- /home/luxy/.venv/bin/python /mnt/e/WSL/_check/p20_cross.py
```

```bash
# 3) 原始记录只读抽查（P1-01 = 行 111）
sed -n '111p' /home/luxy/paper_lora_project/training_output/drill_part1_runs.jsonl
# 4) 代码位置
grep -n 'const _tc = d.tool_calls\[0\]' /home/luxy/paper_lora_project/webui/index3.html   # 8421
grep -n 'slice(-6000)\|matchAll(/✅' /mnt/e/WSL/_check/drill/run.mjs                      # 123 / 129
```

判据说明：卡片识别用 `(✅|❌)\s*(工具名)\s*(\d+\.\d+s)`（耗时锚定）；**不用**关键词 `✅ 名字`，因为答案正文里的 `✅/❌` 列表符号会造成误判（本次实测到 `epoch`）。

---

## 8. 口径边界（诚实声明）

1. 只有 **22/132** 条记录带 `api_tools`，`api` 与 `ui` 的对比**只对这 22 条成立**（lead 的 20 条在其中）。其余 110 条是旧跑手记录，不能用本次方法对账。
2. `api_tools` 是**"模型提出"**的调用（`run.mjs:61-67` 监听 `/chat/tools` 响应累加），**不等于执行**；`ui cards` 才是执行证据。二者不可互换。
3. P1-13 的差额因 `msgs` 截断而**不可判定**，本报告未把它计入"丢弃"（计入则 5→7）。
4. 第 3 类（`run.mjs:61-67` 异步竞态）只做理论提示，本批未观察到实际影响，不计入结论。
5. 本报告未运行任何会改动状态的操作；未重启服务、未改代码、未入库、未 commit。
