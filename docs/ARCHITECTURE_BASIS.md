# 奇美拉蜂巢 · 架构依据文件 v1.0

**定位：架构依据文件（不新增功能，只固化"依据"）。** 本文回答"这个架构为什么长这样、每条结论的证据是什么、哪些已验证/哪些待复核"。

日期：2026-10-26 · 配套：`docs/LITERATURE_REGISTRY.md`（R1 登记表）· 依据编号：E 实测 / L 文献 / O 开源 / I 内部 / D 决策

## 0. 合规声明（项目原则 R1–R5）

| 规则 | 要求 | 本文件如何满足 |
|---|---|---|
| R1 | 新方向至少 2 篇文献依据，记入 LITERATURE_REGISTRY | §3 收录 21 条已核验文献（arXiv 标题抓取验证）+ 5 条待复核；登记表已建 |
| R2 | 无文献依据最多做 2 小时小验证 | 所有新方向（LoRA/多库/奇美拉）均有文献或本轮实测；无"盲做" |
| R3 | 方案文档必须含「文献依据」章节 | §3 为独立文献依据章节（含核验状态） |
| R4 | 决策写清依据哪篇文献哪条结论 | §6 决策台账逐条带编号（如 D1→L1/L2/E2） |
| R5 | 实验前先问"有没有人做过" | §4 开源依据列出同类项目（Composio/OpenHands/Voyager/ADAS 等）及差异 |

## 1. 依据总览

| 编号 | 类别 | 代表证据 | 置信度 |
|---|---|---|---|
| E | 本机实测 | GPU/模型加载/显存/KV/速度/上下文/兼容性/编排/奇美拉闸门 | 高（可复现，日志留档） |
| L | 文献 | LoRA/QLoRA/vLLM/RAG/ToolLLM/DSPy/RouteLLM 等 21 条已核验 | 高（标题已核验）；ID 未核验者标注待复核 |
| O | 开源实测 | GitHub API：stars/license/更新时间（2026-10-23/26 两次） | 中（数值会漂移；许可需本地复核） |
| I | 项目内部 | 原则文件/幻影工具报告/tool_retriever/基准库/注册表/测试 | 高（仓库内可查） |
| D | 决策台账 | Q1–Q20 + P1–P2.7 落地决策 | 高（逐条可追溯） |

## 2. 实测依据（E）

### E1 硬件与环境（本机）

| 项 | 实测值 |
|---|---|
| GPU0 | RTX 5060 Ti 16,311 MiB（sm_120，Blackwell） |
| GPU1 | RTX 3060 12,288 MiB（sm_86） |
| CPU / 内存 | Intel Core Ultra 7 270K Plus 24 线程 / WSL MemTotal 15.3 GiB |
| 软件 | Ubuntu 24.04.5 LTS · WSL2 kernel 6.18.33.2 · Python 3.12.3 · torch 2.8.0+cu128 · vLLM 0.11.0 |
| 约束 | C 盘仅 ~22GB 空闲；WSL 无 systemd/cgroup/Docker；WSL 无 node（node 只在 Windows） |

### E2 模型实测（决定主脑/工蜂/质量模式选型）

| 模型 | 权重 | KV 容量 | 上下文实测 | 速度实测 | 显存 | 结论 |
|---|---|---|---|---|---|---|
| Qwen2.5-14B-AWQ（主脑） | 9.3786 GiB | 4.67 GiB / 25,504 tok（util 0.90，无 LoRA） | 20480 可用（20K 请求通过；24K 拒绝） | 38–39 tok/s（256 输出）；prefill ~4.3K tok/s | 15.9G（含桌面） | **主脑可用，20480 定档** |
| Qwen2.5-14B（带平台 LoRA 参数） | 9.3786 GiB | 仅 2.14 GiB / 11,712 tok | 11264（启动实测成功） | — | 15.9G | 主脑必须禁用 LoRA（LoRA 参数吃 2.5GiB KV） |
| Qwen2.5-7B-AWQ（工蜂） | 6.43 GiB（含 LoRA） | 2.87 GiB / 53,680 tok | 32768 | 基座 41.4 / 挂 LoRA 26.2 tok/s | 9.9G | **工蜂可用，max-loras=2** |
| Qwen3-8B-AWQ | 5.71 GiB | 4.86 GiB / 35,376 tok | 32768 | 30 tok/s（关思考） | 11.3G | 候选工蜂 |
| Qwen3-14B-AWQ | 9.36 GiB | 4.65 GiB / 30,448 tok | 20480 | 8.8–13.6 tok/s（偏慢） | 15.9G | 不推荐做主脑 |
| Qwen2.5-32B-AWQ（异构 TP=2） | 9.09 GiB/卡 | 40,320 + 11,504 tok | 8192（GPU1 限制） | 10–16 tok/s | 15.9G+11.5G | 质量模式可用（占双卡） |
| Gemma-2-27B GPTQ-4bit | 18.26 GB 文件 | — | 8192（未成功） | — | 不可行 | **不可用**（见 E3） |
| Qwen3-32B-AWQ | 19.34 GB 已下载 | — | 未测 | 未测 | — | 候选（未验证） |

