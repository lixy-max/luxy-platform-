# 未完成任务汇总（Open Tasks）

> 生成：2026-10-13（DSH 会话）｜维护方式：做完一条就在这里划掉，并在 `HANDOVER.md` 补一节
> 现状基线（实测于 2026-10-13）：
> - 门禁：后端 **699 OK** · 前端 **371/371** · JS 语法 **3/3** · `make lint` 绿
> - 新护栏：`make check-local-agent`（本地 Agent 路径冒烟，12 项）· `make lint`（pyflakes）
> - 服务：后端 :8001 200 · 前端 :8080 200 · 底座 :8000 200（Qwen2.5-7B-Instruct-AWQ, max_model_len 32768）
> - 知识库：共 **17** 个，其中 **9 个是 0 块空库**；`agent_use` = 10 条目 / 74 块 / bge-m3·1024
> - 学习增强：enabled=True · timeout=100s · kb=existing/agent_use · escalate/distill 均为空（不烧云）

图例：**P0** 阻塞主功能｜**P1** 影响主体验｜**P2** 值得做｜**P3** 有余力再做

---

## A. 学习闭环（本项目当前主线，未完成）

### A1 ★ Agent 模式不经过「学习增强」——本地做不好时不会走云、不会入库  **P0**
- **现状**：学习增强挂在 `POST /chat/completions(/stream)`；而 Agent 模式走 `POST /chat/tools`。
  两条路互不相通 ⇒ 在 Agent 模式里，本地模型答不出来**不会**触发云接手，也不会采轨迹入库。
- **为什么要紧**：用户要的闭环是「本地先做 → 不会的才走云 → 云的轨迹清洗入库」。
  只有 Q&A 路（学习增强）通了，Agent 路（工具能力）没通，等于闭环只闭合了一半。
- **做法（建议）**：在前端 Agent 循环里加一层判定：
  1. 本轮的 `tool_calls` 为空 **且** 回答明显不满足任务要求（可用规则+判官）→ 判定"本地不行"；
  2. 把该轮交给 `POST /chat/completions/stream`（带 `learn.enabled`）重做 → 云接手 → 后端阶段3采轨迹入库。
- **验收**：界面 Agent 模式 + 本地底座，发一条本地确实做不好的任务 → 日志出现 handoff → `agent_use` 条目/块数增长 → 能检索到新经验。

### A2 ★ 工具表要按上下文预算裁剪  **P0**
- **现状**：`buildToolsSchema()` 现在暴露 **88 个工具**，请求体 34 KB ≈ **10K tokens**。
  8K 以下的底座（如 4096 的 Qwen3-4B 默认档）**直接把请求撑爆**（实测 13036 tokens → 400）。
- **做法**：用 `/chat/context` 拿到 `max_model_len`，按预算分级给工具：
  例如 ≤8K 只给 20 个核心只读工具；16K 给 40 个；≥32K 给全量。
  （或分两阶段：先给"发现类"工具，用到某域再补该域工具。）
- **验收**：把底座切到 4K 档，Agent 模式仍能用（不再 400），且能完成"列出知识库"这类基本任务。

### A3 答题质量类学习成果**不进** `agent_use`  **P1**
- **已确立的规矩**（实测结论）：`agent_use` 是**工具调用经验库**；"答题规范"类内容放进去
  **检索不到**（实测 5/5 查询落空）——在库 ≠ 能查到。
- **待落地**：给答题质量类成果一个去处：单独建库（如 `qa_standards`）**或**写成项目规范文件。
- **验收**：随便问一个"什么时候该用某工具"，能从 `agent_use` 检索到；而答题规范能在它自己的去处检索到。

### A4 蒸馏产物要"经验型短条目"，不是"答案集"  **P1**
- **现象**：把云答案整段汇总成文档入库 → 检索命中率极低（语义是"怎么做" vs 内容是"某题答案"）。
- **做法**：蒸馏时额外产出一节「可照做的规则」（短句、带触发条件），放在文档最前面。
- **验收**：用"字数上限 怎么满足""不要用术语 怎么做"这类**元问题**能检索到新增条目。

### A5 设计"难到本地做不了"的任务集  **P1**
- **现象**：现有 100 条（"写 400 字说明""列 5 个误解"）对本地 7B **太浅**：
  实测 3~11 秒答完、**云接手 0 条、入库 0 条** ⇒ 学不到东西。
