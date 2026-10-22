# 平台作为插件嵌入 DSH —— 接入说明书

> **适用版本：v4.9.7+** · 校验方式：`GET /version` 应返回 `{"version":"4.9.7",...}`
>
> ⚠ 本文档基于 v4.9.2 实测编写，v4.9.3~v4.9.3~v4.9.7 的变更已同步以下条目：
> 端点总数 **136** · `/health` 含 `version` 字段 · `/chat/tools` 支持 `kb` 参数 ·
> `upload_dataset` 自动把 `messages` 转 `conversations` · 出厂预置工作流 **4 节点** ·
> 训练数据格式必须 `conversations`（`system` 为顶层字段）· 双卡模式 `gpu_mode=dual`
>

> **读者**：DSH 集成方 / 驱动工具调用的大模型。
> **范围**：只写「DSH 接入这一侧」的契约。平台各 API 的逐字段语义见另一份《平台 API 使用手册》，本文不重复。
> **证据等级**：文中每个结论都带标记 —— 【实测】= 本次真跑过并贴真实返回；【代码核实】= 读实现确认（未实跑，因需重启/破坏性）；【设计建议】= 平台未提供保证，由 DSH 侧自行实现。

## 0. 本次实测环境快照

| 项 | 值 |
|---|---|
| 后端 | `http://127.0.0.1:8001`（WSL2 Ubuntu-24.04，pid 51094） |
| 后端进程启动时间 | 2026-10-22 12:09:55 |
| 磁盘 `lora_manager.py` mtime | 2026-10-22 **13:06:03**（比进程新 56 分钟 ⚠ 见 §4.6） |
| vLLM | `http://127.0.0.1:8000` —— 本次实测完整观察到**两轮**生命周期：`running:true`(pid 39858) → 训练抢占 `running:false` → **自动重启**(pid 68526) → 第二轮训练 → **自动重启**(pid 76196) |
| 前端 | `http://127.0.0.1:8080`（`webui/index3.html`，pid 50） |
| browser sidecar | `http://172.20.64.1:9224`（Windows 侧，可达，`connected:false`） |
| 实测期间状态 | **5/5 `mode=full` → 4/5 `mode=no-gpu` → 5/5 `mode=full`**，如此往复两轮；三种状态的真实返回都收录在 §1.2 / §4.4。实测期间**别人连跑了两轮真实训练**（`t1_med_lora_v1`→`t1_law_lora_v1`→`t1_cs_lora_v1`） |
| 只读声明 | 全程未调 `/training/start`（只发过**必失败**的校验请求）、未调 `/vllm/start` `\|` `/vllm/stop`、未碰 `:8005` `:8084` |

---

# 一、插件契约：DSH 需要知道什么

## 1.1 唯一入口

```
BASE = http://127.0.0.1:${BACKEND_PORT:-8001}
```

**DSH 只应认识这一个 base。** 平台内部所有能力（本地推理 / 云端推理 / 知识库 / 仿真 / 训练 / 浏览器）都从这 8001 出，由后端自己决定往哪转。

- `BACKEND_PORT` 是后端自己的环境变量（`lora_manager.py:8917`：`_PORT = int(os.environ.get("BACKEND_PORT", "8001"))`），默认 8001【代码核实】。
- 不要直连 `:8000`（vLLM）做业务调用 —— 绕过 8001 会丢失知识库注入、`sim_guard`、`confidence`、`hallucination`、模型路由，且拿不到统一的降级语义。`:8000` 只用于 §1.2 第三级健康探测。
- 不要直连 `:8080`（前端）—— 那是给人看的，不是 API。

## 1.2 三级健康检查（**必须分开用，不能混为一谈**）

这是本文最重要的一节。三级各自的语义**完全不同**，用错会误判平台状态。

### 第 1 级：`GET /health` —— 只代表「进程活着」

```bash
curl -s http://127.0.0.1:8001/health
```

【实测】返回（HTTP 200，13ms）：
```json
{"status": "ok"}
```

**语义**：FastAPI 进程能响应。**它不代表能推理、不代表 vLLM 在跑、不代表知识库可用。**
`/health` 的实现就是 `return {"status": "ok"}`（`lora_manager.py:895`），没有任何依赖检查【代码核实】。

**DSH 用它做什么**：只做**端口探测 / 熔断器复位**。绝不能用它判断"可以发 LLM 请求了"。

### 第 2 级：`GET /readiness` —— 告诉你「现在能干什么、缺什么」

```bash
curl -s http://127.0.0.1:8001/readiness
```

【实测 A：vLLM 在跑时，5/5】
```json
{
  "ok": true, "ok_count": 5, "total": 5,
  "mode": "full",
  "hint": "功能完整：分析与文档工作流都可用。",
  "models_dir": "~/models",
  "models": ["Qwen2.5-0.5B-Instruct", "Qwen3-4B-Instruct", "Qwen2.5-7B-Instruct-AWQ", "..."],
  "checks": [
    {"name": "运行依赖",              "ok": true, "detail": "齐全",                     "fix": "pip install -r requirements.txt"},
    {"name": "本地模型",              "ok": true, "detail": "~/models 下有 9 个模型", "fix": "..."},
    {"name": "推理底座 (vLLM :8000)", "ok": true, "detail": "运行中",                    "fix": "..."},
    {"name": "云端 API Key（可选）",  "ok": true, "detail": "已配置",                    "fix": "..."},
    {"name": "预设工作流",            "ok": true, "detail": "3 个",                      "fix": "..."}
  ]
}
```

【实测 B：vLLM 被停时，4/5】—— 这就是「缺一样」的真实形态
```json
{
  "ok": false, "ok_count": 4, "total": 5,
  "mode": "no-gpu",
  "hint": "可启动但没有推理底座：知识库/规则、文档工具（读表/筛选/汇总/导出）仍可用；需要大模型时请启动底座或配置云端 Key。",
  "checks": [
    {"name": "运行依赖",              "ok": true},
    {"name": "本地模型",              "ok": true,  "detail": "~/models 下有 9 个模型"},
    {"name": "推理底座 (vLLM :8000)", "ok": false, "detail": "未启动"},   ← 差的就是这一项
    {"name": "云端 API Key（可选）",  "ok": true,  "detail": "已配置"},
    {"name": "预设工作流",            "ok": true,  "detail": "3 个"}
  ]
}
```

**关键坑（必须知道）**：

1. `ok` 是 `ok_count == total` 的**全等**，不是"能用"。4/5 时 `ok:false`，但**知识库/仿真/文档能力完全可用**。**`ok:false` 不允许当作"平台不可用"**。
2. `mode` 才是能力档位，共 4 档【代码核实 `lora_manager.py:6233-6239`】：

   | mode | 触发条件 | DSH 可用能力 |
   |---|---|---|
   | `full` | 有模型 **且** vLLM 在跑 | 全部 |
   | `no-gpu` | 有模型但 vLLM 没跑 |  KB / 仿真 / 文档 / 云端 LLM；**无本地 LLM、无训练** |
   | `cloud-only` | 无本地模型但配了云端 Key | 云端 LLM + KB + 仿真；**注意数据会出境** |
   | `minimal` | 既无模型又无 Key | 仅 KB / 仿真 / 文档规则 |

3. 「缺云端 Key」和「缺 vLLM」都会让 `ok_count` 变 4/5，**必须看 `checks[].name` 才能区分缺的是哪一项**。DSH 应解析 `checks` 数组而不是只看计数：

```python
def capabilities_from_readiness(r):
    c = {x["name"]: x["ok"] for x in r["checks"]}
    return {
        "llm_local":  c.get("推理底座 (vLLM :8000)", False),
        "llm_cloud":  c.get("云端 API Key（可选）", False),
        "models":     c.get("本地模型", False),
        "kb_sim_docs": True,                      # 与 mode 无关，永远可用
    }
```

### 第 3 级：`GET /vllm/status` 的 `running` + `:8000/v1/models` —— **真正能推理的唯一标志**

```bash
curl -s http://127.0.0.1:8001/vllm/status
curl -s http://127.0.0.1:8000/v1/models
```

【实测 A：在跑】
```json
{"running": true, "pid": 39858, "model": "~/models/Qwen2.5-7B-Instruct-AWQ",
 "port": 8000, "model_path": "~/models/Qwen2.5-7B-Instruct-AWQ"}
```
`GET :8000/v1/models` → HTTP 200，返回 OpenAI 兼容模型表（含底座与已加载的 LoRA）：
```json
{"object":"list","data":[
  {"id":"~/models/Qwen2.5-7B-Instruct-AWQ","object":"model","max_model_len":32768,...},
  {"id":"swk_persona_v3","object":"model","root":"~/swk_persona_v3","parent":"~/models/Qwen2.5-7B-Instruct-AWQ",...}
]}
```

【实测 B：停着】
```json
{"running": false, "pid": null, "model": null, "port": 8000, "model_path": null}
```
`GET :8000/v1/models` → `ConnectionRefusedError(111, 'Connection refused')`【实测】

**为什么两个都要看**：`/vllm/status` 是**后端自己认为**的状态（可能进程刚死还没更新），`:8000/v1/models` 是**实测**。判断"能否本地推理"应当：
```python
def can_infer_local():
    st = GET("/vllm/status")
    if not st.get("running"):
        return False
    return probe("http://127.0.0.1:8000/v1/models", timeout=2).status == 200
```

### 三级健康检查对照表

| 级别 | 端点 | 200 的含义 | 不能用来判断 | 建议调用频率 |
|---|---|---|---|---|
| 1 | `/health` | 进程活着 | **能否推理** | 心跳 30s / 熔断复位 |
| 2 | `/readiness` | 自检跑完（**200 也可能 ok:false**） | 能否推理（vLLM 挂了也可 200） | 启动时 1 次 + `mode` 变化时 |
| 3 | `/vllm/status` + `:8000/v1/models` | 本地底座真的能出 token | 云端是否可用 | 每次走本地 LLM 前，缓存 5–10s |

> ⚠ 三条都 HTTP 200 的状态是可能同时存在的（进程活着 + 自检 4/5 + vLLM 停），此时**只有云端/确定性能力可用**。

## 1.3 端口关系（谁调谁）

```
                    ┌──────────── Windows 宿主机 ────────────┐
                    │  browser sidecar  :9224                │
                    │  (node server.mjs + Edge/CDP)          │
                    └────────────────▲───────────────────────┘
                                     │  HTTP  WSL→172.20.64.1:9224
                                     │  （WSL 里 127.0.0.1:9224 连不上）
┌────────────────────────────────────┴──────────────────────────────────┐
│                        WSL2 · Ubuntu-24.04                            │
│                                                                       │
│   前端 webui/index3.html ──serve.py──► :8080                          │
│        │                                  （给人用，DSH 不接）        │
│        │ fetch(apiBase)                                               │
│        ▼                                                              │
│   ★ 平台后端 lora_manager.py  :8001  ★   ←── DSH 唯一入口            │
│        │   绑定 127.0.0.1（ss 实测），非 0.0.0.0                      │
│        │                                                              │
│        ├── POST /chat/tools ──► 模型 provider 路由                    │
│        │        ├── provider=vllm   ──► http://127.0.0.1:8000/v1      │
│        │        └── provider=openai ──► https://api.deepseek.com/v1   │
│        ├── POST /sim/run        ──► 纯本地确定性计算，**不调任何模型** │
│        ├── POST /kb/*           ──► chromadb + bge embedding（本地）  │
│        ├── POST /training/*     ──► train_lora.py（会先停 :8000）     │
│        └── POST /browser/call   ──► 172.20.64.1:9224 sidecar          │
│                                                                       │
│   vLLM :8000（绑定 0.0.0.0）OpenAI 兼容 /v1，只由后端调用             │
└───────────────────────────────────────────────────────────────────────┘
```

**调用方向总结（记这 4 行就够）**：