### E3 兼容性与工程实测（关键结论）

| # | 实测现象 | 结论/处置 |
|---|---|---|
| E3.1 | sm_120 上 vLLM：`Failed to get device capability: SM 12.x requires CUDA >= 12.9`，`FlashInfer requires sm75+` | `VLLM_USE_FLASHINFER_SAMPLER=0` 可正常推理（平台 vllm_manager 已内置） |
| E3.2 | Gemma-2 要求 bf16（float16 数值不稳定） | GPTQ 的 gptq_marlin 在 sm_120 触发 `device not ready`；纯 gptq 只支持 fp16 → 与 Gemma-2 冲突 |
| E3.3 | Gemma-2-27B 单卡：18.26GB > 15.9GB；TP=2：3060 侧 OOM（PyTorch 已分配 11.19GiB） | **27B 级量化在本机不可行**（单卡/TP=2 均失败） |
| E3.4 | WSL 不支持 UVA/pin memory：`V1 CPU offloading requires uva support` | vLLM CPU offload 在 WSL 不可用 |
| E3.5 | 异构双卡 TP=2（5060Ti+3060）可跑 32B AWQ | **32B 质量模式可行**（10–16 tok/s，8192 ctx） |
| E3.6 | vLLM 原启动未传 `--host`（可能绑 0.0.0.0）；`VLLM_ALLOW_RUNTIME_LORA_UPDATING=True` | 已加 `VLLM_HOST=127.0.0.1` 与 `VLLM_ENABLE_LORA` 开关（提交 439d91c） |
| E3.7 | 平台默认 LoRA 参数（max-loras=2/rank=128/max-num-seqs=16）额外占 2.5GiB | 主脑路径禁用 LoRA（20480 ctx），工蜂保留 LoRA |
| E3.8 | 平台 `/vllm/stop` 耗时 17s；`/vllm/status` 曾有 stale pid | 已挂起（bug 清单 §9） |

### E4 编排/调度实测

| 实验 | 结果 |
|---|---|
| 18 任务工具路由（全量 68 / 检索 8 工具） | Qwen2.5-14B **100%/100%**；7B **100%/100%**；Qwen3-8B 88.9%/94.4%；Qwen3-14B 88.9%/100% |
| 蜂群决策（12 任务） | **11/12 = 91.7%**（唯一 miss：医疗指南类选了 kb 而非 med LoRA）；平均 1.24s |
| 蜂群执行（6 任务） | **6/6 = 100%**：direct/kb/capability（含自动起工蜂 22.0s + 挂 LoRA）/kb+capability |
| 多步编排（multi route） | 3 步链路（查 BCPCN → 农学 LoRA 总结 → 写领导摘要）**27.2s 成功** |
| 聊天 `auto_orchestrate` | direct 2.0s / kb 16.9s / capability 10.6s / chimera 4.3s（4 类路由全对） |
| LoRA 装载/卸载 | 装载 2–4s；双 LoRA 并发（max-loras=2）成功；卸载成功 |

### E5 奇美拉（缺口映射）实测

| 实验 | 结果 |
|---|---|
| 缺口"浏览器自动化" | 候选 5 个（agent-browser 44,607★ / selenium 34,567★ / crawlee 26,265★ / ego-lite 19,622★ / mcp-chrome 12,510★）→ **5/5 auto_approved** |
| 缺口"PDF 表格抽取" | 候选 3 个 → 全部 needs_review（GitHub 返回 NOASSERTION，政策闸门拒绝自动批准 ✓ 保守） |
| 中文缺口→英文检索词 | 表格→table、抽取→extraction…（本地启发式，无云翻译） |
| 自动入库新能力 | 浏览器自动化 5 个候选自动注册为 proposed 能力（ext:github:*）；GET /capabilities?status=proposed = 5 |
| 候选库 | data/chimera/candidates.jsonl 累计 21 条 |