- **做法**：任务要命中本地短板：严格格式/数量约束、多步依赖、专业深度、需要真实工具数据。
- **验收**：跑 20 条 → 云接手比例落在 **20%~60%**（全 0 说明太浅，全 100% 说明太难/链路坏）。

### A6 ★ reflect 收尾超时把【已经产出的答案】丢掉  **P0**
- **实测（318 条全量复核）**：`rc=-9`（超时）64 条，其中 **64/64** 的 `diagnostics` 都含
  `[reflect] 超时` —— 也就是**超时切在收尾阶段，不是工具循环**（仅少数死在工具循环）。
  其中 **32 条连已经写好的 `final` 都没留下** ✗。
- **为什么要紧**：这些答案本来产出过，却因为收尾被切而丢失 ⇒ **学习数据白丢**，
  而且会让"失败率"看起来比实际高。
- **做法（二选一或都做）**：
  1. 从子进程的部分 stdout / 会话日志里**抢救最后一次 assistant 文本**当 `final`；
  2. 把"收尾阶段"的预算与"工具循环"分开（例如整体 100s，但收尾至少留 15s
     或用 `--no-reflect` 之类开关），避免答完被切。
- **顺便**：`ms` 是**量化预算标记**（rc=0 都落在 60.8~61.0s，rc=-9 都落在 100.0~100.1s），
  **不要**拿它当 agent 速度指标。
- **验收**：同一批任务重跑 → 超时条里"有 final"的比例显著上升、空产出率下降。

### A7 `read` 工具的路径形态错配（最大单一失败源）  **P1**
- **实测**：`read` 报错 145 次，其中 **99 次**是把 WSL 路径解析成 `E:\home\luxy\...`
  （样例：`Error: cannot read "E:\home\luxy\paper_lora_project\training_data\xxx.jsonl": not found`）。
  **限定到只读三类 read/glob/grep 时占 42.3%；只看 read 则占 68%**。
  ★ 而 `glob`/`grep` 的路径错配为 **0** ⇒ 问题**只出在 `read`** ✓（比"三类都错"更精确）。
- **关键判断**：读取**能力**是有的（`pwsh` 616/621 成功）⇒ 这是**返回信息没给出路**，
  不是缺工具。
- **做法**：`read` 失败时的报错要带"这是 WSL 路径；请改用 `\wsl.localhost\...` 形态，
  或改用 pwsh/glob"。更彻底的做法是让 headless agent **在 WSL 内运行**（路径天然原生）。
- **验收**：重跑同批任务 → `read` 的路径类报错降到接近 0，且失败轨迹的平均调用次数下降。


---

## B. 工具链与平台

### B1 清理 9 个 0 块空知识库  **P1**
- **清单**（探测遗留）：`probe_top3_r2b` `probe_top3_r2c` `probe_trainsets_r2` `r2_embed_probe`
  `r2_size_xcheck` `r2b_top3_xcheck` `tmp_xlsx_probe` `verify_r2_xlsx` `verify_r3_ds`
  （另有 `learned_test` `learned_chat` 等测试库，量小但可归置）
- **注意**：`r2_lora_fs_probe`（2536 块）是探测产物但体量大，删前先确认没被引用。
- **验收**：`/kb/list` 只剩真正在用的库，`agent_use`/`xiyouji`/`minimax_h3_prompt` 完好。

### B2 换底座时旧 LoRA 的处理  **P1**
- **现状**：底座换成 Qwen3-4B（hidden 2560）后，7B 训的 LoRA（hidden 3584）必然 400；
  前端已修成"明确告知 + 停止重试"，但**没有**主动卸载/提示切换。
- **做法**：切底座时若检测到已加载 LoRA 与新底座不兼容 → 先卸载并提示，而不是等用户撞 400。
- **验收**：切到 4B → 页面不再出现任何 LoRA 相关的 400，且明确提示"已卸载不兼容的 LoRA"。

### B3 训练闭环端到端验证  **P1**
- **内容**：起训练 → 训练完自动拉起 vLLM → 自动挂载新 LoRA → 在对话/Agent 里真的用上。
- **注意**：训练要停 vLLM 释放显存（现在 vLLM 常驻 0.85 util）。
- **验收**：一条命令/一次点击跑完，`/loras/loaded` 出现新 LoRA，Agent 能调用它。

