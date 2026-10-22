# 云边架构 · 第 2 步：DSH 侧接入（2026-10-21）

> **结论**: ✅ **打通** —— DSH 现在能通过 `paper_lora_orchestrate` 问平台「这任务该怎么做」✓✓

---

## 一、架构（已跑通 ✓）

```
DSH（云端/前端）
  ↓ 调工具 `paper_lora_orchestrate` ✓
DSH 插件 dsh-plugin-paper-lora
  ↓ HTTP POST http://127.0.0.1:8001/orchestrate ✓
平台编排决策层 orchestrator.py
  ↓ 返回执行计划（分步 + 模型路由 + 置信度信号 + 能力清单）✓
DSH 拿到计划 ⇒ 按计划逐步执行（执行由 DSH 或前端做 ✓）
```

---

## 二、交付物

### 1. 平台侧（第 1 步 ✓）
```
orchestrator.py（328 行）✓
POST /orchestrate ✓
GET  /orchestrate/capabilities ✓
```

### 2. DSH 插件侧（第 2 步 ✓）
```
文件: E:\WSL\dsh-plugin-paper-lora\src\index.js（565 → 623 行 ✓）
新增 2 个工具:
  · paper_lora_orchestrate     → POST /orchestrate
  · paper_lora_capabilities    → GET  /orchestrate/capabilities

⚠ 关键: 安装位置是 **web profile**（不是 headless ✗）
   C:\Users\A\.dsh\profiles\web\node_modules\dsh-plugin-paper-lora\
   （DSH_PROFILE=web ✓ 我一开始同步到 headless ✗ 绕了一圈 ✗）
```

---

## 三、实测（**全部通过** ✓）

### `paper_lora_capabilities`
```
✅ ok=true
✅ tools=92 · sim_nodes=6 · lora=41 · kb=29
✅ models: local_vllm=127.0.0.1:8000/v1 · cloud=deepseek-chat
           inference_gpu=1 · train_gpu=1 · gpu_mode=dual
✅ features: tool_routing ✓ grounding_search ✓ simulation_constraint ✓
             kb_cleaning ✓ lora_training ✓ confidence_scorer ✓
             **confidence_in_chat=false** ✓（诚实标注未实现 ✗）
             **cloud_to_local_orchestration=false** ✓（诚实 ✓）
```

### `paper_lora_orchestrate`
输入: `帮我算一下 1438 条轨迹里 12 个失败占多少百分比`
```
✅ route: local_7b ✓（"无明显特征 ⇒ 默认本地 ✓ 数据不外传 ✓"）
✅ plan: [{'action':'simulate','target':'run_simulation','params':{'node':'math_eval'}}] ✓
   reason: "任务涉及计算 ⇒ 必须走仿真节点 ✗ **禁止心算** ✗" ✓✓ 正确 ✓
✅ confidence: medium
     has_deterministic_compute=**true** ✓
     has_grounding_step=false ✓ · platform_covered=true ✓
✅ warnings 3 条诚实提示 ✓
```

### 其他场景（第 1 步已验证 ✓）
```
"列出平台所有 LoRA"        → local_7b · [call_tool] ✓
"训练一个工具调用 LoRA"     → local_7b · [call_tool, train] ✓
"最近大模型置信度校准的论文" → cloud · [retrieve] ✓
"知识库里有什么文档"        → local_7b · [kb_query] ✓
"你好"                   → local_7b · [direct_answer] ✓
```

---

## 四、⚠ 诚实边界（**仍未实现** ✗）

```
❌ 编排结果的实际执行 ✗ —— 只出计划 ✓ 执行需 DSH/前端做 ✓
❌ 置信度接入对话流程 ✗ —— 只给信号 ✓ 不拒答 ✗
❌ 云端自动触发本地训练 ✗ —— 需 DSH 调 /training/start ✓
❌ 多步计划的依赖管理/回滚 ✗
❌ 计划质量评估 ✗ —— 无法证明"计划对"✗ 只能说"符合规则"✓
⚠ cloud_to_local_orchestration=false ✓ —— 这条链路**还是半条** ✗
   · 平台能**出计划** ✓
   · 但**自动执行**还没接 ✗
```

---

## 五、⚠ 这次踩的坑（记下 ✓）

```
① 同步错了 profile ✗
   DSH_PROFILE=web ✓ 但我先同步到 headless ✗ ⇒ 白重启一次 ✗
   ⇒ 教训: **先确认 DSH_PROFILE** 再同步 ✓

② DSH 重启会杀掉我的后台 job ✗
   评测跑到 12/18 被杀 ✗（但已完成 12/12 = 100% ✓ 数据没丢 ✓）
   ⇒ 教训: 长任务别挂在 DSH 子进程下 ✗

③ 插件是**复制品**不是软链 ✗
   ⇒ 改源码不生效 ✗ 必须同步到安装位置 ✓
```

---

## 六、下一步

| # | 任务 | 说明 |
|---|---|---|
| **3** | 置信度接入对话流程 | 低置信 ⇒ 拒答/升级 ✓ |
| 4 | 云端触发本地训练 | DSH 调 `/training/start` ✓ |
| 5 | KB 自动挂载 | 按任务自动建/查库 ✓ |
| 6 | **重跑评测**（被 DSH 重启杀了 ✗）| easy 补 6 跑次 + mid 28 跑次 ✓ |

---

*2026-10-21 · 云边第 1-2 步打通 ✓✓ · DSH 能调平台拿执行计划了 ✓ · 执行环节仍缺 ✗*
