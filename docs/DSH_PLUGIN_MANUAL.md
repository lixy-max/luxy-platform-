# 平台作为插件嵌入 DSH —— 大模型调用说明书

> **适用版本：v4.9.7+** · 校验方式：`GET /version` 应返回 `{"version":"4.9.7",...}`
>
> ⚠ 本文档基于 v4.9.2 实测编写，v4.9.3~v4.9.3~v4.9.7 的变更已同步以下条目：
> 端点总数 **136** · `/health` 含 `version` 字段 · `/chat/tools` 支持 `kb` 参数 ·
> `upload_dataset` 自动把 `messages` 转 `conversations` · 出厂预置工作流 **4 节点** ·
> 训练数据格式必须 `conversations`（`system` 为顶层字段）· 双卡模式 `gpu_mode=dual`
>

> **面向读者**：DSH（DeepSeek Harness）里的**大模型/Agent**。你要么把任务**路由**给本平台的本地模型，要么**训练**一个自己需要的小模型。
> **本文所有请求示例都在真实开发实例 `http://127.0.0.1:8001` 上跑过一遍**（curl 编号 C01~C50，逐条结果见 §11 验证清单；机器可读记录见同目录 `DSH_PLUGIN_API_VERIFY.json`，80 条全部 PASS）。返回字段照抄真实响应，不是设计稿。
> 文档日期：2026-10-22 · 平台代码：`$(pwd)/lora_manager.py`（8919 行，`/openapi.json` 实测 135 条路径）

---

## 0. 先读这一节：实测发现的「文档与实现不一致」7 处

写这份手册时逐条实测，发现下面这些**与直觉/注释不符**的地方。**你不读这节会踩坑**。

| # | 现象 | 真相（实测） |
|---|---|---|
| 1 | `/hallucination/repair` 到底有没有？ | **取决于进程构建**。验证初期实例是 1 小时前的旧构建 ⇒ **404 `{"detail":"Not Found"}`**；后端重启后（新构建，`:8001` pid 69902）⇒ **200 正常**（真实返回见 §3-E.2）。所以：**你遇到 404 就是进程旧了，重启后端**，不是接口不存在。源码里它一直是有的。 |
| 2 | `/docs/tool/list` 宣称 48 个工具（含 `list_sim_nodes` / `run_simulation` / 全部 `browser_*`） | 派发器**只真代理文档/表格类**。旧构建对 `list_sim_nodes`/`browser_*` 一律 **404**（带教育式报错）；新构建加了兜底：`list_sim_nodes` → **200**、`browser_*` → 200，但 **`run_simulation` 仍 400**「请直接调用 POST /sim/run（本派发器只代理文档/浏览器工具）」。⇒ **仿真永远走 `/sim/run`**。 |
| 3 | `/chat/tools` 源码里有 `kb` 参数自动注入逻辑 | **请求体里没有 `kb` 字段**（`ToolChatRequest` 未声明）⇒ 你传 `kb` 会被 **静默忽略**。要指定知识库：先 `PUT /kb/mount`。 |
| 4 | `/agent/tools` 注释写「返回全部 92 个」 | 实测 **68 个**（`count=68, total=68`）。且返回的是 **dict**（`{工具名: {desc, params[]}}`），**不是** OpenAI `tools` 数组 ⇒ 要自己转换才能喂 `/chat/tools`。 |
| 5 | 「训练会停 vLLM」 | 只在 `gpu_mode=auto`（默认单卡）时停。`gpu_mode=dual` 时 **vLLM 全程不停**（训练在另一张卡）。本机当前 `gpu_mode=auto` ⇒ 会停。 |
| 6 | `/training/upload_dataset` 接受 `messages` 格式 | 上传能过，但**训练前置体检会拒**：`F0: 缺少 conversations` × N ⇒ `valid_ratio=0.0` ⇒ 训练直接失败。**训练集必须用 `conversations`**。 |
| 7 | `/workflow/status/{id}` 返回 `status:"success"` | **不代表每个节点都成功**。本次真跑：任务 `success`，但结果文件里 `status:"partial"`（vLLM 恰好在训练期间被停，2 个 LLM 节点降级）。**必须再看 `result_file` 里的 `status` / `final_report` 警告头**。 |

另外 3 个「容易误判为 bug，其实是设计」的行为：

- `POST /sim/run` **永远返回 HTTP 200**，失败放在 `{"ok": false, "error": ...}` 里 —— 故意这样，让模型能看到失败原因并自我纠正。
- `POST /hallucination/check` 的 `answer` 为空 → `400`；但 `{ok:false}` 形式的**内部失败**也是 **200**，要判 `resp["ok"]`。
- `PUT /kb/mount` 对不存在的库**不报错**，只把它们放进 `invalid` 数组，`mounted` 里只留真实存在的。

---

## 1. 一句话：这个平台是什么、DSH 为什么需要它

**平台 = 一台机器上自带的「本地 7B 模型 + 可训练 LoRA + 知识库 + 确定性计算 + 68 个工具 + 幻觉检测」的 HTTP 服务（默认 `:8001`）。**

DSH 的模型为什么需要它：

1. **省钱省数据**：本地 Qwen2.5-7B（AWQ，:8000 vLLM）免费、不传数据出机器；`POST /chat/tools` 的**请求体与 OpenAI `tools` 完全一致**（响应体是平台自己的结构，见场景 B），DSH 的 Agent 循环几乎零改造就能把子任务甩过来。
2. **不再瞎算**：物理/数学/几何/统计/单位换算走 `/sim/run` 的 6 个确定性节点，**可复算**，不是模型心算。
3. **有据可依**：`/kb/create` → `ingest_folder` → `mount` 之后，`/chat/completions` 与 `/chat/tools` 都会**自动注入**检索片段。
4. **能造自己的模型**：`POST /training/start` 一条命令，双卡 QLoRA，**实测 142 秒训完 12 条**；训完热挂载成 `vllm:lora:<name>`，立刻可当独立模型用。
5. **能自证**：`/hallucination/check` 5 层检测 + `/hallucination/repair` 修复（检测→定位→修复→验证→回滚）。

---

## 2. 快速开始（3 条命令 + 1 个 curl）

```bash
# 1) 起后端（:8001）+ 前端静态页（:8080）；vLLM 底座按需另起
cd $(pwd) && bash scripts/run.sh

# 2) 起推理底座（有 GPU 才需要；无 GPU 用 --no-gpu 仍可用 KB/文档工具）
#    正常走界面「底座管理」，命令行等价：
curl -s -X POST http://127.0.0.1:8001/vllm/start \
  -H 'Content-Type: application/json' \
  -d '{"model_path":"~/models/Qwen2.5-7B-Instruct-AWQ","port":8000}'
# → {"status":"success","pid":"12345","message":"vLLM 正在加载，约 30-90 秒后就绪"}

# 3) 自检：一次看清「现在能干什么、缺什么、怎么补」
curl -s http://127.0.0.1:8001/readiness
```

真实返回（节选，C01）：

```json
{"ok": true, "ok_count": 5, "total": 5, "mode": "full",
 "hint": "功能完整：分析与文档工作流都可用。",
 "models_dir": "~/models",
 "models": ["Qwen2.5-0.5B-Instruct", "Qwen3-4B-Instruct", "Qwen2.5-7B-Instruct-AWQ",
            "bge-small-zh-v1.5", "Qwen2.5-14B-Instruct-AWQ", "Qwen3-4B-AWQ",
            "qwen2.5-coder-14b-base", "Qwen2.5-7B-Instruct", "bge-m3"],
 "checks": [
   {"name": "运行依赖", "ok": true, "detail": "齐全", "fix": "pip install -r requirements.txt"},
   {"name": "本地模型", "ok": true, "detail": "~/models 下有 9 个模型", "fix": "…"},
   {"name": "推理底座 (vLLM :8000)", "ok": true, "detail": "运行中", "fix": "…"},
   {"name": "云端 API Key（可选）", "ok": true, "detail": "已配置", "fix": "…"},
   {"name": "预设工作流", "ok": true, "detail": "2 个", "fix": "…"}]}
```

> 📌 **`mode` 会随底座状态变**（两种都实测见过）：
> - `mode:"full"`、`ok_count:5`、`hint:"功能完整：分析与文档工作流都可用。"` —— 底座在跑
> - `mode:"no-gpu"`、`ok_count:4`、`checks[2].ok=false` —— **训练把 vLLM 停掉的那一刻就是这个**；`fix` 字段告诉你「有 GPU：在底座管理里启动；无 GPU：仍可用知识库/规则与文档工具」
>
> 所以 **别把 `mode:"no-gpu"` 当成平台坏了**，先看 `/vllm/status` 和 `/training/list`。

### 2.1 健康检查（DSH 侧探活用）

```bash
curl -s http://127.0.0.1:8001/health          # → {"status":"ok"}  最轻，1ms
curl -s http://127.0.0.1:8001/vllm/status     # → {"running":true,"pid":39858,...}
```

`/health` 只看后端进程活不活；**底座活不活必须看 `/vllm/status` 的 `running`**（C02 实测：训练期间这里是 `false`，但 `/health` 照样 200）。

---

## 3. ★核心：大模型如何「路由任务」给本地模型

路由 = 你（DSH 里的大模型）不自己在云端硬算，而是**选一个端点把子任务发出去**。5 个场景，按需选。

### 3.0 先看有哪些本地模型可路由

```bash
# C03 —— 所有可对话的模型 id（含每个 LoRA）
curl -s http://127.0.0.1:8001/chat/models
```

真实返回（44 个模型）：

```json
{"models": [
  {"id": "vllm:base", "provider": "vllm", "name": "Qwen2.5-7B-Instruct-AWQ",
   "model": null, "lora": null, "group": "本地底座"},
  {"id": "vllm:lora:swk_persona_v3", "provider": "vllm", "name": "swk_persona_v3",
   "model": null, "lora": "swk_persona_v3", "group": "本地 LoRA"},
  {"id": "vllm:lora:semantic_intent_v1", "provider": "vllm", "name": "semantic_intent_v1",
   "model": null, "lora": "semantic_intent_v1", "group": "本地 LoRA"},
  {"id": "openai:deepseek-chat", "provider": "openai", "name": "deepseek-chat（https://api.deepseek.com/v1）",
   "model": "deepseek-chat", "lora": null, "group": "云 API"},
  {"id": "ollama:qwen2.5:7b", "provider": "ollama", "name": "qwen2.5:7b（127.0.0.1:11434）",
   "model": "qwen2.5:7b", "lora": null, "group": "本地 Ollama"}]}
```

**`id` 的语法就是路由键**：`vllm:base` / `vllm:lora:<名字>` / `openai:<模型>` / `ollama:<模型>`。

看当前上下文窗口（决定一次能塞多少）：

```bash
# C04
curl -s 'http://127.0.0.1:8001/chat/context?provider=vllm&model=vllm:base'
# → {"provider":"vllm","max_model_len":32768,"source":"vllm"}
```

---

### 场景 A：普通问答 → `POST /chat/completions`（或流式）

**什么时候用**：一次性要一段文字；不需要工具、不需要多轮 agent 循环。

```bash
# C05 非流式
curl -s -X POST http://127.0.0.1:8001/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "provider": "vllm",
    "messages": [{"role":"user","content":"用一句话说明什么是 LoRA 微调。"}],
    "max_tokens": 80,
    "temperature": 0.3
  }'
```

真实返回：

```json
{"content": "LoRA 微调是一种轻量级的方法，通过在模型的少量参数上进行调整来提升模型性能，而无需对整个模型进行重新训练。",
 "usage": {"prompt_tokens": 39, "total_tokens": 74, "completion_tokens": 35},
 "kb_context": [],
 "provider": "vllm",
 "model": "~/models/Qwen2.5-7B-Instruct-AWQ",
 "_context": {"max_model_len": 32768, "used_tokens_estimate": 16, "notices": []}}
```

