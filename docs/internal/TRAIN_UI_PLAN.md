# LoRA 训练界面改造方案（数据格式标准化 + 一键清洗）· 2026-10-19

> 用户两点要求：
> **① 底层提示词优化** —— 无论云模型还是本地模型，生成数据时都**严格按标准数据格式** ✓
> **② 用户自己上传数据** —— 新增功能：**一键排列 + 数据清洗** ✓
>
> 本文 = 代码分析 + 开源调研 + 实施方案（**不动代码** ✓）

---

## 第一部分 · 现状代码分析（逐文件实证）

### 1.1 数据生成链路（`data_generator.py`）✗ 三个问题

| 位置 | 现状 | 问题 |
|---|---|---|
| **L246** `system_prompt` | 提示词要求输出 `{"instruction": "...", "output": "..."}` | **Alpaca 格式** ✗ 不是 sharegpt ✓ |
| **L312** 转 sharegpt | `{"from": "system", "value": system_prompt}` **塞进 conversations[0]** | **致命 ✗✗** = LLaMA-Factory 位置错位 bug 的源头 ✓（我前两天实测被抓 ✓） |
| 全流程 | 生成后**没有任何格式校验** ✗ | 坏了也照单全收 ✗ 训练时才炸 ✗ |

**⚠ 关键证据**：LLaMA-Factory 官方文档明确要求
> "human and observation must appear at odd positions, while gpt and function must appear at even positions"
> `system` = **独立字段** ✗ 不能放 conversations 里 ✓

⇒ 现在的提示词**从根上就产出了非法格式** ✗✓

### 1.2 上传链路（`/training/upload_dataset`，L2456）✗ 校验形同虚设

```python
# 现状只做这些：
if not file.filename.lower().endswith(".jsonl"):  # ① 后缀
d = json_lib.loads(line)
if "conversations" not in d and "messages" not in d:  # ② 有这两个键就行
    raise ValueError(...)
```
**完全不检查**：✗ 位置顺序 ✗ system 是否混入 ✗ tools 是否字符串 ✗ 工具名白名单
✗ token 长度 ✗ 重复 ✗ system 占比 ✗ train/val 切分

⇒ **垃圾数据能一路直通训练** ✗（我实测过：430 条假变体 ✓ 全靠"人工发现"才没练 ✗）

### 1.3 训练前体检（`train_lora.py data_preflight`）⚠ 口径与标准格式**不匹配**

```python
system = _est_tokens(c[0].get("value")) if c else 0   # ← 把 conversations[0] 当 system ✗
```
- 对**旧格式**（system 混在 conversations[0]）✓ 能测对 ✓
- 对**标准格式**（system 是独立字段）✗ → `c[0]` 是 human ✗ → **system 占比算成 100%** ✗ → 会误杀正确的数据 ✗✗

⇒ **三处口径不一致** ✗：生成端 ✗ / 上传端 ✗ / 训练端 ✗ 各说各话 ✓

### 1.4 前端训练页（`webui/pages/train.js` + `#page-train`）

**已有** ✓：LoRA 名称 / 任务需求 / 格式提示 / 数据来源（生成/复用/上传）✓ / 审查开关 ✓ / 条数 / 底座 / Provider ✓ / 自动重启 ✓ / 日志 ✓
**没有** ✗：上传后**无任何反馈** ✗（不显示条数/格式/问题 ✓）/ **无清洗** ✗ / **无预览** ✗ / **无排列** ✗ / **无 train-val 切分** ✗

---

## 第二部分 · 开源方案调研（有限搜索 ✓）