| 谁 | 调谁 | 何时 |
|---|---|---|
| DSH | `:8001` | **永远只调这个** |
| DSH | `:8000/v1/models` | 仅健康探测（2s 超时），不做推理 |
| 后端 `:8001` | `:8000` vLLM / 云端 / `:9224` sidecar / 本地 chromadb | 由后端按 provider 自行决定 |
| 前端 `:8080` | `:8001` | 与 DSH 无关，别复用 |

## 1.4 启动 / 停止命令

```bash
# ── 后端（默认 8001；改 BACKEND_PORT 即可多实例）──
cd $(pwd)
python lora_manager.py

# 换端口（多实例）
BACKEND_PORT=8011 python lora_manager.py

# 需要局域网/Windows 直连时（默认只绑 127.0.0.1）
WORKBENCH_HOST=0.0.0.0 BACKEND_PORT=8001 python lora_manager.py

# ── 官方脚本（会一起拉前端与底座）──
bash scripts/start.sh          # 读 BACKEND_PORT/FRONTEND_PORT/VLLM_PORT
bash scripts/stop.sh
bash scripts/run.sh --no-gpu   # 无 GPU 路径

# ── vLLM 底座 ──
curl -s -X POST http://127.0.0.1:8001/vllm/start \
  -H "Content-Type: application/json" \
  -d '{"model_path":"~/models/Qwen2.5-7B-Instruct-AWQ","port":8000}'
curl -s -X POST http://127.0.0.1:8001/vllm/stop
curl -s -X POST http://127.0.0.1:8001/vllm/switch \
  -H "Content-Type: application/json" -d '{"model_name":"Qwen2.5-14B-Instruct-AWQ"}'
```

`/vllm/start` 请求体【代码核实 openapi】：`model_path`（**必填**）、`port`(默认 8000)、`max_model_len`、`gpu_memory_utilization`。
`/vllm/stop` 返回【代码核实 `lora_manager.py:2156`】：`{"status":"success","message":"vLLM 已停止"}`。

**⚠ 不要用 `/vllm/recommend` 取参数**（本次实测该端点在运行进程中**忽略 `?name=`**）：

```bash
curl -s "http://127.0.0.1:8001/vllm/recommend?name=Qwen2.5-7B-Instruct-AWQ"
curl -s "http://127.0.0.1:8001/vllm/recommend?name=Qwen3-4B-AWQ"
```
【实测】两个不同 `name` 返回**完全相同**的无效值：
```json
{"model_name":"","size_gb":51.26,"max_model_len":1024,"gpu_memory_utilization":0.9,"source":"vllm_manager.auto_config_for_model"}
```
（`model_name` 空、`max_model_len:1024` 与真实底座 32768 不符 ⇒ 这个端点在当前构建里是坏的，见附录 B #7。）

**要 max_model_len 请读 vLLM 自己的模型表**（唯一可靠来源）：
```bash
curl -s http://127.0.0.1:8000/v1/models
```
【实测】`data[0].max_model_len = 32768`（`Qwen2.5-7B-Instruct-AWQ`）。要模型清单与体积用 `curl -s http://127.0.0.1:8001/models/list`（【实测】返回 `{name,path,size_gb}`）。

## 1.5 有无 GPU 的能力差异表

依据：`/readiness` 的 `checks[]` + `mode` 字段 + 平台自己的 `hint` 文案【代码核实 `lora_manager.py:6222-6239`】。
无 GPU 时 `mode` 一定是 `no-gpu` 或 `cloud-only` / `minimal`。

| 能力 | 端点 | 有 GPU（mode=full） | 无 GPU（no-gpu / cloud-only） | 依据 |
|---|---|---|---|---|
| 确定性计算 | `/sim/run` | ✅ | ✅ **完全可用**（纯 Python，不碰模型） | 【实测】停 vLLM 后仍 200 |
| 知识库检索 | `/kb/multi_query` | ✅ | ✅ **完全可用**（`embedding_device: cpu`） | 【实测】+ settings 实测 `embedding_device="cpu"` |
| 知识库建设 | `/kb/create`·`ingest_folder`·`rebuild` | ✅ | ✅（CPU embedding，慢） | 【代码核实】 |
| 文档工具 | `/docs/*`、`/workflow/*` | ✅ | ✅（规则类不依赖模型） | 【代码核实】 readiness hint |
| 本地大模型 | `/chat/tools` `model=vllm:*` | ✅ | ❌ **502** | 【实测】`{"detail":"调用 vllm 失败：ConnectionError ... Connection refused"}` |
| 云端大模型 | `/chat/tools` `model=openai:*` | ✅ | ✅（需 Key；无需 GPU） | 【实测】本次 deepseek-chat 可用 |
| LoRA 训练 | `/training/start` | ✅ | ❌（无 CUDA） | 【代码核实】 |
| LoRA 挂载推理 | `/loras/load` + `/chat/tools` | ✅ | ❌ | 【代码核实】 |
| 幻觉检测 | `/hallucination/check` | ✅ | ✅（走规则+embedding） | 【实测】vLLM 停时仍 200 |
| 浏览器自动化 | `/browser/*` | ✅ | ✅（sidecar 在 Windows，与 GPU 无关） | 【实测】sidecar 可达 |
| 置信度评分 | 响应内 `confidence` | ✅ | ✅（纯特征+权重） | 【实测】 |

**DSH 侧结论**：无 GPU ≠ 平台不可用。降级策略应是「**把 LLM 类请求切走，确定性/检索类请求照旧**」，见 §4。

---

# 二、DSH 侧工具声明（可直接粘贴）

下面 5 个工具定义按 DSH 的 function-calling 格式给出。每个都附**本次真跑过的请求体与真实返回**（字段名与实现一致）。

统一返回包装约定（推荐 DSH 侧实现）：

```jsonc
{ "ok": true,
  "data": { /* 平台原始返回，原样透传，不裁剪 */ },
  "degraded": false,        // true = 平台降级但没报错（详见 §4.3）
  "error": null,            // 仅 ok=false 时填
  "retryable": false }      // 决定 DSH 要不要重试（详见 §4.2）
```

## 2.1 `local_chat` —— 普通问答

```json
{
  "name": "local_chat",
  "description": "用平台的大模型做普通问答。当用户的问题需要读本机资料、需要走本地隐私数据、或批量低成本问答时用它。默认走云端 deepseek-chat；若要数据不出本机，必须显式指定 model='vllm:base'（此时需要 /vllm/status.running=true，否则返回 502）。平台会自动注入已挂载知识库、附带置信度与幻觉风险字段，但即使这两项失败也会照常回答，不要因缺字段而报错。不适合：需要最新世界知识、超长上下文、多模态生成。",
  "parameters": {
    "type": "object",
    "properties": {
      "messages": {
        "type": "array",
        "description": "OpenAI 格式消息数组，至少一条 user；不能为空（空数组会被 400 拒绝）",
        "items": {
          "type": "object",
          "properties": {
            "role": {"type": "string", "enum": ["system", "user", "assistant"]},
            "content": {"type": "string"}
          },
          "required": ["role", "content"]
        },
        "minItems": 1
      },
      "model": {
        "type": "string",
        "description": "可选。'vllm:base' 强制本地（隐私必须）；'openai:deepseek-chat' 走云端；'auto' 让后端路由。不传时后端默认 openai:deepseek-chat（注意：默认不是本地！）",
        "default": "openai:deepseek-chat"
      },
      "max_tokens": {"type": "integer", "default": 2048, "minimum": 1, "maximum": 32768},
      "temperature": {"type": "number", "default": 0.3, "minimum": 0, "maximum": 2}
    },
    "required": ["messages"]
  }
}
```

真实请求（本轮实跑）：
```bash
curl -s -X POST http://127.0.0.1:8001/chat/tools \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"用一句话说明什么是 LoRA。"}],"max_tokens":120}'
```

真实返回（HTTP 200，1082ms，**注意字段**）：
```json
{
  "content": "LoRA（低秩适应）是一种通过冻结预训练模型权重、仅训练少量低秩分解矩阵来高效微调大模型的方法。",
  "tool_calls": [],
  "sim_guard": null,
  "confidence": {
    "ok": true, "prob": 0.6804, "level": "high",
    "threshold": {"high_min": 0.75, "conformal_q": 0.25},
    "signals": {"n_tools": 0, "finished": true, "elapsed_s": 0.87, "error": false},
    "caveats": ["⚠ prob = **失败概率** ✓ · 绝对值不准 ⚠ ECE 0.174 ⇒ 只看档次 ✓", "..."]
  },
  "hallucination": {
    "ok": true, "risk": "medium",
    "issues": [{"layer": "L3-接地", "type": "weak_grounding", "severity": "medium",
                "detail": "回答接地率偏低 12.2%（5/41）⚠ 部分内容无来源"}],
    "summary": "⚠ 检出 1 处疑似幻觉"
  },
  "router": null,
  "provider": "openai", "model": "deepseek-chat",
  "context": {"limit": 65536, "used": 14, "dropped": 0, "truncated": false,
              "tools_used": 0, "tools_dropped": 0},
  "usage": {"prompt_tokens": 11, "completion_tokens": 30, "total_tokens": 41}
}
```

**返回值约定**：取 `content` 作为文本；`provider`/`model` 告诉你**实际**走了哪条路（可能被后端路由改写）；`confidence`/`hallucination` 可能为 `null` 或字段缺失，**必须容错**。

强制本地（隐私场景）【实测，210ms】：
```bash
curl -s -X POST http://127.0.0.1:8001/chat/tools \
  -H "Content-Type: application/json" \
  -d '{"model":"vllm:base","messages":[{"role":"user","content":"回答两个字：你好"}],"max_tokens":30}'
```
返回 `"content":"你好","provider":"vllm","model":"~/models/Qwen2.5-7B-Instruct-AWQ"`。

> ⚠ **这一条依赖 vLLM 正在跑**。训练窗口内（§4.4）实测返回 **502**：
> `{"detail":"调用 vllm 失败：ConnectionError: ... port=8000 ... Connection refused"}`
> ⇒ DSH 发请求前先看 `/vllm/status.running`（§1.2 第 3 级），别在训练窗口里白等 120s。

> ⚠ **实测踩坑**：`/chat/tools` 的默认模型是**云端** `openai:deepseek-chat`（openapi 里 default 就写着），而 `/chat/completions` 的默认 provider 是**本地** `vllm`。两个"chat"端点默认值不一致！凡要求"数据不出本机"的场景，**必须显式传 `model`**。

## 2.2 `local_tool_call` —— 带工具调用

```json
{
  "name": "local_tool_call",
  "description": "让平台模型对给定工具集做原生 function-calling，返回结构化的 tool_calls。当你要先拿到'模型认为该调哪个工具、参数是什么'再自己执行时用它（例如让平台模型决定该用哪个 sim 节点）。工具执行由调用方负责，平台只返回 tool_calls 不执行。若平台判定为'缺参数的确定性计算题'，会直接返回反问而完全不调模型（见 §4.3），此时 tool_calls 为空。",
  "parameters": {
    "type": "object",
    "properties": {
      "messages": {"type": "array", "items": {"type": "object"}, "minItems": 1},
      "tools": {
        "type": "array",
        "description": "OpenAI tools 格式数组：[{type:'function',function:{name,description,parameters}}]",
        "items": {"type": "object"}
      },
      "tool_choice": {"type": "string", "default": "auto", "enum": ["auto", "none", "required"]},
      "model": {"type": "string", "default": "openai:deepseek-chat"},
      "max_tokens": {"type": "integer", "default": 2048},
      "temperature": {"type": "number", "default": 0.3}
    },
    "required": ["messages", "tools"]
  }
}
```

真实请求（本轮实跑）：
```bash
curl -s -X POST http://127.0.0.1:8001/chat/tools \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"请用工具帮我算 1234*5678+90，不要自己心算。"}],"tools":[{"type":"function","function":{"name":"math_eval","description":"计算一个数学表达式","parameters":{"type":"object","properties":{"expr":{"type":"string"}},"required":["expr"]}}}],"tool_choice":"auto","max_tokens":200}'
```