### B4 `workflow_latest` / 其它工具的报错引导  **P1**（已量化，升级自 P2）
- **实测**：`paper_lora_workflow_latest` **报错 60 次**（同类里第二高），
  `paper_lora_list_sheets` 32 次、`paper_lora_kb_query` 30 次、`paper_lora_call` 27 次。
- **做法**：这些工具在"没有结果/没有该工作表/查询为空"时，要在返回里给出
  **下一步可照做的做法**（"先跑一次工作流：<具体命令>"），而不是只回一个错误。
  这正是 F1 结论里"该补返回信息"那一类（72 条 / 34.4%）。
- **验收**：重跑 → 这些工具的报错里"带 next-step 指引"的占比 100%，且对应任务的成功率上升。

### B5 工具语义类历史遗留  **P2**
- `export_docx` 等"把一整坨 JSON 当正文"的旧问题已堵；但同类语义错误需再扫一遍（`docs/FAILURE_CASES.md` 有清单）。

### B6 `docs/BASELINE.md` / `docs/EVAL_PROTOCOL.md` 与现状对齐  **P2**
- 这两个文档成文于 2026-10-08，晚于它们的代码与基线都已变化。

### B7 LoRA registry 与磁盘不一致  **P2**
- **实测**：`~/loras/registry.json` 有 21 条记录，但其中 **5 条没有目录**
  （`swk_persona_v1` / `swk_persona_v2` / `swk_ckpt32` / `swk_ckpt64` / `swk_ckpt96`）——
  它们本轮早先被归档到 `~/loras_archive/`（带 `_archived` 标记），registry 没同步；
  另有 **2 个目录没有记录**（`dimensions_cov_v1` / `dimensions_cov_v2`）。
- **影响**：`GET /roles` 已会跳过缺目录的项（不会崩）；但"清单与实际不符"会继续误导文档与用户。
- **做法**：给 registry 里已归档的条目打 `archived: true`（或移出 registry 到 `archived.json`），
  并给 `dimensions_cov_v1/v2` 补记录。
- **验收**：registry 与 `ls ~/loras` 一致（或差异项都有明确标注）。

### B8 文档里的底座/模型清单已过期  **P2**
- **实测**：`~/models` 共 9 个；HANDOVER §9 列的 `Qwen3-4B-Instruct-AWQ` **不存在**，
  `Qwen2.5-0.5B-Instruct` **漏列**。
- **做法**：底座清单改为从 `ls ~/models` 生成（或写脚本校验文档与磁盘一致）。
- **验收**：文档里的清单与 `ls ~/models` 一致。


---

## C. 论文分析主线（**本会话完全未碰**）

### C1 ★ 端到端走通：上传论文 → 跑工作流 → 出报告/台账  **P0**
- 这是项目最初的主线，本轮全部精力在"工具链 + 本地 Agent"，**没有验证过它现在还能不能跑**。
- **验收**：传一篇 PDF → 默认工作流 → `outputs/` 出报告，内容非 JSON 垃圾、可读。

### C2 数据资产与代码状态一致性复核  **P1**
- `HANDOVER.md` §9 记的资产（papers 30 篇、`registry.json` 评测值、`extract_v2/v3`）需要与当前代码/工作流对一遍。

---

## D. 虚拟生命（二次开发，已暂停）

### D1 P0–P5 清单的剩余项  **P2**
- 其中 **P3 记忆层** 与 **P2 剩余项** 尚未开始。
### D2 西游记数据集里的现代场景噪声  **P2**
- 样本中混入"在学校被堵在厕所"这类现代语境，与孙悟空语体冲突，会污染人格 LoRA。

---

## E. 工程护栏（本轮已建一部分，仍缺）

### E1 把两条护栏接进发布流程  **P1**
- 现状：`make lint` / `make check-local-agent` 要**手动**跑。
- 做法：`make release` 前先跑两者；或加 `pre-commit`（无 CI 环境）。
- **验收**：故意在 `vllm_manager.py` 去掉 `--tool-call-parser` → `make release` 应失败。

### E2 前端用例没有覆盖"本地 Agent 路径"  **P2**
- 371 个用例主要是文档流水线；本地 agent 目前只靠 `check_local_agent.sh`（静态+一次端到端）。
- 做法：把"选本地底座 → agent → 有 tool_calls → 执行 → 汇总"做成一个可重复的界面探针。

