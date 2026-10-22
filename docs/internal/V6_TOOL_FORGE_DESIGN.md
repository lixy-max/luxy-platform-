> **⚠ 诚实声明（2026-10-21）**: 本文是**最小工程原型**设计 ✗
> **没有实现** MetaAgent 的自动工具创建/验证 ✓
> 详见 `docs/METAAGENT_HONESTY_REVIEW.md`

# V6.0 工具草案助手（Tool Forge）设计（2026-10-21）

> **一句话**：让平台在遇到"缺工具"时，由 LLM 生成工具草案 → **静态安全四闸** → **人工审核** → 注册 → A/B 验证。
> **目标**：从"工具固定 91 个"变成"工具可生长" ✓
> **不追求全自动**（安全第一 ⚠ 文献 MetaAgent 是全自动 ✗ 我们保守）

---

## 一、为什么做（V6.0 在路线图的位置）

```
V1-V4: 本地算力调度 ✓ QLoRA 流水线 ✓ 统计评估 ✓ 数据飞轮（骨架 ✓ 闭环验证 ✓）
V5:    全参微调（未开始 ❌ 本机条件勉强 ⚠ 建议 7B+LoRA 替代）
V6:    **自造子代理 / 工具自我生长** ← 本文
```

**依据文献**：MetaAgent: Toward Self-Evolving Agent via Tool Meta-Learning
🔗 https://arxiv.org/abs/2508.00271
```
它的做法: agent 通过"工具元学习"自我演化 —— 缺工具 ⇒ 自动创建/组合 ⇒ 自测 ⇒ 注册 ✓
我们的做法: 同一思路 ✓ 但【半自动】✓ 多一道人工审核闸 ✗
```

---

## 二、五步流程

```
① scan   从轨迹找"缺工具"信号 ✓
   ↓
② draft  LLM 生成工具草案（**不注册** ✗）
   ↓
③ check  静态安全四闸（**不执行代码** ✗）
   ↓
④ approve **人工审核** ⇒ 注册 ✓（唯一注册入口 ✓）
   ↓
⑤ A/B    注册后必须验证新工具是否真有用 ✓ 没提升 ⇒ 撤销回滚 ✗
```

---

## 三、★ 安全四闸（核心设计 ✓）

### 闸 1：危险模式扫描 ✓
```
直接拒绝的模式:
  os.system / os.popen / subprocess          ← 任意命令执行 ✗
  eval() / exec() / __import__               ← 动态执行 ✗
  socket / urllib.request / pickle / ctypes  ← 网络与底层 ✗
  shutil.rmtree/move/copy                    ← 文件操作 ✗
  open(..., "w")                             ← 写文件 ✗
  import os / import sys                     ← 不允许导入 ✗
```

### 闸 2：import 白名单 ✓
```
只允许: json / re / requests / typing / datetime / collections / math ✓
（requests 允许是为了调后端自己的 API ✓ 不是任意外网 ✗）
```

### 闸 3：语法检查 ✓
```
ast.parse(code) ✓ —— **只解析不执行** ✗（exec 是禁区 ✗）
```

### 闸 4：结构检查 ✓
```
必须有 `def handler(...)` 或 `async def handler(...)` ✓
必须有 return ✓（返回 dict ✓ 不是字符串 ✗）
```

### ★ 强制人工审核 ✓
```
approve 是【唯一注册入口】✓
要求 status == 'checked' ✓（--force 才跳过 ⚠ 危险）
注册时自动备份 _tool_docs.json.bak_<ts> ✓ 可回滚 ✓
```

---

## 四、实现（`scripts/tool_forge.py` · 约 460 行）

| 命令 | 作用 | 实测 |
|---|---|---|
| `scan [--limit N]` | 从轨迹找缺工具信号 ✓ | ✅ **16 条** tool_error 信号 |
| `draft --need "..."` | LLM 生成草案（**不注册** ✗） | ✅ `query_docs_in_kb`（GET /api/query_docs · 9 行 handler）|
| `check --draft X` | 4 道静态闸 ✓ | ✅ 全过（危险 0 · import 白名单 ✓ · 语法 ✓ · 结构 ✓）|
| `list` | 草案列表 ✓ | ✅ 1 个（status=checked）|
| `approve --draft X` | 人工审核 ⇒ 注册 ✓ | ✅ 实装 |