真实返回（HTTP 200，1107ms）：
```json
{
  "content": "",
  "tool_calls": [
    {"id": "call_00_vb59DJuKA4H0uTL5vpKe6704", "name": "math_eval", "args": {"expr": "1234*5678+90"}}
  ],
  "sim_guard": null,
  "confidence": {"ok": true, "prob": 0.9305, "level": "low", "...": "..."},
  "hallucination": {"ok": true, "risk": "low", "issues": [], "summary": "未发现内容层幻觉 ✓"},
  "context": {"limit": 65536, "used": 23, "tools_used": 1, "tools_dropped": 0},
  "usage": {"prompt_tokens": 294, "completion_tokens": 44, "total_tokens": 338}
}
```

**返回值约定**：`tool_calls[].args` **已经是解析好的对象**（不是 JSON 字符串），这是相对裸 OpenAI 的便利点；`tool_calls[].name` 与 `id` 直接可用。
⚠ 本轮该请求的 `confidence.level` 是 `"low"`（prob=0.9305），但模型其实正确地调了工具 —— 见 §4.3 对 `level` 的解读，**不要一律按 level 阻断**。

## 2.3 `local_compute` —— 确定性计算（禁止大模型心算）

```json
{
  "name": "local_compute",
  "description": "调用平台的确定性仿真节点做数值/单位/统计/几何/物理计算，返回可复算的结果。任何涉及数字运算、单位换算、几何面积体积、物理公式、占比、描述统计的请求都必须用它，禁止大模型心算。可先用 action='list' 拿节点清单与参数说明。该端点永远返回 HTTP 200：ok=false 表示缺参数或节点不存在，此时应把 hint_params/hint_usage 原样反馈给用户追问，而不是重试。",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list", "run"], "default": "run"},
      "node": {
        "type": "string",
        "description": "run 时必填。可用值：math_eval / unit_convert / ratio / stats / geometry_calc / physics_calc",
        "enum": ["math_eval", "unit_convert", "ratio", "stats", "geometry_calc", "physics_calc"]
      },
      "args": {
        "type": "object",
        "description": "节点参数。必须按 /sim/nodes 返回的 params 字段给；math_eval 用 {expr}; unit_convert 用 {value,from,to}; stats 用 {values}; ratio 用 {a,b}; geometry_calc 用 {shape,radius|width/height|base/height|...}; physics_calc 用 {formula,...}"
      }
    },
    "required": ["action"]
  }
}
```

### 第一步：拿节点清单（**必须先做**，参数名以它为准）

```bash
curl -s http://127.0.0.1:8001/sim/nodes
```

【实测】返回 6 个节点，结构如下（节选）：
```json
{
  "count": 6,
  "nodes": [
    {"name": "math_eval", "label": "数值表达式求值",
     "description": "安全地计算一个数学表达式（+ - * / // % ** 与 abs/round/min/max/sqrt/log/exp/sin/cos/tan/pow，常量 pi/e/tau）",
     "params": {"expr": "string · 要计算的表达式，如 \"(1+2)*3/sqrt(16)\""},
     "tags": ["math"]},
    {"name": "unit_convert", "label": "单位换算",
     "description": "确定性单位换算 ✓ 长度/质量/面积/体积/速度/压力/能量/功率/时间/数据量/温度。用法: {\"value\":100,\"from\":\"km\",\"to\":\"m\"}",
     "params": {"value": "number（数值 ✓）", "from": "string（源单位 ✓ 如 km）", "to": "string（目标单位 ✓ 如 m）",
                "dimension": "string 可选（length/mass/area/volume/speed/pressure/energy/power/time/data）"}},
    {"name": "stats",  "params": {"values": "number[] | string · 数字列表，或逗号分隔字符串 \"1,2,3\""}},
    {"name": "ratio",  "params": {"a": "number · 分子", "b": "number · 分母", "digits": "number · 百分比保留几位小数（默认 4）"}},
    {"name": "geometry_calc", "params": {"shape": "string（形状名 ✓ 如 circle）", "radius": "number · width/height/base/base_a/base_b/a/b/c ..."}},
    {"name": "physics_calc",  "params": {"formula": "string（公式名 ✓ 如 force）", "mass/distance/time/speed/v0/angle_deg/..."}}
  ]
}
```

### 第二步：执行

```bash
# 数值表达式
curl -s -X POST http://127.0.0.1:8001/sim/run \
  -H "Content-Type: application/json" \
  -d '{"node":"math_eval","args":{"expr":"1234*5678+90"}}'
```
【实测】HTTP 200：
```json
{"ok": true, "node": "math_eval", "label": "数值表达式求值",
 "result": {"expr": "1234*5678+90", "value": 7006742.0, "display": "7006742"}}
```

```bash
# 单位换算（注意参数名是 from / to，不是 from_unit / to_unit）
curl -s -X POST http://127.0.0.1:8001/sim/run \
  -H "Content-Type: application/json" \
  -d '{"node":"unit_convert","args":{"value":3.5,"from":"km","to":"m"}}'
```
【实测】HTTP 200：
```json
{"ok": true, "node": "unit_convert",
 "result": {"value": 3500.0, "from": "km", "to": "m", "dimension": "length",
            "formula": "3.5 km × 1000 = 3500（SI基准）÷ 1 = 3500 m"}}
```

### 第三步：缺参数（**这是正常的追问，不是错误**）

```bash
curl -s -X POST http://127.0.0.1:8001/sim/run \
  -H "Content-Type: application/json" \
  -d '{"node":"math_eval","args":{}}'
```
【实测】**HTTP 200**（不是 4xx）：
```json
{"ok": false, "node": "math_eval",
 "error": "ValueError: 缺少 expr（要计算的表达式）",
 "hint_params": {"expr": "string · 要计算的表达式，如 \"(1+2)*3/sqrt(16)\""},
 "hint_usage": "请按 hint_params 传 args；也可以用 math_eval 传 {\"expr\": \"...\"} ✓"}
```

节点名写错时同样 200：
```json
{"ok": false, "node": "no_such_node_zzz",
 "error": "未知仿真节点 'no_such_node_zzz'，可用: geometry_calc, math_eval, physics_calc, ratio, stats, unit_convert"}
```

**返回值约定**：`ok:true` → 读 `result.value`（或 `result` 内的具名字段）；`ok:false` → **不要重试**，把 `hint_params` / `hint_usage` 透传给用户追问。

## 2.4 `local_kb_query` —— 知识库检索

```json
{
  "name": "local_kb_query",
  "description": "在平台已挂载的知识库里做语义检索，返回带来源与分数的片段。当问题需要依据本机私有文档/语料、或需要给出可核对出处时用它。两个动作：action='mount' 先挂载（替换式，会持久化到 settings.kb_mounted），action='query' 检索。注意：平台在 /chat/tools 里会自动注入挂载库的内容，所以纯问答不一定需要显式调用本工具；需要自己拿到片段做引用/拼接时才调。知识库名不存在时接口仍返回 200，错误在 errors 字段里，不要当致命错误。",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list", "mount", "query"], "default": "query"},
      "kb_names": {
        "type": "array",
        "description": "mount 必填（替换式，传空数组=全部卸载）；query 不传时用当前已挂载的库",
        "items": {"type": "string"}
      },
      "query": {"type": "string", "description": "query 必填，检索语句"},
      "top_k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 20},
      "min_score": {"type": "number", "description": "可选，低于此分数的片段被丢弃"},
      "merge": {"type": "boolean", "default": true, "description": "多库结果是否合并排序"}
    },
    "required": ["action"]
  }
}
```

```bash
# 看有哪些库
curl -s http://127.0.0.1:8001/kb/list
# 看当前挂了哪些
curl -s http://127.0.0.1:8001/kb/mounted
```
【实测】`/kb/mounted`（下面这次快照是本次实测中某一刻，注意它是**全局共享的可变状态**）：
```json
{"ok": true, "mounted": ["xiyouji", "learned_skills", "ab_clean_20261021"], "count": 3}
```
> ⚠ **`kb_mounted` 是平台级全局状态**，任何调用方 `PUT /kb/mount` 都会改它（本次实测期间就被并发测试改成了 `["_verify_kb"]`）。DSH **每次**要用之前都该重读 `/kb/mounted`，**不要缓存超过一次会话**；也不要假设"我挂的还在"。

```bash
# 挂载（PUT，替换式）
curl -s -X PUT http://127.0.0.1:8001/kb/mount \
  -H "Content-Type: application/json" \
  -d '{"kb_names":["xiyouji","learned_skills","ab_clean_20261021"]}'
```
【实测】HTTP 200：
```json
{"ok": true, "mounted": ["xiyouji", "learned_skills", "ab_clean_20261021"],
 "invalid": [], "count": 3,
 "note": "挂载已持久化到 settings.kb_mounted ✓ 多库同时可用 ✓"}
```
> `invalid` 是失效库名列表（会被自动剔除）【代码核实 `lora_manager.py:8822`】。**`mount` 是替换式**：只想加一个库时，必须把原有库一起传进来。

```bash
# 检索
curl -s -X POST http://127.0.0.1:8001/kb/multi_query \
  -H "Content-Type: application/json" \
  -d '{"kb_names":["ab_clean_20261021"],"query":"清洗规则","top_k":2}'
```
【实测】HTTP 200（54ms）：
```json
{
  "ok": true, "query": "清洗规则", "kb_names": ["ab_clean_20261021"],
  "kb_count": 1, "total": 2,
  "per_kb": {"ab_clean_20261021": {"count": 2, "error": null}},
  "results": [
    {"text": "t and Control should be high, −100% should be low; ...",
     "source": "raw_2610.03190v1.txt", "chunk_idx": 0, "score": 0.485044002532959,
     "_kb": "ab_clean_20261021"},
    {"text": "/huggingface.co/etigerstudio/Nautil-SFT ...",
     "source": "raw_2610.03190v1.txt", "chunk_idx": 0, "score": 0.48363131284713745,
     "_kb": "ab_clean_20261021"}
  ],
  "errors": {}, "merged": true
}
```

**返回值约定**：片段在 `results[].text`，出处 `results[].source` + `results[]._kb`，相关度 `results[].score`（越大越相关）。`total==0` 且 `errors` 非空 = 库不可用，**不是 HTTP 错误**：
```json
{"ok": true, "total": 0, "results": [],
 "per_kb": {"__no_such_kb_zzz__": {"count": 0, "error": "ValueError: 知识库 '__no_such_kb_zzz__' 不存在"}},
 "errors": {"__no_such_kb_zzz__": "ValueError: 知识库 '__no_such_kb_zzz__' 不存在"}}
```

## 2.5 `local_train_model` —— 训练 LoRA

```json
{
  "name": "local_train_model",
  "description": "在本机 GPU 上训练一个 LoRA。两步：先用 action='upload' 以 multipart/form-data 上传 jsonl 数据集（不是 JSON！），再用 action='start' 启动训练。训练期间（单卡模式）平台会停掉 vLLM 约数分钟，所有本地 LLM 调用会 502，必须提前把 LLM 流量切到云端。训练不可中断，是重操作，只有在用户明确要求时才可调用；平台设置 collect_mode=true 时 start 会返回 403。上传后建议先 action='datasets' 核对条数与角色。",
  "parameters": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["datasets", "upload", "start"], "default": "start"},
      "dataset_path": {"type": "string", "description": "upload 必填：本机 jsonl 绝对路径，每行一条 {\"messages\":[{role,content}...]} 或 {human,gpt}"},
      "name": {"type": "string", "description": "upload 必填：数据集名（建议英文数字下划线）"},
      "lora_name": {"type": "string", "description": "start 必填：只能含字母/数字/下划线/中划线（含空格会被 400）"},
      "dataset_file": {"type": "string", "description": "start 必填：数据集文件绝对路径（用 upload 返回的 path）"},
      "data_count": {"type": "integer", "description": "start 必填：训练样本条数，必须在 10–5000 之间（越界 400）", "minimum": 10, "maximum": 5000},
      "task_desc": {"type": "string", "default": ""},
      "format_hint": {"type": "string", "default": ""},
      "base_model": {"type": "string", "description": "可选，默认用 settings.base_model_path"},
      "auto_restart_vllm": {"type": "boolean", "default": true, "description": "训练完是否自动重启 vLLM（默认 true）"},
      "lora_rank": {"type": "integer"},
      "lora_epochs": {"type": "integer"}
    },
    "required": ["action"]
  }
}
```

