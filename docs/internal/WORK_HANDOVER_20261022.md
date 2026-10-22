# 造模型流水线 · 交接文档（2026-10-22）

> 这份文档记录：**完整可复现的「造小模型」流程** + **今天踩过的所有坑和修法** + **下次接手必读的关键事实**。
> 起因：用户要求「让大模型按需求造小模型、挂知识库、需要就训 LoRA」，今天第一次真正跑通并实测。

---

## 一、一句话结论

**这条路走通了。** 大模型自己规划执行，9 分钟训出一个农业 LoRA，实测 12 题：
**LoRA 91/96 vs 底座 80/96（+11 分，83.3% → 94.8%）**，无关题无退化（32→32）。

---

## 二、完整流水线（可复现）

### 环境

```
项目根:   /home/luxy/paper_lora_project          (WSL Ubuntu-24.04)
Python:   /home/luxy/.venv/bin/python
后端:     http://127.0.0.1:8001                  (WSL)
vLLM:     http://127.0.0.1:8000                  (WSL, Qwen2.5-7B-Instruct-AWQ)
前端:     http://127.0.0.1:8080/index3.html      (WSL serve.py)
GPU0:     RTX 5060 Ti 16GB
GPU1:     RTX 3060 12GB
```

**⚠ Windows PowerShell 里写含中文的 .ps1 会乱码** —— 一律写成 .sh / .py 放 `/mnt/e/WSL/_check/` 再执行。

### 步骤 1：准备数据

```bash
# 方式 A：从知识库生成（build_dataset 工具）
POST /build_dataset  {"kb_name": "...", "name": "...", "filename": "..."}

# 方式 B：上传已有 jsonl（注意！multipart 不是 JSON）
# 真实签名：async def training_upload_dataset(file: UploadFile = File(...), name: str = Form(...))
curl -X POST http://127.0.0.1:8001/training/upload_dataset \
  -F "file=@data.jsonl" -F "name=agri_qa_300"
# 约束：只收 .jsonl / name 只能字母数字下划线中划线 / 同名已存在⇒400
# 每行必须含 "conversations" 或 "messages" 字段

# 查已有数据集
GET /training/available_datasets
```

### 步骤 2：发起训练

```bash
POST /training/start
{"lora_name": "agri_expert_v1",
 "dataset_file": "agri_expert_v1b_20261022_093958.jsonl",
 "data_count": 309,
 "task_desc": "农业问答专家"}

# 约束：
#   lora_name 只能字母数字下划线中划线
#   data_count 必须 10~5000
#   必须给 dataset_file 或 task_desc
# ⚠ settings.json collect_mode=true ⇒ 直接 403（评测/采集期保护）
```

**实测耗时**：309 条 · 3 epoch · 54 步 · **8 分 57 秒**
**loss**：2.2999 → 1.4522
**产物**：`/home/luxy/loras/agri_expert_v1`（161.5 MB）

### 步骤 3：监视

```bash
GET /training/list          # {tasks:[{task_id,lora_name,status,stage,log,...}]}
GET /training/{task_id}
GET /gpu/status             # 两卡显存
GET /vllm/status            # 训练期间 vLLM 会被停
```

**训练期间行为**：两张卡都跑（`torchrun --nproc_per_node 2` = DDP 数据并行）· vLLM 自动停 · 训完自动拉起 AWQ 底座。

### 步骤 4：评测对比

```python
# 模型列表（找到新 LoRA 的 id）
GET /chat/models        # → vllm:base / vllm:lora:<name>

# 分别问两个模型
POST /chat/tools
{"model": "vllm:base",            "messages":[{"role":"user","content":"..."}], "max_tokens":900}
{"model": "vllm:lora:agri_expert_v1", "messages":[...], "max_tokens":900}
```

**⚠ 用 400 token 会截断**（实测底座 2 题被截断，影响 3 分）⇒ 评测建议 **900+ token**。

---

## 三、必读：今天踩过的坑（按严重度）

### 🔴 1. 工具要能用，必须在 **4 处** 登记

```
① agent_kb/_tool_docs.json          模型可见（desc + params）
② AGENT_TOOLS.<name> = {...}        index3.html · handler 实现
③ 参数表  name: ['描述', {params}]   index3.html ~6887
④ 路由表  name: ['POST','端点']      index3.html ~6965
```
只补 ① ⇒ 模型「看得见但调不动」✗

