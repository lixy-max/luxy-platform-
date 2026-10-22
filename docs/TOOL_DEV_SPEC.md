# 工具开发规范（TOOL DEV SPEC）· P-85 · 2026-10-19

> **由来**：T4 暴露「KB 里写了工具、但它没暴露成 agent 工具」✗ ⇒ 模型从 KB 学到 → 去调 → **未知工具** ✗
> 实测：P1-45 一条任务里 **23 次未知工具** ✗ 全是同一个 `delete_knowledge_base` ✗
> 本规范 = **单一真源 + 5 处一致性检查** ✓ 保证不再发生 ✓

---

## 一、★ 单一真源原则

```
agent_kb/_tools.json  = 唯一真源 ✓（工具名 / 路由 / 参数 / 是否危险 ✓）

其余都是【派生】✗ 不允许手写 ✗：
  ① agent_kb/_tool_docs.json     ← 工具文档（描述/参数/示例/错误 ✓）
  ② agent_kb/tool_docs.md        ← KB 内容（供检索 ✓）必须只含【已暴露】的工具 ✗
  ③ webui/index3.html AGENT_TOOL_DOCS      ← 前端 schema 描述 ✓
  ④ webui/index3.html AGENT_TOOL_ENDPOINTS ← 通用分发器（HTTP 路由 ✓）
  ⑤ 本地 handler（AGENT_TOOLS.xxx = {...} ✓ 用于需要定制逻辑的工具 ✓）
```

**派生顺序**（改工具时按这个顺序 ✓）：
```
_tools.json → _tool_docs.json → tool_docs.md → 前端 DOCS/ENDPOINTS → 检查 ✓
```

---

## 二、新增一个工具的 3 步流程（★ 必须全做 ✓ 少一步就是 bug ✗）

### 第 1 步：后端路由（若有新能力 ✓）
```python
# lora_manager.py
@app.post("/your/tool/endpoint")
def your_tool(req: YourReq):
    ...
```

### 第 2 步：注册到真源 + 文档（一条命令 ✓）
```python
# ① 加进 _tools.json
tools['your_tool_name'] = {
    'dangerous': False,
    'args': [{'name': 'param1', 'type': 'string', 'required': True, 'desc': '参数说明 ✓'}],
    'endpoint': 'your/tool/endpoint',      # 不含前导 / ✓
    'method': 'POST',
}
# ② 加进 _tool_docs.json（描述要具体 ✓ 带触发词 ✓）
docs['your_tool_name'] = {
    'desc': '这个工具做什么（参数 x ✓ y 可选）· 用户说"…"时用',
    'params': [{'name': 'param1', 'type': 'string', 'desc': '说明 ✓', 'required': True}],
    'examples': [], 'errors': [], 'usage_count': 0, 'source': 'manual',
}
```

### 第 3 步：同步到前端 + 检查（两条命令 ✓）
```bash
python scripts/sync_tool_docs.py     # 把 docs 同步进 index3.html（DOCS + ENDPOINTS ✓）
python scripts/check_tools.py        # 5 项一致性检查 ✓ 必须全过 ✓
```

---

## 三、★ 5 项一致性检查（`scripts/check_tools.py` ✓ 每次改工具后必跑 ✓）

| # | 检查 | 不通过会怎样 |
|---|---|---|
| **C1** | 每个 `_tools.json` 工具都有文档 ✓ | 模型不知道参数 ✗ 乱传 ✗ |
| **C2** | 每个 `_tools.json` 工具都在前端 DOCS ✓ | schema 里没有 ✗ 模型调不到 ✗ |
| **C3** | 每个前端 DOCS 工具**都可执行** ✓（有 endpoint / handler / 文档工具 ✓） | 模型调了也失败 ✗ |
| **C4** | ★ **KB 里的工具必须都暴露** ✓ | **模型从 KB 学到 → 去调 → 未知工具 ✗✗**（P-84 根因 ✓） |
| **C5** | 暴露的工具都在 KB 里 ✓ | 检索不到 ⚠ 只能靠 schema ✓ 降级 ⚠ |

**自动修**：
```bash
python scripts/check_tools.py --fix-kb   # 自动从 KB 删除未暴露的工具 ✗
```

---

## 四、命名规范（★ 避免"幻影工具" ✗）

实测模型会**自己编工具名** ✗（`upload_file` / `mkdir` / `read_knowledge_base` / `clone_workflow_config` ✗）：
⇒ **命名必须遵循现有模式** ✓ 让模型能猜到 ✓