### upload —— **必须是 multipart/form-data**（字段名 `file` + `name`）

```bash
# 先造一个最小 jsonl（自包含，可直接复制运行）
cat > /tmp/ds_demo.jsonl <<'JSONL'
{"messages":[{"role":"user","content":"问题0"},{"role":"assistant","content":"答案0"}]}
{"messages":[{"role":"user","content":"问题1"},{"role":"assistant","content":"答案1"}]}
{"messages":[{"role":"user","content":"问题2"},{"role":"assistant","content":"答案2"}]}
JSONL

curl -s -X POST http://127.0.0.1:8001/training/upload_dataset \
  -F "file=@/tmp/ds_demo.jsonl" -F "name=dsh_probe_ds_01"
```
【实测】HTTP 200：
```json
{"status": "success", "name": "dsh_probe_ds_01",
 "path": "$(pwd)/training_data/dsh_probe_ds_01.jsonl",
 "count": 3}
```

**同名再传会被拒绝（不覆盖、也不自动改名）** —— 第二次跑上面同一行【实测】：
```json
{"detail": "'dsh_probe_ds_01.jsonl' 已存在"}          // HTTP 400
```
⇒ DSH 必须**自己生成唯一 name**（建议 `f"{lora_name}_{timestamp}"`），或收到 400 时换名重传。返回的 `name` 与 `path` **必须以响应为准**，不要用你传的值拼路径。
- 同名会被改写为带后缀的名字（上面的 `_0b14f0` 就是被加的后缀）→ **以返回的 `name`/`path` 为准**。
- 用 JSON 发会 **422**（【实测】`{"detail":[{"type":"missing","loc":["body","file"],"msg":"Field required"}]}`），这是判断"有没有写错 Content-Type"的最快信号。

### datasets —— 上传后核对

```bash
curl -s http://127.0.0.1:8001/training/available_datasets
```
【实测】每个数据集含 `{name, path, size, mtime, lines, purpose, sample_task, roles}`，例如
`{"name":"dsh_probe_ds_0b14f0.jsonl","lines":3,"sample_task":"问题0","roles":["user","assistant"]}`。
`lines` 与 `roles` 可用来在 start 前断言格式正确。

### start —— 只读校验（本文**未真跑训练**，以下为校验路径实测）

```bash
# 故意写错 lora_name（含空格）→ 400，证明字段名正确且校验先于建任务
curl -s -X POST http://127.0.0.1:8001/training/start \
  -H "Content-Type: application/json" \
  -d '{"lora_name":"zz zz invalid","dataset_file":"/tmp/nope.jsonl","data_count":10}'
```
【实测】HTTP 400：`{"detail":"LoRA 名称只能包含字母、数字、下划线、中划线"}`

【实测】`data_count=1` → `{"detail":"数据条数必须在 10-5000 之间"}`；
【实测】缺 `lora_name` → 422 `Field required`（证明必填字段名）。

真正的启动（**本文按约束未执行**）：
```bash
curl -s -X POST http://127.0.0.1:8001/training/start \
  -H "Content-Type: application/json" \
  -d '{"lora_name":"my_lora_v1","dataset_file":"$(pwd)/training_data/my.jsonl","data_count":40,"task_desc":"领域问答","auto_restart_vllm":true}'
```
预期返回（【代码核实】`training_start` 建任务后立即返回 `task_id`，训练在后台线程）：
`{"task_id":"5bf55a0a", ...}` → 之后用 `GET /training/status/{task_id}` 轮询。

真实轮询（本轮对着**别人正在跑的任务**实跑）：
```bash
curl -s http://127.0.0.1:8001/training/status/5bf55a0a
```
【实测】训练中：
```json
{"task_id": "5bf55a0a", "status": "running", "stage": "训练 LoRA",
 "lora_name": "t1_med_lora_v1", "started_at": "2026-10-22T13:18:05.469220",
 "finished_at": null, "error": null, "log": ["...", " 28%|██▊       | 7/25 [00:36<01:29, 4.96s/it]", "..."]}
```
【实测】**同一个 id，训练结束后再查**（同一条 curl 复跑）：
```json
{"detail":"任务不存在"}          // HTTP 404 —— 任务已回收，不是失败
```
（这条 404 实证了 §4.4 的规则：**完成后的 `status` 可能查不到，`404` 不代表失败**。）

**返回值约定**：`status` ∈ `running/pending/success/failed/cancelled`（以实测 `running`、`success` 为准）；`stage` 用于展示进度（`释放显存`/`训练 LoRA`/`重启 vLLM`）；`log` 是字符串数组（尾部追加，取最后 N 行展示）。任务不存在 → **404** `{"detail":"任务不存在"}`【实测】。

## 2.6 附加工具：`browser_run`（可选，需 Windows sidecar）

```json
{
  "name": "browser_run",
  "description": "通过 Windows 侧的 browser sidecar 操控 Edge 浏览器（打开网页、取可交互元素、点击、填表、截图）。当任务需要真实网页交互（登录后操作、表单提交、需渲染的页面）时用它。前置两步鉴权：先带 Origin 头取 token，再在 X-Sidecar-Token 头里带上。任何动作前应先调 browser_snapshot 拿 ref。该端点永远返回 HTTP 200，ok=false 表示动作失败（如缺 ref、工具名不存在、sidecar 没起），不是 HTTP 错误。sidecar 不可用只影响本工具，不影响对话/检索/计算。",
  "parameters": {
    "type": "object",
    "properties": {
      "tool": {
        "type": "string",
        "description": "动作名。可用 13 个：browser_open/snapshot/click/fill/read/screenshot/download/upload/scroll/move/wait/tabs/close",
        "enum": ["browser_open", "browser_snapshot", "browser_click", "browser_fill", "browser_read",
                 "browser_screenshot", "browser_download", "browser_upload", "browser_scroll",
                 "browser_move", "browser_wait", "browser_tabs", "browser_close"]
      },
      "args": {"type": "object", "description": "动作参数。open 用 {url}；click 用 {ref}；fill 用 {ref,text,submit}；tabs 用 {action:'list'|'new'|'close'}"}
    },
    "required": ["tool", "args"]
  }
}
```

**鉴权（两步，必须照做）**：`/browser/sidecar-token` 只给**同源页面**发 token【代码核实 `lora_manager.py:5072-5090`】，判定依据是 `Origin` 或 `Referer` 头以 `http://127.0.0.1:8080` 或 `http://localhost:8080` 开头。

```bash
# ① 不带 Origin → 403
curl -s http://127.0.0.1:8001/browser/sidecar-token
# 【实测】{"detail":"只允许同源页面获取 sidecar token ✗"}

# ② 带 Origin → 200 + token
curl -s -H 'Origin: http://127.0.0.1:8080' http://127.0.0.1:8001/browser/sidecar-token
# 【实测】{"ok":true,"token":"PLACEHOLDER"}
```

```bash
# ③ 带 token 调用（否则 403 P-52）
TOK=$(curl -s -H 'Origin: http://127.0.0.1:8080' http://127.0.0.1:8001/browser/sidecar-token \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
curl -s -X POST http://127.0.0.1:8001/browser/call \
  -H "Content-Type: application/json" -H "X-Sidecar-Token: $TOK" \
  -d '{"tool":"browser_snapshot","args":{}}'
```

【实测】无 token：**403** `{"detail":"P-52: sidecar token 不匹配 ✗（防 CSRF ✓ 从 /browser/sidecar-token 取 ✓）"}`

【实测】带 token 成功（HTTP 200）：
```json
{"ok": true, "url": "https://www.w3schools.com/html/html_file_input.asp",
 "title": "404 - Page not found | W3Schools.com",
 "refs": [{"ref": "r1", "tag": "a", "role": "link", "name": "", "kind": "link", "isPassword": false,
           "href": "https://www.w3schools.com"}, "..."]}
```

【实测】失败形态（**仍是 HTTP 200**）：
```json
{"ok": false, "error": "未知浏览器工具 'no_such_tool'（可用: browser_open, browser_snapshot, ...）"}
{"ok": false, "error": "缺少 ref（先 snapshot 拿 ref ✓）", "tool": "browser_click"}
```

**⚠ 重复调用守卫（90 秒滑窗，实测踩到）**：`POST /browser/call` 用 `md5(tool + "|" + str(args))` 做键，**90 秒内完全相同的请求会被直接拦截**（【代码核实 `lora_manager.py:5338-5357`】）：
```json
{"ok": false, "tool": "browser_tabs", "duplicate": true,
 "error": "⚠ 重复调用已拦截（37 秒内已发过完全相同的请求）⇒ 请换策略：换 URL / 换选择器 / 换工具 ✗",
 "hint": "找文献优先 academic_search 或直接 open 目标 URL，不要先开首页 ✗"}
```
- 括号里的数字是**剩余秒数**（不是窗口长度），窗口固定 **90s**。
- 键包含 `str(args)`，所以 `{"action":"list","x":1}` 与 `{"action":"list"}` 是**不同**键，能过（【实测】分别返回 `ok:true`）。
- DSH 侧：要么每次轮询**加一个无意义的抖动参数**，要么把同一动作的轮询间隔设为 **> 90s**。别对同一 payload 做重试 —— 重试只会被拦。

**返回值约定**：`ok:true` → 具体动作字段（`refs[]` / `tabs[]` / `text` / 截图路径）；`ok:false` → 把 `error` 原样告知用户（它对模型很友好，已经写清怎么修）；`duplicate:true` 单独处理（**不要重试**，改 payload 或等 90s）。`browser_snapshot` 的 `refs[].ref` 是后续 `click/fill` 的句柄。

> **DSH 侧注意**：`Origin` 头本质是"冒充同源页面"，这是平台当前的鉴权设计（防跨站 CSRF）。如果你的 DSH 跑在**另一台机器**上，最稳的做法是直接从 sidecar 的 token 文件读取（`/mnt/e/WSL/browser_sidecar/.sidecar_token`，Windows 侧是 `E:\WSL\browser_sidecar\.sidecar_token`）【实测该文件存在，48 字节】，而不是伪造 Origin。

---

# 三、路由规则：DSH 何时该把请求交给平台

## 3.1 判定规则（可直接实现）

```python
PLATFORM = "http://127.0.0.1:8001"

def route(query: str, ctx) -> str:
    """返回 'platform' | 'cloud' | 'platform_compute' | 'platform_kb'"""
    q = query.strip()

    # ── 规则 1：确定性计算 一律平台，禁止大模型心算 ────────────────
    # 理由：大模型算 1234*5678 会错，而 /sim/run 可复算且永不出错；成本 0 token
    if has_arithmetic(q) or has_units(q) or has_geometry_or_physics(q):
        return "platform_compute"          # → /sim/run（必要时先 /sim/nodes）

    # ── 规则 2：本地/隐私数据 → 平台，且必须 model="vllm:base" ──────
    # 理由：数据不出本机是硬约束；云端 Key 一用就出境
    if ctx.mentions_private_doc or ctx.user_says("不要上传"/"本地"/"隐私"):
        return "platform"                  # → /chat/tools(model=vllm:base) + /kb/multi_query

    # ── 规则 3：需要可核对出处 → 平台知识库 ──────────────────────
    # 理由：平台返回 source/score，可引用；云端无法给本机出处
    if ctx.needs_citation or ctx.mentions_local_corpus:
        return "platform_kb"               # → PUT /kb/mount + POST /kb/multi_query

    # ── 规则 4：低成本 / 大批量 → 平台本地 ────────────────────────
    # 阈值建议：批量 >= 20 条，或单条 < 200 字且无强推理需求
    # 理由：本地 0 边际成本；云端按 token 计费
    if ctx.batch_size >= 20 or (len(q) < 200 and not needs_hard_reasoning(q)):
        return "platform"                  # → /chat/tools(model=auto)

    # ── 规则 5：专用领域能力 → 平台（挂对应 KB / LoRA）───────────
    # 理由：LoRA 与 KB 都是平台侧资产，云端拿不到
    if ctx.domain in KNOWN_DOMAINS:        # 例：农业/医疗/前端UI/西游记…
        return "platform"                  # → /chat/tools(model=vllm:lora:<名>)

    # ── 兜底：超长上下文 / 最新世界知识 / 强推理 → 云端 ───────────
    # 理由：本地 7B/14B + 32768 上限，且知识截止于训练时
    if ctx.tokens > 16000 or needs_fresh_knowledge(q) or needs_hard_reasoning(q):
        return "cloud"

    return "platform"
```