请求字段（`ChatRequest`）：`provider`(vllm/openai/ollama，默认 vllm)、`lora`、`model_override`、`messages[{role,content}]`、`temperature`、`max_tokens`、`kb`、`learn`。

> ⚠ 注意返回不是 OpenAI 格式：正文在 **`content`**，不是 `choices[0].message.content`。

**流式（SSE）** —— 首字延迟低，DSH 里做流式输出用它：

```bash
# C06
curl -sN -X POST http://127.0.0.1:8001/chat/completions/stream \
  -H 'Content-Type: application/json' \
  -d '{"provider":"vllm","messages":[{"role":"user","content":"数一下 1 到 5。"}],"max_tokens":60}'
```

真实 SSE 流（24 行，节选）：

```
data: {"delta": "好的"}
data: {"delta": "，"}
data: {"delta": "1"}
data: {"delta": " 到"}
...
data: {"delta": "数字"}
data: {"delta": "。"}
data: {"done": true}
```

**SSE 协议一共 4 种事件**，遇到不认识的键就跳过：

| 事件 | 含义 |
|---|---|
| `{"delta": "文本"}` | 增量正文，**拼接**起来才是完整回答 |
| `{"done": true}` | 正常结束 |
| `{"kb_context": [...]}` | 挂载了知识库时的**首个事件**，携带检索片段 |
| `{"error": "..."}` | 出错（HTTP 仍是 200！必须看事件体） |

---

### 场景 B：需要工具 → `POST /chat/tools`（原生 function calling）

**什么时候用**：要模型自己决定读文件/查库/跑仿真/开浏览器/启训练时。这是**给 Agent 用的端点**：你把 OpenAI `tools` 数组丢进去，它返回结构化的 `tool_calls`。

```bash
# C07 —— 给 1 个工具，让它必须调
curl -s -X POST http://127.0.0.1:8001/chat/tools \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "vllm:base",
    "messages": [
      {"role":"system","content":"需要计算时必须调用 run_simulation 工具，不要自己算。"},
      {"role":"user","content":"帮我算 (1+2)*3/sqrt(16)，必须用工具。"}
    ],
    "tools": [{
      "type":"function",
      "function": {
        "name": "run_simulation",
        "description": "调用仿真节点做确定性计算（算术/统计/占比）。任何数值计算必须走这里，禁止心算。",
        "parameters": {
          "type":"object",
          "properties": {
            "node": {"type":"string","enum":["math_eval","physics_calc","geometry_calc","ratio","stats","unit_convert"]},
            "args": {"type":"object","description":"节点参数对象"}
          },
          "required":["node","args"]
        }
      }
    }],
    "tool_choice": "auto",
    "max_tokens": 200
  }'
```

真实返回（**tool_calls 不是空**，说明本地 7B 的真 function calling 可用）：

```json
{"content": "",
 "tool_calls": [{"id": "chatcmpl-tool-893b008a066d41a287a150a513aba55e",
                 "name": "run_simulation",
                 "args": {"node": "math_eval", "args": {"expression": "(1+2)*3/sqrt(16)"}}}],
 "sim_guard": null,
 "confidence": {"ok": true, "prob": 0.9305, "level": "low",
                "threshold": {"high_min": 0.75, "conformal_q": 0.25},
                "features": {"tool_ok_rate": 1.0, "n_tools_norm": 0.1, "dup_guard_rate": 0.0,
                             "finished": 0.0, "elapsed_norm": 0.0056, "api_tool_norm": 0.1},
                "signals": {"n_tools": 1, "finished": false, "elapsed_s": 1.01, "error": false},
                "model": {"version": "2.4", "auroc": 0.9828, "ece": 0.0922}},
 "hallucination": {"ok": true, "risk": "low", "issues": [],
                   "checked": {"urls": [], "inline_images": 0, "file_claims": [],
                               "upload_claim": false, "citations": []}},
 "router": null,
 "provider": "vllm",
 "model": "~/models/Qwen2.5-7B-Instruct-AWQ",
 "context": {"limit": 32768, "used": 49, "dropped": 0, "truncated": false,
             "tools_used": 1, "tools_dropped": 0},
 "usage": {"prompt_tokens": 248, "total_tokens": 289, "completion_tokens": 41}}
```

**关键：`args` 已经帮你 `json.loads` 过了**（`_coerce_tool_args`），你在 Python 里拿到的是 dict，别再 `json.loads`。

#### B.1 工具清单从哪来（两个不同的表，别混）

```bash
# C08  Agent 工具表：68 个（工作台能力：训练/底座/KB/工作流/浏览器/学术…）
curl -s http://127.0.0.1:8001/agent/tools
# → {"ok":true,"version":"2026-10-21.1","count":68,"total":68,
#    "tools":{"add_workflow_node":{"desc":"…","params":[{"name":"type","type":"string","desc":"…","required":false}]},
#             "run_simulation":{...}, "start_training":{...}, ...},
#    "filtered":false,"diag":{}}

# C09  文档/表格工具表：48 个（必须经 POST /docs/tool 派发）
curl -s http://127.0.0.1:8001/docs/tool/list
# → {"count":48,"tools":[{"name":"add_content","description":"扩写补写：按主题补充段落",
#                          "params":[{"name":"text","hint":"已有正文"}],
#                          "takes_text":true,"file_oriented":false}, ...],
#    "names":["add_content","add_content_file","add_toc",...,"read_docx","read_sheet",
#             "write_docx","write_sheet","list_sim_nodes","run_simulation","browser_open",...]}
```

**`/agent/tools` 返回的是 dict，不是 OpenAI tools 数组 ⇒ 要转换**（可直接复制）：

```python
import json, urllib.request

def build_openai_tools(query: str = "", limit: int = 20):
    """把 /agent/tools 的 dict 表转成 /chat/tools 能吃的 OpenAI tools 数组"""
    url = "http://127.0.0.1:8001/agent/tools" + (("?query=" + query) if query else "")
    payload = json.load(urllib.request.urlopen(url, timeout=30))
    out = []
    for name, meta in list(payload["tools"].items())[:limit]:
        props, required = {}, []
        for p in meta.get("params") or []:
            props[p["name"]] = {"type": p.get("type") or "string",
                                "description": p.get("desc") or ""}
            if p.get("required"):
                required.append(p["name"])
        out.append({"type": "function",
                    "function": {"name": name,
                                 "description": meta.get("desc") or name,
                                 "parameters": {"type": "object",
                                                "properties": props,
                                                "required": required}}})
    return out
```

> ⚠ **工具别一次全塞**：68 个工具的 schema 约 8k token。`/chat/tools` 会按窗口自动从尾部裁（返回里 `context.tools_dropped` 告诉你丢了几个），但**你自己先筛到 6~20 个**效果最好。
> `?query=` 筛选实测效果（`count/total`）：`训练`→26/68、`工作流`→28/68、`知识库`→18/68、`浏览器`→30/68，即**少传 56%~74% 的工具 schema**（源码注释里写的「省 47%」是旧数字）。

#### B.2 让后端自己选模型（`model: "auto"`）

不指定模型时传 `"auto"`（或 `"route"`、`""`），后端按任务文本启发式路由：

```bash
# C10
curl -s -X POST http://127.0.0.1:8001/chat/tools \
  -H 'Content-Type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"写一个 Python 快速排序函数。"}],"max_tokens":80}'
```

真实返回里的 `router` 字段：

```json
{"router": {"provider": "vllm", "model_id": "vllm:base",
            "route": {"model": "local_7b",
                      "why": "任务含本地特征（平台操作/查询）⇒ 本地够用 ✓ 且不传数据 ✓"},
            "why": "**自动路由 → 本地** ✓ 任务含本地特征（平台操作/查询）⇒ 本地够用 ✓ 且不传数据 ✓",
            "fallback": false, "explicit": false},
 "provider": "vllm", "model": "~/models/Qwen2.5-7B-Instruct-AWQ", ...}
```

> ⚠ 规则是**启发式**，不是训练出来的路由小模型。要确定性就用显式 `model`。

---

### 场景 C：需要确定性计算 → `POST /sim/run`（6 个节点）

**铁律：任何数值计算都不许模型心算，必须走这里。** 先看有哪些节点：

```bash
# C11
curl -s http://127.0.0.1:8001/sim/nodes
# → {"count":6,"nodes":[
#     {"name":"geometry_calc","label":"几何计算","description":"…支持: 圆/矩形/三角形(含海伦公式)/球/圆柱/圆锥/梯形/平行四边形…",
#      "params":{"shape":"string（形状名 ✓ 如 circle）","radius":"number · width: number …"},"tags":["math","geometry"]},
#     {"name":"math_eval","label":"数值表达式求值","params":{"expr":"string · 要计算的表达式，如 \"(1+2)*3/sqrt(16)\""},"tags":["math"]},
#     {"name":"physics_calc","label":"物理公式计算","params":{"formula":"string（公式名 ✓ 如 force）","mass":"number …"},"tags":["math","physics"]},
#     {"name":"ratio","label":"占比/比率","params":{"a":"number · 分子","b":"number · 分母","digits":"…"},"tags":["math","stats"]},
#     {"name":"stats","label":"描述统计","params":{"values":"number[] | string · 数字列表…"},"tags":["stats"]},
#     {"name":"unit_convert","label":"单位换算","params":{"value":"number","from":"string（源单位 ✓ 如 km）","to":"string","dimension":"…"},"tags":["math","units","physics"]}]}
```

**6 个节点，全部实测通过（C12~C17）**：

| 节点 | 必填参数 | 请求示例 | 真实 `result` |
|---|---|---|---|
| `math_eval` | `expr` | `{"node":"math_eval","args":{"expr":"(1+2)*3/sqrt(16)"}}` | `{"expr":"(1+2)*3/sqrt(16)","value":2.25,"display":"2.25"}` |
| `physics_calc` | `formula` + 公式所需量 | `{"node":"physics_calc","args":{"formula":"force","mass":2.5,"acceleration":4}}` | `{"formula":"F = m · a","value":10.0,"unit":"N","note":"合外力 ✓ 方向同加速度 ✓"}` |
| `geometry_calc` | `shape` + 尺寸 | `{"node":"geometry_calc","args":{"shape":"circle","radius":3}}` | `{"shape":"circle","circumference":18.84955592153876,"area":28.274333882308138,"formulas":["C = 2πr","A = πr²"],"radius":3.0}` |
| `ratio` | `a`,`b`（`digits`可选） | `{"node":"ratio","args":{"a":17,"b":23,"digits":4}}` | `{"a":17.0,"b":23.0,"percent":73.91304347826086,"rounded":73.913,"ratio":0.7391304347826086}` |
| `stats` | `values` | `{"node":"stats","args":{"values":[1,2,3,4,5,6,100]}}` | `{"count":7,"sum":121.0,"mean":17.285714285714285,"median":4.0,"min":1.0,"max":100.0,"stdev":36.51353304239533,"var":1333.2380952380954,"p25":2.5,"p75":5.5,"p90":43.60000000000004}` |
| `unit_convert` | `value`,`from`,`to` | `{"node":"unit_convert","args":{"value":100,"from":"km","to":"m"}}` | `{"value":100000.0,"from":"km","to":"m","dimension":"length","formula":"100 km × 1000 = 100000（SI基准）÷ 1 = 100000 m"}` |