### E3 ✅ 大部分已完成（2026-10-14）—— 仅两个二进制产物待你定口径
- `_reflect_out/`、`runs/p1_eval/`、`runs/persona_test_*.json`、`train_lora.py.orig-p1a4`、
  `doc_flows/doc_flow_new.json`、`论文输入修复要点清单.docx` —— 决定入库还是清理，别让工作区长期浑浊。
- **注意**：`.gitignore` 已排除 `data/trajectories/`、`agent_kb/90_learned.md`（成果文件，不入库）。
- **已做**：`.gitignore` 加 `*.orig-*`；删 `train_lora.py.orig-p1a4`；入库 6 个文本产物
  （`doc_flows/doc_flow_new.json`、`runs/p1_eval/*`、`runs/persona_test_swk_persona_{v2,v3}.json`）。
- **待你定口径**：`_reflect_out/采购摘录_待填.xlsx` 与 `论文输入修复要点清单.docx`
  （二进制产物；仓库此前无情例 → 入库 / 移到 `outputs/` / 加 `.gitignore`，三选一）。

### E4 ★ 服务启动后又被 WSL 回收（"启动成功但过一会儿全没了"）  **P0**
- **现象**：双击 `启动工作台.bat` → 汇总显示 后端 200 / 前端 200 / 底座在加载 → **一两分钟后三个端口全 000**，
  进程表只剩 `init`。
- **实测证据（2026-10-15 01:32）**：
  - `dmesg`：`EXT4-fs (sdd): unmounting filesystem` → 随后 `mounted filesystem`
    —— 发行版被**整体拆掉又挂载**；
  - `/proc/1` 是 WSL 的 `init(Ubuntu-24.04)`，**没有 systemd**；
  - 服务确实是 `setsid nohup … &` 起的（`scripts/start.sh` 第 77/99 行）⇒ **不是脚本没脱离**。
- **根因**：WSL2 在**最后一个会话结束时回收发行版**；本项目一直靠
  `E:\WSL\workbench-keepalive.bat`（跑完 `start.sh` 后 `exec tail -f /dev/null`）**占住会话**。
  该脚本由计划任务 `PaperLoraWorkbench-AutoStart` 调用，而该任务已被**禁用**
  （用户早前要求不要开机自启）⇒ 于是"启动器窗口一结束，服务和发行版一起被收走"。
- **修法（互不冲突，建议 A）**：
  - **A（推荐）**：改 `启动工作台.bat`：跑完 `start.sh` 后**另起一个隐藏的 keeper 会话**
    （`start /min "" wsl.exe -d Ubuntu-24.04 -- bash -lc "exec tail -f /dev/null"`）
    ⇒ 发行版常驻 ✓ 而启动器窗口照旧打印汇总并返回 ✓ **不等于开机自启** ✓
  - **B**：把计划任务改回"手动"状态，需要常驻时手动跑 keepalive（不登录自启）。
  - **C（更强，需明确同意）**：keeper 里加 supervisor 循环 —— 服务不在就自动拉起
    ⇒ 重启后无人值守也能恢复，但**语义上等于自动启动**，必须先获得用户同意。
- **验收**：双击启动器 → 等 3 分钟 → 三端口仍为 200；关掉启动器窗口后仍为 200。
- **副产物**：`start.sh` 的健康检查在 WSL 冷启动时可能"第一次探活失败"而误报，
  可加短重试（与 E4 一起做）。


---

## F. 工具链优化遗留（本轮做了一部分）

### F1 ✅ 已完成（2026-10-14）—— 失败类型已全量分类
- 已完成：从 123 条轨迹诊断 → 结论"缺的不是工具，是**工具返回的信息**"；
  给 `/training/available_datasets` 加 `purpose/sample_task/roles` 后空产出 4/5 → 1/6。
- 待做：把剩余失败按类型分完（如"数据集为空""workflow_latest 空"），逐类补信息或补引导。

### F2 GLM-2.7 的接入位（**待用户确认**）  **P1**
- 用户已为智能体团队配置 **GLM-2.7**。需要决定用在哪儿：
  - 学习增强的**接手模型**（`learn.escalate_model`）？
  - 蒸馏模型（`distill_model`）？
  - 还是 DSH 侧 agent 团队的模型？
- 当前学习增强的 `escalate_model` / `distill_model` 都是**空**（有意为之：不让超时烧云）。
  若要启用，建议先用一条任务做对照（成本、时延、清洗通过率），再决定。