### 产物结构
```
data/flywheel/forge/
  ├── signals.json                 ← scan 产出的"缺工具"信号
  └── drafts/<tool_name>.json      ← 草案（含 spec / checks / status ✓）
```

---

## 五、实测样例（`query_docs_in_kb`）

```json
{
  "name": "query_docs_in_kb",
  "endpoint": "/api/query_docs",
  "method": "GET",
  "description": "查询指定知识库中的文档数量及名称列表",
  "params": [{"name": "knowledge_base_name", "type": "string"}],
  "handler_code": "async def handler(args):\n    import requests\n    ...\n    return {...}"
}
```

**generate 出的 handler（人工审核视角 ✓）**：
```python
async def handler(args):
    import requests
    kb_name = args['knowledge_base_name']
    url = f'http://127.0.0.1:8001/kb/{kb_name}/docs'
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f'Failed to query docs in knowledge base {kb_name}')
    data = response.json()
    return {'document_count': len(data), 'documents': data}
```
**判断 ✓**：只有 `requests.get` ✓ 无危险调用 ✓ 有错误处理 ✓ 有 return dict ✓ **可以人工通过** ✓
**但**：`/kb/{name}/docs` 这个 endpoint **需要先存在** ✗（否则 404 ✗）⇒ 这是已知限制 ⚠

---

## 六、⚠ 已知限制（诚实 ✗）

```
✗ scan 的信号只到【工具名】级别 ✗
   trajectories 的 tool_errors 字段只有名字 ✓ 没有错误详情 ✗
   ⇒ 无法自动判断"缺什么参数/什么能力" ✗ 需人工看轨迹 ✓

✗ handler 只生成"调后端 API 的薄封装" ✓ 不支持复杂逻辑 ✗（安全考虑 ✓）
   ⇒ 新工具依赖后端先有对应 endpoint ✗（否则 404 ✗）

✗ **未做全自动** ⚠（文献 MetaAgent 是全自动 ✗ 我们保守 ✓）
   理由: 自动注册未审核的生成代码 = 安全风险 ✗ 已有代码执行能力 = RCE 级风险 ✗

✗ 未做"工具组合生成"（把多个现有工具组合成新工具 ✓ 这是 MetaAgent 的能力之一 ⚠）
```

---

## 七、下一步（按优先级）

| # | 内容 | 工时 | GPU |
|---|---|---|---|
| **1** | **`scan` 增强**：从失败轨迹里提取"想要的参数/能力"（不是只有工具名 ✓） | 1h | ✗ |
| **2** | **工具组合生成**：LLM 把 2-3 个现有工具编排成新工具 ✓ | 2h | ✗ |
| **3** | **注册后自动 A/B**：新工具加进评测，对比"有/无"成功率 ✓ | 1h | ⚠ 要 vLLM |
| **4** | **撤销机制**：注册后没提升 ⇒ 一键撤销 + 恢复 .bak ✓ | 0.5h | ✗ |
| **5** | **UI 页面**：草案列表 + 审核界面（现在只有 CLI ✓） | 2h | ✗ |

---

## 八、安全声明（必须写清楚 ✓）

```
本工具【不执行】生成的代码 ✗ 只做静态检查 ✓
真正执行生成代码的只有【注册后平台调用时】✓ 且那时已经过人工审核 ✓
★ 如果有人用 --force 跳过审核 ⇒ 风险自负 ⚠（默认不允许 ✗）
★ 生产环境建议：把生成的 handler 也纳入 code review + 单元测试 ✓
```

---

*2026-10-21 · V6.0 工具草案助手设计 · 灵感来源 MetaAgent（**未实现其核心** ✗） (arXiv 2508.00271) · **半自动 · 人工审核强制 · 静态四闸** ✓*
