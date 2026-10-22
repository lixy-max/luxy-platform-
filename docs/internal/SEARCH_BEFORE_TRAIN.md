# 先找不训（Search Before Train）· T2 交付 · 2026-10-19

> 对应 `docs/PROJECT_PRINCIPLES.md` **原则二**
> 用户要求：「当用户想要一个什么样的模型，不是直接闷头训练，而是优先去找（刚好我们也有浏览器自动化）」

---

## 一、这是什么

`tools/search_existing.py` + `/tools/search_existing` 接口 + `search_existing_models` Agent 工具

**一条命令/一次调用，回答："这个能力有没有现成的？"**

```
用户: "我想要一个工具调用更强的模型"
        ↓
  search_existing_models("function calling")
        ↓
  ★ 结论: [A] 有现成模型可直接用 ✓
    · Salesforce/Llama-xLAM-2-8b-fc-r（8B · 下载 98k+ ✓）
    · MadeAgents/Hammer2.1-7b（7B · 榜单第一系 ✓）
    · Nexusflow/NexusRaven-V2-13B（13B · 超 GPT-4 系 ✓）
    下一步: 下载 → 和评测口径对比 → 能用就【不要训】✓
```

---

## 二、用法

### 命令行
```bash
# 全面搜（模型 + 项目 + 论文）
python tools/search_existing.py "function calling"

# 只搜模型
python tools/search_existing.py "角色扮演" --kind model

# 只搜 LoRA
python tools/search_existing.py "chinese" --kind lora

# 只搜项目 / 论文
python tools/search_existing.py "tool retrieval" --kind repo
python tools/search_existing.py "tool retrieval" --kind paper

# 机器可读（给前端/Agent 用）
python tools/search_existing.py "xLAM" --json
```

### API（平台内）
```bash
curl -X POST http://127.0.0.1:8001/tools/search_existing \
  -H 'Content-Type: application/json' \
  -d '{"query":"function calling","kind":"all","limit":8}'
```

### Agent 工具（对话里直接说）
```
"帮我找找有没有现成的工具调用模型"
⇒ Agent 调 search_existing_models ⇒ 返回候选 ✓
```

---

## 三、检索源（实测可用 ✓）

| 源 | 状态 | 说明 |
|---|---|---|
| **hf-mirror.com** | ✅ **200** | HuggingFace 国内镜像 ✓ 模型 + LoRA ✓<br>（HF 主站被墙 ✗ 000；镜像可用 ✓ 不需要代理 ✓） |
| **GitHub API** | ✅ 200 | 开源项目 ✓（已降噪：排除 awesome/list 类 ✗） |
| **arXiv API** | ⚠ 限流 | 论文 ✓（429 需重试 ✓ 已实现 3 次重试 ✓） |
| **ModelScope** | ❌ 404 | 端点没找对 ✗（待补 ⚠ 可选） |
| **Clash 代理** | ⚠ 未启用 | 用户有梯子 ✓ 但镜像够用 ✓ 代理可留作备用 ✓ |

---

## 四、核心逻辑

### 4.1 已知优秀模型快查表（curated ✓ 人工核实过）
| 能力 | 已知优秀模型 |
|---|---|
| **工具调用** | Salesforce/Llama-xLAM-2-8b-fc-r（8B ✓ 98k 下载）· MadeAgents/Hammer2.1-7b（7B ✓）· Nexusflow/NexusRaven-V2-13B · Trelis/Llama-2-7b-function-calling-v2 |
| **角色扮演** | NousResearch/Hermes-3-Llama-3.1-8B · Sao10K/L3-8B-Stheno-v3.2 · microsoft/Phi-3-mini-4k-instruct |
| **向量** | BAAI/bge-m3（我们已在用 ✓）· BAAI/bge-small-zh-v1.5 |
| **中文** | Qwen/Qwen2.5-7B-Instruct（我们底座 ✓）· Qwen/Qwen3-8B |

**⇒ 快查表先命中 ⇒ 再补实时搜索 ✓**（快 + 准 ✓）

### 4.2 同义词多查询合并 ✓
```
"function calling" ⇒ 自动搜: function calling / function-calling / tool calling / tool-calling / tool use
"角色" ⇒ 自动搜: roleplay / role-play / character / persona
"中文" ⇒ 自动搜: chinese / zh / qwen / baichuan
⇒ 避免漏搜 ✓
```

### 4.3 分级结论（★ 核心价值）
| 等级 | 含义 | 建议 |
|---|---|---|
| **[A]** | 有现成模型可直接用 ✓ | **下载 → 对比 → 能用就不训** ✓ |
| **[B]** | 有现成 LoRA / 工程方案可借 ✓ | 下载 LoRA 挂 / 照方案实现 ✓ |
| **[C]** | 没找到现成方案 ✗ | **这时才考虑自己训** ✓ + 先做 2h 小验证 ✓ |