### E6 平台回归实测

| 指标 | 实测值 |
|---|---|
| 测试 | **888 tests OK**（skipped=6；含新增 60+ 项） |
| API 端点 | 168 个（lora_manager.py） |
| 模块 | 32 个顶层 .py + scripts/ 37 个 |
| 知识库 | 38 个（含 kb_toolchain：69 文档 / 71 chunks） |
| 能力注册表 | 25 个（20 LoRA：16 兼容 / 4 不兼容；5 个外部 proposed） |
| 模型基准库 | 10 个模型（能力界限/可挂 LoRA/状态） |

## 3. 文献依据（L）

登记表：`docs/LITERATURE_REGISTRY.md`（R1 要求）。下列 21 条 arXiv 标题经本机抓取核验（2026-10-26）：

| 编号 | 文献 | 与本架构的关系 |
|---|---|---|
| L1 | LoRA (2106.09685) | 工蜂单步能力用 LoRA；主脑不挂 LoRA |
| L2 | QLoRA (2305.14314) | AWQ 量化 + 训练可行性（本项目训练/推理共用 GPU 的依据） |
| L3 | vLLM/PagedAttention (2309.06180) | 推理服务与 KV 管理选型（实测 vLLM 0.11.0） |
| L4 | Self-RAG (2310.11511) | 知识库检索 + 自反思（KB 路由/动态库注入） |
| L5 | LightRAG (2410.05779) | 研究项：KB 增强候选（未落地） |
| L6 | RAG (2005.11401) | 多知识库 + 动态库作为上下文的总依据 |
| L7 | GraphRAG (2404.16130) | 研究对照（未落地） |
| L8 | Voyager (2305.16291) | 技能库/单步能力解耦（能力注册表思想来源） |
| L9 | ToolLLM (2307.16789) | 工具调用能力/工具链知识库 |
| L10 | Gorilla (2305.15334) | 工具检索与选择 |
| L11 | DSPy (2310.03714) | 第三内化通道候选（程序优化替代 LoRA） |
| L12 | GEPA (2507.19457) | 提示词/程序优化候选 |
| L13 | RouteLLM (2406.18665) | 云/本地路由对照（本项目用规则+主脑判断） |
| L14 | ADAS (2408.08435) | 自动设计 agent 对照（奇美拉思想来源之一） |
| L15 | Darwin Gödel Machine (2505.22954) | 自进化对照（本项目飞轮为可控闭环） |
| L16 | SEAL (2506.10943) | 自改权重对照（本项目不做） |
| L17 | Tool Call Repair/RAG (2510.17874) | 工具修复；项目原则二引用（提示/检索可 +36~56%） |
| L18 | xLAM (2409.03215) | 小模型工具调用（工蜂选型参考） |
| L19 | Self-Discover (2402.03620) | 多步编排对照（本项目用显式 steps） |
| L20 | Qwen2.5 (2412.15115) | 底座依据（主脑/工蜂） |
| L21 | AgentCoord (2404.11943) | 多智能体协作对照（曾误作 MoL，已纠正） |

**待复核（禁止当已核验引用）**：AgentBench（arXiv ID 抓取失败）、Qwen3 技术报告（ID 未确定）、MoL（Mixture of LoRA Experts，ID 未找到）、EasyDistill（按仓库引用）、Anthropic Tool Search Tool（工程文档）。

## 4. 开源依据（O）

两次 GitHub API 实测（2026-10-23 全景 / 2026-10-26 本轮选型）。**数值会漂移；许可最终以本地 LICENSE 检测为准。**

| 类别 | 代表仓库（stars / 许可） | 与本项目关系 |
|---|---|---|
| Agent/工具 | Composio 30,751/MIT；OpenHands 91,864/MIT；smolagents 30,057/Apache；MCP servers 91,567/NOASSERTION | 对照：未直接复用；奇美拉候选源 |
| 技能/自进化 | Voyager 7,292/MIT（2024-04 停更）；ADAS 1,659；DGM 2,453；SEAL 1,875 | 思想来源（能力解耦/技能库） |
| RAG | LightRAG 40,332/MIT；GraphRAG 36,448/MIT；LlamaIndex 52,668；LangChain 148,471 | 研究项对照 |
| 推理服务 | vLLM 95,538；sglang 38,174；llama.cpp 132,381；ollama 184,797 | 本项目用 vLLM（E3 实测） |
| 训练 | unsloth 78,572；peft 21,851；trl 19,602；ms-swift 15,935 | LoRA 训练栈参考（本项目用 peft/trl 系） |
| 评测 | lm-eval-harness 14,316；inspect_ai 3,093；AgentBench 3,791 | 评测对照 |
| 本轮新增 | agent-browser 44,607/Apache；selenium 34,567/Apache；crawlee 26,265/Apache；pdfplumber 10,850；camelot 3,705 | 奇美拉候选（E5） |