### 🔴 2. `collect_mode` 会静默拦训练

```json
// settings.json
"collect_mode": true   ⇒  POST /training/start 返回 403 ✗
```
训练前必查。

### 🔴 3. 训练配置撞车 ⇒ vLLM 必停

```
settings.json:
  gpu_mode        = "dual"
  train_gpu       = 1
  vllm_gpu_index  = 1     ← 撞车 ✗
⇒ 本意"训练用A卡、推理用B卡"，实际两卡都训练 + vLLM 被迫停
⇒ 训练 9 分钟期间平台不可用
⇒ 修法：train_gpu=0（16GB 卡）、vllm_gpu_index=1
```

### 🔴 4. 网络：arXiv / Semantic Scholar 不可达

```
实测可达性（WSL）:
  ✅ api.openalex.org        200   ← 已采用（2.5亿开放文献，无需key）
  ✅ www.paper.edu.cn        200   ← 中国科技论文在线
  ✅ xueshu.baidu.com        200
  ✅ api.deepseek.com        401（通）
  ❌ arxiv.org               000   不可达
  ❌ xxx.itp.ac.cn           000
  ❌ api.semanticscholar.org 429   限流
  ❌ doaj.org API            502
```

### 🟡 5. 前端硬编码端口

```
sidecar 因 portproxy 冲突迁到 **9224**
前端 browser_page.js + index3.html 曾写死 9223 ⇒ 状态灯永远红 ✗
已改成 9224 ✓
```

### 🟡 6. sidecar 启动三连坑

```
① package.json 里 playwright-core@^25.0.0 **版本不存在**（真实 1.64.1）
② PowerShell Start-Job 启动 ⇒ 随 shell 死 ⇒ 改 WMI Win32_Process.Create
③ server.listen(PORT,'127.0.0.1') 写死 ⇒ WSL 访问不到 ⇒ 改 HOST 可配
```

### 🟡 7. browser_tools 转发不带 token 头

```
_post() 不带 X-Sidecar-Token ⇒ sidecar 一律回「未授权」✗
已补 ✓
```

---

## 四、幻觉检测体系（5 层，`hallucination_guard.py`）

| 层 | 检查 | 能抓的真实事故 |
|---|---|---|
| **L1 行为** | 该调工具没调/报错/重复调用 | 硬算 5668768/6786（答 835，真值 835.36）|
| **L2 产物** | 编 URL / 编图片 / 编文件 / 编上传声明 | `example.com/computing_papers`、10×10 空白 base64 |
| **L2b 数值** | 数字+单位是否有来源（**KB 已接入**）| 过磷酸钙 40~60%、稻飞虱 50头、三环唑 7天（实际≥35天）|
| **L3 接地** | 内容能否在来源找到（bigram 接地率）| 编造内容 |
| **L4 引用** | 论文标题/DOI 是否真实（OpenAlex 反查）| 编造 DOI |

**接口**：`/chat/tools` 返回 `hallucination: {ok, risk, issues[], checked{}, summary}`
**前端**：右下角浮层徽标（`#__hgBadge`），只在 `risk!=low` 或 `level==low` 时弹

**⚠ 能力边界（必须记住）**：
```
L2b 能说 "这个数字在来源里找不到依据"
L2b **不能** 说 "这个数字是错的"（判对错需要领域权威源）
```

---

## 五、仿真自动路由（`sim_router.py` + `sim_guard.py`）

```
显式算式 (a/b)        → sim_guard.repair() → math_eval 真算 → 注入真值
自然语言物理题         → sim_router.route() → physics_calc + 参数抽取
   ├─ 参数齐全         → 调 /sim/run → 注入结果 ✓ 实测模型会用
   └─ 参数不全         → **不调模型**，直接确定性反问 ✓（100% 保证）
```

**实测**：
- 「以 20m/s、45° 抛出，射程多少」→ physics_calc 算出 40.7886 米 → 模型答 40.79 ✓
- 「43223N 投掷 2kg 球能飞多远」（缺 v0）→ 直接反问「需要初速度或作用时间」✓