---

## F1 实测结论（2026-10-14，209 条轨迹全量分类）

- **互斥主分类**：名字猜错-工具名不存在 11(5.3%) · 空产出 32(15.3%) · 挖矿超时仍有稿 26(12.4%) ·
  **报错后未换路 40(19.1%)** · 自造探测物 4(1.9%) · 其它失败 29(13.9%) · 成功对照 67(32.1%)
- **调用次数**：成功 8.61（p50 7 / max 30）vs 失败 26.30（p50 23 / max 55）→ **失败重 3.05 倍**；
  只读深挖 read+glob+grep：成功 2.61 vs 失败 12.52（**4.8 倍**）
- **结论分布**：该补【返回信息】72 条(34.4%) · 该补【引导文案】66 条(31.6%) · 真的【缺工具】4 条(1.9%)
- **数据真相**：`archived_*` 只有 46 行（26+20）；149 是 `trajectories.round2.jsonl`；
  4 个文件合计 209 行 / 191 个不同 session（36 行重复）
- 全部数字由 `data/trajectories/analyze_failures.py` 复现（两次运行输出逐字节一致）；
  完整报告见 `docs/FAILURE_CLASSES.md`


---

## 队友产出的文档（2026-10-14）

| 文档 | 来源 | 内容 |
|---|---|---|
| `docs/FAILURE_CLASSES.md` | teammate `failure-miner` | 209 条轨迹的失败分类 + 机制发现（reflect 收尾超时丢答案 / read 路径错配） |
| `data/trajectories/analyze_failures.py` | 同上 | 复现全部数字的离线脚本（`git add -f` 纳入，因 `data/` 被忽略） |
| `docs/KB_TIDY_REPORT.md` | teammate `kb-curator` | 17→8 库清理，含删前/删后指纹对比与备份路径 |
| `docs/AUDIT_ASSETS_2026-10-13.md` | teammate `asset-auditor` | 资产与工作区审计；HANDOVER §9 的过期断言逐条纠正 |

## G. 工作流链条（2026-10-15 代码级审计，详见 `docs/CHAIN_AUDIT.md`）

结论：**组件几乎全都在 ✓**（节点拆分 / 节点级 LoRA 动态切换 / 节点级 KB 检索 /
产物落盘 + 按节点导出训练数据），**缺两处接线 + 两处健壮性**。

### G1 ✅ 已完成（2026-10-15）—— 工作流产物已回流到知识库

> **实现**（已端到端验收）: `workflow_learn.py`（清洗 → 汇总 → 入库；默认目标 `workflow_learned`，
> ★ **默认拒写 `agent_use`**）+ 引擎注册 `kb_write` 字段 + `/workflow/run` 成功分支调用。
> 实测: 真实 result 文件 → 目标库 1 条目 / 9 块 ✓；`agent_use` 指纹入库前后不变 ✓（10 条目 / 74 块）；新增 9 个单测。
- 证据：`workflow_engine.py` 对 KB **只有读**（`GET /kb/list`:557、`POST /kb/{name}/query`:1514）；
  `/workflow/run`（`lora_manager.py:1160`）不碰 `kb.` / `learn_sink`；学习链路只挂在
  `/chat/completions`(5701) 与 `/reflect/ingest`(4483)。⇒ 跑 100 次工作流，KB 一点不变。
- 做法：节点加 `kb_write{mode,target}`；运行收尾把节点 trace 清洗后入库；
  **默认写新建的 `workflow_learned`，绝不写 `agent_use`**（混库会"进得去查不到"，已实测 5/5 落空）。
- 验收：跑一次工作流 → 目标库块数增长且能被"这次结论是什么"检索到；`agent_use` 指纹不变。

### G2 ✅ 已完成（2026-10-15）—— agent 可新建/克隆/切换工作流配置
- 证据：agent 工作流工具只有 `list_workflow_nodes / add_workflow_node / update_workflow_node /
  delete_workflow_node / validate_workflow / run_workflow / list_workflow_*`，
  三个节点工具都硬用 `currentConfigName`；而"另存"能力**前端已有**
  （`webui/index3.html` 2224-2245 → `PUT /workflow/config?name=<新名>`，后端 `lora_manager.py:1121` 支持）。