| 动词 | 用途 | 例 |
|---|---|---|
| **list_** | 列出现有 | `list_history` · `list_loras` · `list_knowledge_bases` |
| **get_** | 取详情 | `get_settings` · `get_workflow_result` · `get_training_status` |
| **create_** | 新建 | `create_knowledge_base` · `create_workflow_config` |
| **read_** | 读内容 | `read_upload` · `read_docx` · `read_sheet` |
| **write_** | 写文件 | `write_docx` · `write_sheet` |
| **run_** | 执行 | `run_workflow` · `run_eval` · `run_simulation` |
| **load_/unload_** | 挂/卸 | `load_lora` · `unload_lora` |
| **switch_** | 切换 | `switch_model` · `use_workflow_config` |

⚠ **不要用**：`upload_file` ✗（真名 `read_upload` 是读 ✓ 上传走浏览器 `browser_upload` ✓）
⚠ **不要用** `mkdir` / `ls` / `cat` 这类 **shell 命令名** ✗（不是我们的工具 ✗）

---

## 五、描述写法规范（★ 直接影响工具选择准确率 ✓）

### 好描述（✓ 51 chars 左右 · 带触发词 ✓）
```
"列出 uploads 目录里的文件（用户上传的文档/表格）· 用户问"我传的文件/上传区有什么"时先调它"
     ↑ 做什么                          ↑ 参数/范围        ↑ 什么场景该用（模型靠这个选对 ✓）
```

### 坏描述（✗ 太短 · 没有场景 ✗）
```
"读 Word 文档"    ← 9 字 ✗ 模型不知道什么时候该用 ✗
"内置工具：xxx"   ← 兜底文案 ✗ 最差 ✗
```

### 写法要点
1. **做什么**（一句话 ✓）
2. **参数**（主要参数点名 ✓ 可选参数标注 ✓）
3. **触发场景**（"用户说 X 时用" ✓ 这是模型选择的依据 ✓）
4. **注意事项**（危险 ✓ 会占 GPU ✓ 只读 ✓）
5. 长度 **30-60 字** ✓（太长浪费 token ⚠ 见 P-79 ✓）

---

## 六、提交前 checklist（★ 每次改工具必查 ✓）

```
☐ 第 1 步 后端路由（若有新能力 ✓）
☐ 第 2 步 _tools.json + _tool_docs.json 都加了 ✓
☐ 第 3 步 sync_tool_docs.py 跑过 ✓
☐ check_tools.py 5 项全过 ✓
☐ 页面能打开（无 JS 错误 ✓）
☐ 实际调一次新工具（成功 ✓）
☐ commit 说明里写了工具名 ✓
```

---

## 七、已知的"陷阱工具"（记录 ✓ 防止再犯）

| 工具 | 陷阱 | 处理 |
|---|---|---|
| `delete_knowledge_base` | 一度在 KB 里写了但没暴露 ✗ ⇒ 23 次未知工具 ✗ | ✓ 已暴露（dangerous=true ✓）+ 加 `rebuild_knowledge_base` ✓ |
| `polish_text` / `translate_text` / `summarize_text` / `proofread_text` | **别名 ✗** 真名是 `polish` / `translate` / `condense` / `proofread` ✓ | ✓ 已从 KB 删除别名 ✓ |
| `read_docx_file` / `write_docx_file` | 不存在 ✗（真名 `read_docx` / `write_docx` ✓） | ✓ 已删除 ✓ |
| `ingest_to_knowledge_base` | 不存在 ✗（入库走 `browser_upload` 或 KB 页 ✓） | ✓ 已删除 ✓ |
| `upload_file`（模型编的 ✗） | 命名误导 ✗ | ⚠ 记录 ✓ 只能靠描述里说清"上传走浏览器"✓ |

---

## 八、脚本清单

| 脚本 | 作用 |
|---|---|
| `scripts/check_tools.py` | **5 项一致性检查** ✓ + `--fix-kb` 自动修 ✓ |
| `scripts/sync_tool_docs.py` | 把 `_tool_docs.json` 同步进 `index3.html`（DOCS + ENDPOINTS ✓） |
| `scripts/gen_tool_docs.py` | 从真实轨迹生成/更新工具文档（含示例/错误 ✓） |
| `scripts/check_tool_docs.py` | 检查文档质量（描述长度/参数说明 ✓） |

---

*规范 · 2026-10-19 · P-85 · 依据：T4 实测（P-84「KB 写了没暴露」⇒ 23 次未知工具 ✗）+ [Toolshed 2410.14594](https://arxiv.org/abs/2410.14594)（工具文档增强 ⇒ 检索 +46~56% ✓）*