**关键差异结论**：没有单一开源项目实现"主脑编排 + 小模型多 LoRA 能力 + 知识库隔离/动态库 + 缺口自动映射"闭环；本项目是把这些能力按"蜂巢（本地）+ 奇美拉（外部映射）"组合起来的自研层。

## 5. 内部依据（I）

| 编号 | 文件 | 内容 |
|---|---|---|
| I1 | docs/PROJECT_PRINCIPLES.md | R1–R5 原则（调研先行、先找不训、评测口径先行） |
| I2 | docs/PHANTOM_TOOLS_20261022.json | 幻影工具：37 个工具有文档无 handler（能力清单必须做可执行性硬门） |
| I3 | scripts/tool_retriever.py | 工具检索 92→8（工具上下文预算） |
| I4 | docs/LITERATURE_REGISTRY.md | 文献/开源依据登记表（R1） |
| I5 | data/models_registry.json | 主脑基准库（10 模型 / 能力界限 / 可挂 LoRA / 状态） |
| I6 | data/capabilities/registry.json | 能力注册表（20 LoRA + 5 外部 proposed） |
| I7 | data/chimera/candidates.jsonl | 奇美拉候选库（21 条） |
| I8 | tests/（888 项） | 回归基线 |

## 6. 决策依据（D）

| 决策 | 内容 | 依据 |
|---|---|---|
| D1 | 主脑 = 14B-AWQ，且**不挂 LoRA** | E2（无 LoRA 可 20480；带 LoRA 仅 11264）+ L1 |
| D2 | 工蜂 = 7B-AWQ + **多单步 LoRA**（max 2 并发） | E2/E4 + L1/L8 |
| D3 | 训练与推理时分复用（GPU0 训练/推理互斥；训练走 GPU1） | E1/E2 + L2 |
| D4 | 知识库多库 + 会话级隔离 + 动态库作为上下文 | L4/L6 + E6（38 库） |
| D5 | 奇美拉 propose-only（不自动安装） | L14/L15/L17 + E5（政策闸门实测） |
| D6 | 缺口映射：先本地检索（GitHub/HF），中文缺口自动英文化 | E5 + I4 |
| D7 | 质量模式 = 32B AWQ 异构 TP=2（占双卡，10–16 tok/s） | E2/E3.5 |
| D8 | 27B/32B 单卡不可行；Gemma-2-27B 标记 unavailable | E3.2/E3.3 |
| D9 | 云端**未配置 key** → cloud 路由/云审诚实降级（不伪造） | E3 + D 台账（Q10） |
| D10 | 主脑调度注入"模型基准库 + 能力清单 + KB 白名单 + 路由规则" | E4（91.7%）+ I5 |
| D11 | 能力解耦：一个 LoRA = 一个单步能力；多步由主脑编排 | L8/L9 + E4 |
| D12 | 工具上下文治理：工具链 KB + tool_retriever（生态审查 P0） | I2/I3 + L26（工程文档） |

## 7. 依据 → 架构结论（可追溯）

| 架构结论 | 支撑依据 | 验证方式/结果 |
|---|---|---|
| 主脑 14B @20480 无 LoRA，38–39 tok/s | E2、E3.7、L1 | 平台托管 13–25s 就绪；20K 请求通过 |
| 工蜂 7B @32768，可挂 2 个 LoRA | E2、L1、L8 | 装载/双 LoRA/卸载/26.2 tok/s 实测 |
| 主脑能自动编排（91.7%） | E4、I5 | 12 任务决策 + 6 任务执行 100% |
| 多步任务可编排 | E4、L19 | 3 步 27.2s 成功 |
| 缺口→候选→计划→自动入库 | E5、L14/L15/L17 | 浏览器自动化 5/5 auto_approved + 5 proposed |
| KB 隔离与动态库 | L4/L6、E6 | 846→888 测试；动态库检索 0.788 |
| 质量模式 32B TP=2 | E2、E3.5 | 8192 上下文 + 10–16 tok/s |
| 27B/Gemma 不可用、Qwen3-32B 未测 | E3.2/E3.3、E2 | 失败日志留档 |
| 云端能力暂缺（key 未配置） | D9 | cloud_review=skipped；cloud 路由降级 direct |
| 工具上下文需治理 | I2/I3、L26 | 工具链 KB 69 文档；tool_retriever 92→8 |