- 做法：补两个 agent 工具 `create_workflow_config(name, from_current)` 与 `use_workflow_config(name)`。
- 验收：对 agent 说"按这个需求搭一套工作流" → 它会新建配置 → 加节点 → 校验 → 运行，且不动原配置。

### G3 节点级 LoRA 缺前置兼容校验  **P1**
- 证据：`LoraRouter.ensure()` 失败只回后端 detail（`workflow_engine.py:205`）；
  而 `/loras` 已带 `compatible`、`registry.json` 有 `hidden_size` —— 信息现成，只是没人前置用。
- 做法：`validate_workflow` 与运行预检里一次性列全不兼容节点 + 建议。
- 验收：底座切到 Qwen3-4B 时校验旧工作流 → 立刻列出不兼容节点。

### G4 多 LoRA 节点的换入换出成本无提示  **P1**
- 证据：vLLM `--max-loras 2`；`LoraRouter.swaps` 只记耗时（192），没有抖动预判。
- 做法：统计配置里不同 LoRA 个数，超限提示预计切换次数/耗时，并建议同 LoRA 节点相邻。
- 验收：3 LoRA 的配置在校验阶段给出提示。

### G5 ✅ 已完成（随 G1）—— 节点新增 kb_write 字段
- 证据：`NODE_FIELD_SPECS`（766+）只有 `kb_name/top_k/min_score`（读），没有写回字段。

---

## H. 实战演练（2026-10-15 定稿，**晚上开始，先不做**）

> 完整方案见 **`docs/DRILL_PLAN_2026-10-15.md`**（运行条件 / 逐条协议 / 判分方案 / 100 条任务规格 /
> 分阶段估时 / 只记录不实现的缺口 / 风险对策 / 今晚执行顺序）✓

用户已确认的 8 项决策: 云接手先补 A1 ✓ · 学习增强目标库新建 `test_use_agent` ✓ ·
LoRA 数据取 part1 任务数据 ✓ · "规定"用 `docs/LORA_DATA_AND_EVAL_GUIDE.md` ✓ ·
"读库→清洗→训练"只记录缺失 ✓ · part2 = 同能力换说法 ✓ · 配比每 50 条 = 40/6/4 ✓ · 判分两段制 ✓

### H1 补 A1：agent 失败/超时也自动走云  **P0（当晚第一件）**
- agent 模式走 `/chat/tools`，学习增强挂 `/chat/completions` ⇒ 两者不通 ✗
- 做法：agent 循环里"本轮无 tool_calls 且结果不达标 / 超时 / 报错"→ 转 `/chat/completions`（带 learn）重做
- 验收：故意让本地失败 1 条 → 云接手 → `test_use_agent` 块数增长，且 `agent_use` 指纹不变 ✓

### H2 新建 `test_use_agent` 并切学习增强目标库  **P0（当晚最先做）**
- 现状 `learn.kb_name=agent_use` ✗（宝贵库，不该混入对话/工作流长文）
- 验收：新库存在；跑 1 条走云任务后块数增长；`agent_use` 条目/块数不变 ✓

### H3 写 100 条任务 + 每任务验收点  **P0**
- part1 = 50（40 可完成 + 6 边界 + 4 故意不可能）· part2 = 50（同能力换说法）
- 必须包含用户给的 3 条种子任务；覆盖全部 skill（对照 50 个 agent 工具）
- 每条附带 1~5 条"可引用证据的验收点"（判官用 ✓）

### H4 part1 全量 50 条 + 四本账核对  **P0**
- 四本账: 结果 / 轨迹 / 入库（`test_use_agent`）/ LoRA 数据 ✗ 不许混
- 达标线: 一次成功率 ≥70% · 云接手后合格率 ≥80%

### H5 LoRA 闭环（数据评分 → v1 → 调数据 → v2 → 测提升）  **P0**
- 数据合规评分用 `docs/LORA_DATA_AND_EVAL_GUIDE.md`（★ 先不改数据 ✓）
- 复跑对比用 **part1 核心子集 20 条**（不重跑 50 ✗）
- 达标线: v2 相对 v1 提升 ≥5 个百分点

### H6 part2 50 条（同能力换说法）  **P1**
- 与 part1 对比"换说法是否仍稳" ✓

### H7 【只记录】缺口：读库 → 清洗 → 训练 没有串起来的流程  **记录项**
- 组件都在 ✓（kb 查询/rebuild · learn_sink 清洗 · train_lora QLoRA）
- 缺的是一条把三者串起来的流程 ✗ —— 本次只记录、不实现（用户要求 ✓）