## 3.2 五条规则的阈值、理由与平台侧对应能力

| # | 判定 | 阈值（建议） | 为什么 | 平台侧能力 |
|---|---|---|---|---|
| 1 | 需要确定性计算 | 含数字运算/单位/几何/物理量 → 一律 | 大模型心算会错且不可复算；平台节点返回 `formula` 与 `display` | `POST /sim/run`（6 节点，永不失败，永远 200） |
| 2 | 需要本地/隐私数据 | 用户显式说"不要上传/本地/隐私"，或内容来自本机文件 | 数据不出本机；云端一调就出境 | `POST /chat/tools` + `model="vllm:base"`；`/kb/*` |
| 3 | 需要可核对出处 | 要求引用/出处/依据 | 平台返回 `source`+`score`+`_kb`；云端只能编 | `PUT /kb/mount` → `POST /kb/multi_query` |
| 4 | 需要低成本/大批量 | 批量 ≥ 20 条；或单条 < 200 字且非强推理 | 本地边际成本≈0，省 token | `POST /chat/tools`（`model="auto"` 让后端路由） |
| 5 | 需要专用领域能力 | 命中已知领域列表 | LoRA 与 KB 是平台侧资产 | `/chat/tools`（`model="vllm:lora:<名>"`）+ 挂载对应 KB |
| 6 | 需要超长上下文/最新知识 | 上下文 > 16000 token；或问"最近/今年/现在" | 本地 `max_model_len` 实测 32768，且知识有截止日期 | **云端**（不走平台） |

## 3.3 平台**不适合**承担什么（必须绕开）

| 不适合的场景 | 原因 | DSH 应怎么做 |
|---|---|---|
| 需要极强推理（多步数学证明、复杂代码重构、长链规划） | 本地底座实测是 Qwen2.5-7B-Instruct-AWQ，能力有上限 | 直接走云端 |
| 需要最新世界知识（新闻、股价、版本号） | 模型权重有时效，且平台无联网检索 | 走云端 / DSH 自己的搜索工具 |
| 需要多模态生成（图像/音频/视频） | 平台只做文本；`/docs/*` 只处理 docx/xlsx 结构，不生成图像 | 用专门的生成侧工具 |
| 需要高并发低延迟 | 单张消费级卡跑 7B，`/chat/tools` 实测 0.2–1.5s/次但无批处理队列 | 串行调用 + 排队，或走云端 |
| 训练期间需要本地 LLM | 单卡模式训练会**停掉 vLLM**（§4.4） | 训练窗口内全量切云端 |
| 需要保证幻觉检测一定给出结论 | 检测失败不阻塞回答（§4.5），可能缺 `hallucination` 字段 | 视为"可选增强"，不要当成断言依据 |

---

# 四、降级与容错契约（**实现方最该照抄的一节**）

## 4.0 超时阈值（建议值，按端点区分）

| 端点 | 连接超时 | 读超时 | 理由 |
|---|---|---|---|
| `/health` | 0.5s | 1s | 只探活，慢就是有问题 |
| `/readiness` | 1s | 3s | 要 import + 扫目录，实测 3ms，但冷启动可能慢 |
| `/vllm/status` | 0.5s | 2s | 纯状态查询 |
| `:8000/v1/models` | 0.5s | 2s | 探活 |
| `/sim/nodes`·`/sim/run` | 1s | 10s | 实测 1–40ms，10s 已极宽松 |
| `/kb/multi_query` | 1s | 30s | 实测 54ms，但大库 / CPU embedding 冷查询会慢 |
| `/chat/tools`（本地） | 2s | 120s | 实测 0.2s，但长回答可到分钟级 |
| `/chat/tools`（云端） | 2s | 180s | 云端波动大 |
| `/training/upload_dataset` | 2s | 120s | 文件上传 |
| `/training/start` | 2s | 15s | **立即返回 task_id**，不等训练 |
| `/browser/call` | 1s | 30s | 浏览器动作本身可能等页面加载 |

## 4.1 平台**不可达**（连接拒绝 / 超时）

**判定**：`ConnectionRefused` / `ConnectTimeout` / `ReadTimeout` 且**没有 HTTP 状态码**。

```python
def call_platform(path, body=None, timeout=(2, 120)):
    for attempt in (1, 2):                       # 最多 2 次，退避 0.5s / 1.5s
        try:
            return http(BASE + path, body, timeout=timeout)
        except ConnectionRefused:
            break                                # ← 拒绝不重试：进程没了，重试无意义
        except (ConnectTimeout, ReadTimeout):
            if attempt == 1:
                sleep(0.5); continue
            break
    return None                                  # 交给上层降级
```

| 情况 | 怎么办 | 重试次数 | 理由 |
|---|---|---|---|
| **连接拒绝**（ECONNREFUSED） | **立即**回退云端，**不重试** | 0 | 端口没监听 = 后端没起来/已崩，重试只会拖慢 |
| **连接超时** | 重试 1 次（退避 0.5s） | 1 | 可能只是瞬时拥塞 |
| **读超时** | 重试 1 次（退避 1.5s），仍失败则回退 | 1 | 长回答可能刚好卡边界；再试一次通常能过 |
| 回退目标 | `model="openai:deepseek-chat"` + 显式标注「平台不可达，已回退云端」 | — | 用户必须知道数据出境了 |

**隐私例外的硬规则**：如果本次请求因**隐私**才走平台（§3.2 规则 2），平台不可达时 **`/sim/run` 与 `/kb/*` 必须再试一次**，若仍不可达则**拒绝回答并告知用户**，**不允许静默回退云端**（否则等于偷偷上传）。

## 4.2 平台返回 5xx

| 状态码 | 实测/含义 | DSH 策略 |
|---|---|---|
| **502** | 【实测】`调用 vllm 失败：ConnectionError ... Connection refused` —— 后端活着但**后端的上游模型挂了** | 重试 **1** 次（退避 2s）；仍 502 则 **切云端**（若允许）或提示"本地底座未启动"。**不要**当成平台整体宕机 |
| 503 / 504 | 网关/上游超时（本次未实测到） | 重试 2 次，退避 1s / 3s，再降级 |
| 500 | 平台内部异常（本次未实测到） | 重试 1 次；仍失败则记录并降级 |
| **400** | 【实测】`{"detail":"messages 不能为空"}`；`{"detail":"LoRA 名称只能包含字母、数字、下划线、中划线"}` | **不重试**，这是**你的参数错了**，改参数 |
| **403** | 【实测】`collect_mode` 开启时 `/training/start` 拒绝；`/browser/call` token 不匹配 | **不重试**，功能被策略禁用，告知用户 |
| **404** | 【实测】路径不存在 / 任务不存在 / 端点未部署 | **不重试**，做**能力探测**后跳过该功能 |
| **422** | 【实测】字段名/类型错（如 upload 用 JSON、缺 `lora_name`） | **不重试**，按 `detail[].loc` 修字段名 |

**指数退避建议**：`delay = 0.5 * 2**(n-1)`，上限 8s，总重试预算 ≤ 3 次 / ≤ 12s。所有**非幂等**操作（`/training/start`、`/kb/mount`、`/vllm/start`）**最多只重试 1 次**，避免重复建任务。

## 4.3 平台返回「降级响应」（HTTP 200 但语义降级）—— 最容易被误判的一块

### 4.3.1 `finish_reason: "sim_router_deterministic"` = **缺参数反问，不是错误**

**真实来源**【代码核实 `lora_manager.py:7034-7043`】：`/chat/tools` 在识别出"确定性计算题但缺参数"时**完全不调模型**，直接返回：

```json
{
  "content": "这道题**缺少必要参数，无法得出确定结果** ⚠\n\n识别到的题型：抛体射程（节点 physics_calc）\n缺失参数：v0\n\n请补充上述参数后我再计算。**在没有这些参数的情况下，任何具体数值都是编造的** ✗",
  "tool_calls": [],
  "finish_reason": "sim_router_deterministic",
  "model": "sim_router(deterministic)",
  "confidence": {"prob": 0.0, "level": "low"},
  "hallucination": {"ok": true, "risk": "low", "issues": [],
                    "checked": {"by": "sim_router", "node": "physics_calc"},
                    "summary": "确定性反问：缺参数，未调用模型"}
}
```

**注意：这是唯一带 `finish_reason` 的响应形态。** 实测确认：
- 普通 `/chat/tools` 成功响应**没有 `finish_reason` 字段**（§2.1 的真实返回里确实没有）；
- `POST /chat/completions` 响应**也没有** `finish_reason`（【实测】只有 `content/usage/kb_context/provider/model/_context`）；
- `POST /chat/completions/stream` 是自定义 SSE：`data: {"delta": "字"}` … 最后 `data: [DONE]`，**全程没有 finish_reason 事件**【实测，243 个事件】。

**所以 DSH 不能只靠 `finish_reason` 判定**，要写成或条件：

```python
def is_deterministic_ask(resp) -> bool:
    return (resp.get("finish_reason") == "sim_router_deterministic"
            or resp.get("model") == "sim_router(deterministic)"
            or ("缺少必要参数" in (resp.get("content") or ""))
            or ("缺少参数" in (resp.get("content") or "") and not resp.get("tool_calls")))
```
流式场景下按关键词 `缺少必要参数` 累积检测。

**动作**：把 `content` **原样交给用户**（它已经写好了"缺什么、请补充"），并**不要**重试、**不要**切云端（云端会编数字）。可选：把 `content` 里的"缺失参数"解析出来后，主动向用户发起追问表单。

> ⚠ **本次实测的重大发现**：这条兜底在 `/chat/tools` 上**当前并未生效**。我用代码注释里的原句「43223N/45°/2kg 能飞多远」实测，`sim_router.route()` 确实返回 `{"matched":true,"can_compute":false,"missing":["v0"]}`，但 `/chat/tools` 仍然外发云端并给出了幻觉答案（`hallucination.risk="high"`）。根因见 §4.6（运行进程比磁盘代码旧）。**而 `/chat/completions/stream` 上这条兜底是生效的**（实测直接吐出同一段反问文案）。DSH 侧必须两者都能处理。

### 4.3.2 `hallucination.risk` = low / medium / high 的含义与动作

实现：【代码核实 `hallucination_guard.py:276`】`risk = 'high' if 存在 high 严重度问题 else ('medium' if 有任意问题 else 'low')`。

