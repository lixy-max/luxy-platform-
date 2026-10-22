# 开源封装清单（Open-Source Packaging Checklist）

> 本文是**执行清单**，不是愿景文档。每一项都有：**为什么 / 怎么做 / 验收标准 / 工时**。
> 现状事实取自一次**只读审计**（见 §0），不是印象。
> 状态标记：`[ ]` 未做 · `[x]` 已完成 · `[!]` 阻断发布 · `[?]` 需你决策

---

## §0 现状审计结论（事实，用于判断工作量）

| 检查项 | 结果 |
|---|---|
| **明文密钥是否进过 git 历史** | **否** ✓（`git log -S` 命中 0 次；`settings.json` 从未被提交）|
| 工作区 `settings.json` | 含明文密钥（已被 `.gitignore` 挡住，仅本地）|
| 被追踪文件数 / 体积 | **64 个 / 1.6 MB**（`.git` 12 MB）|
| 用户数据（`papers/ uploads/ outputs/ training_data/ data/ loras/`）| **全部 0 个被追踪** ✓ |
| 运维脚本 `_check/` | **0 个被追踪** ✓ |
| 已有要件 | `.gitignore` ✓ `.gitattributes` ✓ `README.md`(27KB) ✓ `settings.example.json` ✓ |
| **缺失要件** | **`LICENSE`** ✗ · **`requirements.txt`** ✗ · `CONTRIBUTING.md` ✗ · CI ✗ |
| 硬编码路径 | `/home/luxy/...` 出现在多个脚本与文档中（需配置化）|
| 代码规模 | `lora_manager.py` 3.5k 行 · `workflow_engine.py` 1.9k 行 · `webui/index3.html` **6.3k 行 / 306KB 单文件** |
| 测试基线 | 后端 **690 OK** · 前端 **219 passed** |

**结论**：历史干净、数据未泄露 → **可以直接公开现有仓库 + 完整开发历史**；
剩下的工作是"**让别人能跑起来**"与"**让卖点可验证**"。

---

## §1 P0 合规与安全（**阻断发布**）

- [x] **[!][?] A1 选择 LICENSE** —— MIT / Apache-2.0 / AGPL-3.0 三选一  <!-- 已选 MIT，LICENSE 已生成 -->
  - 为什么：无许可证 = 法律上"保留所有权利"，别人不能用
  - 怎么做：新建 `LICENSE`；README 顶部加徽章；`pyproject.toml`/元数据里填 SPDX 标识
  - 验收：`LICENSE` 存在且 git 追踪；README 有对应说明
  - 工时：**由你决定**（唯一我无法代决的事）
  - 提示：若希望"别人改了必须开源"→ AGPL；若希望"最容易被采用"→ MIT/Apache-2.0

- [x] **[!] A2 密钥治理**  <!-- 已完成：密钥已轮换；settings.example.json 为脱敏示例；git 历史已重写清除泄露 key（163 提交全部改哈希，备份 bundle 另存）-->
  - 为什么：`settings.json` 明文密钥；用户会照抄这个模式
  - 怎么做：① 读取优先级改为 `环境变量 > settings.json`（`OPENAI_API_KEY` 等）
    ② `settings.example.json` 里改成占位符并写注释 ③ **启动时检测到明文密钥就打警告**
  - 验收：单测覆盖"环境变量优先"；启动日志含警告；`grep -r "sk-" .` 无命中
  - 工时：1–2 h

- [x] **[!] A3 发布前密钥扫描闸门**  <!-- 已完成：scripts/check_no_secrets.sh 扫描跟踪文件 + 禁止文件类型 + 解压包内检查；已接入 scripts/make_release.sh，并有 git-less find 兜底 -->
  - 为什么：历史干净是"现在的结论"，将来一次手滑就毁了
  - 怎么做：加一个 `scripts/secret_scan.sh`（`gitleaks detect` 或自写正则）＋ CI 步骤
  - 验收：故意放入假密钥能被拦下
  - 工时：1 h