---

## J. 平台问题修复（2026-10-15 ~ 10-19 · 详见 `docs/PLATFORM_ISSUES.md`）

| # | 问题 | 状态 | 验收依据 |
|---|---|---|---|
| P-1 | 学习链提炼恒为空模板 ⇒ KB 长不大 | ✅ 已修 | KB 0 → 1 条目/30 块 ✓ |
| P-2 | 清洗按长度丢任务 ⇒ 用户短口气全丢 | ✅ 已修 | 真实语料保留 22/23 = 95.7% ✓ |
| P-3 | 并发训练崩 CUDA | ✅ 已修 | 第二个 start 立刻 409 ✓ + flock 单测 ✓ |
| P-4/P-7 | 截断数据也开训 | ✅ 已修 | 旧数据 40/40 超 cutoff ⇒ 拒绝 ✓ |
| P-5 | 无梯度检查点 + 孤儿泄漏 | ✅ 已修 | 训练首次跑完 25/25 步 ✓ |
| P-6 | system 提示 9253 字符 | ✅ 已修 | 269 字符 ✓ |
| P-8 | 危险操作能改发布默认配置 | ✅ 已修 | 默认配置下 PUT 次数 0 ✓ |
| P-9 | 云轨迹无正确动作计划 | ✅ 已修 | PLAN 解析 + planned_tools ✓ |
| P-10 | 工具口径不一致（50 vs 52） | ✅ 已修 | 目录重生成 52 ✓ + 防漂移测试 ✓ |
| P-11 | 训练状态假 running | ✅ 已修 | 启动对账 ✓ |
| P-12 | vLLM 启动无互斥 | ✅ 已修 | keeper 日志见"已有锁 ✓"✓ |
| P-13 | reflect 超时丢 final | ✅ 已修 | 打捞 + final_salvaged ✓ |
| P-14 | `/loras` 字段名误解 | ✅ 澄清 | 返回 `loras` 而非 `all` ✓ |
| P-15 | 云接手无工具 ⇒ 接手后仍不完成 | ⏳ 待做（P0 ✓） | 建议: 解析 PLAN 由平台代执行 |
| P-16 | 标签形态与推理形态不一致 | ✅ 验证推翻 ✗ | 格式本来就对齐 ✓（详见 P18 报告） |
| P-17 | API 传 lora_rank 不生效 | ✅ 已修 | 注入被 env 覆盖 ⇒ 已挪位置 ✓（待复测 ✓） |
| P-18 | 训练-推理格式不对齐 | ✅ 验证为误判 ✓ | 三层验证: 格式本来对齐 ✓ |
| P-19 | 工具定义重复（92 个含重名） | ✅ 已修 | 92 → 73 · 重复 [] ✓ |
| P-20 | 页面可能漏渲染工具卡 | ⏳ 待查（P1） | API 30% vs UI 25% ⇒ 少算 ✓ |
| P-21 | 演练方法: 对照跑批被改状态任务污染 | ✅ 已处置 | core19 排除 P1-20 ✓ |
| P-22 | 并行工具调用被静默丢弃（只执行第 1 个） | ✅ 已修 | 执行率 61% → 100% ✓ |
| P-23 | 学习链语料不合格（免责模板 66.7% / 重复 60%） | ✅ 已加闸门 | `agent_use` 未被污染 ✓ |
| P-24 | 演练任务设计缺陷（P-8 守卫致 4 条必败） | ✅ 已修 | 写操作 2/6 → 4/6 ✓ |
| P-25 | 对照跑批期间任务文档被改（测量污染） | ✅ 纪律已立 | 两臂同版本重跑 ✓ |
| P-26 | 发布闸门被演练产物卡住 | ✅ 已修 | 只看已入库文件 ✓ |
| P-27 | vLLM 自杀循环（keeper 探活杀加载中实例） | ✅ 已修 | 启动 1 次（前 5 次）✓ |
| P-28 | 用"杀所有 node"停服务差点杀跑批 | ✅ 教训已立 | 只杀指定 PID ✓ |
| P-29 | WSL 访问不到 Windows 127.0.0.1 | ✅ 已绕过 | 前端直连 sidecar ✓ |
| P-30 | CORS 预检缺 Allow-Headers | ✅ 已修 | POST+JSON 通 ✓ |
| P-31 | 工具名与动作名差 `browser_` 前缀 | ✅ 已修 | 双向兼容 ✓ |
| P-32~P-34 ⚠ | 登录流程 3 bug（fill 自动提交 / ref 过期 / 业务错误误判） | ✅ 已修 | 真实站点登录成功 ✓ `c4c3151` |
| P-35~P-40 ⚠ | 浏览器语义标注 / ref 稳定 / href 兜底 / 死循环 / model 传递 / 150s 超时 | ✅ 已修 | `7faabfd` |
| P-41 | snapshot 截 600 字符把 refs 全截掉 | ✅ 已修 | 代码注释 ✓ |
| P-42 | snapshot 只收视口元素（下方区块看不到） | ✅ 已修 | 收全页 ✓ |
| P-43 | 缺 scroll 工具（模型想滚动却没工具） | ✅ 已修 | `browser_scroll`（13 工具）✓ |
| P-44 | snapshot 正文只取 3000 字符 | ✅ 已修 | 3000 → 12000 ✓ |
| P-45 | `read_docx/read_sheet` 元数据缺 `out_dir`（agent 读不到 E 盘文件） | ✅ 已修 | curl 读 `E:\zy.work\from_agent` 200 / 5200 字符 ✓ |
| P-45b | 前端旧 handler 丢掉 `out_dir`（写不到 E 盘） | ✅ 已修 | `write_docx(out_dir=E:\zy.work)` 真落盘 ✓ |
| P-46 | agent 模式 `nativeToolCall` 写死 max_tokens 2048（长文被截断） | ✅ 已修 | 跟随 `#chat-max-tokens`（256~8192）✓ |
| P-47 | 主对话下拉选不到云模型（agent 无法主动用云） | ✅ 已修（真页面已验证） | 云模型选项实测出现 ✓ |
| P-48 | 计算纪律诱导逐项仿真 → 24 轮迭代耗尽 → 无工具云接手 → 输出"无法完成" | ⏳ 已规避未修 | 任务提示（算术合并/≤3 次）→ 5 轮完成 ✓ |
| P-49 | `write_docx(table)`/`write_sheet(rows)` 传 JSON 字符串 ⇒ 静默丢数据却返回成功 | ✅ 已修 | 828 tests ✓ · 实测字符串表格→真 Word 表 ✓ |
| P-50 | `marked` 走 CDN，弱网 undefined ⇒ agent 回合崩（`marked is not defined`） | ✅ 已修 | 372/372 ✓ · 实测任务跑通 ✓ |