| risk | 实测样例 | 含义 | DSH 建议动作 |
|---|---|---|---|
| `low` | `{"ok":true,"risk":"low","issues":[],"summary":"未发现内容层幻觉 ✓"}` | 未检出问题（**注意**：答案过短时检测会 `skipped:"answer_too_short"`，`low` 是"没查"不等于"没问题"） | 正常使用，可展示"✓ 未检出" |
| `medium` | `{"ok":true,"risk":"medium","issues":[{"layer":"L3-接地","type":"weak_grounding","severity":"medium","detail":"回答接地率偏低 12.2%（5/41）"}]}` | 有可疑但**不致命**（常见于短回答 + KB 有来源） | 正常使用，但**给回答加"部分内容无出处"提示**；涉及数字/事实时建议再查 KB |
| `high` | `{"ok":false,"risk":"high","issues":[{"layer":"L3-接地","type":"weak_grounding","severity":"high","detail":"回答接地率仅 7.1%（6/85）⇒ 大量内容无依据"}]}` | 有高危问题（几乎不复用来源 / 数字无依据） | **不要直接采信**：改为走 `local_kb_query` 取片段重答，或标注"高度存疑"，或转云端重问；`ok=false` 时**不要**把它当接口错误 |

**必知的观测事实**【实测】：同一条 `risk=medium` 的响应 `ok` 仍是 `true`；`risk=high` 时 `ok=false`。所以 **`hallucination.ok` 与 HTTP 状态无关，也与"整个请求成功与否"无关** —— 请求本身是 200 且 `content` 完整可用。

### 4.3.3 `rolled_back: true` 的含义

**字段归属先说清**：`rolled_back` **不在** `/chat/tools` 的响应里（那里的 `hallucination` 只有 `ok/risk/issues/checked/version/summary`）。它属于**独立端点** `POST /hallucination/repair`【代码核实 `lora_manager.py:6080-6124`】：

```
请求: {"answer": "...", "source_text"?: "...", "kb_names"?: [...], "max_rounds"?: 1-3, "check_network"?: true}
返回: {ok, changed, text, before, after, repairs[], verified, rolled_back}
```

含义：修复器「检测 → 定位 → 改写 → 再验证」，**改写后风险没有下降（`verified=false`）时自动回滚到原文**，并置 `rolled_back=true`。

| 字段 | 含义 | DSH 动作 |
|---|---|---|
| `changed: false` | 没找到可修的地方 | 用原文，不必提示 |
| `verified: true` + `changed: true` | 改写后风险确实下降 | 用 `text`（是修正版） |
| **`rolled_back: true`** | **改坏了，已退回原文；`text` == 原文** | 用 `text`（原文），并把 `before.risk` 如实告知用户；**不要**把这次修复当成"已修复"。也就是：`rolled_back=true` 是"**修复失败、保持原样**"，不是错误、也不是成功 |

> ⚠ **实测状态**：`POST /hallucination/repair` 当前返回 **HTTP 404** `{"detail":"Not Found"}`（端点存在于磁盘代码但不在运行进程里，见 §4.6）。DSH 必须先探测再用：404 → 跳过修复功能，只做 `POST /hallucination/check`（该端点【实测】200 可用）。

## 4.4 训练期间 vLLM 会停 —— 必须提前切流

【代码核实 `lora_manager.py:2525-2615`】`/training/start` 的阶段机：

| 阶段 | 单卡模式（默认 `gpu_mode="auto"`） | 双卡模式（`gpu_mode="dual"` 且有 ≥2 卡） |
|---|---|---|
| 阶段2 释放显存 | **`vllm_manager.stop_vllm()`** ⇒ :8000 全挂 | **不停** vLLM（训练在另一张卡） |
| 阶段3 训练 LoRA | 整个训练期间 :8000 不可用 | 平台照常可用 |
| 阶段4 重启 vLLM | `start_vllm()` + `wait_for_startup_or_fail(timeout=180)` | 跳过 |

**要点**：`约 2 分钟` 指的是 **`timeout=180` 的重启+预热预算**，**不是**总停机时间 —— 单卡模式下停机时间 = **整个训练时长 + 最多 180s 重启**。

**本次实测恰好完整观察到一轮真实训练（别人的任务，我只读轮询）**：

| 时刻 | 观测 | 证据 |
|---|---|---|
| 13:18:05 | `t1_med_lora_v1` 开始，`/training/list` → `status:"running", stage:"训练 LoRA"` | 【实测】 |
| 训练中 | `/vllm/status` → `{"running":false,"pid":null}`；`:8000/v1/models` → 连接拒绝；`/readiness` → **4/5 `mode:no-gpu`**；`/chat/tools(model=vllm:base)` → **502** | 【实测】 |
| 同期 | `/sim/run`、`/kb/multi_query`、`/hallucination/check` **全部照常 200** | 【实测】 |
| 训练完 | `/training/status/5bf55a0a` → `status:"success"`；`/vllm/status` → **`running:true` 且 pid 从 39858 变成 68526**（阶段4 自动重启生效）；`/readiness` 回到 **5/5 `mode:full`**；`/loras` 里出现 `t1_med_lora_v1` | 【实测】 |

**训练任务的留存行为不稳定（两种都实测到）**：

| 观察 | 证据 |
|---|---|
| 完成后**立即回收**：`/training/status/{id}` → **404**，`/training/list` → `{"tasks":[]}` | 【实测】`t1_med_lora_v1`（13:18 开始，13:23 后查） |
| 完成后**仍留在列表**：`/training/list` 同时列出 2 条 `status:"success", stage:"完成"` 的记录 | 【实测】`t1_law_lora_v1`、`t1_cs_lora_v1`（13:35 查） |

⇒ **不要用 `/training/status/{id}` 或 `/training/list` 判定训练成败**：`404` 可能只是"被回收了"，`success` 也可能过一会儿就消失。**可靠的完成信号是这三条同时成立**：

```python
def training_done(lora_name) -> bool:
    return (GET("/vllm/status").get("running") is True          # 阶段4 重启完成
            and lora_name in [x["name"] for x in GET("/loras")["loras"]]   # LoRA 已注册
            and GET("/readiness")["mode"] == "full")            # 自检回到 full
```
【实测】训练完 `t1_med_lora_v1` 后三者全部满足：`/vllm/status` pid 从 39858 → 68526 → 76196（每轮训练都换 pid），`/loras` 里出现 `t1_med_lora_v1`，`/readiness` 回 `5/5 mode=full`。

**DSH 处理流程**：

```python
def during_training_guard():
    st = GET("/vllm/status")
    tasks = GET("/training/list")["tasks"]
    running = [t for t in tasks if t["status"] in ("running", "pending")]
    if not st.get("running") and running:
        # 明确是训练导致的停机，而不是故障
        return "training_window"       # → 所有 LLM 请求切云端；/sim、/kb、/docs 照常

def wait_vllm_back(max_wait=900, interval=5):
    deadline = now() + max_wait
    while now() < deadline:
        if GET("/vllm/status").get("running") and probe8000_ok():
            return True
        sleep(interval)
    return False
```

| 现象 | 判定 | 动作 |
|---|---|---|
| `/vllm/status.running=False` **且** `/training/list` 有 running 任务 | **训练窗口，非故障** | LLM 切云端；`/sim`·`/kb`·`/docs` 继续用平台；5s 轮询 `/vllm/status`，上限 15 分钟 |
| `running=False` 且无训练任务 | 真故障 | 走 §4.1 回退；可提示用户 `POST /vllm/start` |
| `/chat/tools` 返回 502 且错误串含 `port=8000` + `Connection refused` | 上游底座没起 | 同上，**不要**重试超过 1 次 |
| 训练 `status="failed"` | 平台会自动尝试重启（`auto_restart_vllm` 默认 true） | 继续轮询，不要自己调 `/vllm/start` 抢 |

**硬规则**：训练窗口内**禁止**调用任何 `model="vllm:*"` 的请求（必然 502，白等 120s）。用 `/vllm/status` 做闸门，缓存 5s 即可，别每个请求都探。

## 4.5 已知的「失败即降级」行为清单

平台的设计哲学是**单点失败不阻塞主流程**。下面每一条都是契约，DSH **不能**把它们当错误处理。

| # | 行为 | 证据 | 平台返回形态 | DSH 必须怎么做 |
|---|---|---|---|---|
| 1 | **知识库失效不阻塞对话** | 【实测】`/kb/multi_query` 传不存在的库仍 **200**、`ok:true`，错误在 `per_kb[].error` 与 `errors{}` 里；【代码核实】`/chat/tools` 的 KB 注入包在 `except` 里，异常只打日志 `[chat/tools] KB 注入异常(忽略)` 并继续 | 无 KB 上下文的正常回答 | 读 `errors` 但**不中断**；回答里若出现"知识库里没有"属正常 |
| 2 | **失效挂载被自动清理** | 【代码核实】`/kb/mount` 返回 `invalid[]` 且只持久化 `valid`（`lora_manager.py:8822`）；流式路径打印 `[stream] 已自动移除失效挂载`（7625/7653） | `{"ok":true,"mounted":[...],"invalid":["旧库名"]}` | 以返回的 `mounted` 为新真相更新本地缓存；`invalid` 只做提示 |
| 3 | **幻觉检测失败不阻塞回答** | 【代码核实】`/hallucination/check` 内部 `except` 后返回 `{"ok":false,"error":"..."}`（HTTP 仍 200，见 `lora_manager.py:6166`）；`/chat/tools` 里 `hallucination` 可能缺失 | 有 `content`，但 `hallucination` 可能为 `null`/缺失/`ok:false` | 用 `resp.get("hallucination") or {}` 取值；**缺字段≠回答失败** |
| 4 | **仿真缺参数不调模型，直接确定性反问** | 【实测】`/sim/run` 缺参 → **200 + `ok:false` + `hint_params` + `hint_usage`**；【代码核实】`/chat/tools` 的 sim-router 分支 `不调模型` 直接返回 `finish_reason="sim_router_deterministic"` | 见 §4.3.1 | 当**追问**处理，不重试、不切云端 |
| 5 | **浏览器 sidecar 不可用只影响 `browser_*`** | 【实测】`/browser/status` → `{"ok":true,"sidecar":{"ok":true,"connected":false,...},"url":"http://172.20.64.1:9224"}`：**`connected:false` 但接口仍 200**；【代码核实】`browser_tools.py:130` 失败时返回带 `hint` 的说明对象 | `{"ok":false,"error":...,"hint":"启动方法（Windows 侧）: cd browser_sidecar && node server.mjs"}` | 只降级浏览器类工具；对话/检索/计算完全不受影响 |
| 6 | 模型路由异常降级为原逻辑 | 【代码核实】`except Exception as _re: print("[model_router] 路由异常(降级为原逻辑 ⚠)")` | 仍按传入 `model` 走 | 无感知，不用处理 |
| 7 | `collect_mode=true` 时禁训练 | 【代码核实】`/training/start` 直接 403 | `{"detail":"采集/评测模式已开启（collect_mode=true）——禁止启动真实训练 ✗"}` | 不重试；告知用户去改 `settings.json` |
| 8 | 响应形状随版本漂移（本次新增发现） | 【实测】`/hallucination/repair` 404 但代码里存在 | 见 §4.6 | 先探测再调用，见 §4.6 的 `feature_probe()` |
| 9 | **浏览器重复调用被静默拦截** | 【实测】90s 内相同 `{tool,args}` → `{ok:false, duplicate:true, error:"重复调用已拦截（N 秒内...）"}`；N 是剩余秒数，不是窗口 | 见 §2.6 | **不重试**；加抖动参数或间隔 >90s |

## 4.6 ⚠ 版本漂移：磁盘代码 ≠ 运行进程（本次实测最重要的一处坑）

【实测】`lora_manager.py` 的 mtime 是 **2026-10-22 13:06:03**，而运行中的后端进程启动于 **2026-10-22 12:09:55** —— **进程比代码旧 56 分钟**。后果（均已实测）：

| 磁盘代码里有 | 运行时表现 | 影响 DSH 的什么 |
|---|---|---|
| `/chat/tools` 的 sim-router 确定性兜底（7034 行） | **不生效**，仍外发模型 → 幻觉答案 | §4.3.1 的回问路径在 `/chat/tools` 上收不到 |
| `POST /hallucination/repair`（6080 行） | **404 Not Found** | §4.3.3 的 `rolled_back` 拿不到 |
| `/hallucination/check` | 200 正常（早就有） | 可用 |