- [x] **[!] A4 数据与版权声明**  <!-- 已生成 DATA_AND_COPYRIGHT.md：不含论文/模型/密钥，用户数据归用户 -->
  - 为什么：`papers/` 是受版权保护的论文全文（虽未追踪，但文档里别放全文）
  - 怎么做：README 注明"不附带论文；示例请用公开 arXiv 链接"；文档里只放链接与 ID
  - 验收：仓库内无 PDF/论文全文；README 有该声明
  - 工时：0.5 h

- [x] A5 第三方依赖许可清单  <!-- 已生成 THIRD_PARTY_LICENSES.md：15 条直接依赖，许可证读自安装元数据 -->
  - 怎么做：`pip-licenses --format=markdown` 生成；核对底座与 LoRA 训练栈的许可
    （Qwen3 / bge-m3 / LLaMA-Factory / vLLM / Chroma 等）
  - 验收：`THIRD_PARTY_LICENSES.md` 存在
  - 工时：1 h

---

## §2 P1 可安装 / 可运行（**阻断发布**：决定"别人能不能跑起来"）

- [x] **[!] B1 配置化（最大的一步）**  <!-- MODELS_DIR / BASE_MODEL_PATH 已可配，默认值零变化（本轮 grep 核实） -->
  - 为什么：`/home/luxy/...`、端口、模型路径硬编码 → 换台机器就跑不动
  - 怎么做：① 新增 `config.py`（或 `settings.json` 扩字段）集中读取
    ② 全部硬编码路径改为读配置，**并给默认值 + 启动自检**（缺什么明确报什么）
    ③ 文档写明每个字段去哪拿
  - 验收：`grep -rn "/home/luxy" --include=*.py .` 在非示例/文档处为 0；
    改一个配置目录后服务仍能起
  - 工时：4–6 h

- [x] **[!] B2 `requirements.txt`（只列直接依赖 + 钉版本）**  <!-- requirements.txt 存在（本轮核实） -->
  - 为什么：缺文件无法安装；`pip freeze` 全量会把间接依赖写死
  - 怎么做：从 `pyproject`/import 反推直接依赖，钉到已验证版本
    （fastapi/uvicorn/vllm/transformers/peft/llamafactory/pdfplumber/pypdf/
    pypdfium2/rapidocr-onnxruntime/python-docx/openpyxl/sentence-transformers/chromadb…）
  - 验收：干净虚拟环境 `pip install -r requirements.txt` 成功；后端 690 测试通过
  - 工时：1 h

- [x] **[!] B3 一键脚本**  <!-- scripts/setup.sh + scripts/run.sh 存在（本轮核实） -->
  - 怎么做：`scripts/setup.sh`（建 venv、装依赖、检查 GPU/模型路径、初始化 settings）
    ＋ `scripts/run.sh`（起后端/前端）
  - 验收：在干净目录按 README 三步操作能起服务
  - 工时：2–3 h

- [x] **[!] B6 新鲜克隆冒烟（发布验收标准）**  <!-- 在 git archive 全新副本、不预建目录，第一次运行即 699 全绿 -->
  - 怎么做：`git clone` 到临时目录 → setup → **跑 690 后端 + 219 前端测试** → 起服务 →
    跑一次最小工作流
  - 验收：**全绿**；任何一步失败都要在发布前修掉（这一步会暴露所有"只在我机器上能跑"的问题）
  - 工时：1 h（＋修问题的时间）

- [x] B4 降级路径（决定"可试用性"）  <!-- 新增 GET /readiness + 前端引导条；已用空 MODELS_DIR 实测降级为 cloud-only/minimal 并给出可执行修复建议 -->
  - 为什么：没有 GPU / 没有云端 Key 的人占了绝大多数
  - 怎么做：提供 `--no-gpu` / `--kb-only` 模式（KB 检索 + 规则抽取 + 模板报告）
  - 验收：无 GPU 环境能跑出报告（哪怕质量低）；README 明确两种模式的差别
  - 工时：3–4 h

- [ ] B5 Docker / compose（可选）
  - 注意：vLLM 镜像很大，务必标注显存需求；建议 compose 只含后端 + 前端，vLLM 可选
  - 工时：3–4 h

