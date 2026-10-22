# 重启后接续点（2026-10-21）

> 用户重启机器调风扇 ⚠ 重启会杀掉：vLLM / backend / frontend / WSL 进程

---

## 一、重启后第一件事：检查服务

```bash
# ① 看服务是否自动恢复
curl -s -o /dev/null -w 'vLLM:%{http_code}\n' http://127.0.0.1:8000/v1/models
curl -s -o /dev/null -w 'backend:%{http_code}\n' http://127.0.0.1:8001/health
curl -s -o /dev/null -w 'frontend:%{http_code}\n' http://127.0.0.1:8080/index3.html
```

**预期**：
- `keeper.sh` 会自动拉起 backend + frontend ✓
- **`start.sh` 会启动 vLLM** ⚠ —— 已确认 `settings.inference_gpu = 1`（3060）✓ **不会占用户 5060** ✓
- 若 vLLM 没起，用 `E:\WSL\_check\t4_7b_start.sh` 手动起（7B ✓ 3060 ✓）

---

## 二、当前 7B 验证进度（**中断在臂1 开始前**）

### 已完成 ✓
```
✓ 7B vLLM 启动成功（PID 2097 · 3060 11.1GB · hermes parser ✓）
✓ /chat/models 有 vllm:base = Qwen2.5-7B-Instruct-AWQ ✓
✓ 7B 工具调用测试通过（tool_calls: 1 ['list_knowledge_bases'] ✓）
```

### 未开始 ❌
```
❌ 臂1: 7B × KB关 × 4B档 15题   ← 从这开始
❌ 臂2: 7B × KB开(agent_use) × 4B档 15题
❌ 判分对比
```

### 重启后直接跑这两条（PowerShell）
```
cd E:\WSL\_check\drill

# 臂1: KB 关
node eval_v2_run.mjs --tier 4b --repeats 1 --kb off --tag 7b_b_kb0 --timeout-min 4

# 臂2: KB 开
node eval_v2_run.mjs --tier 4b --repeats 1 --kb agent_use --tag 7b_b_kb1 --timeout-min 4
```

### 判定（关键 ✓）
```
✅ 7B 上 KB开 > KB关（≥+10pp）⇒ **检索路线复活** ✓ 飞轮通道 C 可启用 ✓
❌ 7B 上 KB开 ≤ KB关 ⇒ **检索对本地模型全线无用** ✓ 只走 A+B ✓（结论更强 ✓）
```

⚠ 注意：KB 注入**默认已关闭** ✗ —— 跑 `--kb agent_use` 前需要**在页面开** `localStorage.ds_kb_inject='on'`
   ⇒ 驱动脚本目前**没有**注入这个开关 ✗ ⇒ **需要在 `eval_v2_run.mjs` 加 `--kb-inject on` 参数** ⚠
   或临时改 `injectAgentKbContext` 默认值 ✓

---

## 三、待做任务（顺序 2→3→4→1）

| # | 任务 | GPU | 工时 | 状态 |
|---|---|---|---|---|
| **4** | **7B 验证**（进行中 ⚠ 中断在臂1 前）| 3060 ✓ | 40min | ⏸ 等重启 |
| **2** | Bootstrap CI（补 V3.0 缺口）| ✗ | 0.5h | ❌ 未做 |
| **3** | P-89 补丁脚本陷阱 | ✗ | 0.5h | ❌ 未做 |
| **1** | 飞轮期 2/3（训练固化 + 自动化）| ⚠ 期2要 | 1-2h | ❌ 未做 |

---

## 四、当前资产快照（不会丢 ✓ 都在 git）

```
git: 541 提交 · 0 未提交 ✓
最新提交: 4458e09（C完成: LLM真抽象20条做法）
文档: ARCHITECTURE_REPORT_V2.md ✓ FUTURE_DIRECTIONS_REFS.md ✓
     KB_FLYWHEEL_PLAN.md（391行）✓ KB_FLYWHEEL_REFERENCES.md（241行）✓
     PLATFORM_ISSUES.md（3458行·含问题清单完工盘点 ✓）
脚本: scripts/flywheel.py（460行 ✓）· scripts/tool_retriever.py ✓
     scripts/eval_v2_score.py ✓ · tools/eval_v2_run.mjs ✓
数据: data/flywheel/skills_candidate.jsonl（20 条真抽象 ✓）
     data/learning/skills/skills.jsonl（22 条 ✓）
评测集: docs/EVAL_SET_V2.json（50 条 ✓）
```

---

## 五、风扇问题（用户处理中）

```
诊断（之前查的）:
  ⚠ ArmouryCrateService: Stopped ✗
  ⚠ 用户账号非管理员（DESKTOP-3V6BQ24\A · IsInRole=False）✗
  ✓ AsusFanControlService: Running ✓
  ✓ GPU 温度 28°C 空闲 ✓（无散热风险 ✓）

建议（已给用户）:
  1. 以管理员运行 Armoury Crate ✓
  2. 或 Start-Service ArmouryCrateService（需管理员 ⚠）
  3. 或装 MSI Afterburner 绕开 ✓（GPU 风扇独立 ✓）
  4. 或 BIOS 设风扇曲线 ✓
```

---

*2026-10-21 · 重启接续点 · 从「二、7B 验证」继续 ✓*