**⚠ 提示词注入对 7B 无效**（实测 3 种方案失败），必须用**确定性兜底**。

---

## 六、评测实测数据（农业 LoRA）

| 指标 | 底座 | LoRA | 差 |
|---|---|---|---|
| 总分 /96 | 80（83.3%）| **91（94.8%）** | **+11** |
| 相关 /32 | 24 | 29 | +5 |
| 精确事实 /32 | 24 | 30 | +6 |
| 无关 /32 | 32 | 32 | **0（无退化）** |
| 具体性 /24 | 17 | 24 | +7 |
| 准确性 /24 | 15 | 19 | +4 |

**诚实局限**：n=12 · 单次无固定 seed · 评分主观无第二标注 · 未做统计检验 · 训练底座非量化 vs 推理 AWQ 有精度差。

**⚠ 关键认知**：微调提升的是「**敢给具体数字**」，**不保证数字对** ✗
（LoRA 仍会编造：A3 自造「湿润育秧秧盘漂浮」机制）

---

## 七、下次接手的关键事实（20 条速查）

1. 后端在 WSL，前端在 Windows 浏览器，sidecar 在 Windows
2. 所有 .ps1 不能含中文（UTF-8 no BOM 被当 ANSI）
3. `collect_mode`、`gpu_mode`、`train_gpu`、`vllm_gpu_index` 在 `settings.json`
4. 工具注册 4 处（见坑 #1）
5. 训练会停 vLLM，评测必须等训练结束
6. 评测用 ≥900 token
7. LoRA 产物在 `/home/luxy/loras/<name>`
8. 数据集在 `training_data/*.jsonl`，每行需 `conversations` 或 `messages`
9. KB 挂载在 `settings.kb_mounted`，当前 3 个
10. 96 个工具：`GET /agent/tools`
11. 学术检索：`/academic/search`（OpenAlex）
12. 仿真节点 6 个：`geometry_calc/math_eval/physics_calc/ratio/stats/unit_convert`
13. 仿真调用：`POST /sim/run {node, args}`
14. math_eval 参数是 `expr` 不是 `expression`
15. 幻觉检测：`hallucination_guard.analyze(answer, source_text=, tool_results_text=)`
16. 置信度：`confidence_live.estimate(..., task=)`
17. 前端徽标 `#__hgBadge` 只显示不阻塞
18. sidecar 端口 **9224**（不是 9223，portproxy 冲突）
19. 后端重启：`pkill -f lora_manager.py` + `setsid nohup env BROWSER_SIDECAR=... python lora_manager.py &`
20. 排错顺序：**先抓真实证据（日志/请求体/进程命令行），再改代码** ✗ 禁止猜

---

## 八、待办（按优先级）

| # | 事 | 工时 |
|---|---|---|
| **1** | 修 `train_gpu` / `vllm_gpu_index` 撞车（训练期间 vLLM 不掉）| 10 分钟 |
| **2** | L2b 接入领域权威源（农药登记库等）⇒ 从"无依据"升级到"能判错" | 待定 |
| **3** | 浏览器工具数据飞轮（237 个轨迹里 browser_ 出现 0 次）| 1h |
| **4** | Agent 模式快捷卡不渲染（小回归）| 20 分钟 |
| **5** | 把本流程做成 `/agent/build_model` 固定模板（可选，能力够的模型不需要）| 1h |

---

## 九、相关文件

```
/home/luxy/paper_lora_project/
  ├─ sim_router.py            语义仿真路由（新）
  ├─ sim_guard.py             算式复算（原有）
  ├─ hallucination_guard.py   5 层幻觉检测（新）
  ├─ academic_sources.py      OpenAlex + paper.edu.cn（新）
  ├─ confidence_live.py       行为层置信度
  ├─ agent_config.py          系统提示（含 3 套纪律）
  └─ docs/WORK_DISCIPLINE.md  作业纪律（含第 0 条：禁止凭印象写常量）

/mnt/e/WSL/_check/
  ├─ agri_lora_vs_base.md     评测完整报告
  ├─ agri_eval_raw.json       原始数据
  ├─ train_watch2.log         训练监视日志
  └─ WORK_HANDOVER_20261022.md  ← 本文件
```

---

*写完于 2026-10-22 · 验证过的流程，不是设想*