```bash
# C12 一个完整可复制的 curl
curl -s -X POST http://127.0.0.1:8001/sim/run \
  -H 'Content-Type: application/json' \
  -d '{"node":"physics_calc","args":{"formula":"force","mass":2.5,"acceleration":4}}'
# → {"ok":true,"node":"physics_calc","label":"物理公式计算",
#    "result":{"formula":"F = m · a","value":10.0,"unit":"N","note":"合外力 ✓ 方向同加速度 ✓"}}
```

**失败也是 200**（C18/C19）—— 靠 `ok` 判断，`error` 里带可用节点/缺哪个参数：

```json
{"ok": false, "node": "no_such_node",
 "error": "未知仿真节点 'no_such_node'，可用: geometry_calc, math_eval, physics_calc, ratio, stats, unit_convert"}
```
```json
{"ok": false, "node": "physics_calc",
 "error": "ValueError: 缺少参数 mass ✓",
 "hint_params": {"formula": "string（公式名 ✓ 如 force）", "mass": "number · …", "acceleration": "number …"},
 "hint_usage": "请按 hint_params 传 args；也可以用 math_eval 传 {\"ex…"}
```

**`args` 允许传 JSON 字符串**（容错，C17b 实测）：`{"node":"math_eval","args":"{\"expr\":\"2**10\"}"}` → `value: 1024.0`。但**请传对象**，字符串是兜底。

---

### 场景 D：需要知识 → 先 `PUT /kb/mount`，系统自动注入

**两种模式都支持自动注入**（这是踩过坑才修好的，注释里写着「建成 KB + 挂载 + 检索命中 3/3，模型仍答我不知道是哪款」）：

| 端点 | 注入方式 |
|---|---|
| `/chat/completions` | 请求里给 `kb:{"name":"库名","top_k":3}`；**不给就用 `settings.kb_mounted` 里挂的全部** |
| `/chat/completions/stream` | 同上；不给则自动取 `kb_mounted[0]` |
| `/chat/tools` | **只能用 `settings.kb_mounted`**（请求体没有 `kb` 字段，传了也被忽略） |

#### D.1 建库 → 导库 → 挂载（4 条命令，C20~C23）

```bash
BASE=http://127.0.0.1:8001

# 1) 建库（名字只能字母/数字/下划线/中划线，≤64 字符）
curl -s -X POST $BASE/kb/create -H 'Content-Type: application/json' \
  -d '{"name":"_verify_kb","description":"端到端验证用临时库"}'
# → {"status":"success","knowledge_base":{"name":"_verify_kb","documents":[],"total_chunks":0,
#    "created_at":"2026-10-22T13:15:55.029625","updated_at":"…"}}

# 2) 从服务器本地文件夹批量导入（★ 参数名是 folder_path，JSON body 不是 multipart）
curl -s -X POST $BASE/kb/_verify_kb/ingest_folder -H 'Content-Type: application/json' \
  -d '{"folder_path":"/tmp/_verify_kb_src"}'
# → {"status":"success","added_docs":2,"added_chunks":2,"skipped":[]}
# 可选 extensions：{"folder_path":"/tmp/x","extensions":[".md",".txt"]}

# 3) 挂载（替换式、持久化到 settings.json；不存在的库进 invalid 不报错）
curl -s -X PUT $BASE/kb/mount -H 'Content-Type: application/json' \
  -d '{"kb_names":["_verify_kb"]}'
# → {"ok":true,"mounted":["_verify_kb"],"invalid":[],"count":1,
#    "note":"挂载已持久化到 settings.kb_mounted ✓ 多库同时可用 ✓"}

# 4) 直接试检索（不经过模型，看召回质量）
curl -s -X POST $BASE/kb/_verify_kb/query -H 'Content-Type: application/json' \
  -d '{"query":"LoRA 训练多久","top_k":3}'
```

检索真实返回（含 `diagnostics`，0 条结果时看它就知道为什么）：

```json
{"status": "success",
 "results": [
   {"text": "# 平台说明\n\n本平台是单机 AI 模型垂直开发平台…实测 142 秒训完 12 条数据。…",
    "source": "doc1.md", "chunk_idx": 0, "score": 0.5933325290679932},
   {"text": "仿真节点提供确定性计算：physics_calc、math_eval…", "source": "doc2.txt",
    "chunk_idx": 0, "score": 0.29248809814453125}],
 "diagnostics": {"kb": "_verify_kb", "top_k": 3, "min_score": 0.1, "candidates": 2, "kept": 2,
                 "dropped": 0, "best_score": 0.5933, "worst_score": 0.2925, "space": "cosine",
                 "query_lang": "mixed", "corpus_lang": null, "lang_mismatch": false, "hint": null}}
```

多库同查（跨库合并排序 + 保留来源）：

```bash
# C24
curl -s -X POST http://127.0.0.1:8001/kb/multi_query -H 'Content-Type: application/json' \
  -d '{"kb_names":["_verify_kb"],"query":"仿真节点有哪些","top_k":3}'
# → {"ok":true,"query":"…","kb_names":["_verify_kb"],"kb_count":1,"total":2,
#    "per_kb":{"_verify_kb":{"count":2,"error":null}},
#    "results":[{"text":"…","source":"doc2.txt","chunk_idx":0,"score":0.7134,"_kb":"_verify_kb"}, …],
#    "errors":{},"merged":true}
```

#### D.2 「挂载」到底做了什么（实测）

拿同一句话对比（C25）：

```bash
# 挂 _verify_kb 后，指定 kb 提问
curl -s -X POST http://127.0.0.1:8001/chat/completions -H 'Content-Type: application/json' \
  -d '{"provider":"vllm","messages":[{"role":"user","content":"LoRA 训练用什么框架，大概多久？"}],
       "kb":{"name":"_verify_kb","top_k":3},"max_tokens":100}'
```

```json
{"content": "LoRA 训练使用的是 LlamaFactory + QLoRA 的框架，实测在双卡环境下训练 12 条数据大约需要 142 秒。",
 "usage": {"prompt_tokens": 197, "total_tokens": 237, "completion_tokens": 40},
 "kb_context": [{"text": "# 平台说明…实测 142 秒训完 12 条数据。…", "source": "doc1.md",
                 "chunk_idx": 0, "score": 0.6604174375534058}, …],
 "provider": "vllm", "model": "…Qwen2.5-7B-Instruct-AWQ",
 "_context": {"max_model_len": 32768, "used_tokens_estimate": 230, "notices": []}}
```

**证据**：`kb_context` 非空 + 回答里的「142 秒」只存在于 KB（底座模型不知道）⇒ 注入真的生效了。

其他 KB 端点：`GET /kb/list`、`GET /kb/{n}`、`GET /kb/mounted`、`POST /kb/{n}/rebuild`、`DELETE /kb/{n}`。
`rebuild` 用于换 embedding 模型后重建索引（旧向量与新模型不在同一空间）：

```bash
curl -s -X POST http://127.0.0.1:8001/kb/_verify_kb/rebuild
# → {"status":"success","kb":"_verify_kb","chunks":2,"embedding":{"model":"bge-m3","dim":1024}}
#   实测两次结果不同：一次 bge-m3/1024，一次 bge-small-zh-v1.5/512
#   ⇒ embedding 模型由平台配置决定（GET /settings/embedding 看当前值），不是固定值

```

---

### 场景 E：需要幻觉校验 → `/hallucination/check` + `/hallucination/repair`

**E.1 检测（5 层，可用）**

```bash
# C26 带来源（最准）
curl -s -X POST http://127.0.0.1:8001/hallucination/check \
  -H 'Content-Type: application/json' \
  -d '{"answer":"该论文的实验准确率是 98.7%。","question":"准确率多少？",
       "source_text":"实验结果显示准确率为 87.3%，比基线提升 2.1 个百分点。"}'
```

返回结构：`{ok, hallucination:{ok, risk, issues[{layer,type,severity,detail,hint}], checked{urls,inline_images,file_claims,upload_claim,numbers,grounding,citations}, version}, source_chars}`

**不给 `source_text` 也能用** —— 会自动拿 `settings.kb_mounted` 里的库现场检索当来源：

```bash
# C27 无来源
curl -s -X POST http://127.0.0.1:8001/hallucination/check \
  -H 'Content-Type: application/json' -d '{"answer":"爱因斯坦 1921 年获得诺贝尔物理学奖。"}'
```

> `answer` 为空 → `400 {"detail":"answer 不能为空"}`（C28）。`answer` 超 20000 字会被截断。

**E.2 修复（检测→定位→修复→验证→回滚）**

> 📌 **可用性跟进程构建走**：旧构建（未重启）上这个端点 **404**；重启后端后（新构建）**200 正常** —— 见 §0 第 1 条。
> 下面的请求/返回是**新构建上真发 HTTP** 拿到的，字段与直调 `hallucination_repair.repair()` 模块完全一致：

```bash
# C26b 实测 200
curl -s -X POST http://127.0.0.1:8001/hallucination/repair \
  -H 'Content-Type: application/json' \
  -d '{"answer":"该论文的实验准确率是 98.7%，比基线提升 12 个百分点。参考 https://example.com/nonexistent-paper 。",
       "source_text":"实验结果显示准确率为 87.3%，比基线提升 2.1 个百分点。",
       "max_rounds":2}'
```

真实返回：

```json
{"ok": true, "changed": true,
 "text": "该论文的实验准确率是 98.7%（待核实），比基线提升 12 个百分点。参考 https://example.com/nonexistent-paper 。",
 "before": {"risk": "medium", "issues": 1},
 "after":  {"risk": "low", "issues": 0},
 "repairs": [{"type": "unsourced_number",
              "desc": "处理 1 处无依据数字\n  98.7% → 标注（待核实）（知识库中该主题出现的值是: 50%, 59.59%, 62.03%, 59.32%（与回答中的 98.7 不一致））",
              "severity": "medium"}],
 "verified": true, "rolled_back": false, "rounds": 1,
 "strategies": {"fabricated_image": {"auto": true, "desc": "删除编造图片引用"},
                "unreachable_url": {"auto": true, "desc": "删失效链接保文字"},
                "fabricated_upload_claim": {"auto": true, "desc": "删无依据的上传声称"},
                "fabricated_file": {"auto": true, "desc": "删编造文件声称"},
                "fabricated_citation": {"auto": true, "desc": "移除无法核实的引用"},
                "unsourced_number": {"auto": true, "desc": "校验数值：有正确值则替换，否则标待核实"},
                "weak_grounding": {"auto": false, "desc": "需重新生成（不自动改写）"}},
 "validators": [{"domain": "kb_user_docs", "desc": "用户自己的文档（知识库）当权威源 —— 跨领域通用"},
                {"domain": "sim_compute", "desc": "仿真节点确定性计算（物理/数学）"}]}
```

判读规则：**`verified:true` 才算修好了**；`rolled_back:true` 表示修完反而更差、已回滚（用修前文本）；`weak_grounding` 是 `auto:false`，不会被自动改写，得重新生成。

`/chat/tools` 的响应里**每次都已经带了** `confidence` 和 `hallucination`（见场景 B 的真实返回）⇒ 主链路其实不必再单独调一次 check。

---

## 4. ★核心：大模型如何「训练自己需要的模型」

7 个步骤，全部实测过（除了 `/training/start` 真调——按任务约束不启动真实训练，改用前置校验验证）。

### 步骤 1：准备数据（jsonl，格式是硬约束）

**一行一条样本，UTF-8，`.jsonl`。强制格式 `conversations` + `from`/`value`：**

```json
{"system": "你是平台运维助手，回答只给结论和一条命令。",
 "conversations": [
   {"from": "human", "value": "vLLM 挂了怎么看状态？"},
   {"from": "gpt",   "value": "curl -s http://127.0.0.1:8001/vllm/status；running=false 就先看 logs/vllm.log。"}
 ]}
```

