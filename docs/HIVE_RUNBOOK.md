# 蜂巢（奇美拉蜂群）运行手册 · Runbook

> 适用：`/home/luxy/paper_lora_project`（Luxy 平台 + 蜂巢 S18–S23 模块）
> 硬件：RTX 5060 Ti 16GB（GPU0，主脑）+ RTX 3060 12GB（GPU1，工蜂/训练）

## 1. 服务与端口

| 服务 | 端口 | 启动方式 | 说明 |
|---|---|---|---|
| 主脑 14B AWQ | :8000 | `vllm_manager.start_vllm(...)` | 路由决策 LLM（S18） |
| 工蜂 7B AWQ | :8002 | `swarm.start_worker()` | 本地执行 + 动态 LoRA（S19） |
| 后端 API | :8001 | `lora_manager.py` | 平台 + 蜂巢端点 |
| 前端 | :8080 | `webui/serve.py 8080` | 路由任务表/控制台 |

```bash
# 一键启动（后端/前端）
cd /home/luxy/paper_lora_project
setsid nohup env BACKEND_PORT=8001 VLLM_ENABLE_LORA=0 \
  /home/luxy/.venv/bin/python lora_manager.py > logs/backend.out 2>&1 &
setsid nohup /home/luxy/.venv/bin/python webui/serve.py 8080 > /tmp/frontend.log 2>&1 &

# 主脑（GPU0）
setsid nohup env VLLM_ENABLE_LORA=0 VLLM_HOST=127.0.0.1 \
  VLLM_USE_FLASHINFER_SAMPLER=0 /home/luxy/.venv/bin/python -c \
  "import sys;sys.path.insert(0,'.');import vllm_manager as vm;print(vm.start_vllm('/home/luxy/models/Qwen2.5-14B-Instruct-AWQ',port=8000,extra_args={'max_model_len':20480},venv_python='/home/luxy/.venv/bin/python'))" \
  > /tmp/vllm_main.log 2>&1 &

# 工蜂（GPU1）
setsid nohup env VLLM_USE_FLASHINFER_SAMPLER=0 /home/luxy/.venv/bin/python -c \
  "import sys;sys.path.insert(0,'.');import swarm;print(swarm.start_worker())" \
  > /tmp/vllm_worker.log 2>&1 &
```

> ⚠️ 不要用 `pkill -9 -f vllm` 一把杀：会同时杀主脑+工蜂。按端口定位 PID（`ss -ltnp | grep 8000`）。

## 2. 蜂巢端点一览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/hive/decide` | POST | 三路由决策（Local/Cloud/Sim）+ 记忆上下文 |
| `/hive/dispatch` | POST | 执行入口（S18 决策桩） |
| `/hive/pool` | GET | 工蜂池状态（已加载 LoRA/槽位） |
| `/hive/route_local` / `/hive/run_local` | POST | LoRA 能力路由 / 本地执行 |
| `/hive/lora/load|unload`、`/hive/loras` | POST/GET | 动态加载/卸载/注册表 |
| `/hive/run_failover` | POST | 本地失败→云端教师→轨迹回收 |
| `/hive/failover/stats|reset` | GET/POST | 失败阈值统计/复位 |
| `/hive/trajectories` | GET | 云端轨迹（统一 schema） |
| `/hive/quality/run|stats` | POST/GET | 轨迹质检 |
| `/hive/autotune/check|trigger|jobs` | POST/GET | 阈值检查/触发训练/任务列表 |
| `/hive/train/plan|jobs` | POST/GET | 训练资源计划（3060→5060 回退）/任务 |
| `/hive/hotload/*` | POST/GET | shadow→评测→灰度→晋级→回滚 |
| `/hive/sim/run|reports|stats` | POST/GET | 仿真压测/脏数据隔离 |
| `/hive/memory/search|context|stats|record|cleanup|clear` | GET/POST | 主脑路由临时知识库 |
| `/architecture/status` | GET | 18 项架构自检 |

## 3. 环境变量（按需）

