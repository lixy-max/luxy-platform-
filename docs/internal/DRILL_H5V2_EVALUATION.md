# H5-v2 评估：工具调用形态数据 + rank 扫描（2026-10-18）

> 目的：治 P-16（标签形态与推理形态不一致 ✗）—— 把标签从"描述计划"改成**直接输出 tool_call** ✓
> 并做 rank 扫描（8 / 32 / 128 ✓）区分"容量墙 vs 数据不足" ✓
> 方法：同一批 core20 · 同一套页面驱动协议 · 只换模型 ✓

---

## 一、结果清单

### 1.1 四臂对比（n=20/臂 ✓ 全部真跑 ✓）

| 指标 | arm0 基线<br>旧system·无LoRA | arm1 对照<br>新system·无LoRA | arm2<br>+v1数据LoRA(r8) | **v2r32**<br>+v2工具调用数据(r32) |
|---|---|---|---|---|
| 有工具调用率 | 45% | 45% | 40% | **25%** ↓ |
| ★ 写操作率 | 15% | 5% | 5% | **0%** ✗ |
| 零工具率 | 55% | 55% | 60% | **75%** ↑ |
| 云接手率 | 40% | 65% | 80% | **90%** ↑ |
| 平均耗时 | 27s | 18s | 26s | 24s |

### 1.2 数据与训练侧（全部跑通 ✓）

```
✓ v2 数据构建: training_data/drill_part1_v2_toolcall.jsonl（40+10 条 ✓）
  · 每条 = system(215字 ✓) + human + ★function_call（工具调用 ✓）+ observation + gpt
  · 每条带相关工具子集 schema（1~4 个 ✓ 避免 52 个全塞）
  · 体检: 248 token/条 ✓ 超 cutoff 0 条 ✓ 通过前置闸门 ✓
✓ 三档 rank 全部训成（直接跑 train_lora.py ✓ 每档约 2 分钟 ✓）:
  · drill_v2r8x  → r=8   ✓
  · drill_v2r32x → r=32  ✓
  · drill_v2r128x→ r=128 ✓
  · 全部带 --gradient_checkpointing true ✓（P-5 生效 ✓ 训练都不崩 ✓）
✓ 单档验证: 直接跑日志显示 `--lora_rank 32 --gradient_checkpointing true` ✓ 产物 r=32 ✓
```

---

## 二、结论（先说不好听的 ✓）

### 2.1 一句话

**v2（工具调用形态数据）不但没有提升，反而让表现更差** ✗：
```
工具调用率 45% → 25% ↓ · 写操作率 5% → 0% ↓ · 零工具率 55% → 75% ↑ · 云接手率 80% → 90% ↑
⇒ 模型变得更少动手、更多把活推给云 ✗
```

### 2.2 ★ 为什么（关键诊断 ✓ 也解释了 v1 为什么没用）

```
假设: 训练让模型学会了"输出工具调用"✓，但输出的【格式】不是 vLLM 运行时解析器认的那种 ✗
   · 训练侧: LLaMA-Factory 的 qwen 模板会把 function_call 渲染成它自己的格式 ✓
   · 推理侧: vLLM 用 --tool-call-parser hermes ✓ 只认 <tool_call>{"name":...,"arguments":...}</tool_call> ✓
   ⇒ 两边格式若不一致 ⇒ 模型吐出来的东西解析器提取不到 ⇒ 【看不到工具被调用】✗
   ⇒ 而模型又"觉得自己已经调了"⇒ 不再输出正常答案 ⇒ 质量闸门判定不合格 ⇒ 转云 ✗
   ⇒ 这正好解释: 工具率 ↓ + 写操作 0 + 云接手 90% ↑
佐证: v1 数据（描述计划 ✗）时工具率 40~45%；v2（伪 tool_call ✗）反而 25% ↓
      ⇒ 【学到的是格式，不是能力】✗ —— 格式错 = 全错 ✓
```

### 2.3 本次真正学到的东西（这才是价值 ✓）