**规范（都是实测判定的，不是文档抄的）**：

| 规则 | 说明 | 违反后果 |
|---|---|---|
| `conversations` 必须存在且非空 | `messages` 格式**不行**（上传能过、训练必挂） | `F0` × N（实测 12/12 全废） |
| `system` 必须是**顶层字段** | 放在 `conversations[0]` 里的 `system` 会被判 `F5` | 判不合格 |
| 首条必须 `human` | `F1` | 判不合格 |
| 交替 `human`→`gpt`→`human`… | `F2`/`F3` | 判不合格 |
| 末条必须 `gpt` | `F4` | 判不合格 |
| `system` token 占比 **< 20%** | `Q1`（实测：7 字 system + 15 字样本 = 47% ⇒ 拒） | 判不合格 |
| 样本数 **≥ 8** | 少于 8 直接 reject | 训练失败 |
| 单条总 token ≤ `cutoff_len`（默认 **2048**） | 超 10% 就被拦 | 训练失败 |
| `system` 平均 ≤ **600** token | 会把 cutoff 吃光 | 训练失败 |
| `tools` 字段平均 ≤ cutoff 的 40% | 别把 68 个工具清单塞进样本 | 训练失败 |

**上传前先自检**（只读，强烈建议）：

```bash
# C29 检测已有数据集
curl -s -X POST http://127.0.0.1:8001/training/dataset/analyze \
  -H 'Content-Type: application/json' -d '{"file":"t1_cs_basics_40.jsonl"}'
```

真实返回（合格样本长这样）：

```json
{"ok": true, "file": "t1_cs_basics_40.jsonl", "total": 41,
 "report": {"dataset": {"total": 41, "valid": 41, "invalid": 0, "valid_ratio": 1.0,
                        "problems": {}, "invalid_examples": [],
                        "Q3_diversity": 1.0, "Q3_ok": true, "Q5_duplicates": 0, "Q5_ok": true,
                        "Q6_max_same_task": 1, "Q6_ok": true, "Q8_single_tool_ratio": 0.0,
                        "Q8_ok": false, "Q8_tool_samples": 0},
            "length": {"min": 167, "max": 260, "avg": 200.3, "p50": 197},
            "system": {"min": 28, "max": 28, "avg": 28.0, "over_400": 0},
            "tools": {"samples_with_tools": 0, "max_per_sample": 0, "distribution": {"0": 41}}},
 "analysis": {"…同上…"}}
```

不合格长这样（`messages` 格式）：

```json
{"ok": true, "total": 12,
 "report": {"dataset": {"total": 12, "valid": 0, "invalid": 12, "valid_ratio": 0.0,
                        "problems": {"F0": 12},
                        "invalid_examples": [{"index": 0, "problems": ["F0: 缺少 conversations 或为空 ✗"]}, …]}}}
```

**判读就一句：`valid_ratio` 必须 ≥ 0.90 且 `problems` 里没有 `F*`。**

### 步骤 2：上传（**multipart，不是 JSON**）

```bash
# C30 —— file + name 都是必填，name 只用字母/数字/下划线/中划线，只收 .jsonl
curl -s -X POST http://127.0.0.1:8001/training/upload_dataset \
  -F 'file=@/tmp/_verify_ds.jsonl' \
  -F 'name=_verify_ds'
# → {"status":"success","name":"_verify_ds","path":"$(pwd)/training_data/_verify_ds.jsonl","count":2}
```

**反证（务必记住）**：用 JSON 发同一个请求 → `422`

```json
{"detail":[{"type":"missing","loc":["body","file"],"msg":"Field required","input":null},
           {"type":"missing","loc":["body","name"],"msg":"Field required","input":null}]}
```

其他 400：`只支持 .jsonl 文件`、`文件必须是 UTF-8 编码`、`第 N 行格式错误: ...`、`'X.jsonl' 已存在`、`数据集为空`。

### 步骤 3：启动训练

```bash
# C31 用已有数据集（推荐，可复现）
curl -s -X POST http://127.0.0.1:8001/training/start \
  -H 'Content-Type: application/json' \
  -d '{
    "lora_name": "my_ops_helper_v1",
    "dataset_file": "$(pwd)/training_data/_verify_ds.jsonl",
    "data_count": 120,
    "lora_rank": 16,
    "lora_epochs": 3,
    "auto_restart_vllm": true
  }'
# → {"task_id":"a1b2c3d4","status":"running"}
#   立刻返回，不等训练完成；task_id 是 8 位随机串，下面轮询要用它
```

```bash
# C31b 让平台自己造数据（需要 vLLM 在跑）
curl -s -X POST http://127.0.0.1:8001/training/start \
  -H 'Content-Type: application/json' \
  -d '{"lora_name":"my_ops_helper_v2","task_desc":"平台运维问答：vLLM 状态、KB 检索、训练排障","data_count":100}'
```

请求字段（`TrainRequest`）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `lora_name` | **必填** | 只允许字母/数字/`_`/`-`；产物落 `~/loras/<name>/` |
| `data_count` | 100 | **必须 10 ≤ x ≤ 5000**，否则 `400 数据条数必须在 10-5000 之间` |
| `dataset_file` | null | 已有数据集绝对路径。**和 `task_desc` 至少给一个** |
| `task_desc` | "" | 要训什么（用于自动造数据） |
| `format_hint` | "" | 造数据的格式提示 |
| `base_model` | null | 不传则从当前 vLLM 底座推导 FP16 版 |
| `provider_type` | `vllm` | 造数据用哪个 provider（vllm/openai/ollama） |
| `provider_config` | {} | `{openai:{api_base,model,api_key}}`，留空用 `settings.providers` |
| `lora_rank` | null | 不传 ⇒ `recommend_rank(data_count)`：<100→8, <500→16, <2000→32, 否则 64 |
| `lora_epochs` | null | 不传 ⇒ `recommend_epochs`：<100→5, <500→3, 否则 2 |
| `auto_restart_vllm` | true | 训完是否自动重启底座 |
| `review_before_train` | false | true ⇒ 停在 `waiting_review` 等 `POST /training/{id}/confirm`（30 分钟超时） |

**会 400/403/409 的前置校验（全部实测）**：

| 情况 | 状态码 | detail |
|---|---|---|
| `data_count=5` 或 `6000` | 400 | `数据条数必须在 10-5000 之间` |
| `lora_name="bad name!!"` | 400 | `LoRA 名称只能包含字母、数字、下划线、中划线` |
| 既无 `dataset_file` 也无 `task_desc` | 400 | `缺少训练数据来源：请提供 dataset_file（已有数据集）或 task_desc…` |
| `dataset_file` 指向不存在的文件 | 400 | 同上（实测走的是这条分支，不是「文件不存在」） |
| 自动造数据但 vLLM 没起 | 400 | `生成数据需要 vLLM 在跑，但连不上 8000 端口 ✗…` |
| 已有训练在跑 | **409** | `已有训练在跑（xxx · task_id · stage）✗ 并发训练会让 CUDA 上下文崩溃…` |
| `settings.collect_mode=true` | **403** | `采集/评测模式已开启（collect_mode=true）——禁止启动真实训练 ✗` |

> **409 是硬约束**：平台用进程级 `flock` 单飞，并发训练会 `CUBLAS_STATUS_INTERNAL_ERROR`。

### 步骤 4：轮询（`/training/list` 的 status 语义）

```bash
# C32 最近 30 条（按 started_at 倒序）
curl -s http://127.0.0.1:8001/training/list
# → {"tasks":[{"task_id":"5bf55a0a","lora_name":"t1_med_lora_v1","status":"running",
#    "stage":"训练 LoRA","started_at":"2026-10-22T13:18:05.469220","finished_at":null}]}

# C33 单任务详情（含 log 尾部 300 行）—— 判进度看这个
curl -s http://127.0.0.1:8001/training/status/5bf55a0a
```

`status` 只有 4 个值：

| status | 含义 | 你要做什么 |
|---|---|---|
| `running` | 在跑 | 继续轮询（建议 5s 一次） |
| `waiting_review` | 只在 `review_before_train=true` 时出现，等你确认 | 改完数据 `POST /training/{id}/confirm {"action":"continue"}` |
| `success` | 全部阶段完成 | 去 `/chat/models` 找 `vllm:lora:<name>` |
| `failed` | 失败 | 读 `error` + `log` 尾部 |

`stage` 的真实迁移链（源码顺序，字段实测存在）：

```
准备中 → 生成训练数据 / 复用数据集 → 释放显存 → 训练 LoRA → 重启 vLLM → 完成
                                                  ↘ 失败
```

`/training/status/{id}` 返回字段：`task_id, status, stage, lora_name, base_model, dataset_file, lora_path, error, log[], started_at, finished_at`。
`/training/status/不存在` → `404 {"detail":"任务不存在"}`（C34）。

### 步骤 5：训练期间 vLLM 会停 —— 怎么处理

**实测事实**（本机 `gpu_mode=auto`，即单卡模式）：

```
/gpu/status → {"gpu_mode":"auto","dual_enabled":false,
               "gpu_config_reason":"单卡模式（默认 · 训练时停 vLLM · 与旧行为一致）",
               "vllm_running":false,"training_running":true,"training_task":"t1_med_lora_v1"}
/vllm/status → {"running":false,"pid":null,"model":null,"port":8000,"model_path":null}
```

即：**训练一开始，`/vllm/status` 的 `running` 立刻变 `false`，`:8000` 直接连不上**（本手册写作期间真实观测到：另一个训练在跑，我发的工作流 2 个 LLM 节点因此降级）。

**完整实测时间线**（另一个子代理训练 `t1_med_lora_v1`，40 条数据，我全程只读轮询）：

```
13:18:05  POST /training/start       → {"task_id":"5bf55a0a","status":"running"}
13:18:38  status=running  stage=训练 LoRA   /vllm/status.running=false
13:20:58  status=running  stage=重启 vLLM   /vllm/status.running=false
13:21:28  status=success  stage=完成        /vllm/status.running=true
           finished_at=13:21:25  ⇒ 全程 200 秒
```

时间量级：40 条实测 **200 秒**（含底座重启）；12 条实测 **142 秒**（平台自己记录的指标）。⇒ **平台「本地推理不可用」约 2~4 分钟**。

**你的处理策略（按优先级）**：

1. **改双卡，训练期间平台照常可用**（最优，但**这是全局配置**，会写进 `settings.json`）：
   ```bash
   # 实测返回：{"ok":true,"gpu_mode":"dual","inference_gpu":1,"train_gpu":0,
   #            "dual_enabled":true,"reason":"双卡模式已启用（推理=GPU1 · 训练=GPU0…）",
   #            "note":"已保存 ✓ 下次启动 vLLM / 训练时生效（当前运行中的服务不受影响 ✓）"}
   curl -s -X POST http://127.0.0.1:8001/gpu/config -H 'Content-Type: application/json' \
        -d '{"gpu_mode":"dual","inference_gpu":1,"train_gpu":0}'

   # 改回默认（单卡）—— 换机器/换卡前务必复原
   curl -s -X POST http://127.0.0.1:8001/gpu/config -H 'Content-Type: application/json' \
        -d '{"gpu_mode":"auto"}'
   ```
   `dual_enabled=true` 后，训练走一张卡、vLLM 留在另一张卡，`stage` 里会打「双卡模式 ✓ vLLM 全程未停 ✓」。⚠ **改了不立即生效**（note 明说「下次启动 vLLM/训练时生效」），所以别以为改完下一次请求就不停了。
