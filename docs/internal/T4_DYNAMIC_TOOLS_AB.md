# T-4 动态关键词工具检索 · 设计与 A/B 方案（2026-10-21）

> **背景（P-79）**：全量 92 个工具的 schema 约 **8,600 字符 ≈ 4,300 token** ✗
> 占 32K 上下文的 **13%** ✗ ⇒ 挤占任务空间 + 增加选择难度
> **用户决策**：不做"直接砍到 30 个"✗ 而是**动态关键词检索 + A/B 验证** ✓

---

## 一、实现（两端一致 ✓）

| 端 | 文件 | 作用 |
|---|---|---|
| **Python 参考** | `scripts/tool_retriever.py` | 离线验证工具覆盖率 ✓（不占 GPU ✓）|
| **前端** | `webui/index3.html` | 实际生效 ✓（`buildToolsSchema(query)` 里筛 ✓）|

### 1.1 算法：域优先（比纯词匹配稳健 ✓）

```
① 检测域: 用户这句话命中哪些域 ✓（6 个域: workflow/kb/training/doc/file/system）
② 核心工具: 总是包含 ✓（15 个 · 只读/状态/模型管理）
③ 域工具: 命中域的全部工具 ✓
④ 无域命中 ⇒ 退回全量 ✓（安全兜底 ✗ 不许漏必需工具 ✗）
⑤ 上限 50 ✓（保留核心 ✓）
```

### 1.2 A/B 开关 ✓

```javascript
localStorage.setItem('ds_dynamic_tools', 'off');   // 关 = 全量 92 个（对照组 ✓）
localStorage.removeItem('ds_dynamic_tools');       // 开 = 动态筛选（默认 ✓）
```

### 1.3 离线验证（覆盖率 ✓ 关键 ✓）

```
Python 版: 57 工具 · V2 任务 30 条 × 期望工具 ⇒ **覆盖率 30/30 = 100%** ✓✓
          平均选中 30.4 个 ⇒ token 省 **47%** ✓

JS 版（Node 独立测试 ✓ 从 index3.html 抽 4,226 字符纯逻辑 ✓）:
          63 工具 · V2 任务 29 条 ⇒ **覆盖率 29/29 = 100%** ✓✓ 与 Python 一致 ✓
          域检测 7 用例全对 ✓ "你好" ⇒ 无域 ⇒ 全量兜底 63 ✓
```

### 1.4 修过的 3 个坑（实测 ✓）

```
① 截断太狠 ✗ top_k=30 ⇒ V2-01 需 42 个 ⇒ 砍必需工具 ✗ ⇒ 改 50 ✓
② 域关键词漏 ✗ "录音转文字入库" 只命中 kb ✗ ⇒ 补"录音/音频/转文字/副本"到 file ✓
③ 工具归类错 ✗ read_upload 同时在 doc 和 file ⇒ 先匹配 doc ⇒ 归错 ✗ ⇒ 从 doc 移除 ✓
```

---

## 二、A/B 测试方案

### 2.1 两臂

| 臂 | 开关 | 工具数 | 说明 |
|---|---|---|---|
| **A（对照）** | `ds_dynamic_tools='off'` | 全量 ~92 | 现状 ✓ |
| **B（实验）** | 默认（开）| 按域 15-50 | 动态筛选 ✓ |

### 2.2 指标（对齐 EVAL_PROTOCOL M1-M5 ✓）

| 指标 | 怎么算 | 期望 |
|---|---|---|
| **任务成功率** | V2 单次通过率 ✓ | B ≥ A ✓ |
| **工具选择准确率** | 期望工具被调用的比例 ✓ | B ≥ A ⚠ |
| **schema token** | 实际下发工具数 × 平均字符 ✓ | **B 显著低于 A** ✓✓ |
| **首轮延迟** | 发送到第一个工具调用 ✓ | B ≤ A ✓ |
| **漏工具率** | 期望工具不在下发列表里的比例 ✓ | **B 必须 = 0** ✗✓（已离线验证 100% ✓）|

### 2.3 跑法（等 GPU 空闲 ✓）

```bash
# ① 离线验证（不用 GPU ✓ 已通过 ✓）
cd /home/luxy/paper_lora_project
/home/luxy/.venv/bin/python scripts/tool_retriever.py

# ② 冒烟（要 vLLM ✓）
cd E:/WSL/_check/drill
node eval_v2_run.mjs --tier hard --limit 3 --tag t4_smoke_on     # 开（默认 ✓）
# 手动在页面 localStorage 关掉后:
node eval_v2_run.mjs --tier hard --limit 3 --tag t4_smoke_off    # 关

# ③ 正式 A/B（hard 7 题 × 3 次 × 2 臂 = 42 次 · 约 1-2 小时 ⚠）
node eval_v2_run.mjs --tier hard --tag t4_on
node eval_v2_run.mjs --tier hard --tag t4_off

# ④ 判分对比
cd /home/luxy/paper_lora_project
/home/luxy/.venv/bin/python scripts/eval_v2_score.py
```

### 2.4 通过标准

```
✅ B 的"漏工具率 = 0"（硬性 ✗ 有一个就不及格 ✗）
✅ schema token 显著下降（≥30% ✓）
✅ 任务成功率 B ≥ A − 5pp（不显著变差 ✓）
✅ 工具选择准确率 B ≥ A
⇒ 全满足 ⇒ **默认开启** ✓ 否则回滚 ✗
```

---

## 三、风险与缓解

| 风险 | 缓解 |
|---|---|
| **漏掉必需工具** ✗ | ✅ 离线验证 100% 覆盖 ✓ + 无域命中退回全量 ✓ + 核心 15 总是包含 ✓ |
| **多域任务工具太多** ⚠ | 上限 50 ✓ 核心优先 ✓ |
| **关键词判断错** ⚠ | 域是粗分类 ✓ 比词级匹配稳健 ✓ + A/B 验证 ✓ |
| **中文分词** ⚠ | 不用分词 ✓ 用子串匹配 ✓ |
| **A/B 混淆** | localStorage 开关 ✓ 同一浏览器 ✓ 交替跑 ✓ |

---

## 四、当前状态

| 项 | 状态 |
|---|---|
| Python 参考实现 | ✅ 完成（`scripts/tool_retriever.py`）|
| 覆盖率验证 | ✅ **100%**（30/30 V2 任务）|
| 前端接入 | ✅ 完成（`buildToolsSchema(query)` ✓）|
| JS 语法 | ✅ 4 块全过 ✓ |
| JS 逻辑验证 | ✅ **100%**（29/29 ✓ 与 Python 一致 ✓）|
| **A/B 实跑** | ⏳ **要 GPU（等不玩游戏时）** ⚠ |

---

*设计 · 2026-10-21 · `scripts/tool_retriever.py` + `webui/index3.html` · 离线覆盖率 100% ✓*