---

## §3 P2 可信度（**这个项目的卖点所在**，优先级仅次于阻断项）

- [x] **D3 `make repro`：一键复现"云端 token −76%"的对照实验**  <!-- 已实现：make repro 读 outputs/ 真实运行记录；52 次运行，中位降幅 73.5%（30896→8198），并输出限界说明 -->
  - 为什么：**这是本项目最不可替代的东西** —— 别人能自己验证结论
  - 怎么做：脚本跑同一篇论文两次（云边协同 vs 全云），输出 token/耗时/质量三项对照表
  - 验收：一条命令产出对照表；README 里贴出真实数字
  - 工时：1 天

- [x] **D1 评测口径写清楚（最大短板）**  <!-- 已完成：docs/EVAL_PROTOCOL.md（六层被测对象、判分、±2-4pt 噪声与两次比对、留出集纪律、限界、证据标准、七条不做的事）-->
  - 现状：参考集由启发式构造，**已知有边界偏差**；历史上出现过多次"指标测错对象"
  - 怎么做：① 文档写清参考集怎么来的、有什么偏差 ② 加 **schema 断言**（字段必须 `list[str]`）
    ③ 加**人工金标准小样本**（20–50 条，人工标注）
  - 验收：`EVAL_METHOD.md` + 金标准集 + 断言测试
  - 工时：1–2 天

- [x] **D2 度量守卫化（把教训变成测试）**  <!-- 部分完成：tests/_guards.py 守卫共享状态（outputs/cache/uploads/库文件哈希）；测试台用三类结果区分；教训已写进 agent_kb/30_pitfalls.md -->
  - 为什么：本项目已积累 **13 条度量教训**（缓存污染、单位盲区、句子边界、覆盖率指标测错对象…）
  - 怎么做：把每条教训写成一条断言/测试（例："逐字自检必须配形状自检"）
  - 验收：`tests/test_metric_guards.py` 覆盖 ≥8 条教训
  - 工时：1 天

- [x] **D4 基准数据与对照表**  <!-- 已完成：docs/BASELINE.md（门禁计数、规模、运行时、成本对照、性能、DSH 形态数据；数字由脚本现测写入）-->
  - 怎么做：把散在各报告里的数字整理成一张表（含**三次失败实验**）
  - 验收：`BENCHMARKS.md`
  - 工时：0.5 天

---

## §4 P3 文档与定位

- [x] **E1 README 重写（面向使用者）**  <!-- 已完成：README.md 面向使用者重写（一键启动、界面导览、常见用法、文档编辑流水线、测试与自检、FAQ）-->
  - 结构：**这是什么 / 三步跑起来 / 一张架构图 / 实测效果 / 局限与不适用 / 失败案例 / 许可**
  - 注意：现有 README 27KB 偏内部记录，需要另写一份"门面"版
  - 工时：半天

- [x] E2 架构图（云边分工 + LoRA 挂在哪 + 数据流）
  - 工时：2 h

- [x] **E3 失败案例集（最稀缺的内容）**  <!-- 已完成：docs/FAILURE_CASES.md（24 个真实失败，七组归类，含复现/根因/修法/防复发）-->
  - 已有素材：`claims_v1` 不可用 / `analyze_data_v1` 比底座差 / `dimensions_v2` 无改善 /
    覆盖式监督更差（precision 0.818→0.254）/ 生产里挂着测试产物 LoRA 的发现 /
    **13 条度量陷阱**
  - 怎么做：整理成 `FAILURES.md`，每条含"假设 / 做法 / 实测 / 机理 / 教训"
  - 验收：≥6 个案例，每个都有数字
  - 工时：0.5 天

- [x] E4 定位声明
  - 明确写："**这是技术案例与方法论，不是开箱即用的框架**"（避免错误期待）
  - 工时：1 h

- [ ] E5 中英双语 / 截图 / 演示 GIF（可选）

---

## §5 P4 社区与发布

- [x] F1 **CI（GitHub Actions）**：跑后端 690 + 前端 219 测试
  - 为什么：把"测试纪律"变成**可见的信任**（本项目最大亮点之一）
  - 工时：2–3 h