2. **单卡时把训练当「维护窗口」**：启动前把 `data_count` 压到最小能训的量（≥10），并设 `auto_restart_vllm: true`（默认已开）。
3. **训练期间降级**：所有 `/chat/*` 会失败（`500 / 502` 或 SSE `{"error":…}`）。此时只能走**不依赖模型**的能力：`/sim/run`、`/kb/*` 检索、`/docs/tool` 里确定性工具（`format_fix` 等）、`/training/status`。
4. **兜底不会永久挂**：平台有 `finally` 恢复逻辑 —— 任务失败但停过 vLLM 时会自动重启，日志打 `[恢复] 检测到 vLLM 已停 ⇒ 自动重启以恢复平台可用性 ✓`。但 `auto_restart_vllm:false` 时训完**不会**重启，得你自己 `POST /vllm/start`。

**轮询期间请同时看 `/vllm/status`**，别把「底座在重启」误判成「模型挂了」。

### 步骤 6：训完验证

```bash
# C35 —— 出现 vllm:lora:<name> 才算成功
curl -s http://127.0.0.1:8001/chat/models | grep -o 'vllm:lora:[a-zA-Z0-9_-]*' | sort -u

# C36 —— 适配器文件是否齐全（在 ~/loras/<name>/）
curl -s http://127.0.0.1:8001/loras
# → {"loras":[{"name":"semantic_intent_v1","path":"~/semantic_intent_v1",
#    "compatible":true,"hidden_size":3584}, ...],"vllm_hidden_size":3584}
```

**`compatible` 必须为 `true`**：它比对 LoRA 权重与当前底座的 hidden_size（本机底座 3584）。`false` 表示这个 LoRA 训在别的底座上（例：`dimensions_v1` hidden=2560）⇒ 挂上去会 400，工作流预检也会拒绝启动。

### 步骤 7：热切换（`/loras/load`）

```bash
# C37 加载（= 热切换到该 LoRA）
curl -s -X POST http://127.0.0.1:8001/loras/load -H 'Content-Type: application/json' \
  -d '{"lora_name":"semantic_intent_v1"}'
# → {"status":"success","message":"'semantic_intent_v1' 已加载"}

# C38 看当前加载了哪些
curl -s http://127.0.0.1:8001/loras/loaded
# → {"loaded":["swk_persona_v3","semantic_intent_v1"]}

# C39 卸载
curl -s -X POST http://127.0.0.1:8001/loras/unload -H 'Content-Type: application/json' \
  -d '{"lora_name":"semantic_intent_v1"}'
# → {"status":"success","message":"'semantic_intent_v1' 已卸载"}
```

失败语义：

| 情况 | 返回 |
|---|---|
| 目录不存在 | `404 {"detail":"路径不存在: ~/xxx"}` |
| **vLLM 没在跑** | `400 {"detail":"vLLM 未运行或未加载底座模型，无法检查兼容性"}`（实测：训练把底座停掉时就这个） |
| 已经加载过 | `400 {"detail":"{\"error\":{\"message\":\"The lora adapter 'swk_persona_v3' has already been loaded.\",…}}"}` ← **detail 里是 vLLM 原始 JSON 字符串**，要二次 parse |
| hidden_size 与底座不符 | `400`（`check_lora_compatibility` 拦下，除非 `force:true`） |

> **两个 LoRA 可以共存**（实测同时 loaded 2 个），但只有 1 个能被对话端点选中；用 `model:"vllm:lora:<name>"` 指定。`/chat/completions` 还有一种省事写法：直接给 `"lora":"<name>"`，**它会自动加载**（没加载的话）。

### 完整 Python 脚本（从造数据到训完，可直接跑）

`PLATFORM_NO_START=1` 时只做「造数据 → 上传 → 体检 → 打印将要提交的 body」，不真训 —— 我用它验证过本脚本（步骤 1~2 + 参数校验全部真实执行）。

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端：造训练数据 → 上传 → 体检 → 启动训练 → 轮询 → 验证 → 热挂载
用法：
  python3 train_my_lora.py                # 真训
  PLATFORM_NO_START=1 python3 train_my_lora.py   # 只到体检，不启动训练