**DSH 侧必须实现能力探测**（别假设端点存在）：

```python
_CAP = {}
def feature_probe():
    """启动时跑一次，把端点存在性缓存下来。"""
    _CAP["halluc_repair"] = (GET("/openapi.json").get("paths", {}).get("/hallucination/repair") is not None)
    _CAP["sim_router_deterministic"] = False   # 运行期由响应动态确认，见 §4.3.1
    return _CAP

def call_optional(path, body):
    if path not in probe_once_openapi_paths():
        return None                    # 静默跳过，不要反复 404 重试
    return POST(path, body)
```

`GET /openapi.json` 是**判断"这个构建到底有什么"的最可靠手段**（本次实测运行进程暴露 134 个路径，且确实不含 `/hallucination/repair`）。

---

# 五、部署关系

## 5.1 同机 vs 分离部署 + WSL ↔ Windows 地址差异

**四条方向本次全部实测**（这条最容易踩，务必按表来）：

| 发起方 | 目标 | 地址 | 实测结果 |
|---|---|---|---|
| **Windows** | WSL 后端 | `http://127.0.0.1:8001/health` | ✅ 200 `{"status":"ok"}`（WSL2 localhostForwarding 生效） |
| **Windows** | WSL 后端（用 WSL 网卡 IP） | `http://172.20.64.1:8001/health` | ❌ 无法连接（8001 只绑 `127.0.0.1`，网卡 IP 不转发） |
| **WSL** | Windows sidecar | `http://172.20.64.1:9224/health` | ✅ 200 |
| **WSL** | Windows sidecar（用 loopback） | `http://127.0.0.1:9224` | ❌ `curl` 返回 `000`（连不上） |

**规则一句话**：
- **WSL 里访问 Windows 服务 → 用 `172.20.64.1`**（= WSL 默认网关，`ip route` 实测 `default via 172.20.64.1`）。`127.0.0.1` 在 WSL 里指向 **WSL 自己**，永远访问不到 Windows 服务。
- **Windows 里访问 WSL 服务 → 用 `127.0.0.1`**（端口转发），**不要**用 `172.20.64.1`。
- 若要让 Windows 侧（或局域网）用 `172.20.64.1:8001` 直连，必须 `WORKBENCH_HOST=0.0.0.0` 启动后端【代码核实 `lora_manager.py:8918` 的提示语】。

sidecar 地址解析优先级【代码核实 `_browser_sidecar_base()`，`lora_manager.py:874-893`】：
```
① 环境变量 BROWSER_SIDECAR（WSL 里通常设 http://172.20.64.1:9224）
② settings.json 的 browser_sidecar 字段
③ 默认 http://127.0.0.1:9224
```
【实测】当前 `/browser/status` 返回 `"url":"http://172.20.64.1:9224"`，说明走的是 ① 或 ②。

**分离部署（两机）时**：DSH 必须把 `BASE` 配成**它自己网络位置能通的那个地址**，并在启动时用 `/health` + `/readiness` 确认；同时 sidecar 与后端通常**不在同一台**，`browser_*` 工具要么显式配 `BROWSER_SIDECAR` 指向 DSH 可达的地址，要么整体关闭。

## 5.2 多实例：`BACKEND_PORT` + 前端 `?api=` + 数据目录隔离

```bash
# 实例 A（默认）
cd $(pwd) && python lora_manager.py
# 实例 B（独立端口 + 独立项目目录副本 ⇒ 独立数据）
BACKEND_PORT=8011 python $(pwd)_b/lora_manager.py
```

**数据目录隔离原理（关键）**【代码核实 `lora_manager.py:62,83`】：
```python
PROJECT_DIR  = Path(__file__).resolve().parent     # ← 不是 cwd！
SETTINGS_FILE = PROJECT_DIR / "settings.json"
```
即：**一切私有状态都挂在 `lora_manager.py` 自己所在的目录下** —— `settings.json`（含 `providers` 的 Key、`kb_mounted`、`lora_dir`）、`training_data/`、`uploads/`、`cache/`、`data/flywheel/`。
⇒ **复制一份目录 = 一个完全隔离的实例**；只改 `BACKEND_PORT` 不复制目录 = 两个进程**抢同一个 `settings.json`**（会互相覆盖挂载/Key），**不要这样多实例**。

前端连哪个后端【代码核实 `webui/index3.html:1799-1801`】：
```
优先级：window.__LUX_API__  >  URL 的 ?api= 参数  >  默认 127.0.0.1:8001
```
```bash
# 让 8080 的前端指到 8011 的后端
http://127.0.0.1:8080/?api=http://127.0.0.1:8011
```

**DSH 多实例注意**：每个实例的 `/readiness`、`/sim/nodes`、`/kb/list` 都各自独立，**不要跨实例复用 `task_id`**（`training_tasks` 在进程内存里，`GET /training/status/{id}` 在 A 实例上查 B 实例的任务会 404【实测 404 形态】）。

## 5.3 关键配置项（`settings.json`）

路径：`PROJECT_DIR/settings.json`。下面「现值」是本次从磁盘读到的**真实值**（`api_key` 已打码）。

| 键 | 现值（实测） | 作用 | DSH 是否关心 |
|---|---|---|---|
| `base_model_path` | `~/models/Qwen2.5-7B-Instruct-AWQ` | 本地推理底座；训练完重启 vLLM 用它 | 间接（决定 `model` 名） |
| `lora_dir` | `~` | LoRA 产物目录 | 否 |
| `vllm_gpu_index` | `1` | 推理用哪张卡 | 否 |
| `train_gpu_index` | `1` | 训练用哪张卡 | 否 |
| `gpu_mode` | `"auto"` | `auto`=单卡（训练会停 vLLM）；`dual`=双卡不停 | **是**（决定 §4.4） |
| `kb_mounted` | `["xiyouji","learned_skills","ab_clean_20261021"]` | **当前挂载**，`/chat/tools` 自动注入 | **是**（决定是否自带出处） |
| `providers` | `vllm.api_base=http://127.0.0.1:8000/v1`；`openai.api_base=https://api.deepseek.com/v1`,`model=deepseek-chat`,`api_key_set=true` | 模型后端与 Key | **是**（`model` 前缀） |
| `browser_sidecar` | **磁盘上不存在**（未配置，走默认/环境变量） | sidecar 地址覆盖 | 是（若要用 `browser_*`） |
| `collect_mode` | `false` | `true` ⇒ `/training/start` 返回 403 | **是**（训练前必查） |
| `embedding_device` | `"cpu"` | 知识库 embedding 设备（无 GPU 也能用） | 间接 |
| `vllm_chat_api` / `lora_manager_api` | `http://localhost:8000/v1/chat/completions` / `http://localhost:8001` | 内部互调地址 | 否 |

**两处「文档/直觉与实现不一致」必须注意**：

1. **`GET /settings` 不返回 `browser_sidecar`**（【实测】返回体只有 16 个键：`base_model_path, collect_mode, dual_gpu_mode, embedding_device, gpu_mode, inference_gpu, kb_mounted, lora_dir, lora_manager_api, providers, train_gpu, train_gpu_index, venv_python, vllm_chat_api, vllm_gpu_index`）。但后端**会读** `settings.json` 里的 `browser_sidecar`。⇒ 想配 sidecar 必须**手改 `settings.json`**，别指望 API。
2. **`PUT /settings` 只接受 5 个键**（openapi 实测：`base_model_path / lora_dir / vllm_chat_api / lora_manager_api / venv_python`）。改 `kb_mounted` 要用 `PUT /kb/mount`，改 `providers` 要用 `PUT /providers/{name}`，改 `gpu_mode`/`collect_mode`/`browser_sidecar` **只能手改文件然后重启后端**。用 `PUT /settings` 传这些键会被**静默忽略**（不是报错）。

## 5.4 端口冲突排查

```bash
# 1) 谁占了 8001（实测可用）
ss -lntp | grep ':8001'
# 实测输出：LISTEN 0 2048 127.0.0.1:8001 0.0.0.0:* users:(("python",pid=51094,fd=13))

# 2) 一次看全 4 个端口（实测）
ss -lntp | grep -E ':(8000|8001|8080|9224)'

# 3) 按端口找 PID（不依赖 ss 的 users 列权限）
fuser -n tcp 8001 2>/dev/null
# 或
lsof -iTCP:8001 -sTCP:LISTEN -P -n

# 4) 确认它是不是平台后端（别杀错）
ps -o pid,lstart,etime,cmd -p $(fuser -n tcp 8001 2>/dev/null | tr -d ' ')

# 5) 按端口杀进程
kill $(fuser -n tcp 8001 2>/dev/null | tr -d ' ')           # 优雅
kill -9 $(fuser -n tcp 8001 2>/dev/null | tr -d ' ')        # 顽固时

# 6) 端口被占但找不到 owner（容器/其他用户）
sudo ss -lntp | grep ':8001'
```

**⚠ 本机红线端口（绝对不要动，用户正在用）**：
```
:8005  → ~/lora_manager.py（另一个平台的实例）
:8084  → python -m http.server（静态文件服务）
```

**换端口而不是杀进程**：如果 8001 被占但那是别人的实例，正确做法是 `BACKEND_PORT=8011` 起自己的（配合 §5.2 的目录隔离）。

**常见冲突原因**：
| 现象 | 原因 | 处理 |
|---|---|---|
| `[Errno 98] Address already in use` | 上一个后端没退干净 | 按上面 3)~5) 杀 |
| `/health` 200 但行为像旧版本 | **同名端口跑着旧进程**（本次实测就是这个：进程比代码旧 56 分钟） | 确认 `ps` 启动时间 vs `stat` 文件 mtime，重启后端 |
| 起了新实例但 DSH 连的还是老的 | 多实例端口写混 / 前端 `?api=` 没带 | 用 `/openapi.json` 的路径数比对版本 |

---

# 六、给大模型看的 30 秒速查卡

> 直接塞进 system prompt。全部 curl 均已实跑。

| 我想做什么 | 打哪个端点 | 一条 curl |
|---|---|---|
| 普通问答（可用云端） | `POST /chat/tools` | `curl -s -X POST http://127.0.0.1:8001/chat/tools -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"你好"}]}'` |
| 问答但**数据不出本机** | `POST /chat/tools` + `model` | `curl -s -X POST http://127.0.0.1:8001/chat/tools -H 'Content-Type: application/json' -d '{"model":"vllm:base","messages":[{"role":"user","content":"你好"}]}'` |
| 让模型决定调哪个工具 | `POST /chat/tools` + `tools` | `curl -s -X POST http://127.0.0.1:8001/chat/tools -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"算 1+1"}],"tools":[{"type":"function","function":{"name":"math_eval","parameters":{"type":"object","properties":{"expr":{"type":"string"}}}}}]}'` |
| **算数/单位/几何/物理**（禁止心算） | `GET /sim/nodes` → `POST /sim/run` | `curl -s -X POST http://127.0.0.1:8001/sim/run -H 'Content-Type: application/json' -d '{"node":"math_eval","args":{"expr":"1234*5678+90"}}'` |
| 查本机知识库 | `GET /kb/mounted` → `POST /kb/multi_query` | `curl -s -X POST http://127.0.0.1:8001/kb/multi_query -H 'Content-Type: application/json' -d '{"query":"清洗规则","top_k":3}'` |
| 挂载知识库（替换式） | `PUT /kb/mount` | `curl -s -X PUT http://127.0.0.1:8001/kb/mount -H 'Content-Type: application/json' -d '{"kb_names":["xiyouji"]}'` |
| 训练 LoRA（①上传数据集） | `POST /training/upload_dataset`（**multipart**） | `curl -s -X POST http://127.0.0.1:8001/training/upload_dataset -F 'file=@/tmp/ds.jsonl' -F 'name=my_ds'` |
| 训练 LoRA（②启动，重操作） | `POST /training/start` → 轮询 `/training/status/{id}` | `curl -s -X POST http://127.0.0.1:8001/training/start -H 'Content-Type: application/json' -d '{"lora_name":"my_lora","dataset_file":"/abs/my.jsonl","data_count":40}'` |
| 判断平台能干什么 | `GET /readiness`（看 `checks[]`+`mode`） | `curl -s http://127.0.0.1:8001/readiness` |
| 判断**能否本地推理** | `GET /vllm/status` 的 `running` | `curl -s http://127.0.0.1:8001/vllm/status` |
| 这个构建到底有哪些端点 | `GET /openapi.json` | `curl -s http://127.0.0.1:8001/openapi.json` |
| 浏览器自动化 | `GET /browser/status` → 取 token → `POST /browser/call` | `TOK=$(curl -s -H 'Origin: http://127.0.0.1:8080' http://127.0.0.1:8001/browser/sidecar-token \| python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])'); curl -s -X POST http://127.0.0.1:8001/browser/call -H 'Content-Type: application/json' -H "X-Sidecar-Token: $TOK" -d '{"tool":"browser_snapshot","args":{}}'` |

