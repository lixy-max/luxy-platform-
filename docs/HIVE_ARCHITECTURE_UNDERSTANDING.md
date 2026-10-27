# 蜂巢（Hive）架构理解 + 两项技术方案

**交付**：① 我对蜂巢的工程化理解与现状对照 ② 27B Orchestrator 路由 JSON 的 System Prompt 结构 ③ vLLM/SGLang 多 LoRA 动态加载与路由实现方案。**日期：2026-10-27**

## 0. 一句话理解

蜂巢 = **一个会自我进化的本地推理集群**：27B（或 14B+）中枢做"拦截/意图/拆解/路由"，本地工蜂用动态 LoRA 干活；干不动就整包 Context 甩给云端教师；云端成功轨迹被清洗、打标、入库，同类型轨迹攒够阈值就自动在 3060 上训 LoRA，**热加载回工蜂**——同类任务下次不再出云。另有一条独立的"仿真/压测"链路模拟并发与脏数据，保护本地库。

## 1. 把"蜂巢"翻译成工程模块

| 蜂巢概念 | 工程模块（建议命名） | 职责 | 关键接口/数据 | 当前仓库现状 |
|---|---|---|---|---|
| 中枢调度器 Orchestrator | `hive/orchestrator`（现有 orchestrator.py 升级） | 请求拦截、意图识别、任务拆解、**三路由决策 Local/Cloud/Sim**、失败阈值/超时判定 | `POST /hive/decide` → 决策 JSON（含 target/confidence/subtasks/fallback） | ⚠️ 部分：orchestrator.py 只有 local_7b/cloud 两路由且是规则式、只出计划不执行；14B 主脑可用，27B 未部署 |
| 云端教师 Cloud Teacher | `hive/cloud_teacher`（cloud_bridge + cloud_workflow_builder） | 高难任务兜底；输出成功答案 + **多步操作轨迹**；限流/成本记账 | provider(DeepSeek/Kimi/GLM)、context 打包、trajectory 回传 | ✅ 基本可用：DeepSeek/Kimi/GLM 已接；cloud_build 有 golden+工作流；⚠️ 缺"通用轨迹"结构（当前是工作流 JSON，不是操作轨迹） |
| 工蜂执行器 Worker Nodes | `hive/worker_pool`（现 swarm worker + lora_manager /loras） | 本地 7B~14B 多节点；按任务**动态挂载专项 LoRA**执行 | vLLM `/v1/load_lora_adapter`、`model=<lora_id>`、worker 池/槽位 | ⚠️ 部分：单工蜂 7B(:8002) 已开 --enable-lora(max-loras 2, rank128, cpu 4)，load/unload 已封装；**多节点池+路由缺失** |
| 仿真节点 Simulation Node | `hive/sim`（新模块；现有 sim_nodes.py 是算子库） | 独立流量发生器：局域网并发/压测路由/合成数据/**拦截脏数据**（隔离区，不污染 KB） | 场景脚本、并发数、QPS、脏数据判定、压测报告 | ❌ 缺失：现有 `sim_nodes.py` 只是 6 个确定性算子（几何/数学/物理/统计…），不是流量发生器；`sim_guard` 只约束"计算必须走仿真" |

## 2. 核心数据链路（自动进化飞轮）→ 工程映射

| 飞轮步骤 | 工程实现 | 现状 |
|---|---|---|
| ① Failover 触发 | Worker 超时/失败 → Orchestrator 决策升级（阈值：超时 T、重试 N、连续失败 K） | ⚠️ 部分：任务表 item 失败可 escalate_cloud；缺全局超时/失败阈值判定 |
| ② 云端兜底 | Cloud Teacher 收完整 context → 返回 answer + trajectory（tool calls/steps/status） | ⚠️ 部分：DeepSeek 已接；cloud_workflow_builder 产物是 workflow JSON+golden，缺通用 trajectory schema |
| ③ 轨迹归因/质检 | 27B 中枢清洗：过滤本地没有的工具调用、过长/超纲步骤；打标签入库 | ⚠️ 部分：有 decompose/score/lint/工具文档；缺"云轨迹→本地可用"过滤 + 分类标签库 |
| ④ 自动触发微调 | 按任务类型统计高质量轨迹数 ≥ 阈值 → 调 3060 训练 LoRA/小模型全量微调 | ❌ 缺失：训练是手动的（我们刚手动训了 workflow_author_v1）；缺阈值守护进程 |
| ⑤ LoRA 下发热加载 | 训练产物校验→注册→vLLM 热加载→灰度→晋级；同类任务本地解决 | ⚠️ 部分：/loras/load 已封装 vLLM 热加载；缺训练完成自动触发 + 灰度/回滚 |

## 3. 我之前理解的偏差（纠正）

- 之前把主线做成了"路由任务表 + 轨迹蒸馏 + 奇美拉 + 指标"等**分散能力**，没有围绕 **"中枢三路由 → 工蜂动态 LoRA → 云端教师 → 飞轮自动微调"** 这条主轴组织；
- 仿真节点被我理解成"确定性计算节点"（sim_nodes 算子库），而你要的是**独立流量发生器/压测/合成数据/脏数据拦截**；
- 27B 中枢没部署（当前 14B AWQ 主脑）；27B 模型其实在盘上（gemma-2-27b-it-gptq-4bit，18GB），但 16GB 单卡放不下，需要双卡 TP2 或换更小量化；
- 云端教师只落了"工作流 JSON+golden"，还没落你要求的**通用多步操作轨迹**；
- 自动微调阈值触发、热加载灰度回滚都还没接成闭环。

## 4. 技术方案 A：27B Orchestrator System Prompt 结构（输出 Local/Cloud/Sim JSON）

### 4.1 设计原则

- **Inventory 注入**：把 worker/LoRA/云端额度/仿真场景/工具白名单/KB 作为运行时 JSON 注入，禁止模型假设不存在的能力；
- **枚举硬约束**：target 只能是 `local|cloud|sim`；字段用 JSON Schema 约束（guided decoding 强制）；
- **政策优先级**：sim（测试/压测/合成/脏数据）→ local（能力覆盖+预算够）→ cloud（缺能力/高风险/失败阈值）；
- **可执行性**：local 分支必须给出 `worker_id + lora_id`；cloud 分支给 `provider/model + context_budget`；sim 分支给 `scenario + 并发/时长 + 脏数据策略`；
- **失败兜底**：给 `fallback_chain` 与 `timeout/retry`；JSON 非法两次 → 规则路由兜底并记录审计。

### 4.2 System Prompt 模板（可直接使用）

```
你是「蜂巢中枢调度器」（7B~27B 本地模型），负责把用户请求路由到三种执行体之一：
  local（本地工蜂 + 动态 LoRA）、cloud（云端教师）、sim（仿真节点）。

【硬约束】
1) 只输出一个 JSON 对象，以 { 开始、以 } 结束，不要 markdown/解释/多余文本。
2) 只能使用下方 INVENTORY 中真实存在的能力；禁止编造 worker_id / lora_id / provider / tool。
3) target ∈ {"local","cloud","sim"}；无法判断时 target="local" 且 confidence<0.5，并给 clarification。
4) 只要请求涉及"压测/并发/模拟/合成数据/测试脏数据" → 必须 target="sim"。
5) local 必须给出 routing.worker_id + routing.lora_id（可为 "base"）；能力不足 → cloud。
6) cloud 必须给出 cloud.provider/model（来自 INVENTORY）+ cloud.context_policy（full/digest）+ 原因。
7) 若命中 failover（错误次数≥阈值或超时风险高）→ target="cloud"，routing.escalate=true。
8) 你的输出会被 JSON Schema 校验；字段缺失/非法将被拒绝。

【INVENTORY】（运行时注入，每次请求可能不同）
{
  "workers":[{"id":"w1","gpu":"3060-12G","base":"Qwen2.5-7B-AWQ","status":"ready",
              "max_loras":2,"loaded":["code_v2"],"slots_free":1,"p95_latency_ms":180}],
  "loras":[{"id":"code_v2","capability":["code","sql"],"rank":16,"score":0.86,"base":"7B"},
           {"id":"format_v1","capability":["json","table"],"rank":8,"score":0.79,"base":"7B"}],
  "cloud":[{"provider":"deepseek","model":"deepseek-flash","rpm":60,"cost":"low"},
           {"provider":"kimi","model":"kimi-k2.6","rpm":3,"cost":"medium"}],
  "sim":[{"scenario":"http_concurrent","max_users":500},{"scenario":"dirty_data","sink":"quarantine"}],
  "tools":["write_sheet","read_sheet","paper_lora_kb_query","browser_open"],
  "kbs":["qiaojiao_toys_2025_v2"],
  "policy":{"sim_keywords":["压测","并发","模拟","合成","脏数据"],
            "failover":{"max_errors":3,"timeout_s":60},"daily_cloud_budget":200}
}

【ROUTING POLICY（按序判断）】
S1 若请求/目标命中 sim_keywords 或是"生成测试数据/压测路由" → sim
S2 若任务类型 ∈ worker+lora 能力 且 估算 tokens ≤ worker 预算 → local（选分数最高且空闲的 worker/lora）
S3 若 缺工具/缺 LoRA/上下文超本地预算/高风险/失败次数≥max_errors → cloud
S4 其他 → local(base)，confidence 低 + clarification

【OUTPUT JSON（严格）】
{
  "request_id":"...",
  "target":"local|cloud|sim",
  "confidence":0.0-1.0,
  "task_type":"code|rag|format|data|workflow|other",
  "intent_summary":"一句话",
  "subtasks":[{"id":1,"desc":"...","executor":"local:code_v2|cloud:deepseek-flash|sim:http_concurrent"}],
  "routing":{
    "worker_id":"w1|null","lora_id":"code_v2|null","why":"...",
    "timeout_s":60,"max_retries":1,"escalate":false
  },
  "cloud":{"provider":"deepseek|null","model":"deepseek-flash|null",
           "context_policy":"full|digest","why":"...","est_cost":"low|medium|high"},
  "sim":{"scenario":"http_concurrent|dirty_data|null","users":0,"duration_s":0,
         "dirty_policy":"quarantine|reject|null","report_to":"...|null"},
  "fallback_chain":["local:code_v2","cloud:deepseek-flash"],
  "needs_clarification":false,
  "reason_trace":["S2: 能力命中 code_v2","S2: tokens 3.2k ≤ 8k"]
}

【示例1｜local】用户："把这份表格按单价排序"
{"request_id":"r1","target":"local","confidence":0.9,"task_type":"data",
 "intent_summary":"表格排序","subtasks":[{"id":1,"desc":"排序并写回","executor":"local:format_v1"}],
 "routing":{"worker_id":"w1","lora_id":"format_v1","why":"格式/表格能力命中","timeout_s":60,"max_retries":1,"escalate":false},
 "cloud":{"provider":null,"model":null,"context_policy":"digest","why":"","est_cost":"low"},
 "sim":{"scenario":null,"users":0,"duration_s":0,"dirty_policy":null,"report_to":null},
 "fallback_chain":["local:format_v1","cloud:deepseek-flash"],"needs_clarification":false,
 "reason_trace":["S2: format_v1 score .79","S2: tokens 1.1k"]}

【示例2｜cloud】用户："把这份合同和最新法规逐条比对，给出风险与修改建议"
{"request_id":"r2","target":"cloud","confidence":0.84,"task_type":"rag",
 "intent_summary":"合同条款与法规比对","subtasks":[{"id":1,"desc":"逐条比对并给修改建议","executor":"cloud:deepseek-flash"}],
 "routing":{"worker_id":null,"lora_id":null,"why":"需最新法规 grounding 与长链推理，本地能力不足","timeout_s":180,"max_retries":1,"escalate":true},
 "cloud":{"provider":"deepseek","model":"deepseek-flash","context_policy":"full","why":"缺法规检索工具+长上下文","est_cost":"low"},
 "sim":{"scenario":null,"users":0,"duration_s":0,"dirty_policy":null,"report_to":null},
 "fallback_chain":["cloud:deepseek-flash","local:base"],"needs_clarification":false,
 "reason_trace":["S3: tools 缺 browser/法规库","S3: 失败阈值未到但风险高"]}

【示例3｜sim】用户："用 500 并发压一下我们的路由，看看会不会把脏数据写进知识库"
{"request_id":"r3","target":"sim","confidence":0.95,"task_type":"other",
 "intent_summary":"并发压测+脏数据拦截验证","subtasks":[{"id":1,"desc":"500 并发压测","executor":"sim:http_concurrent"},
   {"id":2,"desc":"注入脏数据并验证隔离","executor":"sim:dirty_data"}],
 "routing":{"worker_id":null,"lora_id":null,"why":"测试请求不消耗本地推理","timeout_s":600,"max_retries":0,"escalate":false},
 "cloud":{"provider":null,"model":null,"context_policy":"digest","why":"","est_cost":"low"},
 "sim":{"scenario":"http_concurrent","users":500,"duration_s":120,"dirty_policy":"quarantine","report_to":"data/sim/reports/"},
 "fallback_chain":["sim:http_concurrent"],"needs_clarification":false,
 "reason_trace":["S1: 命中 压测/并发/脏数据"]}
```

### 4.3 强制 JSON 的工程手段（不靠"祈祷模型听话"）

- **vLLM**：请求带 `response_format={"type":"json_schema","json_schema":{"name":"route","schema":{...}}}`（0.11 支持 guided decoding），schema 即上面的输出结构；
- **SGLang**：用 `regex`/`json_schema` 约束（`sampling_params` 支持），或 `--grammar-backend`；
- **双层校验**：schema 校验 → 失败把错误回喂模型重试 1-2 次 → 仍失败走**规则路由兜底**（关键词+能力表），并写审计；
- **决策日志**：请求/INVENTORY 摘要/决策/延迟/后续结果 → `data/hive/decisions.jsonl`，用于后续蒸馏"路由 LoRA"。

## 5. 技术方案 B：vLLM/SGLang 本地多 LoRA 动态加载与路由

### 5.1 现状（本机实测）

| 项 | 现状 |
|---|---|
| 推理框架 | **vLLM 0.11.0** 已装；SGLang 未安装 |
| 工蜂启动 | `--enable-lora --max-loras 2 --max-lora-rank 128 --max-cpu-loras 4`（7B AWQ / 3060 12G） |
| 热加载 API | 平台 `/loras/load` → vLLM `/v1/load_lora_adapter`；`/loras/unload` → `/v1/unload_lora_adapter`（需 `VLLM_ALLOW_RUNTIME_LORA_UPDATING=1`） |
| 请求选 LoRA | OpenAI 兼容 `model=<lora_name>`；vLLM 在 batch 内按请求切 LoRA |
| 缺口 | **多 worker 节点池 / LoRA 注册表 / 按能力路由 / 训练完成自动热加载+灰度回滚** 都还没有 |

### 5.2 目标实现架构（工程化）

```
用户请求
  → Hive Orchestrator（27B/14B，JSON 决策）
      target=local → LoRA Router
          ├─ LoRA Registry（SQLite）：lora_id / base / rank / capability标签 / eval分数 / 版本 / 状态
          ├─ Worker Pool Registry：node_id / endpoint(:8002..) / GPU / base / max_loras / loaded / inflight
          ├─ 选型：能力匹配 → eval 分数排序 → 选有空槽的节点
          ├─ 未加载 → POST /v1/load_lora_adapter（必要时 LRU unload 腾槽）
          └─ 调 worker /v1/chat/completions，model=<lora_id>
      target=cloud → Cloud Teacher（DeepSeek/Kimi）+ 轨迹回收
      target=sim   → Sim 节点（压测/合成/脏数据隔离）
```

### 5.3 vLLM 具体实现（本机可跑）

```
# 1) 工蜂节点（每 GPU 一个 vLLM 实例；3060 12G 例）
VLLM_ALLOW_RUNTIME_LORA_UPDATING=1 CUDA_VISIBLE_DEVICES=1 \
python -m vllm.entrypoints.openai.api_server \
  --model /home/luxy/models/Qwen2.5-7B-Instruct-AWQ --served-model-name base-7b \
  --host 127.0.0.1 --port 8002 --enable-lora --max-loras 4 --max-lora-rank 64 \
  --max-cpu-loras 16 --enable-prefix-caching --dtype half

# 2) 动态加载 LoRA（毫秒~秒级，热加载）
curl -X POST http://127.0.0.1:8002/v1/load_lora_adapter \
  -H 'Content-Type: application/json' \
  -d '{"lora_name":"code_v2","lora_path":"/home/luxy/loras/code_v2"}'

# 3) 按 LoRA 路由请求（OpenAI 兼容，model 填 lora 名）
curl -X POST http://127.0.0.1:8002/v1/chat/completions \
  -d '{"model":"code_v2","messages":[{"role":"user","content":"写个SQL"}],"max_tokens":512}'

# 4) 卸载（腾槽/回滚）
curl -X POST http://127.0.0.1:8002/v1/unload_lora_adapter \
  -d '{"lora_name":"code_v2"}'

# 5) 底座直跑（不挂 LoRA）：model=base-7b
```

- `max-loras` = 同时在 GPU 上的适配器数；`max-cpu-loras` = CPU 缓存数（换出/换入更快）；
- **rank 必须 ≤ max-lora-rank**，超出会拒绝加载；同一节点上的 base 必须一致（7B LoRA 不能挂 14B）；
- 多 LoRA 同 batch：vLLM 按请求的 `model` 选 LoRA，混合批处理吞吐优于串行；
- 监控：`/v1/models` 列出已加载 LoRA；加载耗时/显存/命中率写入指标。

### 5.4 SGLang 备选（为什么备选 + 什么时候换）

| 维度 | vLLM 0.11（本机已装） | SGLang（未装） |
|---|---|---|
| 多 LoRA 配置 | `--enable-lora --max-loras --max-lora-rank --max-cpu-loras` | `--enable-lora --lora-paths --max-loras-per-batch`（版本需核查） |
| 动态加载/卸载 | `/v1/load_lora_adapter`、`/v1/unload_lora_adapter` 成熟 | 新版有 load/unload API，成熟度低于 vLLM（需版本验证） |
| 路由兼容 | OpenAI `/v1/chat/completions`，model=lora 名 | OpenAI 兼容层（`/v1/chat/completions`）可用，model 选择类似 |
| 优势 | 生态成熟、本机已验证、迁移成本 0 | 高并发/长上下文性能更好（RadixAttention），多 LoRA 批调度潜力大 |
| 建议 | **一期继续用 vLLM**（本机已验证，风险最低） | 二期压测后若 QPS 不够再评估迁移；路由层保持 OpenAI 兼容，切换不改业务代码 |

### 5.5 Worker 池 / 路由 / 热加载流水线（平台要补的）

```
# LoRA Registry（SQLite 表）
loras(id, base_model, path, rank, capability_json, eval_score, version, status)
  status: shadow(待灰度) / active / deprecated / broken

# Worker Pool（内存 + 心跳）
workers(node_id, endpoint, gpu, base_model, max_loras, loaded[], inflight, p95)

# 路由伪代码
def route_local(task_caps):
    cands = [l for l in active_loras if l.capability & task_caps]
    for l in sorted(cands, key=-eval_score):
        w = pick_worker(base=l.base, need_free_slot=True)
        if not w: continue
        if l.id not in w.loaded:
            if w.free_slots == 0: unload_lru(w)
            load_lora(w, l)               # 热加载
        return w, l
    return None                            # → cloud 兜底

# 训练完成 → 热加载流水线（自动）
train_done(adapter_dir)
  → 校验 adapter_config.base_model 与目标 worker 一致
  → 影子评测（eval set，质量/合法率 ≥ 基线）
  → 注册 status=shadow；小流量灰度（5%）→ 晋级 active / 回滚
  → vLLM load_lora_adapter 热挂（无需重启）
  → 更新 Router 表（能力标签/分数/版本）
```

容量公式（单卡估算）：可用显存 = 总显存 − 基座权重 − KV cache − 系统开销；7B-AWQ≈5.6GB、14B-AWQ≈10GB、27B-4bit≈18GB；LoRA(r16)≈50–100MB/个，r64≈200–400MB；`max-loras` 由剩余显存与并发需求决定（3060 12G 建议 2–4，16G 卡建议 4–8）。

## 6. 落地路线图（建议）

| 阶段 | 内容 | 验收 |
|---|---|---|
| S18 | 27B Orchestrator System Prompt + JSON Schema + guided decoding + 校验/兜底；先用 14B 跑通三路由（Local/Cloud/Sim）= "决策可用" | 决策 JSON 合法率 ≥98%；local/cloud/sim 各 5 例人工核对正确 |
| S19 | LoRA Registry + Worker Pool + 能力路由 + vLLM 热加载/LRU 腾槽（多节点） | 并发 20 请求跨 2 个 LoRA 正确命中；热加载 <5s；无重启 |
| S20 | Failover 门限（超时/失败数）+ 云端教师通用轨迹回收（answer+steps+status→JSONL） | 故意让本地失败 → 自动升级云端并回收轨迹 |
| S21 | 轨迹质检（本地工具白名单过滤/长度/步骤上限/标签分类）+ 阈值守护自动训练 | 某类轨迹攒够 N 条自动起训，产出 adapter+评测报告 |
| S22 | 热加载流水线（校验/影子/灰度/回滚）+ 仿真节点（压测/合成/脏数据隔离）+ 指标看板 | 新 LoRA 自动上线；压测报告；脏数据 0 入库 |

## 7. 诚实边界

- **27B 未部署**：盘上有 gemma-2-27b-it-gptq-4bit（18.3GB）/ Qwen3-32B-AWQ（19.3GB），但单卡 16GB 放不下；需双卡 TP2（16+12GB，异构会降速）或换成更小量化/云端。当前实际中枢是 **14B AWQ**（10GB）；
- **仿真节点缺失**：现有 sim_nodes 是 6 个确定性算子（几何/数学/物理/比例/统计/单位换算），不是流量发生器；压测/合成/脏数据隔离需新建；
- **自动微调/热加载闭环缺失**：训练与 /loras/load 都是手动；阈值守护、影子/灰度/回滚未实现；
- 云端教师目前只产出"工作流 JSON+golden"，通用多步轨迹 schema 与"本地工具白名单过滤"未实现；
- SGLang 未安装未实测，其 LoRA 动态 API 成熟度需按版本验证；一期用 vLLM。