### 4.4 适配性判断（16GB 卡 ✓）
```
params ≤ 9B ⇒ 标 ✓ 适配（fp16 训练可行 ✓）
params > 9B ⇒ 标 ✗ 大（16GB 卡训不了 ✗ 但推理用 AWQ 可以 ✓）
LoRA 检测：tags 含 lora/adapter/peft ✓ GGUF 只算量化 ✗ 不误判 ✓
许可标记：apache-2.0/mit/llama3 等 ⇒ commercial=True ✓
```

---

## 五、真实测试结果

### 测试 1：`function calling`
```
【⭐ 已知优秀模型】
  · Salesforce/Llama-xLAM-2-8b-fc-r   8B  （下载 98k+ ✓）
  · MadeAgents/Hammer2.1-7b           7B  （榜单第一系 ✓）
  · Nexusflow/NexusRaven-V2-13B      13B  （超 GPT-4 系 ✓ 稍大）
  · Trelis/Llama-2-7b-function-calling 7B（较早 ✓）

【搜到的模型】4 条 ·【项目】4 条 ·【论文】4 条
★ 结论: [A] 可直接用 ✓ → 下载对比 → 能用就【不要训】✓
```

### 测试 2：`角色扮演`（中文）
```
【⭐ 已知优秀模型】3 条
  · NousResearch/Hermes-3-Llama-3.1-8B   8B
  · Sao10K/L3-8B-Stheno-v3.2             8B
  · microsoft/Phi-3-mini-4k-instruct     3.8B
★ 结论: [A] 可直接用 ✓
```

### 测试 3：具体模型可搜到 ✓
```
NexusRaven → TheBloke/NexusRaven-V2-13B-GGUF（6072 下载 ✓）
Hammer     → MadeAgents/Hammer2.1-0.5b ✓（还有更新的 2.1 版 ✓）
xLAM       → Salesforce/Llama-xLAM-2-8b-fc-r（98,471 下载 ✓✓）
```

---

## 六、踩过的坑（记录 ✓ 以后不再犯）

| # | 坑 | 解法 |
|---|---|---|
| **1** | HuggingFace 主站被墙 ✗（000） | 用 **hf-mirror.com** ✓ 直连 200 ✓ 不需要代理 ✓ |
| **2** | WSL2 里 `127.0.0.1` ≠ Windows 代理 ✗ | 要用网关 IP（172.20.64.1）✓ 但镜像够用 ✓ |
| **3** | GitHub 搜索噪音大 ✗（搜 "function calling" 出来 awesome-go ✗） | 改 `in:name,description` + 排除 awesome/list/tutorial ✓ |
| **4** | LoRA 误判 ✗（GGUF 被当成 LoRA ✗） | 先排除 gguf ✓ 再查 lora/adapter/peft ✓ |
| **5** | **`lora_manager.py` 里 `import json as json_lib`** ✗ 没有 `json` ✗ | 用 `json_lib.loads` ✓（模块惯例 ✓） |
| **6** | subprocess 子进程编码 ✗（父进程 GBK 解 UTF-8 ✗） | 显式 `encoding='utf-8', errors='replace'` ✓ |

---

## 七、成本 vs 收益（原则二的价值）

| 做法 | 耗时 | 成功率 | 说明 |
|---|---|---|---|
| **搜现成（本工具）** | **10 秒** | 高 ✓ | 一次查询出 4 个候选 ✓ |
| 自己训 LoRA（v3-2-0/1/2） | 每轮 **~12h**（含数据/训练/A/B） | 三轮 **1 次边缘** ⚠ | 追总分方向错 ✗ |
| 对照 | **快 4300 倍** ✓ | 更好 ✓ | 这就是原则二的意义 ✓ |

---

## 八、后续可扩展（不用现在做 ✓）

| 扩展 | 优先级 |
|---|---|
| ✅ 已有：hf-mirror + GitHub + arXiv | 够用 ✓ |
| ⚠ 可选：ModelScope 端点补全 | 低（hf-mirror 已够） |
| ⚠ 可选：Clash 代理接入（访问 HF 主站完整 API ✓） | 低 |
| ⚠ 可选：前端加「找现成模型」按钮（UI 卡片） | 中（等 UI 阶段 ✓） |
| ⚠ 可选：搜索结果**自动下载 + 本地评测对比** | 高（下一步 T4 可复用 ✓） |

---

*T2 交付 · 2026-10-19 · 代码：`tools/search_existing.py` · 路由：`lora_manager.py /tools/search_existing` · 工具：`agent_kb/_tools.json search_existing_models`*