## 8. 架构现状（已实现 / 未实现）

```
蜂巢（本地）
  主脑 14B（无 LoRA，20480，编排/KB 路由/多步）          ✅ P1-P2.6 已实现
  工蜂 7B（32768，多 LoRA 单步能力，max 2）              ✅ P2.3-P2.4 已实现
  知识库：38 库（隔离/会话挂载/动态库 TTL+配额）          ✅ P2.1-P2.2 已实现
  工具链知识库 kb_toolchain（69 文档/71 chunks）          ✅ P2.3 已实现
  模型基准库（10 模型/能力界限/可挂 LoRA）                ✅ P2.7 已实现
奇美拉（外部映射）
  缺口检测（主脑 route=chimera）                          ✅ P2.5 已实现
  候选发现（GitHub/HF + 中英映射）                        ✅ P2.5 已实现
  政策闸门（许可/stars/来源）                              ✅ P2.5 已实现
  声明式计划 IR（不含可执行命令）                          ✅ P2.5 已实现
  新能力自动入库（proposed；不参与路由）                   ✅ P2.7 已实现
  沙箱安装/制品 hash/影子晋级（P3）                        ❌ 未实现
  云端复审（需 API key）                                   ⚠️ 接口就绪，未配置
治理/进化
  审计/预算熔断/KILL 开关/许可发布闸门                     ❌ 计划中
  飞轮（训练→蒸馏→挂载）                                   ⚠️ 项目原有部分能力，尚未与蜂群闭环
```

## 9. 偏差、挂起 bug 与风险

| # | 项 | 状态/影响 |
|---|---|---|
| 1 | `/vllm/stop` 17s、`/vllm/status` 曾 stale pid | 挂起（不影响当前编排） |
| 2 | 平台只托管单 vLLM（主脑+工蜂需手动并行） | 挂起（P2.4 已多实例并存实测，但未平台化） |
| 3 | vLLM 仅绑回环、未启用 api-key | 挂起（同机风险） |
| 4 | `.wslconfig` 内存未调（仍 15.3GB） | 挂起 |
| 5 | 云端未配置 key → cloud 路由/云审缺位 | 已知边界（D9） |
| 6 | P3 沙箱安装未实现（proposed 能力不可路由） | 按设计待做 |
| 7 | 幻影工具 37/68（I2） | 挂起（能力清单需可执行性硬门） |
| 8 | KB 列表含 34 个测试库（路由白名单已缓解） | 挂起（数据清理） |
| 9 | Gemma-27B/Qwen3-14B 等模型占 ~80GB | 挂起（待确认清理） |
| 10 | 文献 L22–L26 待复核 | 登记表已标注，禁止当已核验引用 |

## 10. 附录：验证入口与版本

```
[服务] 主脑 :8000 · 工蜂 :8002 · 平台 :8001 · 前端 :8080
[测试] bash run_tests.sh → Ran 888 tests OK（skipped=6）
[基准库] GET /models/registry · /models/registry/summary · /models/registry/{id}/loras
[能力] GET /capabilities?status=proposed|shadow|active
[奇美拉] POST /chimera/plan {"gap":"..."} · GET /chimera/candidates
[编排] POST /chat/completions {"auto_orchestrate":true,"messages":[...]}
[日志] logs/vllm_14b_24k.log · logs/vllm_worker_7b.log · logs/vllm_gemma27b_*.log
       logs/vllm_32b_tp2_clean.log · /tmp/dl_gemma27b4.log · /tmp/dl_qwen3_32b.log
[提交] 439d91c → 3884146（8 个本地提交，未推送）
[前序文档] 架构分析与规划 v1.1 · 详细设计 v1.0 · 终版架构计划 v1.0 · P2.1–P2.7 实施报告
```

**版本记录**：v1.0（2026-10-26）首次成文；依据随每次方向变更同步更新（R1/R3）。