| 变量 | 默认 | 说明 |
|---|---|---|
| `HIVE_MEMORY_ENABLED` | 1 | 临时知识库开关 |
| `HIVE_MEMORY_SEMANTIC` | 0 | 1=启用动态 KB 语义检索（bge-m3；会增加首查延迟） |
| `HIVE_MEMORY_TTL_H` | 24 | 临时记忆 TTL |
| `HIVE_FAIL_MAX` | 2 | 连续失败阈值（升级云端） |
| `HIVE_TRAIN_REQUIRED_GB` | 6.0 | 训练显存需求（调度判断） |
| `HIVE_TRAIN_OOM_MARGIN` | 1.05 | 显存安全余量 |
| `HIVE_AUTOTUNE_N` | 3 | 单类轨迹达阈值自动训练 |
| `HIVE_CANARY_RATIO` | 0.05 | 热加载灰度比例 |
| `HIVE_SIM_AUDIT_EVERY` | 50 | 仿真规则决策审计采样 |
| `HIVE_W1_MAX_LORAS` | 2 | 工蜂 LoRA 槽位 |

## 4. 数据与产物路径

| 数据 | 路径 |
|---|---|
| 路由决策审计 | `data/hive/decisions.jsonl`（>4MB 自动轮转） |
| 云端轨迹 | `data/hive/trajectories/cloud_trajectories.jsonl` |
| 质检结果 | `data/hive/quality/passed.jsonl` / `rejected.jsonl` |
| 临时知识库 | `data/hive/memory.db` + 动态 KB `data/kbs/hive_route_memory/` |
| LoRA 状态 | `data/hive/lora_state.json` |
| 训练任务 | `data/hive/train_jobs/*.json`、`logs/hive_train_*.log` |
| 自动训练 | `data/hive/autotune/jobs.jsonl`、`training_data/hive_auto/*.jsonl` |
| 仿真 | `data/hive/sim/reports/*.json`、`quarantine.jsonl` |
| 交付报告 | `E:\S*实施报告_*.pdf`（S17–S23） |

## 5. 常见问题

1. **工蜂崩溃/`EngineDeadError`**：vLLM 0.11 在推理 in-flight 时 load/unload LoRA 有竞态 → 已用读写锁+pin 规避；若仍崩，重启工蜂（见 §1）。
2. **主脑 8000 掉线**：`pkill` 误杀是常见原因；重启主脑并检查 `nvidia-smi`。
3. **训练 OOM/资源冲突**：调度器会 3060 优先（必要时停工蜂）→ 5060 回退（必要时停主脑）→ defer；不要手工绕过 `/hive/train/plan`。
4. **语义记忆检索为空**：首查需加载 bge-m3（~3-5s）；确认 `HIVE_MEMORY_SEMANTIC=1` 且动态 KB 未被 TTL 清理。
5. **仿真压测影响审计**：已按 50 条采样；架构检查用全量扫描。
6. **测试**：`SKIP_FE_TESTS=1 PYTHON=/home/luxy/.venv/bin/python timeout 900 bash run_tests.sh`

## 6. 验收清单（S23）

- [x] 架构自检 18/18
- [x] 三路由 local/cloud/sim 正确
- [x] LoRA 热加载/灰度/回滚
- [x] Failover 云端教师 + 轨迹回收
- [x] 质检→阈值→自动训练（3060/5060 回退）
- [x] 仿真压测 + 脏数据隔离（KB 零写入）
- [x] 主脑路由临时知识库（语义检索 + 上下文注入 + TTL）
- [x] 全量测试 1242 OK（S23 时点）


## 7. L2 能力体系（S24 起）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/hive/capability/seed` | POST | 登记 LoRA/工具/KB/工作流/云端模型/数据源到能力基线库 |
| `/hive/capability/stats|list|query?task=` | GET | 统计/列表/任务能力判定（local/cloud/sim/unknown + gap_tags） |
| `/hive/capability/register` | POST | 手动登记能力（evidence/mount/ACL） |
| `/hive/kb/context?task=&worker=` | GET | 分层上下文（L0/L0.5 必读 + L1/L2 挂载 + L3 数据源 + ACL 过滤） |
| `/hive/kb/data_sources` | GET/POST | L3 数据源注册（schema/ACL/敏感度；不 embed 原始数据） |
| `/hive/manifest/validate|absorb` | POST | 云端能力包校验/评分（direct/rebuild/train/deny）/吸收注册 shadow |
| `/hive/manifest/list` | GET | 已落盘 Manifest |

数据：`data/hive/capability.db` · `data/hive/data_sources.json` · `data/hive/manifests/` · `data/hive/s24_acceptance.json`