| 项目 | 它做什么 | 能借鉴什么 | 是否引入 |
|---|---|---|---|
| **[LLaMA-Factory](https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html)** | 训练框架（我们已在用 ✓） | ⭐ **官方格式规范**（已写入 `docs/DATA_QUALITY_SPEC.md` ✓）+ `dataset_info.json` 的 converter 设计 ✓ | **规范 ✓ 不引代码** |
| **[NVIDIA NeMo Curator](https://github.com/NVIDIA-NeMo/Curator)** | 大规模语料 curation（去重/质量过滤/分类） | ⭐ **MinHash LSH 近似去重**算法 ✓ + 质量分类器分层思路 ✓ | 太重 ✗ 只**借算法** |
| **[distilabel](https://github.com/argilla-io/distilabel)**（argilla） | 合成数据生成 + 质量过滤 pipeline（LLM-as-judge ✓） | ⭐ **LLM 质检打分**流程 ✓ + pipeline 编排 ✓ | 可选 ✓ 只对可疑样本 |
| **[sft-dataset-formatter](https://github.com/zaoshangduziteng/sft-dataset-formatter)** | 本地优先 SFT 格式转换：JSON/JSONL/CSV → OpenAI messages → LLaMA-Factory train/val | ⭐⭐ **格式归一化 + 自动切分** 思路**直接可抄** ✓ | 思路 ✓ 自己实现（不引依赖 ✓） |
| **HuggingFace datasets** | 加载/过滤/去重 | `filter/map/unique` 组合 ✓ | 可选 ✓（已在依赖里 ✓） |
| **cleanlab** | 标签质量（分类任务） | — | ✗ 不适用 SFT ✗ |

### 调研结论
```
✗ 不引入 NeMo Curator / distilabel 这类重框架（任务太重 ✗ 依赖太多 ✗）
✓ 采用「自己写一个 ~400 行 dataset_toolkit.py」的方案：
   · 格式规范来自 LLaMA-Factory 官方 ✓（已有 spec ✓）
   · 去重算法借 NeMo Curator 的 MinHash 思路 ✓（纯 Python 实现 ✓ 零依赖 ✓）
   · 格式归一化借 sft-dataset-formatter 思路 ✓（多格式→标准 ✓ + 自动切分 ✓）
   · 可选 LLM 质检借 distilabel 思路 ✓（只对可疑样本 ✓ 省 token ✓）
```

---

## 第三部分 · 实施方案

### 3.1 核心模块：`dataset_toolkit.py`（新建 ✓ ~400 行）

```python
# ===== 唯一格式真相（生成端/上传端/训练端 三处共用 ✓）=====
SHAREGPT_SPEC = {
    'system_field': 'system',          # 独立字段 ✓ 不在 conversations 里 ✗
    'conversations_key': 'conversations',
    'tools_key': 'tools',              # JSON 字符串 ✓
    'position_rule': ['human', 'function_call', 'observation', 'function_call', ..., 'gpt'],
}

# ===== API =====
parse_any(path)              # jsonl/json/csv 自动识别 → 内部统一表示 ✓
normalize(record)            # 任意格式（alpaca/openai/sharegpt/旧格式）→ 标准 sharegpt ✓
validate(record)             # F1-F8 + Q1-Q8 → 问题清单 ✓（复用 DATA_QUALITY_SPEC ✓）
clean(records, opts)         # 修复位置/system/tools + 去重 + 截断 + 过滤 ✓
sort_records(records, by)    # 一键排列：task / length / quality / random ✓
split(records, ratio=0.1)    # 自动切 train/val ✓
report(records)              # 统计报告（可直接给前端渲染 ✓）
find_duplicates(records)     # 精确 hash + MinHash 近似 ✓
```

### 3.2 要求① —— 底层提示词优化（**统一格式 ✓ 云/本地一致 ✓**）

#### 改造点 A：提示词结构化（`data_generator.py`）
当前的「自由发挥」✗ → 改成 **严格 schema + few-shot + 自校验清单** ✓：

```
你是一个训练数据生成专家。必须**严格**输出以下 JSON 结构，一个字符都不能变：

RESPONSE_SCHEMA = {
  "samples": [
    {
      "system":   "系统提示（≤200 字符 ✓ 只放角色与纪律 ✓）",
      "human":    "用户这句话（自然口语 ✓）",
      "tool_calls": [                       # 可为 [] ✓ 不需要工具时留空 ✓
        {"name": "工具名（必须在给定清单内）", "arguments": {...}}
      ],
      "observations": [                     # 与 tool_calls 一一对应 ✓
        {...工具返回的 JSON...}
      ],
      "final":    "模型最终回答"
    }
  ]
}

## 硬性规则（违反即不合格）
1. system 是独立字段 ✗ 不能放进对话里 ✗
2. 对话顺序必须是 human → 每个 tool_call 后紧跟一个 observation → 最后 final ✓
3. tool_calls 里 name 必须在白名单内 ✓ arguments 必须是 JSON 对象 ✓
4. 不要 markdown 代码块 ✗ 不要解释 ✗ 只输出 JSON ✓

## 示例（照这个写）
{"samples":[{"system":"你是 LoRA 工作台 Agent。需要本机信息先调工具。","human":"最近的结果文件列一下","tool_calls":[{"name":"list_workflow_results","arguments":{"limit":5}}],"observations":[{"results":[{"file":"a.docx"}]}],"final":"最近的结果是 a.docx。"}]}
```

#### 改造点 B：生成后**强制校验 + 自动修复 + 重试**（关键 ✓）
```
生成 N 条
  ↓
① JSON 修复器（本地小模型常输出 ```json 包裹 ✗ / 少括号 ✗）
   · 去 markdown 包裹 ✓ · 括号补全 ✓ · 单引号转双引号 ✓
  ↓
② normalize() → validate()
   · 通过 ✓ → 收
   · 不通过 ✗ → 尝试 clean() 自动修复（位置/类型 ✓）
      · 修好 ✓ → 收（记「已修复」✓）
      · 修不好 ✗ → 丢弃并记失败原因 ✓
  ↓
③ 失败率 > 10% ⇒ 【报错并停止】✗ 不静默 ✗（这是 v3-2-0 的教训 ✓）
  ↓
④ 统计报告返回前端（合格率/修复数/失败原因 Top3 ✓）
```

#### 改造点 C：**Provider 无关**（云 + 本地同一套 ✓）
| Provider | 现状 | 改造 |
|---|---|---|
| `openai`（云） | 能出 JSON ✓ | 用同一 prompt ✓ 同一校验 ✓ |
| `vllm`（本地） | 常不按格式 ✗ | **加 JSON 修复器 + 重试** ✓ 建议用 `response_format=json_object` ✓（vLLM 支持 ✓） |
| `ollama`（本地） | 同上 ⚠ | 同上 ✓ + 降低单批条数（小模型一次 5 条比 20 条更靠谱 ✓） |

#### 改造点 D：支持**工具调用格式**（当前只支持纯文本 ✗）
- 现在的 prompt 只产 `{instruction, output}` ✗ → 无法训工具调用 ✗
- 改造后支持 `tool_calls` ✓ + `tools` 字段（JSON 字符串 ✓）

### 3.3 要求② —— 上传数据 + 一键排列 + 数据清洗（新功能）

#### 前端 UI 设计（训练页新增一块 ✓ 插在「数据来源」下方）
```
┌──────────────────────────────────────────────────────┐
│ 📤 上传数据集                                         │
│   [选择文件] .jsonl / .json / .csv    或拖拽到这里      │
├──────────────────────────────────────────────────────┤
│ 📊 自动检测报告（上传后立刻显示 ✓）                     │
│   总条数 500 · 合格 420 · 可修复 60 · 错误 20          │
│   ✅ 位置顺序  ✅ system 独立  ✅ tools 字符串          │
│   ⚠ system 占比 54%（>20% ✗）  ⚠ 重复 12 条           │
│   [查看明细]  [下载问题报告]                          │
├──────────────────────────────────────────────────────┤
│ 🧹 一键清洗（勾选后执行 ✓ 永远不覆盖原文件 ✓）          │
│   ☑ 修复格式（位置 / system / tools 类型）            │
│   ☑ 去重（精确 ✓ 近似 MinHash 可选 ⚠）                │
│   ☑ 过滤（空 / <10 字 / 超 cutoff）                   │
│   ☑ 瘦身 system（>400 字符时截断 ✓）                  │
│   ☐ 排列（按任务 / 按长度 / 按质量 / 随机）            │
│   ☑ 切分 train / val（90 / 10）                       │
│   [执行清洗 →]                                        │
├──────────────────────────────────────────────────────┤
│ ✅ 清洗结果                                           │
│   500 → 462 条（去重 12 · 修复 26 · 丢弃 0）           │
│   [预览前 5 条]  [下载 .jsonl]  [直接用于训练 ←]       │
└──────────────────────────────────────────────────────┘
```

#### 后端新接口（5 个 ✓）
| 接口 | 方法 | 作用 |
|---|---|---|
| `/training/dataset/analyze` | POST | 上传/指定文件 → 返回**统计 + 问题清单**（不写盘 ✓ 只读） |
| `/training/dataset/clean` | POST | 按选项清洗 → 生成 `_clean.jsonl` + 报告 ✓（**保留原文件** ✓） |
| `/training/dataset/preview` | GET | 预览前 N 条（渲染成人类可读 ✓） |
| `/training/dataset/sort` | POST | 一键排列（task/length/quality/random ✓） |
| `/training/dataset/split` | POST | 切 train/val ✓ |

#### 清洗算法细节
| 操作 | 实现 | 默认 |
|---|---|---|
| **修复格式** | `normalize()`：alpaca→sharegpt ✓ openai messages→sharegpt ✓ system 从 conversations 提到顶层 ✓ 位置重排 ✓ tools 转字符串 ✓ | 开 ✓ |
| **精确去重** | `hash(system + json(conversations))` ✓ | 开 ✓ |
| **近似去重** | MinHash + Jaccard ≥ 0.8 ✓（纯 Python ✓ 零依赖 ✓） | **关** ⚠（误伤风险 ✓ 需预览 ✓） |
| **过滤** | 空 / `<10 字` / `>cutoff` ✓ | 开 ✓ |
| **瘦身 system** | >400 字符截断 + 记录 ✓ | 开 ✓ |
| **排列** | 按任务分组 ✓ / 按 token 长度 ✓ / 按质量分 ✓ / 随机 ✓ | 关 ✓ |
| **切分** | 分层随机（按 `_id` 分层 ✓ 防同任务泄漏 ✓） | 开 ✓ |

#### 质量排序（可选 ✓ 借 distilabel 思路）
```
规则分（零成本 ✓ 全量用）:
  + 有工具调用 +2
  + 有真实错误观测 +2（教"改正"✓ 最有价值 ✓）
  + 长度在 200-2000 token 之间 +1
  + system 占比 <20% +1
  - 空观测 -3 / 重复 -3
⇒ 用于【排列】✓ 和【抽样检查】✓ 不做删除 ✓（避免误删）
```
LLM 质检：**只对「可疑样本」（规则分 < 0 ✓）**调用 ✓ 省 token ✓ 可选开 ✓

### 3.4 训练前体检对齐（`train_lora.py` 小修 ✓）
```python
# 现在: system = _est_tokens(c[0].get("value"))   # ✗ 标准格式下会算错
# 改成:
system_text = r.get("system") or ""              # ✓ 优先独立字段
if not system_text and c and c[0].get("from") == "system":
    system_text = c[0].get("value")              # ✓ 兼容旧格式
```

---

## 第四部分 · 实施计划（分 4 阶段 ✓ 每阶段可独立验证）

| 阶段 | 内容 | 工时 | 验收标准 |
|---|---|---|---|
| **P0** | `dataset_toolkit.py`（spec/normalize/validate/clean/report/dups/sort/split） | **4h** | 用它校验手上 4 份真实数据（v3-2-0 ✗ / v3-2-1 ✓ / v3-2-2 ✓ / 40条精标 ✗）→ **必须能准确报出各自已知问题** ✓ |
| **P1** | 后端 5 接口 + 单测（~20 个测试） | **3h** | 828 测试全过 ✓ 新增测试全过 ✓ |
| **P2** | 前端训练页 UI（报告/选项/结果/预览） | **4h** | 375 断言 ✓ E2E 10/10 ✓ 截图对比 ✓ |
| **P3** | 提示词改造 + JSON 修复器 + 强制校验 + 失败率控制 | **3h** | 用**云**和**本地**各生成 20 条 → **100% 通过校验** ✓ |
| **P4** | `data_preflight` 对齐 + 全链路联调 | **1h** | 现有 4 份数据不误杀 ✓ |
| **合计** | | **15h** | |

### 每阶段的回滚点
```
P0 完成 ⇒ git commit（纯新增 ✓ 零风险 ✓）
P1 完成 ⇒ commit（后端新增 ✓ 不影响现有 ✓）
P2 完成 ⇒ commit（前端新增 ✓ 可 CSS 单开关隐藏 ✓）
P3 完成 ⇒ commit（提示词改动 ⚠ 保留旧版可切回 ✓）
P4 完成 ⇒ commit
```

---

## 第五部分 · 不做的（明确边界 ✓）

| 不做 | 理由 |
|---|---|
| ✗ 引入 NeMo Curator / distilabel / datasets 作硬依赖 | 任务太重 ✗（它们面向 TB 级语料 ✓ 我们 MB 级 ✓） |
| ✗ 自动调云 API 做全量 LLM 质检 | 费用 ✗ 且规则分已够用 ✓（只对可疑样本 ✓） |
| ✗ 默认开启近似去重 | 误伤风险 ⚠ → 默认关 ✓ 可选开 + 预览 ✓ |
| ✗ 覆盖用户原文件 | 永远生成 `_clean` 新文件 ✓ |
| ✗ 支持 .xlsx/.parquet 等 | 先做 jsonl/json/csv ✓ 够用 ✓ |

---

## 第六部分 · 一句话总结

```
现状: 生成端产非法格式 ✗ / 上传端不校验 ✗ / 训练端口径错 ✗ —— 三处各说各话
方案: 一个 dataset_toolkit.py 做【唯一格式真相】✓
      生成端强制校验+修复+重试 ✓（云/本地同一套 ✓）
      上传端一键检测+清洗+排列+切分 ✓（永远不覆盖原文件 ✓）
工量: 15 小时 ✓ 分 4 阶段 ✓ 每阶段可验证可回滚 ✓
```

---

*方案文档 · 2026-10-19 · 代码依据：`data_generator.py` L246/L312 · `lora_manager.py` L2456/L2177 · `train_lora.py data_preflight` · `webui/pages/train.js`*
*开源依据：[LLaMA-Factory Data Preparation](https://llamafactory.readthedocs.io/en/latest/getting_started/data_preparation.html) · [NVIDIA NeMo Curator](https://github.com/NVIDIA-NeMo/Curator) · [distilabel](https://github.com/argilla-io/distilabel) · [sft-dataset-formatter](https://github.com/zaoshangduziteng/sft-dataset-formatter)*


---

## ⚠ 2026-10-19 更新（A/B 结果出来后）

**v3-2-2 A/B 结果**：h322 off 30.4 → on 38.4（**+8.0 · t=2.09 边缘显著** ✓）
但：base 臂自身漂移 −7.5（显著 ⚠）· v3-2-2 vs v3-2-1 只 +3.1（不显著）· n=12 太小 ⚠

**对本方案的影响（5 条）**：
1. ★ 提示词必须优先产 **多轮 + 真实观测**（`tool_calls + observations` 序列 ✓）—— 已证明这是关键 ✓
2. ★ `dataset_toolkit.py` 增加 **真实轨迹转换器**（从 `runs.jsonl` 的 msgs 解析 ✓ 原型已写 ✓）
3. ★ 增加 **base 对照臂校验**（训练前记录基线 ✓ 防漂移误导 ✓）
4. ★ 训练页必须显示 **A/B 评估结果**（不只 loss ✓）
5. ★ 评测集 **n ≥ 30** ✓ + **环境隔离**（快照/还原 ✓）
