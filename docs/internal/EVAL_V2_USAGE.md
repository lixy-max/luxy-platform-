# EVAL V2 使用说明（2026-10-21 · T-5）

> 配套：`docs/EVAL_SET_V2.json`（任务集）· `docs/EVAL_V2_DESIGN.md`（设计依据）

---

## 一、两个脚本

| 脚本 | 作用 | 位置 | 运行环境 |
|---|---|---|---|
| **`tools/eval_v2_run.mjs`** | 跑批（驱动 UI ✓ 采集轨迹 ✓） | 项目内 | **Windows**（Node + playwright-core ✓）|
| **`scripts/eval_v2_score.py`** | 判分（分档 + 稳定性 ✓） | 项目内 | WSL（Python ✓ 零依赖 ✓）|

**⚠ 跑批必须在 Windows 上跑**（用 Windows 的 Chrome 驱动页面 ✓ 与 `drill_run.mjs` 一致 ✓）

---

## 二、跑批用法

```bash
# ★ 前置: 需要 3 个服务在线
#   vLLM(:8000) · backend(:8001) · frontend(:8080)

# ① 干跑（只看任务清单 · 不启动浏览器 ✓）
node eval_v2_run.mjs --dry-run

# ② 全量（35 题 × 3-5 次 = 115 次 ⚠ 约 2-4 小时）
node eval_v2_run.mjs --tag baseline

# ③ 只跑 hard 档（7 题 × 3 = 21 次 · 约 30-60 分钟）
node eval_v2_run.mjs --tier hard --tag baseline_hard

# ④ 挂 LoRA 跑（A/B 实验 ✓）
node eval_v2_run.mjs --lora test_3060_xxx --tag v2_lora

# ⑤ A/B 关闸门⑤（对照组 ✓）
node eval_v2_run.mjs --arm off --tag v2_off

# ⑥ 只跑 5 题（快速验证脚本 ✓ 先做这个 ✓）
node eval_v2_run.mjs --limit 5 --tag smoke
```

**参数**：

| 参数 | 说明 | 默认 |
|---|---|---|
| `--tier hard/mid/easy/stability` | 只跑某档 | 全档 |
| `--limit N` | 只跑前 N 题 | 0（不限）|
| `--repeats N` | 覆盖每题重复次数 | 用任务自带（3 或 5）|
| `--lora NAME` | 挂载的 LoRA | 不挂 |
| `--arm off` | 关闭闸门⑤（A/B 对照）| 开 |
| `--tag NAME` | 记录标签（区分批次 ✓）| v2 |
| `--timeout-min N` | 单题超时 | 12 分钟 |
| `--dry-run` | 只列任务 | — |
| `--no-snapshot` | 跳过状态快照 | — |

**输出**：
```
E:/WSL/_check/eval_v2/out/runs_v2.jsonl    ← 逐次记录（追加 ✓）
E:/WSL/_check/eval_v2/out/snapshots.json   ← 状态快照（跑前跑后 ✓）
```

---

## 三、判分用法

```bash
cd /home/luxy/paper_lora_project
/home/luxy/.venv/bin/python scripts/eval_v2_score.py
```

**输出**：
```
E:/WSL/_check/eval_v2/out/EVAL_V2_RESULT.md    ← markdown 报告
E:/WSL/_check/eval_v2/out/EVAL_V2_RESULT.json  ← 结构化汇总
```

---

## 四、判定口径（对齐 EVAL_PROTOCOL M1-M5 ✓）

### 单次通过 ✅
```
必须同时满足:
  ① finished（跑完 ✓）
  ② exec_count > 0（真执行了工具 ✓）
  ③ 非占位行（placeholder=false ✓）
  ④ 未升级到云（escalated=null ✗）
  ⑤ 无 error ✓
```

### ★ 稳定性（本 V2 的核心新增 ✓）
```
同一任务 N 次里:
  通过 ≥2 次  ⇒ **稳定 ✓**
  通过 =1 次  ⇒ **不稳定 ✗**（这正是旧评测集的问题: "只对一次 11/40" ✗）
  N=1（占位行剔除后）⇒ N/A ⚠
```

### 分档报告 ✓
```
hard      主力区分度（多步/状态依赖/多工具 ✓）
mid       辅助
easy      sanity（应该都过 ✓ 不过 = 回归 ✗）
stability ★ 历史抖动用例（5 次重复 ✓ 专测可靠性）
```

### 状态不变量 ✓
```
跑前跑后对比:
  · workflow_config.json md5 必须一致 ✓（默认配置不许被改 ✗）
  · KB 块数必须一致 ✓（检索任务不许污染 KB ✗）
  · LoRA 列表记录（允许增加 = 训练任务产物 ✓ 但不许意外消失 ✗）
```

---

## 五、跑批前置检查清单

```
□ vLLM 在线（curl 127.0.0.1:8000/v1/models = 200）
□ backend 在线（curl 127.0.0.1:8001/health = 200）
□ frontend 在线（curl 127.0.0.1:8080/index3.html = 200）
□ 没有其他跑批在跑（会互相干扰 ✗）
□ 默认配置 = 出厂状态（`workflow_config.json` 未被改 ✓ 否则 V2-01~07 全废 ✗）
□ 学习增强配置 = 想要的（开/关 ⇒ 影响臂定义 ✓）
□ 磁盘够（每条记录 ~10KB × 115 = 1.2MB ✓ 无压力）
□ GPU 空闲（跑批会占满 vLLM 的卡 ⚠）
```

---

## 六、跑完看什么

```
1. 单次通过率（本次 vs 基线）
2. ★ 稳定性（本次 vs 基线）← 最关键的对比
3. 分档: hard 档的差异才是真区分度 ✓
4. easy 档: 必须都过 ✓ 掉了 = 回归 ✗
5. 状态不变量: 必须 100% ✓ 否则数据不可信 ✗
```

---

## 七、与旧评测集的关系

| | EVAL_SET_32（旧）| **EVAL_SET_V2（新）** |
|---|---|---|
| 脚本 | `tools/drill_run.mjs`（markdown 任务 ✓）| **`tools/eval_v2_run.mjs`**（JSON ✓）|
| 判分 | `scripts/drill_score.py` | **`scripts/eval_v2_score.py`** |
| 重复 | 单次 ✗ | **3-5 次 ✓** |
| 稳定性指标 | ✗ | **✓ 核心新增** |
| 分档 | 3 层 | **4 档** |
| 状态快照 | ✓ | ✓ |

**⇒ 旧集保留做历史对比 ✓ 新集用于后续所有 A/B ✓**

---

*使用说明 · 2026-10-21 · 跑批 `tools/eval_v2_run.mjs` · 判分 `scripts/eval_v2_score.py`*