```
① 训练-推理【格式必须逐字对齐】✗ 不对齐时，越"像"工具调用，实际越糟 ✓（P-18 新发现 ✓）
② 40 条样本不足以让行为改变 ✓（GUIDE §1.3 再次验证 ✓）
③ rank 8/32/128 的差别被"格式错"掩盖了 ✗ ⇒ 容量墙实验【本轮无法判定】✗
   （必须先修好格式，再做 rank 对照才有意义 ✓）
```

---

## 三、平台层面新发现（已记入 docs/PLATFORM_ISSUES.md）

| # | 发现 | 证据 | 影响 |
|---|---|---|---|
| **P-17** | 经 `/training/start` 传 `lora_rank` **不生效** ✗（但直接跑 `train_lora.py` 生效 ✓） | 后端日志显示"显式超参: rank=32" ✓ 但产物 r=8 ✗；直接跑同参数 ⇒ r=32 ✓ | 用 API 做超参实验不可靠 ✗ 需排查（可能训练子进程用的是旧 env/旧进程 ✓） |
| **P-18** ★ | **训练-推理格式不对齐** ✗（训练用 LLaMA-Factory 渲染格式 ✓ 推理用 hermes 解析器 ✓） | 见 2.2 ✓ v2 的回归就是这个 ✓ | ★★★ 不修它，任何"工具调用形态"的训练都可能【越训越差】✗ |

---

## 四、下一步（必须先修 P-18 再训 v3）

```
P0 · 格式对齐验证（先验后用 ✓ 30 分钟）:
   1. 用 v2r32 生成 1 条样本 ⇒ 抓原始 output ✓
   2. 看它是否含 <tool_call>{"name":..,"arguments":..}</tool_call> ✓ 且 vLLM 能解析成 tool_calls ✓
   3. 若不能 ⇒ 调整训练数据的 function_call 渲染（用 qwen 模板的实际格式 ✓
      或在数据里直接写死 hermes 文本格式 ✓）
   4. 只有在"单条生成能被解析器识别"之后，才批量训 v3 ✓

P1 · v3 数据: 在 v2 基础上对齐格式 ✓ + 样本量 ≥300 ✓（part1+part2 全任务 ✓ 每题多轮 ✓）
P1 · rank 对照: 格式修好后重做 8/32/128 ✓（否则实验被格式错掩盖 ✗）
P2 · 云接手带工具（P-15 ✓）: 让"兜底"真的能完成任务 ✓
```

---

## 五、诚实声明

```
1. n=20/臂 ⇒ 单点差异不显著 ✗；但 v2r32 是【一致性朝坏方向】（工具↓ 写操作0 云↑）⇒ 方向可判定 ✓
2. rank 8/128 未做核心 20 条评估 ✗（时间原因 ✓ 且格式错掩盖了容量差异 ⇒ 先修格式更有意义 ✓）
3. 本轮没能证明"LoRA 有用"✗ —— 但证明了【为什么没用】✓（格式 + 样本量 ✓）
   这个结论比"再训一轮"更有价值 ✓ 因为它把 v3 的前提条件写清楚了 ✓
4. 所有原始记录都在 training_output/drill_part1_runs.jsonl（每条带 tag ✓ 可复算 ✓）
```

---

## 附：复现命令

```bash
# 构建 v2 数据
/home/luxy/.venv/bin/python scripts/build_drill_dataset_v2.py
# 直接训练（绕过后端 ✓ 能正确传 rank ✓）
TRAIN_RANK=32 /home/luxy/.venv/bin/python train_lora.py \
  /home/luxy/models/Qwen2.5-7B-Instruct \
  /home/luxy/paper_lora_project/training_data/drill_part1_v2_toolcall.jsonl \
  /home/luxy/paper_lora_project/training_output/drill_v2r32x drill_v2r32x 40
# 评估（真页面驱动 ✓ 挂 LoRA ✓）
node E:/WSL/_check/drill/run.mjs --ids 1,3,5,... --lora drill_v2r32x --tag v2r32
# 统计
node E:/WSL/_check/drill/stats.mjs
```