- [ ] F2 `CONTRIBUTING.md` + issue/PR 模板
- [x] F3 `CHANGELOG.md` + 打 tag `v0.2.0`
- [ ] F4 公开路线图（直接用 `HANDOVER.md` §6 的待办）
- [ ] F5 `CITATION.cff`（若希望被引用）

---

## §6 发布验收清单（一条条打勾才能发）

- [x] `LICENSE` 存在且 SPDX 正确  <!-- MIT，Copyright (c) 2026 luxy -->
- [x] `requirements.txt` 在干净环境安装成功  <!-- 已完成：干净 venv(/tmp/cleanenv)+干净副本实测 Ran 699 OK；过程中抓到并修复 requirements.txt 缺 python-multipart 的发布阻断 bug -->
- [x] **新鲜克隆 → 690 后端 + 219 前端测试全绿**  <!-- 已完成：git archive HEAD -> 96 个文件；后端 Ran 699 tests OK、前端 370/370（退出码 0），实测全绿 -->
- [x] 三个服务能起：后端 / vLLM / 前端  <!-- 已完成：scripts/start.sh 一键起 vLLM + 后端 + 前端（幂等、秒级返回），实测 200/200/200 -->
- [x] 跑通一次最小工作流（有报告产出）  <!-- 已完成：多次真跑，例如 10 步文档链路 19.5s、分析工作流产出报告；见 docs/BASELINE.md -->
- [x] `make repro` 能复现 −76% 的对照  <!-- 已完成：make repro 读 outputs/ 真实记录，中位降幅 73.5%（云端 token），并打印限界说明 -->
- [x] `grep -rE "sk-[A-Za-z0-9]{16,}" .` 无命中  <!-- 已完成：scripts/check_no_secrets.sh 作为发布闸门，跟踪文件/禁止类型/解压包三处扫描，无命中 -->
- [x] 无 `/home/luxy` 硬编码（示例与文档除外）  <!-- 已完成：代码里已无功能性硬编码（Makefile 改多候选探测、e2e_tools.js 改相对路径、前端提示改通用）；剩余命中均为注释/示例/mock 数据 -->
- [ ] README 有：三步跑起来 / 架构图 / 实测效果 / 局限 / 失败案例
- [x] `FAILURES.md` ≥6 案例  <!-- 已完成：见 docs/FAILURE_CASES.md（24 例，远超 ≥6 的要求；文件名不同故本条注明）-->
- [ ] CI 绿
- [ ] 打 tag `v0.1.0`

---

## §7 明确**不做**（防止范围蔓延）

- [x] ~~前端 6.3k 行单文件拆分~~ —— **风险最高**（无浏览器自动化，改布局难验证），等社区真来了再做
- [x] ~~后端 3.5k 行拆分~~ —— 同上，先把功能与文档做扎实
- [x] ~~Docker 里的 vLLM~~ —— 镜像过大，标注需求即可
- [x] ~~随仓库发布模型权重与 LoRA~~ —— 版权与体积都不合适，改为"**如何自己训**"文档
- [x] ~~追求"框架级"抽象~~ —— 本项目定位是案例/方法论

---

## §8 建议的执行顺序（每步都能独立验收）

```
第 1 步（~1 天）：A1 LICENSE（你定）→ A2 密钥治理 → A3 扫描闸门 → B2 requirements
第 2 步（~1 天）：B1 配置化 → B3 setup/run 脚本 → B6 新鲜克隆冒烟
第 3 步（~1 天）：D3 make repro → E3 FAILURES.md → E1 README 重写
第 4 步（~1 天）：D1 评测口径 + 金标准小样本 → D2 度量守卫 → F1 CI
第 5 步：B4 降级路径 → D4 基准表 → E2 架构图 → F2–F5
```

**最小可用开源版（MVP OSS）** = 第 1 + 第 2 步 + D3 + E3 + E1（约 **2–3 天**）
→ 产出：**一个别人 clone 下来能跑通、并且能自己验证结论的仓库**。