**三句话记住**：① 只打 `:8001`，别直连 `:8000`。② `/sim/run` 永远 200，`ok:false` 是"缺参数请追问"，不是错误。③ 单卡训练会停 vLLM，先查 `/vllm/status.running` 再发 LLM 请求。

---

# 附录 A：实测通过清单

| # | 请求 | 结果 |
|---|---|---|
| 1 | `GET /health` | 200 `{"status":"ok"}` |
| 2 | `GET /readiness` | 200，5/5（vLLM 在跑）与 4/5 `mode=no-gpu`（vLLM 停）两态均取得 |
| 3 | `GET /vllm/status` | 200，`running:true` 与 `running:false` 两态 |
| 4 | `GET :8000/v1/models` | 200（底座+LoRA 列表）/ 连接拒绝 |
| 5 | `GET /orchestrate/capabilities` | 200 `{"ok":true,"capabilities":{tools:68,sim_nodes:[6],kb:34,lora:43,features:{...}}}` |
| 6 | `GET /sim/nodes` | 200，6 节点含 params |
| 7 | `POST /sim/run` math_eval | 200 `value=7006742.0` |
| 8 | `POST /sim/run` unit_convert | 200 `value=3500.0` |
| 9 | `POST /sim/run` 缺参数 | 200 `ok:false` + `hint_params` |
| 10 | `POST /sim/run` 未知节点 | 200 `ok:false` + 可用节点列表 |
| 11 | `POST /chat/tools` 普通问答 | 200，`provider=openai` |
| 12 | `POST /chat/tools` + tools | 200，`tool_calls[0].name=math_eval` |
| 13 | `POST /chat/tools` `model=vllm:base` | 200，`provider=vllm` |
| 14 | `POST /chat/tools` 空 messages | 400 `messages 不能为空` |
| 15 | `POST /chat/tools` 未知 provider | 502 + ConnectionError 到 :8000 |
| 16 | `POST /chat/completions` | 200，provider=vllm，**无 finish_reason** |
| 17 | `POST /chat/completions/stream` | 200，243 个 SSE 事件，`[DONE]` 收尾，确定性反问生效 |
| 18 | `GET /kb/list` / `GET /kb/mounted` | 200 |
| 19 | `PUT /kb/mount`（幂等） | 200 `{mounted, invalid:[], count, note}` |
| 20 | `POST /kb/multi_query` 正常 | 200，`results[].{text,source,score,_kb}` |
| 21 | `POST /kb/multi_query` 不存在的库 | 200 `ok:true` + `errors{}` |
| 22 | `POST /kb/{n}/ingest_folder` `folder_path` | 400 `路径不存在`（**证明参数名**） |
| 23 | `POST /kb/{n}/ingest_folder` `ingest_path` | 422 `folder_path Field required` |
| 24 | `POST /training/upload_dataset` multipart | 200 `{status,name,path,count}` |
| 25 | `POST /training/upload_dataset` JSON | 422（**证明必须 multipart**） |
| 26 | `POST /training/start` 非法 lora_name | 400（**未触发训练**） |
| 27 | `POST /training/start` data_count 越界 | 400 |
| 28 | `POST /training/start` 缺字段 | 422 |
| 29 | `GET /training/list` / `status/{id}` / `status/不存在` | 200 / 200 / 404 |
| 30 | `GET /browser/status` / `/browser/tool/list` | 200（13 个工具） |
| 31 | `GET /browser/sidecar-token` 无 Origin | 403 `只允许同源页面获取` |
| 32 | `GET /browser/sidecar-token` 带 `Origin: http://127.0.0.1:8080` | 200 + token |
| 33 | `POST /browser/call` 无/错 token | 403 `P-52: sidecar token 不匹配` |
| 34 | `POST /hallucination/check` | 200 |
| 35 | `POST /hallucination/repair` | **404**（见 §4.6） |
| 36 | `GET /settings` / `/providers` / `/chat/models` / `/models/list` / `/loras/registry` / `/tasks` | 200 |
| 37 | `GET /no_such_endpoint` | 404 |
| 38 | Windows→`127.0.0.1:8001` / →`172.20.64.1:8001` | 200 / 失败 |
| 39 | WSL→`172.20.64.1:9224` / →`127.0.0.1:9224` | 200 / 000 |
| 40 | `ss -lntp \| grep -E ':(8000\|8001\|8080\|9224)'` | 四个端口归属全部确认 |
| 41 | `/browser/sidecar-token` + `Origin` → `/browser/call` + token | 200 + 真实浏览器快照（`refs[]`） |
| 42 | `/browser/call` 未知工具 / 缺 ref | 200 + `ok:false` + `error` |
| 43 | `/vllm/recommend` 三种 `name` | 200 但**恒返回同一无效值**（见附录 B #7） |
| 44 | `upload_dataset` 自包含示例（`cat` 造文件 + `-F`） | 200 `{status,name,path,count:3}` |
| 45 | 训练全生命周期观测 | running → vLLM 停 → success → vLLM 自动重启（pid 39858→68526）→ `/loras` 出现 `t1_med_lora_v1` |
| 46 | **文档内 39 条 curl 逐条复跑**（含快速卡表格里的） | **34 条通过 / 5 条按约束跳过 / 0 失败**（复跑脚本见附录 C） |
| 47 | `/browser/call` 90s 重复守卫 | 200 + `{ok:false,duplicate:true}`；换 args 后 200 `ok:true` |
| 48 | 两轮真实训练全周期 | `t1_med_lora_v1` → pid 39858→68526；`t1_law/cs_lora_v1` → pid→76196；每轮都 5/5↔4/5 往返 |

# 附录 B：本次发现的「文档/直觉 vs 实现」不一致清单（9 处）

| # | 说法（文档/直觉） | 实现真相 | 证据 |
|---|---|---|---|
| 1 | `finish_reason: "sim_router_deterministic"` 是降级标志 | **只有 `/chat/tools` 的该分支有 `finish_reason`**；普通 `/chat/tools`、`/chat/completions`、SSE 流**都没有这个字段** | 【实测】+【代码核实 7037】 |
| 2 | `hallucination.repair` 是响应里的子字段 | 实际是**独立端点** `POST /hallucination/repair`，且**当前 404**；`/chat/tools` 的 `hallucination` 无 `repair` 键 | 【实测 404】+【代码核实 6080】 |
| 3 | `/chat/tools` 与 `/chat/completions` 都是"chat" | **默认模型相反**：前者默认 `openai:deepseek-chat`（云端），后者默认 `vllm`（本地） | 【实测】§2.1 |
| 4 | `confidence` 的 `threshold.high_min=0.75` 是分档阈值 | **不是**。真正分档用模型文件里的 `prob_thresholds={high_max:0.7, mid_max:0.85}`；`high_min` 是旧的 `1-conformal_q` 展示值 | 【代码核实 `confidence_live.py:128-144`】+ 模型文件 |
| 5 | `GET /settings` 能看到全部配置 | **看不到 `browser_sidecar`**；`PUT /settings` 只收 5 个键，其余**静默忽略** | 【实测】§5.3 |
| 6 | 代码有的端点就能用 | `/hallucination/repair` 与 `/chat/tools` 的 sim-router 兜底**磁盘有、运行进程无**（进程比代码旧 56 分钟） | 【实测】§4.6 |
| 7 | `/vllm/recommend?name=<模型>` 能给出该模型的推荐参数 | **完全忽略 `name`**：传 `Qwen2.5-7B-Instruct-AWQ` / `Qwen3-4B-AWQ` / 不传，三者返回**逐字节相同**的 `{"model_name":"","size_gb":51.26,"max_model_len":1024,...}`。`max_model_len:1024` 与真实 32768 差 32 倍 | 【实测】§1.4 |
| 8 | 训练任务完成后还能稳定查到它的 `status` | **不稳定**：同一个 id 实测既有"完成后立刻 404、`/training/list` 变空"，也有"完成后仍列在 `/training/list` 里 `status=success`"。`404` 不代表失败 | 【实测】§4.4 |
| 9 | `upload_dataset` 同名会覆盖或自动改名 | **直接 400 拒绝**：`{"detail":"'<name>.jsonl' 已存在"}`。既不覆盖也不加后缀 ⇒ 调用方必须自己保证 name 唯一 | 【实测】§2.5 |

# 附录 C：文档自身的验证方法（可复现）

```bash
# 抽出文档里所有 curl 并逐条复跑（跳过会改状态/启停服务的）
bash /mnt/e/WSL/_check/dsh_verify_curls.sh
# 结果落在 /mnt/e/WSL/_check/out_curlverify3.txt（会先清掉同名数据集，跑完自动恢复 kb_mounted）
```

本轮结果：**39 条 curl（含 §六 快速卡表格里的每一条），34 条通过，5 条按硬约束跳过，0 失败。**

> 提取器同时抓两类写法：① 行首的 curl 命令；② 表格单元格里被反引号包住的 curl 命令。所以快速卡里的 12 条也都被真跑过。

修正记录（初稿 → 定稿）：
| 问题 | 修正 |
|---|---|
| `upload_dataset` 示例引用不存在的 `/tmp/probe.jsonl`（首跑 HTTP 000） | 改为自包含 `cat > /tmp/ds_demo.jsonl` 再 `-F`，复跑 200 |
| 误以为同名上传会"自动加后缀" | 实为 **400 `'<name>.jsonl' 已存在`**，已改写并升级为附录 B #9 |
| 曾建议用 `/vllm/recommend?name=` 取 `max_model_len` | 实测该端点忽略 `name`，改为从 `:8000/v1/models` 取，记入附录 B #7 |
| 快速卡里 `browser/call` 只带 `Origin`（403） | 改为先取 token 再带 `X-Sidecar-Token`，实测 200 |
| 曾断言"训练完成后 `/training/status` 一定 404、`/training/list` 一定清空" | 两轮实测结果不一致（一轮回收、一轮保留 `success`），已改为"留存不稳定 + 三条可靠完成信号"，见 §4.4 |

**未实跑项（明确标注）**：
| 项 | 原因 | 已在文中如何标注 |
|---|---|---|
| `POST /vllm/start` / `stop` / `switch` | 任务硬约束：不要停/启 vLLM | 只给命令，标注【代码核实】 |
| 真实 `POST /training/start`（合法参数） | 任务硬约束：不要训练 | 只发**必然 400/422** 的校验请求证明字段名；真跑示例明确标「本文按约束未执行」 |

---

**文档版本**：v1.0 · 实测时间 2026-10-22 13:05–13:40（约 35 分钟） · 实测脚本存于 `/mnt/e/WSL/_check/dsh_*.py`、`dsh_*.sh`，输出存于 `out_*.txt`（curl 逐条复跑结果见 `out_curlverify3.txt`）。
**与《平台 API 使用手册》的分工**：本文只定义 DSH 如何接入、如何路由、如何降级；端点内部的业务字段语义（如 `/workflow/*`、`/docs/*`、`/reflect/*` 的完整语义）以那份为准。
