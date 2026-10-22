# A/B 混跑实验设计（解决 P-59 的 run-level 漂移）

> 2026-10-19 · 目的：**真正证明闸门⑤ 的因果效应**
> 背景：P-59 —— 分两次跑（1 次基线 / 1 次实验）无法区分"优化效果"和"当天运气"

## 一、为什么"分两次跑"不够

```
第 1 次 (p1gate5):    39.38   ← 可能当天模型状态好
第 2 次 (p1gate5_r2): 34.15   ← 换一天就回落
基线   (p1full):      32.93
⇒ run-level 漂移（±3~4 分）与效应（±3 分）同量级
⇒ 分两次跑的差异 = 效应 + 漂移，无法分离
```

## 二、混跑设计（interleaved A/B）

```
对【每个任务】连续跑两臂（相邻几分钟内）:
  臂 OFF: localStorage.ds_gate5 = 'off'  ⇒ 闸门⑤ 关闭
  臂 ON : 默认（闸门⑤ 开启）
⇒ 两臂共享同一时间窗的模型状态/机器负载
⇒ run-level 漂移对两臂【同向】影响 ⇒ 配对差里漂移抵消 ✓✓
```

## 三、实现

| 部分 | 改动 |
|---|---|
| 前端开关 | `index3.html`: `GATE5_ENABLED = localStorage.getItem('ds_gate5') !== 'off'` ✓ |
| 跑手 | `run.mjs --ab`：每任务排 `{arm:'off'}` + `{arm:'on'}` 两个 job ✓；`ctx.addInitScript` 注入 localStorage ✓；记录 `tag` 按 arm 取（`ab_off` / `ab_on`）✓ |

## 四、运行

```bash
# 冒烟（2 任务 × 2 臂 = 4 跑，约 3 分钟）
node run.mjs --tasks <PART1> --ids 11,30 --ab --timeout-min 8

# 全量（50 任务 × 2 臂 = 100 跑，约 60-90 分钟）
node run.mjs --tasks <PART1> --from 1 --to 50 --ab --timeout-min 12
```

⚠ 两种跑都必须在**没有其他跑批**时进行（否则时序互相污染）

## 五、分析

```bash
python scripts/drill_repeat.py --tags ab_off,ab_on --baseline '' --tasks docs/DRILL_TASKS_PART1.md
```

核心指标：
- **逐任务配对差**（ON − OFF）· 均值 + t 统计量
- 两臂相邻 ⇒ 若 t 显著 ⇒ **因果成立**；若不显著 ⇒ 闸门⑤ 无净效应

## 六、判定标准（事先定好，不事后找理由）

```
|t| > 2 且 配对差均值 > 跨臂噪声 ⇒ 【闸门⑤ 有效】可写进结论
|t| ≤ 2                       ⇒ 【无显著净效应】保留"定向修好 3 个案例"的说法
```

## 七、显存

纯浏览器驱动（headless 跑手 + 页面），不额外占 GPU；vLLM 照常用（跑批期间显存本就紧张 15.4/16.3GB，本实验不新增显存需求）。