---

## 附：本会话（2026-10-13）已完成，避免重复劳动

- 打通「通过 LoRA 平台直接驱动」：`VLLM_PORT` NameError → 修；vLLM 加
  `--enable-auto-tool-choice` + `--tool-call-parser hermes`；前端本地也走原生 function-calling；
  `buildToolsSchema` 并入 20+ 个 agent 工具（0 → 88 个）。
- 前端不再自己猜上下文：新增 `GET /vllm/recommend`，`switchModel()` 改为问后端
  （原先按磁盘体积把 Qwen3-4B 推荐成 4096 → agent 请求 13036 tokens 必 400）。
- 前端静态服务改 no-store（治"改了前端，用户看到的还是旧版"）。
- `ensureLoraLoaded` 检查返回码（原先 400 当成功、弹假提示、每 30 秒无限重试）。
- 三个桌面启动器：统一 UTF-8(无BOM) + chcp 65001 + **纯 CRLF**（原先 CRLF/LF 混用 → 行粘连 → 中文被当命令）；
  修掉汇总里 `000000`（curl 失败时输出了两个 000）；桌面三份已同步替换。
- 静态检查落地：`scripts/lint.sh` + `make lint`（pyflakes）→ 一次扫出并修掉
  `_re_md` 未定义、`except as e` 闭包引用 e（NameError）等真 bug。
- `agent_kb/90_learned.md` 被空蒸馏覆盖 → 用归档轨迹精确还原（7762 字符 / 251 行）并入库回 `agent_use`；
  蒸馏/入库加 `MIN_TRAJ` 阈值防护。
- 文档：`README.md`、`CHANGELOG.md`、`docs/FAILURE_CASES.md`（新增第十二/十三组）、`docs/dsh/`。