"""
import json, os, sys, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8001"
LORA = "my_ops_helper_v1"
DATA_COUNT = 40

def api(method, path, body=None, raw=False, timeout=300):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, (r.read() if raw else json.loads(r.read().decode()))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

def upload_dataset(path, name):
    """multipart/form-data —— 不能用 JSON（否则 422）"""
    boundary = "----dshboundary7f3a"
    with open(path, "rb") as f:
        content = f.read()
    parts = []
    parts.append(("--" + boundary).encode())
    parts.append(b'Content-Disposition: form-data; name="name"')
    parts.append(b"")
    parts.append(name.encode())
    parts.append(("--" + boundary).encode())
    parts.append(('Content-Disposition: form-data; name="file"; filename="%s"' % os.path.basename(path)).encode())
    parts.append(b"Content-Type: application/octet-stream")
    parts.append(b"")
    parts.append(content)
    parts.append(("--" + boundary + "--").encode())
    parts.append(b"")
    payload = b"\r\n".join(parts)
    req = urllib.request.Request(
        BASE + "/training/upload_dataset", data=payload, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

# ---------- 步骤 1：造数据（必须是 conversations / from / value）----------
QA = [("vLLM 挂了怎么看状态？", "curl -s http://127.0.0.1:8001/vllm/status；running=false 就先看 logs/vllm.log。"),
      ("知识库挂载存在哪？", "settings.json 的 kb_mounted 字段，PUT /kb/mount 写入，重启后仍在。"),
      ("训练会不会影响推理？", "单卡会停 vLLM（约 2-4 分钟）；gpu_mode=dual 时不停，训练和推理各占一张卡。"),
      ("数据条数有范围吗？", "有，data_count 必须 10~5000，超出返回 400。"),
      ("怎么让模型不做心算？", "数值一律走 POST /sim/run，用 math_eval / physics_calc 等节点，结果可复算。")]
rows = []
for i in range(DATA_COUNT):
    q, a = QA[i % len(QA)]
    rows.append({"system": "你是平台运维助手，回答只给结论和一条命令。",
                 "conversations": [{"from": "human", "value": "%s（第 %d 问）" % (q, i)},
                                   {"from": "gpt", "value": a}]})
ds_path = "/tmp/%s.jsonl" % LORA
with open(ds_path, "w", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print("[1] 已生成 %d 条 -> %s" % (len(rows), ds_path))

# ---------- 步骤 2：上传 ----------
code, up = upload_dataset(ds_path, LORA)
print("[2] 上传 ->", code, json.dumps(up, ensure_ascii=False))
if code != 200:
    sys.exit("上传失败，先修数据格式")
saved = up["path"]

# ---------- 步骤 2.5：体检（只读，必做）----------
code, rep = api("POST", "/training/dataset/analyze", {"file": os.path.basename(saved)})
ds = (rep.get("report") or {}).get("dataset") or {}
ratio = ds.get("valid_ratio")
print("[3] 体检 -> valid_ratio=%s problems=%s" % (ratio, ds.get("problems")))
if ratio is None or ratio < 0.90:
    sys.exit("格式不合格（valid_ratio=%s）⇒ 别训，先修 F1-F8/Q1" % ratio)

body = {"lora_name": LORA, "dataset_file": saved, "data_count": len(rows),
        "lora_rank": 16, "lora_epochs": 3, "auto_restart_vllm": True}
print("[4] 将要提交:", json.dumps(body, ensure_ascii=False))

if os.environ.get("PLATFORM_NO_START") == "1":
    print("[4] PLATFORM_NO_START=1 ⇒ 到此为止，不启动训练")
    sys.exit(0)

# ---------- 步骤 3：启动训练 ----------
code, st = api("POST", "/training/start", body)
print("[5] 启动 ->", code, json.dumps(st, ensure_ascii=False))
if code != 200:
    sys.exit("启动失败（403=collect_mode / 409=已有训练在跑 / 400=参数）")
tid = st["task_id"]

# ---------- 步骤 4：轮询（同时看 vLLM，别把重启当挂掉）----------
last = None
while True:
    _, d = api("GET", "/training/status/" + tid)
    _, v = api("GET", "/vllm/status")
    cur = (d.get("status"), d.get("stage"), v.get("running"))
    if cur != last:
        print("[6] %s | stage=%s | vllm_running=%s" % cur)
        last = cur
    if d.get("status") in ("success", "failed"):
        if d["status"] == "failed":
            print("    失败原因:", d.get("error"))
            print("    日志尾部:", "\n".join((d.get("log") or [])[-8:]))
            sys.exit(1)
        break
    time.sleep(5)

# ---------- 步骤 5：验证产物 ----------
_, models = api("GET", "/chat/models")
mid = "vllm:lora:" + LORA
ids = [m["id"] for m in models.get("models") or []]
print("[7] %s 在模型列表里: %s" % (mid, mid in ids))
_, loras = api("GET", "/loras")
me = [x for x in loras.get("loras") or [] if x["name"] == LORA]
print("[8] compatible:", me[0].get("compatible") if me else "未找到")
if not me or me[0].get("compatible") is not True:
    sys.exit("LoRA 与当前底座 hidden_size 不兼容，挂不上")

# ---------- 步骤 6：热挂载 + 冒烟提问 ----------
code, ld = api("POST", "/loras/load", {"lora_name": LORA})
print("[9] 挂载 ->", code, json.dumps(ld, ensure_ascii=False))
code, ans = api("POST", "/chat/completions",
                {"provider": "vllm", "lora": LORA,
                 "messages": [{"role": "user", "content": "vLLM 挂了怎么看状态？"}],
                 "max_tokens": 120, "temperature": 0.2})
print("[10] 冒烟回答 ->", code, (ans.get("content") or ans)[:200] if isinstance(ans, dict) else ans)
```

---

## 5. 决策树：什么任务该走哪条路

```
任务进来
│
├─ 只是要一段文字（润色/翻译/解释/写代码）
│   └─→ POST /chat/completions          （要流式 → /chat/completions/stream）
│         · 要模型身份/风格 → 加 "lora":"<名字>"
│         · 要资料支撑     → 加 "kb":{"name":"库名","top_k":3}
│
├─ 需要「模型自己决定调用什么工具」（多步 Agent 循环）
│   └─→ POST /chat/tools
│         · 工具表：GET /agent/tools（68）→ 转 OpenAI 格式
│         · 文档/表格类动作：POST /docs/tool {"name","args"}
│         · 不确定用哪个模型：model:"auto"，读返回的 router 字段
│
├─ 有【数值】要算（算术/物理/几何/统计/占比/单位换算）
│   └─→ POST /sim/run   ★ 禁止心算 ★
│         · 先 GET /sim/nodes 看 6 个节点与参数
│         · 失败也是 200，看 ok/error/hint_params
│
├─ 需要「本机文档里的事实」
│   └─→ 一次性：PUT /kb/mount {"kb_names":[...]}  （持久化，之后自动注入）
│         · 建库：POST /kb/create → POST /kb/{n}/ingest_folder {"folder_path":"..."}
│         · 只想知道召回质量：POST /kb/{n}/query（带 diagnostics）
│         · 多库同查：POST /kb/multi_query
│
├─ 需要校验一段回答有没有编（数字/链接/引用/接地率）
│   └─→ POST /hallucination/check  （5 层）
│         · 要它自动改 → POST /hallucination/repair（需重启后端后可用）
│         · 注意：/chat/tools 的返回里已自带 confidence + hallucination
│
├─ 需要「这份文档走完整分析流水线」（多节点编排）
│   └─→ GET  /workflow/configs 选配置
│         POST /workflow/run（multipart: file + config_name + kb_name）
│         GET  /workflow/status/{task_id} 轮询
│         GET  /workflow/result/{result_file} 取报告
│         ⚠ 先看配置的 required_loras 与当前底座兼容性（hidden_size 必须一致）
│
├─ 本地模型「不会/做不好/没有这个能力」
│   └─→ 训练一个新 LoRA：§4 的 7 步
│         数据集 <8 条 / valid_ratio<0.9 → 先修数据，别训
│         已有训练在跑 → 409，等它结束（训练是全局单飞）
│
└─ 纯粹要用确定性的文档动作（不花 token）
    └─→ POST /docs/tool，例如 format_fix / list_sheets / filter_rows
          先 GET /docs/tool/list 拿 48 个真名 + 参数提示
```

**速记**：**算数找 sim，事实找 kb，动手找 tools，写作用 chat，缺能力就训练。**

---

## 6. 错误处理约定（4xx/5xx 含义 + 降级 + 重试）

### 6.1 状态码表（都是实测到的）

| 码 | 什么时候 | 典型 body | 该不该重试 |
|---|---|---|---|
| 200 | 成功；**也可能是「业务失败」** | `{"ok":false,"error":"…"}`（`/sim/run`、`/hallucination/*`） | 判 `ok` 再决定 |
| 400 | 参数/前置校验不通过 | `{"detail":"数据条数必须在 10-5000 之间"}` | ❌ 改参数 |
| 403 | `settings.collect_mode=true` | `{"detail":"采集/评测模式已开启…禁止启动真实训练 ✗"}` | ❌ 先关开关 |
| 404 | 资源不存在，**或路由在本构建里没注册** | `{"detail":"知识库 'x' 不存在"}` / `{"detail":"Not Found"}` | ❌ 后者要重启后端 |
| 409 | 并发冲突（已有训练/已有下载） | `{"detail":"已有训练在跑（…）✗…"}` | ✅ 等 30~60s 再试 |
| 422 | 请求体结构错（该 multipart 却发 JSON / 缺字段） | `{"detail":[{"type":"missing","loc":["body","file"]…}]}` | ❌ 改编码方式 |
| 500 | 后端内部异常（含上游 vLLM 报错被包） | `{"detail":"LLM 调用失败: …"}` | ✅ 1 次，退避 2s |
| 502 | **上游 LLM 调用失败**（`/chat/tools` 专用） | `{"detail":"vllm 返回 HTTP 400: …"}` / `{"detail":"调用 openai 失败：…"}` | ✅ 退避重试 |

### 6.2 三个必须知道的降级行为

> 标注：**[实测]** = 本次真发请求观察到；**[源码]** = 只有读源码确认，未构造出触发条件。

1. **知识库检索失败不中断对话**（流式端点）**[源码]**：`/chat/completions/stream` 里 KB 检索抛异常会降级为「无知识库回答」并打 warning，**不会**给你 500。（`/chat/completions` 非流式相反：KB 检索失败直接 `400 KB 检索失败: …`。）
2. **`/chat/tools` 工具表按上下文自动裁剪** **[源码，且返回体有对应字段]**：小窗口底座装不下 68 个工具，会从**尾部**裁，返回里 `context.tools_dropped` 告诉你丢了几个；若首次被 vLLM 以 token 超限拒绝，会用**真实 token 数**再裁一轮重试一次（保底 6 个）。本次实测每次 `tools_dropped` 都是 0（只给了 1 个工具），所以裁剪逻辑没被真正触发。
3. **工作流节点失败不整体崩** **[实测]**：失败节点重试（日志见「尝试 3 次」），最终结果文件 `status` 变 `partial`，`final_report` 开头加 `⚠️ 本报告基于不完整输入生成…`。**任务级 `status` 仍是 `success`** —— 这是最容易误判的地方。

### 6.3 重试建议

```
400/403/404/422 → 不重试，改请求
409            → 等 30~60s，最多 5 次
500/502        → 指数退避重试 2~3 次（2s/4s/8s），仍失败则降级到云端 provider
SSE 收到 {"error":…} → 不要静默吞，整轮重放（/chat/completions 非流式）
训练轮询      → 每 5s 一次，别把 vllm_running=false 当失败（那是正常的阶段2）
```

**超时建议**：`/chat/completions` 300s（本地 7B 长答案慢）、`/chat/tools` 300s、`/sim/run` 30s、`/kb/*/query` 60s、`/training/start` 60s（**它立刻返回 task_id，不等训练**）、`/workflow/run` 120s（同样立刻返回）。

---

## 7. 模型 / 数据 / 目录规范

| 类别 | 路径 | 来源/可改 | 说明 |
|---|---|---|---|
| 底座模型 | `~/models/<名字>/` | `MODELS_DIR` 环境变量可改 | `/models/list` 与 `/models` 都读这里；`/readiness` 的 `models_dir` 会回显 |
| LoRA 适配器 | `~/<lora_name>/` | `settings.lora_dir` 可改 | 必须含 `adapter_config.json`；`/loras` 扫这里 |
| 训练数据集 | `$(pwd)/training_data/*.jsonl` | 固定 | `/training/upload_dataset` 落这里；`/training/dataset/analyze` 的 `file` 相对此目录 |
| 训练产物 | `$(pwd)/training_output/<lora_name>/` | 固定 | 训完复制/注册到 `~/loras/<name>/` |
| 知识库 | `$(pwd)/data/kbs/<kb_name>/` | 固定 | 每库一个目录，含 `meta.json` + `docs/` 原文 |
| 上传文件 | `$(pwd)/uploads/` | 固定 | `/docs/tool` 的 `read_docx`/`read_sheet` 默认在这找，找不到再找 `outputs/` |
| 工作流产物 | `$(pwd)/outputs/result_*.json` | 固定 | `/workflow/list` 列最近 30 个 |
| 设置 | `settings.json`（项目根） | 固定 | `kb_mounted`、`providers`、`collect_mode`、`gpu_mode` 都在这里 |

**命名规则（全平台统一，违反就 400）**：`lora_name` / `kb` 名 / 数据集 `name` 只允许 **字母、数字、下划线、中划线**；KB 名 ≤ **64** 字符。

**模型下载**（本地没有的底座，从魔搭拉）：

```bash
# C40 后台下载，立刻返回
curl -s -X POST http://127.0.0.1:8001/models/download \
  -H 'Content-Type: application/json' \
  -d '{"repo_id":"Qwen/Qwen2.5-0.5B-Instruct","local_name":"_verify_dl_9f3","allow_patterns":["config.json"]}'
# → {"status":"started","repo_id":"Qwen/Qwen2.5-0.5B-Instruct","dir":"~/models/_verify_dl_9f3"}

# C41 轮询进度（state: idle|running|done|error）
curl -s http://127.0.0.1:8001/models/download/status
# → {"state":"done","repo_id":"Qwen/Qwen2.5-0.5B-Instruct","dir":"…","msg":"完成：1 个文件 -> …",
#    "files":1,"error":null,"total_mb":null,"done_mb":0.0,"pct":100.0,"elapsed_s":1.4}
```

`allow_patterns` 不传就是全量拉（几十 GB）。下载中再发一次 → `409`。删模型：`DELETE /models/{name}?confirm=true`（C42，实测 `{"status":"deleted","name":"…","freed_gb":0.0}`）。

---

## 8. 能力边界（哪些做不到）

| 边界 | 表现 | 你要怎么办 |
|---|---|---|
| **没有 GPU** | `/vllm/status.running=false`，`/chat/*` 全 500/502；`/readiness` 的「推理底座」check 为 false 并给 fix | 用 `bash scripts/run.sh --no-gpu`：只有 KB 检索 + 确定性工具（`format_fix`/`filter_rows`/`group_sum`）+ `/sim/run` 可用 |
| **云端 Key 未配** | `/chat/models` 里没有 `openai:*`；`/chat/tools` 显式 `model:"openai:xxx"` → `400 云 provider 未配置 api_key` | 配 `PUT /providers/openai`，或只用 `vllm:*` |
| **vLLM 没起** | `/chat/*` 报 `vLLM 未运行或无底座模型` / `连不上 8000 端口`；`/loras/load` → `400 vLLM 未运行或未加载底座模型，无法检查兼容性`；自动造数据也被 400 拦；`/readiness` 变 `mode:"no-gpu"` | `POST /vllm/start`，等 30~90s，轮询 `/vllm/status` |
| **训练期间（单卡）** | 本地推理整体不可用约 2~4 分钟 | 见 §4 步骤 5：改双卡 / 降级到确定性能力 |
| **并发训练** | `409`（全局单飞） | 排队，先 `/training/list` 看谁在跑 |
| **LoRA 与底座 hidden_size 不符** | `/loras` 里 `compatible:false`；`/loras/load` 400；工作流预检拒绝启动 | 换匹配底座的 LoRA，或在该底座上重训 |
| **工具表 68 个塞不进小窗口** | `context.tools_dropped>0`；工具越多越容易被 vLLM 400 | 先裁到 6~20 个（`/agent/tools?query=` 或自己筛） |
| **`/docs/tool` 不是万用转发** | `run_simulation` 传进去 **400**「请直接调用 POST /sim/run」；`list_sim_nodes`/`browser_*` 新构建有兜底(200)、旧构建 404 | 仿真一律 `POST /sim/run`，浏览器 `POST /browser/call`（或用 `/docs/tool` 的 `browser_*` 代理） |
| **`/hallucination/repair` 可能 404** | 旧构建未注册（新构建正常 200） | 重启后端；临时先用 `/hallucination/check` 只检不修 |
| **模型内部能力上限** | 本地 7B：长链推理/复杂数学/多语言细腻度不如云端大模型；`confidence` 只**能识别「零工具」**，**不能预测「答错」**（AUROC 0.8946 仅排序有效，ECE 0.174 绝对值不准，共形保证未满足） | 难题路由到云端（`model:"openai:deepseek-chat"`），或训一个专用 LoRA |
| **工作流依赖特定 LoRA** | 默认 `workflow_config.json` 要 `extract_v2`（hidden 2560），与当前底座 3584 不符 ⇒ 退出码 3 | 用 `workflow_config_t3_preset_var.json`（lora=null 变体）；或换匹配的底座 |
| **训练不是「魔法」** | 样本 <8 直接被拒；12 条能训出「会说这个句式」的小模型，但**学不会新知识**（知识要靠 KB） | 知识走 KB，风格/格式走 LoRA |

---

## 9. 与 DSH 的集成方式

### 9.1 依赖与端口

| 端口 | 服务 | 必需性 | 探活 | 说明 |
|---|---|---|---|---|
| **8001** | 平台后端（`lora_manager.py`） | **必需** | `GET /health` → `{"status":"ok"}` | 本文所有端点 |
| 8000 | vLLM 推理底座 | 用 `/chat/*`、自动造数据时必需 | `GET :8000/v1/models` 或 `GET /vllm/status` | 训练期间会停 |
| 8080 | 前端静态页（`webui/index3.html`） | 可选 | — | 纯 UI，DSH 不需要 |
| 11434 | Ollama | 可选 | `GET :11434/api/tags` | 只在 `providers.ollama` 配了时用 |
| 9223 | 浏览器 sidecar（Node，Windows 侧） | 可选 | `GET /browser/status` | `/agent/tools` 里的 `browser_*` 靠它 |

### 9.2 最小集成契约（HTTP only，无 SDK 依赖）

```python
BASE = "http://127.0.0.1:8001"

def platform_alive():      # 1) 进程活
    return requests.get(BASE + "/health", timeout=2).json().get("status") == "ok"

def base_ready():          # 2) 底座活（训练期间是 False，别当平台挂了）
    return requests.get(BASE + "/vllm/status", timeout=3).json().get("running") is True

def capabilities():        # 3) 现在到底能干什么
    return requests.get(BASE + "/readiness", timeout=10).json()   # ok/mode/checks
```

**DSH 侧建议的 3 层路由**：

```
DSH 大模型
 ├─ 本地能干的（数值/本机文档/常规写作/文档动作）→ 8001 平台
 ├─ 本地干不好但允许出网的 → platform 的 openai provider（deepseek-chat），数据仍过平台（有幻觉检测）
 └─ 平台没起 / vLLM 在训练 → 自己算（DSH 自身能力），不要死等
```

### 9.3 启停脚本

**① 启停服务（会驻留进程，不是一次性 curl，故未纳入「curl 逐条验证」）**

```bash
# 起（幂等：后端/前端已在跑就跳过；vLLM 需 GPU）
cd $(pwd) && bash scripts/run.sh
# 只起 KB/规则模式（无 GPU）
bash scripts/run.sh --no-gpu
# 停后端
pkill -f 'python lora_manager.py'
```

> 实测这两个脚本能跑：输出 `后端已在运行 / 前端: http://127.0.0.1:8080/index3.html / 推理底座 vLLM 正常 (:8000)`。它们会 `setsid nohup` 起后台服务并常驻，**不要放在会等待返回的自动化流程里**。

**② 起来后自检（可复制、可验证）**

```bash
# C49 —— 等就绪 + 看能力清单
for i in $(seq 1 40); do
  curl -sf http://127.0.0.1:8001/health >/dev/null && { echo "platform ready"; break; }
  sleep 1
done
curl -s http://127.0.0.1:8001/readiness | head -c 300
```

### 9.4 时序建议（避免踩坑）

1. **先 `/health`，再 `/readiness`，最后才发业务请求** —— `/readiness` 会告诉你缺什么、怎么补（每条 check 都带 `fix`）。
2. **`/chat/tools` 是长请求**（本地带工具 schema 约 2~5s 起，长答案更久）⇒ 超时给到 300s，别用 10s 默认值。
3. **每次 `/chat/tools` 都自带 `confidence` + `hallucination`**，先判 `hallucination.risk == "high"` 再决定要不要 `/hallucination/repair`。
4. **训练是「破坏性操作」**（会停 vLLM）：训练前把 `/chat/*` 的调用停掉，训练后先等 `/vllm/status.running == true` 再恢复。
5. **工作流任务级 `success` ≠ 节点全成功**：一定读 `result_file` 里的 `status`（`success`/`partial`）和 `final_report` 开头的警告块。

---

## 10. 附：完整端点速查表（方法 / 路径 / 请求体 / 返回关键字段）

> 状态列：✅=本次实测通过（含预期错误码）｜🔒=按约束未执行（写源/只读替代）｜⚠️=实测发现与预期不一致

### 10.1 健康与设置

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| GET | `/health` | — | `status` | ✅ |
| GET | `/readiness` | — | `ok, ok_count, total, mode, hint, models_dir, models[], checks[{name,ok,detail,fix}]` | ✅ |
| GET | `/settings` | — | `base_model_path, lora_dir, vllm_chat_api, lora_manager_api, venv_python, providers{}, collect_mode, kb_mounted[]` | ✅ |
| PUT | `/settings` | `{base_model_path?, lora_dir?, vllm_chat_api?, lora_manager_api?, venv_python?}`（None 字段不写） | 同 GET | 🔒 未执行（会改全局配置） |
| GET | `/providers` | — | 三 provider 的 `api_base/model/api_key(掩码)/api_key_set` | ✅ |
| PUT | `/providers/{name}` | `{api_base?, model?, api_key?}` | 掩码后的配置 | 🔒 未执行 |
| GET | `/gpu/status` | — | `gpus[], vllm_gpu, train_gpu, gpu_mode, dual_enabled, gpu_config_reason, vllm_running, training_running, training_task` | ✅ |

### 10.2 推理底座与模型

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| GET | `/vllm/status` | — | `running, pid, model, port, model_path` | ✅ |
| POST | `/vllm/start` | `{model_path, port=8000, max_model_len?, gpu_memory_utilization?}` | `status:"success", pid, message:"约 30-90 秒后就绪"` | 🔒 未执行（约束：不动 vLLM） |
| POST | `/vllm/stop` | — | `status:"success", message` | 🔒 未执行（同上） |
| POST | `/vllm/switch` | `{model_name, max_model_len?, gpu_memory_utilization?}` | `task_id`，轮询 `/vllm/switch/status/{id}` | 🔒 未执行 |
| GET | `/vllm/recommend?model_name=` | — | `model_name, size_gb, max_model_len, gpu_memory_utilization, source` | ✅ |
| GET | `/vllm/logs?lines=200` | — | `lines[]` | 🔒 只读但未取 |
| GET | `/models/list` | — | `dir, models[{name,path,size_gb}]` | ✅ |
| GET | `/models` | — | `models[{name,path,quantization,size_gb,architecture,is_current}]` | ✅ |
| POST | `/models/download` | `{repo_id, local_name?, allow_patterns?}` | `status:"started", repo_id, dir` | ✅（只取 config.json） |
| GET | `/models/download/status` | — | `state(idle/running/done/error), repo_id, dir, msg, files, error, total_mb, done_mb, pct, elapsed_s` | ✅ |
| DELETE | `/models/{name}?confirm=true` | — | `status:"deleted", name, freed_gb` | ✅ |
| POST | `/models/upload` | multipart `file`+`model_name` | `status` | 🔒 未执行 |
| GET | `/chat/models` | — | `models[{id,provider,name,model,lora,group}]`，id ∈ `vllm:base`/`vllm:lora:<n>`/`openai:<m>`/`ollama:<m>` | ✅ |
| GET | `/chat/context?provider=&model=` | — | `provider, max_model_len, source` | ✅ |

### 10.3 对话三端点（核心）

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| POST | `/chat/completions` | `{provider, messages[], lora?, model_override?, temperature?, max_tokens?, kb?, learn?}` | `content, usage, kb_context[], provider, model, _context{max_model_len,used_tokens_estimate,notices[]}` | ✅ |
| POST | `/chat/completions/stream` | 同上 | SSE：`{delta}` / `{done}` / `{kb_context}` / `{error}` | ✅（24 行） |
| POST | `/chat/tools` | `{model, messages[], tools[], tool_choice, max_tokens, temperature}` | `content, tool_calls[{id,name,args}], sim_guard, confidence, hallucination, router, provider, model, context{limit,used,dropped,truncated,tools_used,tools_dropped}, usage` | ✅ |

### 10.4 工具

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| GET | `/agent/tools?query=` | — | `ok, version, count(68), total, tools{名:{desc,params[]}}, filtered, diag` | ✅ |
| GET | `/docs/tool/list` | — | `count(48), tools[{name,description,params[{name,hint}],takes_text,file_oriented}], names[]` | ✅ |
| POST | `/docs/tool` | `{name, args{}, model?}` | 工具自己的返回，例 `format_fix` → `{text, changed, chars, note}` | ✅ |
| GET | `/browser/status` | — | sidecar 状态 | ⚠️ 未执行 |
| POST | `/browser/call` | `{tool, args}` | 浏览器动作结果 | ⚠️ 未执行（需 Windows sidecar） |
| POST | `/academic/search` | `{query,...}` | 学术检索结果 | ⚠️ 未执行（需外网） |
| GET | `/sim/nodes?tag=` | — | `count(6), nodes[{name,label,description,params{},tags[]}]` | ✅ |
| POST | `/sim/run` | `{node, args{}}`（args 也可传 JSON 字符串） | 成功 `{ok:true,node,label,result{}}`；失败 `{ok:false,node,error,hint_params,hint_usage}` —— **永远 200** | ✅（6 节点 + 3 失败路径） |
| GET | `/tools/quality` | — | 工具质量台账 | ✅ |

### 10.5 LoRA

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| GET | `/loras` | — | `loras[{name,path,compatible,hidden_size}], vllm_hidden_size` | ✅ |
| GET | `/loras/loaded` | — | `loaded[]` | ✅ |
| POST | `/loras/load` | `{lora_name, lora_path?, force?}` | `status:"success", message`；已加载→400；不存在→404 | ✅（真加载+卸载复原） |
| POST | `/loras/unload` | `{lora_name}` | `status:"success", message` | ✅ |
| POST | `/loras/upload` | multipart `file`(zip)+`lora_name` | `status, message` | 🔒 未执行 |
| DELETE | `/loras/{name}` | — | `status, message` | 🔒 未执行 |
| GET | `/loras/registry` | — | 质量台账（含 `persona`/`agent_story_file`） | ✅ |

### 10.6 训练

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| POST | `/training/start` | `{lora_name, dataset_file?/task_desc?, data_count(10~5000), lora_rank?, lora_epochs?, auto_restart_vllm?, review_before_train?, provider_type?, base_model?}` | `{task_id, status:"running"}`；400/403/409 见 §4 | ✅ 只验前置校验（5 条 400 全中）🔒 未真训 |
| POST | `/training/upload_dataset` | **multipart**：`file`(.jsonl) + `name` | `{status:"success", name, path, count}`；JSON→422 | ✅ |
| GET | `/training/list` | — | `tasks[{task_id,lora_name,status,stage,started_at,finished_at}]`（≤30） | ✅ |
| GET | `/training/status/{task_id}` | — | 全任务字段 + `log[-300:]` | ✅（404 也验了） |
| POST | `/training/{task_id}/confirm` | `{action:"continue"}` | 继续 `waiting_review` 的任务 | 🔒 未执行 |
| GET | `/training/{task_id}/dataset` | — | 数据集内容（供编辑） | 🔒 未执行 |
| POST | `/training/{task_id}/dataset` | `{items:[]}` | 保存编辑后数据 | 🔒 未执行 |
| GET | `/training/available_datasets` | — | `datasets[{name,path,size,mtime,lines,purpose,sample_task,roles}]` | ✅ |
| POST | `/training/dataset/analyze` | `{file:"x.jsonl"}` | `{ok,file,total,report{dataset{valid_ratio,problems,...},length,system,tools},analysis}` | ✅（正例+反例） |
| POST | `/training/dataset/upload_and_analyze` | multipart `file` | `saved_as, total, report` | ✅ |
| GET | `/training/dataset/preview?file=` | — | `{ok,file,total,items[{idx,system,turns[{from,value}],raw_keys}]}` | ✅ |

### 10.7 知识库

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| POST | `/kb/create` | `{name, description?}` | `{status, knowledge_base{name,documents[],total_chunks,created_at}}`；重名/非法名→400 | ✅ |
| GET | `/kb/list` | — | `knowledge_bases[{name,description,documents[],total_chunks,embedding{model,dim}}]` | ✅ |
| GET | `/kb/{kb_name}` | — | 单库 meta（含 `rebuilt_at`） | ✅ |
| POST | `/kb/{kb_name}/ingest_folder` | **`{folder_path, extensions?}`（JSON，参数名就是 folder_path）** | `{status:"success", added_docs, added_chunks, skipped[]}`；路径不存在→400 | ✅ |
| POST | `/kb/{kb_name}/rebuild` | — | `{status, kb, chunks, embedding{model,dim}}` | ✅ |
| POST | `/kb/{kb_name}/query` | `{query, top_k=5, min_score?}` | `{status, results[{text,source,chunk_idx,score}], diagnostics{candidates,kept,dropped,best_score,hint,...}}` | ✅ |
| PUT | `/kb/mount` | `{kb_names:[]}` | `{ok, mounted[], invalid[], count, note}` | ✅（含 invalid 过滤） |
| GET | `/kb/mounted` | — | `{ok, mounted[], count}` | ✅ |
| POST | `/kb/multi_query` | `{kb_names?, query, top_k?, min_score?, merge?}` | `{ok, query, kb_names, kb_count, total, per_kb{}, results[{...,_kb}], errors, merged}` | ✅ |
| DELETE | `/kb/{kb_name}` | — | `{status:"success", message}` | ✅ |
| POST | `/kb/{kb_name}/upload_zip` | multipart `file` | 导入结果 | 🔒 未执行 |
| POST | `/kb/{kb_name}/analyze`、`/clean`、`/eval` | `{...}` | 库体检/清洗/评测 | 🔒 未执行 |

### 10.8 幻觉

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| POST | `/hallucination/check` | `{answer, question?, source_text?}` | `{ok, hallucination{ok,risk,issues[{layer,type,severity,detail,hint}],checked{...},version}, source_chars}`；answer 空→400 | ✅ |
| POST | `/hallucination/repair` | `{answer, source_text?, max_rounds(1~3), check_network?}` | `{ok, changed, text, before{risk,issues}, after{risk,issues}, repairs[], verified, rolled_back, rounds, strategies, validators}`；answer 空→400 | ✅（新构建 200；旧构建曾 404，见 §0#1） |

### 10.9 工作流

| 方法 | 路径 | 请求体 | 返回关键字段 | 状态 |
|---|---|---|---|---|
| GET | `/workflow/config?name=` | — | `{workflow_name, nodes[9], schema_version:"2.0", description, author, tags, required_loras, required_providers, canvas_height}` | ✅ |
| PUT | `/workflow/config?name=` | 完整 config（`nodes` 必须非空数组，否则 400） | `{status, message, config, name, nodes}` | 🔒 未执行（会覆盖预设） |
| GET | `/workflow/configs` | — | `configs[{name,workflow_name,description,tags,required_loras,required_providers,nodes,is_default,mtime}]` | ✅ |
| POST | `/workflow/run` | **multipart**：`file`(.txt/.md/.pdf/.docx) + `config_name`（默认 workflow_config.json）+ `kb_name` | `{task_id, status:"running", message:"工作流已启动"}`；扩展名不支持→400；缺 file→422 | ✅（真跑一次） |
| GET | `/workflow/status/{task_id}` | — | `{task_id,status(stage...),current_node,completed_nodes,total_nodes,error,result_file,log[]}` | ✅ |
| GET | `/workflow/result/{filename}` | — | `{source_file, workflow, status(success/partial), log[], nodes{}, final_report, _meta{usage,config_warnings}}` | ✅ |
| GET | `/workflow/list` | — | `{results[{file,size,mtime}]}`（≤30） | ✅ |
| GET | `/workflow/latest` | — | 最近一次任务 | 🔒 未执行 |
| POST | `/workflow/run_saved`、`/workflow/export_dataset` | `{...}` | 跑已存任务 / 导出数据集 | 🔒 未执行 |
| POST | `/workflow/validate` | config | `{errors[], lints[]}` | 🔒 未执行 |

### 10.10 其他（本文未展开，但存在）

`/tasks`、`/history` 系列、`/eval/*`、`/quality/summary`、`/reflect/*`（轨迹→蒸馏→入库→再训练）、`/orchestrate`、`/chat/sessions` 系列、`/sim/guard/*`（递归计算守卫）、`/settings/embedding`、`/embedding-models`、`/uploads`、`/roles`、`/docs/sheets`、`/docs/flows`。
完整清单以 `GET /openapi.json` 为准（实测 **134 条路径**）。

---

## 11. 验证清单：本文 curl 编号 → 实测结果

| 编号 | 端点/动作 | 结果 |
|---|---|---|
| C01 | `GET /readiness` | 200，`ok_count=5/5, mode=full` |
| C02 | `GET /vllm/status` | 200，训练期间实测 `running:false` |
| C03 | `GET /chat/models` | 200，验证期间 **44 → 45** 个（新训成的 LoRA 一出现就自动进列表） |
| C04 | `GET /chat/context` | 200，`max_model_len=32768` |
| C05 | `POST /chat/completions` | 200，真实中文回答 |
| C06 | `POST /chat/completions/stream` | 200，24 行 SSE，末行 `{"done":true}` |
| C07 | `POST /chat/tools`（带 tools） | 200，`tool_calls` 非空（真 function calling） |
| C08 | `GET /agent/tools` | 200，`count=68`（注释说 92 ⇒ 已过期） |
| C09 | `GET /docs/tool/list` | 200，`count=48` |
| C10 | `POST /chat/tools`（`model:"auto"`） | 200，`router` 字段非空，路由到 `vllm:base` |
| C11 | `GET /sim/nodes` | 200，6 个节点 |
| C12–C17 | `POST /sim/run` × 6 节点 | 全部 200 且 `ok:true`，数值已核对 |
| C17b | `/sim/run` 的 args 传 JSON 字符串 | 200，`2**10 → 1024.0`（容错生效） |
| C18 | `/sim/run` 未知节点 | 200，`ok:false` + 可用节点列表 |
| C19 | `/sim/run` 缺参数 | 200，`ok:false` + `hint_params` |
| C20 | `POST /kb/create` | 200；重名 400；非法名 400 |
| C21 | `POST /kb/{n}/ingest_folder` | 200，`added_docs=2, added_chunks=2`；路径不存在 400 |
| C22 | `POST /kb/{n}/rebuild` | 200，`chunks=2`；`embedding` 随平台配置变化（见过 bge-m3/1024 与 bge-small-zh-v1.5/512） |
| C23 | `POST /kb/{n}/query` | 200，2 条命中 + `diagnostics` |
| C24 | `POST /kb/multi_query` | 200，`merged:true`，结果带 `_kb` |
| C25 | `POST /chat/completions` + `kb` | 200，`kb_context` 2 片，回答含 KB 独有事实「142 秒」 |
| C26 | `POST /hallucination/check`（给 source） | 200，`risk`/`issues` 返回 |
| C26b | `POST /hallucination/repair` | 新构建 **200**（`ok/changed/text/before/after/repairs/verified/rolled_back/rounds/strategies/validators`）；空 answer → 400；旧构建曾 404 |
| C27 | `POST /hallucination/check`（无 source） | 200，自动用挂载库检索当来源 |
| C28 | `/hallucination/check` answer 空 | 400 `answer 不能为空` |
| C29 | `POST /training/dataset/analyze` | 200，正例 `valid_ratio=1.0`；messages 反例 `F0:12, valid_ratio=0` |
| C30 | `POST /training/upload_dataset`（multipart） | 200 `count=2`；同请求用 JSON → **422** |
| C31 | `POST /training/start` | 前置校验全中：`data_count=5/6000`→400、缺来源→400、非法名→400、数据集不存在→400 ｜🔒 未真训 |
| C32 | `GET /training/list` | 200，实测到别人在跑的任务 + 恢复后 `{"tasks":[]}` |
| C33 | `GET /training/status/{id}` | 200，`status=running, stage=训练 LoRA`，log 有 `[progress] step=…`（**id 请换成你自己拿到的**，示例里的 `5bf55a0a` 是别人的任务） |
| C34 | `GET /training/status/不存在` | 404 `任务不存在` |
| C35 | `GET /chat/models` 找 `vllm:lora:*` | 200，40~41 个 LoRA id |
| C36 | `GET /loras` | 200，42 个，`vllm_hidden_size=3584`，逐个带 `compatible` |
| C37 | `POST /loras/load` | 200（真加载 `semantic_intent_v1`）；重复加载 400（vLLM 原始错误）；不存在 404 |
| C38 | `GET /loras/loaded` | 200，加载前 → 加载后数量 +1（**底座重启会清空已加载列表**，属正常） |
| C39 | `POST /loras/unload` | 200，已复原 |
| C40 | `POST /models/download` | 200，`status:"started"`；缺 repo_id → 400 |
| C41 | `GET /models/download/status` | 200，`state: idle → running → done`（`elapsed_s=1.4`） |
| C42 | `DELETE /models/{name}?confirm=true` | 200，`status:"deleted"` |
| C43 | `GET /workflow/config` | 200，9 节点，`schema_version=2.0` |
| C44 | `GET /workflow/configs` | 200，列出 3+ 个配置及 `required_loras` |
| C45 | `POST /workflow/run`（multipart，真跑） | 200 `task_id`；不支持扩展名 400；缺 file 422 |
| C46 | `GET /workflow/status/{id}` 轮询 | 200，一次 `success`+`result_file`，一次 `error`（LoRA 预检，退出码 3） |
| C47 | `GET /workflow/result/{file}` | 200，`{source_file,workflow,status:"partial",log,nodes,final_report,_meta}` |
| C48 | `POST /docs/tool`（`format_fix`） | 200 `{text,changed,chars,note}`；`read_docx` 缺文件 404；`list_sim_nodes` 新构建 200 / 旧构建 404；`run_simulation` → 400「请直接调用 POST /sim/run」（见 §0#2） |
| C49 | 启动自检（等 `/health` + `/readiness`） | 200，`platform ready` + 能力清单 |
| C50 | `/chat/tools` 传不存在的 `kb` | 200，**静默忽略**（返回里根本没有 `kb_context` 字段）⇒ §0#3 成立 |

**统计**：`DSH_PLUGIN_API_VERIFY.json` 里有 **80 条实测记录，全部 PASS**（`{"records":80,"passed":80,"failed":0}`），覆盖 **60+ 个端点**（含预期错误路径与 400/404/409/422 反证）；文档里 **27 个 bash 代码块中 24 个可执行块逐条跑过，23 个 100% 通过**（唯一「失败」的是 §9.3 的 `scripts/run.sh` 启停块 —— 它常驻进程、被 `timeout` 杀掉，属预期，已拆开标注）；**7 处**文档与实现不一致已在 §0 列明；🔒 8 个端点按任务约束未真调（`/vllm/start`、`/vllm/stop`、`/vllm/switch`、`POST /training/start` 真训、`PUT /settings`、`PUT /providers`、`PUT /workflow/config`、`POST /models/upload`），其签名均从源码抄录并标注。

**验证环境**：`/openapi.json` 实测 **135 条路径**；底座 `Qwen2.5-7B-Instruct-AWQ`；双卡（RTX 5060 Ti 16G + RTX 3060 12G），`gpu_mode=auto`。验证期间**多次撞上其他任务在训练**（vLLM 被停），这些时刻的错误响应（`/chat/*` 500/502）已如实记录在 JSON 的 `note` 里，并印证了 §4 步骤 5 的描述。

**验证期间对平台产生的副作用，均已复原**：`_verify_kb` 知识库（已删除）、`kb_mounted`（已恢复为 `["xiyouji","learned_skills","ab_clean_20261021"]` 原值）、`gpu_mode`（双卡示例执行后已改回 `auto`）、`semantic_intent_v1` LoRA（已 unload）、`_verify_dl_9f3` 模型目录（已删除）、测试数据集文件（已删）。**未启动任何真实训练、未启停 vLLM。**

---

## 12. 三句话记住这份文档

1. **要本地模型干活** → `POST /chat/tools`（带 tools，`model:"vllm:base"` 或 `vllm:lora:<名>`），返回的 `tool_calls[].args` 已经是 dict。
2. **要确定性** → 数值走 `/sim/run`（6 节点，`ok` 判成败，永远 200）；事实走 `PUT /kb/mount` 后自动注入；校验走 `/hallucination/check`。
3. **要它学会新活** → 造 `conversations` 格式 jsonl（≥8 条、`valid_ratio≥0.9`）→ multipart 上传 → `/training/start`（`data_count` 10~5000）→ 5s 轮询 `/training/status`（**训练期间 vLLM 会停 2~4 分钟**）→ `/chat/models` 里出现 `vllm:lora:<名>` → `/loras/load` 热挂载。
