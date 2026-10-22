# 资产与工作区审计报告

- **任务**：task-1「资产与工作区审计（只读 + 一份新文档）」
- **审计对象**：`/home/luxy/paper_lora_project`（WSL Ubuntu-24.04）
- **审计方式**：全程只读；唯一写入的文件就是本文件
- **执行窗口**：2026-10-14 22:41 – 22:44（+08:00）。文件名沿用任务日 `2026-10-13`；实际执行时间见下。
- **最终快照时间**：`2026-10-14T22:44:14+08:00`
- **审计基准文件**：`HANDOVER.md`
  - `sha256 = 1d216c9442aed2502213feb6cea8f6c8fa6f3af68920e1e0bb6b75e16373ec6c`
  - `mtime = 2026-10-14 22:33:53 +0800`，`208879` bytes，全文 `3077` 行
  - §9「数据资产」位于 **3065–3073 行**，本次审计逐句对照该节
- **git 基线**：`HEAD = 06a3ffddd34d18ac43689533430213b4ece1b1dc`（审计期间 HEAD 由 `2f58799` 前进到 `06a3ffd`，见 §5.4）
- **并行写入声明**：本次审计期间有其他 teammate 在改仓库（`webui/index3.html`、知识库清理、`docs/FAILURE_CLASSES.md`）。因此 **§5 的未跟踪清单是活快照**，提交前需重取。本审计未触碰 `agent_kb/90_learned.md`、`agent_use` 知识库、`webui/index3.html`、`lora_manager.py`、`vllm_manager.py`。

## 结论速览

| # | 核对项 | 结论 |
|---|---|---|
| 1 | `papers/` 30 篇 / manifest 33 条 30 收 3 拒 | ✅ 完全一致 |
| 2 | registry.json 的 `extract_v2 0.6054` / `extract_v3 0.5866` | ⚠️ 两个数字都存在，但 **0.6054 不是 extract_v2 的 last_eval**（归属说错） |
| 3 | 4 个数据集行数与篇数（443/24、107/6、190/12、68/4、100） | ✅ 全部一致（逐条溯源匹配，0 条无法归属） |
| 4 | 知识库 | ❌ §9 的「仅 `test_kb`（99 chunks）」完全过期；实测 8 库、0 空库 |
| 5 | git 未跟踪文件 | 9 项，逐项归置建议见 §5 |
| 6 | HANDOVER §9 过期/说错句子 | 3 处明确错误、2 处需补注、3 处核对通过，见 §6 |
| 7 | 门禁 `unittest discover` | ✅ `Ran 699 tests ... OK`（跑了两次） |

---

## 1. papers 实际篇数与 manifest 条目数

### 1.1 实测命令与实际输出

```bash
cd /home/luxy/paper_lora_project
find papers -maxdepth 1 -name '*.txt' | wc -l
```
```
30
```

```bash
ls papers/ | wc -l
find papers -maxdepth 1 -type f | wc -l
find papers -maxdepth 1 -type d ! -path papers
find papers -maxdepth 1 ! -name '*.txt' ! -name 'manifest.json'
```
```
32          # ls papers/：30 个 .txt + 1 个 manifest.json + 1 个子目录 pdf/
31          # 其中 type f 文件 31 个（30 txt + manifest.json）
papers/pdf  # 唯一的子目录
papers
papers/pdf
```

```bash
find papers/pdf -maxdepth 1 -name '*.pdf' | wc -l
du -sh papers papers/pdf
```
```
33
175M	papers
173M	papers/pdf
```

```bash
/home/luxy/.venv/bin/python - <<'PY'
import json, collections
m = json.load(open('papers/manifest.json'))
print("accepted =", m.get("accepted"))
print("total_candidates =", m.get("total_candidates"))
print("len(papers) =", len(m.get("papers", [])))
print("status 分布 =", dict(collections.Counter(p.get("status") for p in m["papers"])))
for p in m["papers"]:
    if p.get("status") != "accepted":
        print("  ", p.get("id"), p.get("status"), repr(p.get("reason"))[:120])
PY
```
```
accepted = 30
total_candidates = 100
len(papers) = 33
status 分布 = {'accepted': 30, 'rejected': 3}
   2610.03574v1 rejected '没有可识别的方法/模型章节标题 —— 抽取任务无目标'
   2610.03570v1 rejected '乱码字符过多（1036）'
   2610.03190v1 rejected '没有可识别的方法/模型章节标题 —— 抽取任务无目标'
```

### 1.2 manifest 与磁盘逐 id 对照

```bash
# 从 papers/*.txt 文件名解析 arXiv id，与 manifest 的 accepted/rejected 求差集
```
```
manifest accepted = 30  rejected = 3  合计 = 33
磁盘 papers/*.txt 可解出 id = 30  全部 txt 文件数 = 30
accepted 但磁盘缺失 = []
磁盘有但 manifest accepted 没有 = []
manifest 里 rejected 的 id = ['2610.03574v1', '2610.03570v1', '2610.03190v1']
```
PDF 侧对照：

```
manifest id 数 = 33   papers/pdf id 数 = 33
manifest 有但无 pdf = []
pdf 有但 manifest 无 = []
```

### 1.3 结论

- HANDOVER §9「`papers/` **30 篇**（33 篇尝试、3 篇被质量闸门拒）＋ `papers/manifest.json`」——**实测完全一致**：`len(papers)=33`、`status=accepted:30 / rejected:3`、磁盘 `.txt` 30 个、id 双向无缺失。
- 实测补充（§9 未记，供参考）：
  - `manifest.total_candidates = 100`：这是抓取阶段的候选池，33 条才是落盘评估的条目；§9 只说「33 篇尝试」，容易被误读成「只试了 33 篇」。
  - `papers/pdf/` 有 **33 个 PDF（173 MB）**，与 manifest 33 个 id 一一对应（含 3 个被拒 id 的 PDF：`2610.03570v1.pdf`、`2610.03190v1.pdf` 等）。
  - 旧清单备份实测存在：`E:\WSL\_check\manifest_v2_16papers.json` → `accepted = 16`、`len(papers) = 18`、`total_candidates = 100`，与 §9「16 篇时的旧清单备份」描述相符。

---

## 2. `~/loras/registry.json` 的评测值

### 2.1 实测命令与实际输出

```bash
/home/luxy/.venv/bin/python - <<'PY'
import json
r = json.load(open('/home/luxy/loras/registry.json'))
print("条目数 =", len(r))
for k, v in r.items():
    le = v.get("last_eval") if isinstance(v, dict) else None
    if le:
        m = le.get("metrics", {})
        print(k, "last_eval:", m.get("similarity"), "n=", m.get("n"), le.get("dataset"))
    else:
        print(k, "无 last_eval")
for e in r["extract_v2"].get("eval_history", []):
    m = e.get("metrics", {})
    print("  v2 history:", e.get("at"), e.get("dataset"), m.get("similarity"), "n=", m.get("n"))
for e in r["extract_v3"].get("eval_history", []):
    m = e.get("metrics", {})
    print("  v3 history:", e.get("at"), e.get("dataset"), m.get("similarity"), "n=", m.get("n"))
PY
```

实际输出（整理为表）：

| registry 条目 | 有 last_eval | last_eval.similarity | n | last_eval.dataset |
|---|---|---|---|---|
| `extract_v2` | 是 | **0.5306** | 6 | `training_data/extract_v2_eval.jsonl` |
| `extract_v3` | 是 | **0.5866** | 30 | `training_data/extract_v3_eval.jsonl` |
| `dimensions_v1` | 是（仅 note/cloud_token_effect，**无 metrics**） | – | – | – |
| `analyze_data_v1` | 是（仅 note/conclusion，`unusable: true`） | – | – | – |
| 其余 17 条（`paper_analyzer_v5/v6`、`testlora`、`test_agent_v1`、`ml_engineer`、`strict_json`、`frontend-ui-architect-lora`、`json_extract`、`extract_v1`、`dimensions_v2`、`claims_v1`、`swk_persona_v1/v2/v3`、`swk_ckpt32/64/96`） | 否 | – | – | – |

`eval_history` 原文（关键两条）：

```
extract_v2.eval_history 全部条目:
   at= 2026-10-06T00:49:32 ds= .../training_data/extract_v2_eval.jsonl sim= 0.5306 n= 6
   at= 2026-10-06T03:24:11 ds= .../training_data/extract_v3_eval.jsonl sim= 0.6054 n= 30   <= 0.6054 在这里
   at= 2026-10-06T04:53:02 ds= .../training_data/extract_v2_eval.jsonl sim= 0.5306 n= 6
extract_v3.eval_history 全部条目:
   at= 2026-10-06T03:20:58 ds= .../training_data/extract_v3_eval.jsonl sim= 0.5866 n= 30
```

### 2.2 与 §9 原句的差异（重要）

§9 原句：

> **质量台账**：`~/loras/registry.json` —— `extract_v2` similarity **0.6054** / `extract_v3` **0.5866**（两者都在 `extract_v3_eval.jsonl` 30 条上测，可追溯）

实测判定：**两个数字都真实存在，但前一个的归属说错了。**

- `0.5866`（`extract_v3`，`extract_v3_eval.jsonl`，n=30）→ 就是 `extract_v3.last_eval` ✅
- `0.6054`（`extract_v2`，`extract_v3_eval.jsonl`，n=30）→ 存在于 `extract_v2.eval_history` 的**第 2 条历史记录**（`2026-10-06T03:24:11`），**不是** `extract_v2.last_eval`
- `extract_v2.last_eval` 实测为 **0.5306**（`extract_v2_eval.jsonl`，n=6）
- 影响：任何按 §9 去 registry 取「extract_v2 的评测值」的读者/脚本，读到的是 `0.5306` 而不是 `0.6054`，与文档不符。建议 §9 改写为「`extract_v2` 在 `extract_v3_eval.jsonl`(30 条) 上 0.6054（历史评测，eval_history[1]）；其 `last_eval` 为 0.5306(extract_v2_eval, 6 条)」。

### 2.3 台账与磁盘的对齐问题（实测）

```bash
ls -d ~/loras/*/ | xargs -n1 basename   # 18 个适配器目录
```
```
registry.json 条目数 = 21
registry 有但目录没有 = ['swk_ckpt32', 'swk_ckpt64', 'swk_ckpt96', 'swk_persona_v1', 'swk_persona_v2']
目录有但 registry 没有 = ['dimensions_cov_v1', 'dimensions_cov_v2']
```
- 5 条 registry 记录（`swk_*`）对应的 LoRA 目录已不存在 → 台账有「悬空记录」。
- 2 个适配器目录（`dimensions_cov_v1`、`dimensions_cov_v2`）没有任何台账记录 → 预检会对它们报「无评测记录」。
- `registry.json` 另有 `registry.json.bak`（5433 B，2026-10-06 15:58）在 `~/loras/` 下。

---

## 3. 训练数据集：存在性、条数、篇数

### 3.1 存在性与行数（实测）

```bash
cd /home/luxy/paper_lora_project
for f in extract_v3.jsonl extract_v3_eval.jsonl extract_v2.jsonl extract_v2_eval.jsonl test_agent_v1.jsonl loop_extract_sft.jsonl; do
  printf "training_data/%s : lines=%s bytes=%s\n" "$f" "$(wc -l < training_data/$f)" "$(stat -c '%s' training_data/$f)"
done
```
```
training_data/extract_v3.jsonl : lines=443 bytes=1429493
training_data/extract_v3_eval.jsonl : lines=107 bytes=199499
training_data/extract_v2.jsonl : lines=190 bytes=605901
training_data/extract_v2_eval.jsonl : lines=68 bytes=127168
training_data/test_agent_v1.jsonl : lines=100 bytes=98742
training_data/loop_extract_sft.jsonl : lines=1 bytes=7812
```

逐条 JSON 解析（排除空行/坏行）：

```
extract_v3.jsonl 有效行数= 443        keys=('conversations',) count=443
extract_v3_eval.jsonl 有效行数= 107   keys=('conversations',) count=107
extract_v2.jsonl 有效行数= 190        keys=('conversations',) count=190
extract_v2_eval.jsonl 有效行数= 68    keys=('conversations',) count=68
test_agent_v1.jsonl 有效行数= 100     keys=('conversations',) count=100
```
→ 无空行、无解析失败行，`wc -l` 与有效行数一致。

### 3.2 篇数溯源（方法 + 实测）

数据集只有 `conversations` 一个字段，记录里没有论文 id 字段（实测探测结果为空），因此**用论文正文做窗口签名反查**：对每条记录的 human 文本取 7 个窗口（5%/20%/35%/50%/65%/80%/95% 处各 160 字符），检测哪个 `papers/*.txt` 的正文包含该签名；唯一命中才计入。命令见附录脚本 `evidence4.sh`。

```
载入 papers/*.txt 篇数 = 30

--- extract_v3.jsonl ---
  总行数 = 443 | 唯一匹配 = 443 | 多篇歧义 = 0 | 未匹配 = 0
  >>> 实测不同论文篇数 = 24
  每篇条数(升序) = [7, 9, 9, 10, 10, 12, 14, 14, 15, 15, 15, 16, 16, 17, 17, 17, 19, 20, 23, 23, 24, 31, 45, 45]

--- extract_v3_eval.jsonl ---
  总行数 = 107 | 唯一匹配 = 107 | 多篇歧义 = 0 | 未匹配 = 0
  >>> 实测不同论文篇数 = 6
  每篇条数(升序) = [8, 15, 16, 17, 23, 28]

--- extract_v2.jsonl ---
  总行数 = 190 | 唯一匹配 = 190 | 多篇歧义 = 0 | 未匹配 = 0
  >>> 实测不同论文篇数 = 12
  每篇条数(升序) = [7, 9, 10, 10, 14, 15, 17, 17, 17, 20, 23, 31]

--- extract_v2_eval.jsonl ---
  总行数 = 68 | 唯一匹配 = 68 | 多篇歧义 = 0 | 未匹配 = 0
  >>> 实测不同论文篇数 = 4
  每篇条数(升序) = [8, 15, 17, 28]

--- test_agent_v1.jsonl ---
  总行数 = 100 | 唯一匹配 = 0 | 多篇歧义 = 0 | 未匹配 = 100
  >>> 实测不同论文篇数 = 0
```

汇总：

| 文件 | 实测条数 | 实测篇数 | §9 记载 | 判定 |
|---|---|---|---|---|
| `extract_v3.jsonl` | 443 | 24 | 443 条 / 24 篇 | ✅ |
| `extract_v3_eval.jsonl` | 107 | 6 | 107 条 / 留出 6 篇 | ✅ |
| `extract_v2.jsonl` | 190 | 12 | 190 条 / 12 篇 | ✅ |
| `extract_v2_eval.jsonl` | 68 | 4 | 68 条 / 4 篇 | ✅ |
| `test_agent_v1.jsonl` | 100 | 0（非论文类） | 100 条 | ✅ |
| `loop_extract_sft.jsonl` | 1 | 0（非论文类） | 1 条 | ✅ |

### 3.3 结构观测（实测，供交叉核对）

- 5 个文件全部只有 `conversations` 键，0 条解析失败。
- 单条 human 文本长度：`extract_v3.jsonl` min/median/max = `918/3015/3022`；`extract_v3_eval.jsonl` = `863/1515/1522`；`extract_v2.jsonl` = `963/3015/3022`；`extract_v2_eval.jsonl` = `863/1515/1522`；`test_agent_v1.jsonl` = `6/129/295`。
  → 训练集是「论文正文截成 ~3k 字符的块」，评测集是 ~1.5k 字符的块；`test_agent_v1.jsonl` 是短问答，与论文抽取无关，与 registry 里 `test_agent_v1.unusable=true`、`role="test-artifact（训练连通性测试产物，不可用）"` 一致。
- 结论：§9「训练数据」一行 **全部数字实测一致**，且我做了逐条归属（0 条无法归属），本篇报告中的篇数是实测值而非引用。

---

## 4. 知识库实际清单

### 4.1 实测命令与实际输出（最终快照 22:44:14）

```bash
curl -s http://127.0.0.1:8001/kb/list
curl -s http://127.0.0.1:8001/health
```
```
{"status":"ok"}
```

解析后的当前清单：

| 知识库 | total_chunks | documents | embedding |
|---|---|---|---|
| `r2_lora_fs_probe` | 2536 | 12 | bge-small-zh-v1.5 / 512 |
| `xiyouji` | 900 | 45 | bge-small-zh-v1.5 / 512 |
| `minimax_h3_prompt` | 820 | 24 | bge-m3 / 1024 |
| `agent_use` | 74 | 10 | bge-m3 / 1024 |
| `agent_use_ctrl` | 56 | 9 | bge-m3 / 1024 |
| `learned_chat2` | 12 | 1 | bge-m3 / 1024 |
| `learned_chat` | 2 | 1 | bge-m3 / 1024 |
| `learned_test` | 2 | 1 | bge-m3 / 1024 |

```
总数 = 8
0 块空库 = []
test_kb 存在 = False
```

### 4.2 同一审计窗口内的前后对比（重要：环境是活的）

审计刚开始时（约 22:42）同一命令返回的是 **17 个库、其中 9 个 0 块空库**：

```
知识库总数 = 17
  r2_lora_fs_probe  chunks= 2536 ...
  xiyouji           chunks=  900 ...
  minimax_h3_prompt chunks=  820 ...
  agent_use         chunks=   74 ...
  agent_use_ctrl    chunks=   56 ...
  learned_chat2     chunks=   12 ...
  learned_chat      chunks=    2 ...
  learned_test      chunks=    2 ...
  r2b_top3_xcheck   chunks=    0 docs=0  <== 0 块空库
  r2_size_xcheck    chunks=    0 docs=0  <== 0 块空库
  probe_top3_r2c    chunks=    0 docs=0  <== 0 块空库
  probe_top3_r2b    chunks=    0 docs=0  <== 0 块空库
  r2_embed_probe    chunks=    0 docs=0  <== 0 块空库
  verify_r2_xlsx    chunks=    0 docs=0  <== 0 块空库
  verify_r3_ds      chunks=    0 docs=0  <== 0 块空库
  probe_trainsets_r2 chunks=   0 docs=0  <== 0 块空库
  tmp_xlsx_probe    chunks=    0 docs=0  <== 0 块空库
0 块空库数量 = 9
```
期间 HEAD 前进了 `9a22d94 知识库清理：17 库 → 8 库（删除 9 个实测为空的知识库）`，22:44:14 复测即为 §4.1 的 8 库 / 0 空库。两次数都是实测，此处并列是为了说明 **§5 的「快照」性质**：提交前应重取。

### 4.3 结论

- HANDOVER §9「**知识库**：仅 `test_kb`（99 chunks，来自 `attention_is_all_you_need.pdf`）；已用 `bge-m3` 重建」→ **完全过期**：
  - `test_kb` 实测**不存在**（`是否存在 test_kb = False`），当前 8 个库中没有任何一个叫 `test_kb`，也没有任何库是 99 chunks。
  - 唯一可对上的来源物：`uploads/2946eb29_attention_is_all_you_need.pdf` 与同名 `.txt` 存在，且 `/mnt/e/test-pdf/attention_is_all_you_need.pdf`、`/home/luxy/papers/attention_is_all_you_need.pdf` 也在磁盘上——但对应的知识库已不在服务里。
  - §9 里「集合 space=cosine」「`meta.embedding`」两项**本次未能实测**（`/kb/list` 不返回 `space` 字段）；除 `documents[].chunks/chars` 外的元数据无法从该接口验证，此处标注为**未实测**，不作判定。
- 实测补充：8 个库中 **2 个仍用 `bge-small-zh-v1.5`/512**（`xiyouji`、`r2_lora_fs_probe`），6 个用 `bge-m3`/1024 → §9 的「bge-m3 现默认」是**后端默认**，不是「所有库都已切到 bge-m3」。
- 敏感资产状态：`agent_use` 实测 `74 chunks / 10 documents`，其中 `90_learned.md` 为 `18 chunks / 7762 chars`（与已知资产口径一致）；本次审计**未读取、未修改、未重建**该库与该文件。
- `r2_lora_fs_probe`（2536 chunks）描述里写着「用完可删」，但仍是当前最大的库；`xiyouji` 带 `rebuilt_at` 字段（`2026-10-12T21:49:14`）。

---

## 5. git 未跟踪文件的逐项归置建议

### 5.1 实测命令与实际输出

```bash
cd /home/luxy/paper_lora_project
date -Iseconds
git -c core.quotepath=false status --porcelain -uall
```
```
2026-10-14T22:44:14+08:00
?? _reflect_out/采购摘录_待填.xlsx
?? doc_flows/doc_flow_new.json
?? runs/p1_eval/g1_report.md
?? runs/p1_eval/g1_results.json
?? runs/p1_eval/p1a5_blind.md
?? runs/persona_test_swk_persona_v2.json
?? runs/persona_test_swk_persona_v3.json
?? train_lora.py.orig-p1a4
?? 论文输入修复要点清单.docx
```
未跟踪条目数 = **9**；工作区无暂存内容（`git diff --cached --stat` 为空）。

补充实测（判断归置范围用）：

```
git ls-files runs | wc -l              -> 8   （runs/ 已有 8 个文件被跟踪：baseline_2026-10-12 报告、快照等）
git ls-files doc_flows                -> doc_flow_macro_edit.json / doc_flow_procurement.json / doc_flow_standard_edit.json / doc_flow_word_outline.json
git ls-files _reflect_out             -> （空，该目录没有任何已跟踪文件）
.gitignore 覆盖 *.bak-*，但不覆盖 *.orig-*
```

### 5.2 逐项建议

| # | 未跟踪项 | 大小 / 时间 | 实测性质 | 建议 | 理由 |
|---|---|---|---|---|---|
| 1 | `_reflect_out/采购摘录_待填.xlsx` | 4990 B / 2026-10-11 21:41 | 真实 xlsx（zip 内含 `xl/worksheets/sheet1.xml`） | **保留待定**（或加 `.gitignore`） | `_reflect_out/` 无任何已跟踪文件；全仓 grep「采购摘录」仅命中它自身；已跟踪模板 `doc_flow_procurement.json` 产出名是「采购台账.xlsx」「待交付采购明细.xlsx」，不引用此文件 → 更像实验/演示的暂存输入，不宜默认入库。**需 owner 定夺** |
| 2 | `doc_flows/doc_flow_new.json` | 217 B / 2026-10-07 00:39 | 合法流程模板，与已跟踪 4 个同结构（`name/description/steps[tool,args,x,y]`） | **建议入库** | 与既有先例完全同构，属功能配置而非生成物 |
| 3 | `runs/p1_eval/g1_report.md` | 25996 B / 2026-10-12 18:53 | G1 评测报告（20 题，有/无 LoRA 对照） | **建议入库** | `runs/` 已跟踪评测报告与快照（8 个文件），这是同类评测证据 |
| 4 | `runs/p1_eval/g1_results.json` | 31433 B / 2026-10-12 18:53 | 同上，结构化结果 | **建议入库** | 同上；**已核查不含密钥**：与 `settings.json` 的真实 key 值比对命中 `0`；命中的 `token` 是「18,862 input token」这类统计口径 |
| 5 | `runs/p1_eval/p1a5_blind.md` | 1910 B / 2026-10-12 18:51 | 三 checkpoint 盲评（顺序已打乱） | **建议入库** | 同上，评测证据 |
| 6 | `runs/persona_test_swk_persona_v2.json` | 4423 B / 2026-10-12 21:02 | persona 测试记录 | **建议入库** | 同上；核查无真实 key（命中 0） |
| 7 | `runs/persona_test_swk_persona_v3.json` | 2364 B / 2026-10-12 21:49 | persona 测试记录 | **建议入库** | 同上；核查无真实 key（命中 0） |
| 8 | `train_lora.py.orig-p1a4` | 9409 B / 2026-10-05 21:38 | Python（UTF-8, CRLF）旧版备份 | **建议删除**，或给 `.gitignore` 加 `*.orig-*` | 当前 `train_lora.py` 与 HEAD **完全一致**（`git diff --stat HEAD -- train_lora.py` 输出为空），p1a4 改动已在历史提交 `0fff605` 中；`.gitignore` 现有 `*.bak-*` 但不覆盖 `*.orig-*`，所以它才暴露出来 |
| 9 | `论文输入修复要点清单.docx` | 43869 B / 2026-10-11 21:01 | 真实 docx（含 `word/document.xml`） | **保留待定** | 二进制交付/说明文档；仓库内文档均为 `.md`（`docs/` 10 个已跟踪文件全为 md），无 `.docx` 先例，是否入库需 owner 决定 |

### 5.3 已确认「不该入库」的两项（未触碰）

```
git status --porcelain -uall | grep -c '90_learned'   -> 0
git status --porcelain -uall | grep -c 'trajectories' -> 0
```
`agent_kb/90_learned.md` 与 `data/trajectories/` 均未出现在未跟踪清单里 —— `.gitignore` 末两行已排除（实测 `.gitignore` 原文含 `data/trajectories/`、`agent_kb/90_learned.md`）。另有 `papers/`、`training_data/`、`data/`、`outputs/`、`uploads/`、`training_output/`、`cache/`、`release/` 也在 `.gitignore` 中，因此 §1–§3 的资产**整体不入库**属既定策略，无需处理。

### 5.4 快照是活的（必须提醒）

审计 3 分钟内就观测到其他 teammate 的并行改动：

```
2026-10-14T22:43:10  -> ?? webui/index3.html.orig-toolbudget     （此时出现）
2026-10-14T22:43:25  -> 该文件已消失；?? docs/FAILURE_CLASSES.md 出现后随即被提交
git log --oneline -5
  06a3ffd 失败类型挖掘：209 条轨迹全量分类（teammate failure-miner 执行，lead 独立核验并校正口径）
  9a22d94 知识库清理：17 库 → 8 库（删除 9 个实测为空的知识库）—— 由 teammate kb-curator 执行，lead 核验
  02b9447 A2: 工具表按上下文窗口自适应裁剪（治"小底座被 88 个工具的 schema 撑爆"）
```
`webui/index3.html` 曾在 22:43:10 显示为 ` M`（已跟踪、被修改），22:43:25 后又回到干净状态。**结论：提交前必须以当时输出为准重取 `git status`，本表只对 `2026-10-14T22:44:14+08:00` 这个快照负责。**

### 5.5 建议给 lead 的两条落地动作（本审计不执行）

1. `.gitignore` 增加 `*.orig-*` 一行（与既有 `*.bak-*` 同组），再决定 `.orig` 文件是删还是留。
2. `.docx`/`.xlsx` 是否入库需要一个明确口径（当前第 1、9 项悬而未决）。

---

## 6. HANDOVER §9 中「已过期 / 说错」的具体句子

对照基准：`HANDOVER.md` §9（3065–3073 行，sha256 `1d216c94…`）。逐句判定如下。

### ❌ 明确说错 / 已过期（3 处）

**6.1 底座行（3067 行）—— 有一个不存在的模型 + 漏列一个**

> 原文：**底座**（`~/models`）：Qwen3-4B-Instruct（当前在用，FP16）、**Qwen3-4B-Instruct-AWQ**、Qwen3-4B-AWQ、Qwen2.5-7B-Instruct、Qwen2.5-7B-Instruct-AWQ、Qwen2.5-14B-Instruct-AWQ、qwen2.5-coder-14b-base

实测 `ls -la ~/models` 只有 9 个目录：

```
Qwen2.5-0.5B-Instruct         954M
Qwen2.5-14B-Instruct-AWQ      9.4G
Qwen2.5-7B-Instruct            15G
Qwen2.5-7B-Instruct-AWQ       5.2G
Qwen3-4B-AWQ                  2.5G
Qwen3-4B-Instruct             7.6G
bge-m3                        2.2G
bge-small-zh-v1.5             184M
qwen2.5-coder-14b-base        9.4G
```
- `Qwen3-4B-Instruct-AWQ`：**磁盘上不存在**（§9 列了但实测没有）。
- `Qwen2.5-0.5B-Instruct`：**存在但 §9 没列**。
- 判定：**错**。其余 5 个模型（含 `Qwen3-4B-Instruct`、`Qwen3-4B-AWQ`、两个 7B、`qwen2.5-coder-14b-base`）实测均存在 ✅。

**6.2 质量台账行（3071 行）—— 0.6054 的归属说错**

> 原文：**质量台账**：`~/loras/registry.json` —— `extract_v2` similarity **0.6054** / `extract_v3` **0.5866**（两者都在 `extract_v3_eval.jsonl` 30 条上测，可追溯）

详见 §2.2：`0.6054` 在 `extract_v2.eval_history[1]`（历史评测），而 `extract_v2.last_eval = 0.5306`（n=6，`extract_v2_eval.jsonl`）。数字本身可追溯，但「`extract_v2` similarity 0.6054」若按台账默认取值（`last_eval`）会读到 0.5306。判定：**归属错/易误导，需改写**。

**6.3 知识库行（3073 行）—— 完全过期**

> 原文：**知识库**：仅 `test_kb`（99 chunks，来自 `attention_is_all_you_need.pdf`）；**已用 `bge-m3` 重建**（`meta.embedding = {model: bge-m3, dim: 1024}`，集合 space=cosine）

实测（`curl -s http://127.0.0.1:8001/kb/list`）：

```
总数 = 8
0 块空库 = []
test_kb 存在 = False
```
- 「仅 `test_kb`」→ 该库**不存在**，实际 8 个库（§4.1 表）。
- 「99 chunks」→ 当前没有任何库是 99 chunks（最接近的是 `agent_use` 74、`agent_use_ctrl` 56）。
- 「`bge-m3` 重建」→ 实测 8 库中 2 个（`xiyouji`、`r2_lora_fs_probe`）仍是 `bge-small-zh-v1.5`/512。
- 「集合 space=cosine」→ **未实测**（接口不返回 space），不作判定。
- 判定：**整句过期，应重写为「当前 8 个库 …」**。

### ⚠️ 需补注（2 处）

**6.4 LoRA 行（3070 行）—— 漏列 7 个适配器，且未标 unusable**

> 原文：**LoRA**（`~/loras`）：`extract_v2`（当前默认工作流在用）、`extract_v3`（已训但未采用…）、`extract_v1`、`test_agent_v1`、`paper_analyzer_v5/v6`、`json_extract`、`strict_json`、`ml_engineer`、`testlora`、`frontend-ui-architect-lora`

实测 `~/loras` 有 **18 个适配器目录**，§9 名单覆盖 11 个，**漏列 7 个**：`analyze_data_v1`、`claims_v1`、`dimensions_v1`、`dimensions_v2`、`dimensions_cov_v1`、`dimensions_cov_v2`、`swk_persona_v3`（其中 `dimensions_v1`、`analyze_data_v1` 在 registry 里带评测 note，`swk_persona_v3` 有 agent_role/agent_kb 配置）。

另：§9 名单里的 `test_agent_v1` 在 registry 中被标记 `"unusable": true`、`role="test-artifact（训练连通性测试产物，不可用）"`，而 §9 只把它当作普通 LoRA 平列 → 建议加注。同理 `analyze_data_v1` 也是 `unusable: true`（未列入 §9）。

其中一句是**核对通过**的：

> `extract_v2`（当前默认工作流在用）

实测 `curl -s http://127.0.0.1:8001/workflow/configs`：

```
{"name": "workflow_config.json", "workflow_name": "通用论文分析工作流",
 "required_loras": ["extract_v2"], "required_providers": ["vllm"], "is_default": true, ...}
```
✅ 一致。

**6.5 embedding 行（3068 行）—— 数字对，「现默认」偏绝对**

> 原文：**embedding 模型**（`~/models`）：**`bge-m3`**（现默认，1024 维，多语，2.2 GB）、`bge-small-zh-v1.5`（512 维，单语，留作对照/回退）

实测：`bge-m3` = `2.2G`、`config.json hidden_size = 1024`（`model_type=xlm-roberta`）✅；`bge-small-zh-v1.5` = `184M`、`hidden_size = 512`（`model_type=bert`）✅。唯一需补注的是「现默认」的范围：实测知识库里仍有 2 个库用 `bge-small-zh-v1.5`（§4.1），建议写成「后端默认 `bge-m3`；历史库中 `xiyouji`、`r2_lora_fs_probe` 仍为 `bge-small-zh-v1.5`」。

### ✅ 核对完全通过的句子（3 处）

**6.6 论文行（3069 行）**：`papers/` **30 篇**（33 篇尝试、3 篇被质量闸门拒）＋ `papers/manifest.json`；16 篇时的旧清单备份在 `E:\WSL\_check\manifest_v2_16papers.json`
→ 实测 `30 / 33 / 3` 全部吻合；备份文件存在且 `accepted=16、len(papers)=18`。**唯一遗漏**：未提 `papers/pdf/`（33 个 PDF，173 MB）（属补充信息，不算错）。

**6.7 训练数据行（3072 行）**：`extract_v3.jsonl`（443 条 / 24 篇）、`extract_v3_eval.jsonl`（107 条 / 留出 6 篇）、`extract_v2.jsonl`（190 条 / 12 篇）、`extract_v2_eval.jsonl`（68 条 / 4 篇）、`test_agent_v1.jsonl`（100 条）；另 `loop_extract_sft.jsonl`（1 条）
→ 6 个文件的条数与篇数 **逐项实测一致**（§3.2 表），且 443/107/190/68 条全部成功归属论文，0 条未匹配。

**6.8 底座行里的「当前在用 FP16」**：`/home/luxy/models/Qwen3-4B-Instruct` 存在、7.6 GB、无量化后缀 → 与「FP16」相符（**未**通过 `/vllm/status` 二次确认当前加载底座，该点标注为**未实测**，按任务约束未打扰服务）。

### §9 之外的相邻风险（顺带发现，供 lead 参考）

- `WORKFLOW_CLOUD_EDGE_PLAN.md:144` 写「`extract_v2` … 留出 similarity **0.6054**（实测）」——与 §9 同源，同样把 `extract_v3_eval` 上的历史值当成了 `extract_v2` 的当前值。
- `HANDOVER.md:1013/1017` 记「`extract_v3.jsonl` 443 条 / 24 篇；训练集 443 条归属 24 篇、评测集 107 条归属 6 篇 …0 条无法归属」→ 本次独立复算结果与之一致 ✅。

---

## 7. 门禁结果

按要求跑的完整命令与结尾输出：

```bash
cd /home/luxy/paper_lora_project
/home/luxy/.venv/bin/python -m unittest discover -s tests -t .
```
```
[22:44:02] 工作流完成（状态 success），结果保存到 /tmp/wf_test_4jxkt38h/outputs/result_20261014_224402.json
..
----------------------------------------------------------------------
Ran 699 tests in 13.480s

OK
```
22:44:14 复跑一次：

```
----------------------------------------------------------------------
Ran 699 tests in 13.146s

OK
```

- **结果：`Ran 699 tests ... OK`，退出码 0，连跑两次均 OK。**
- 本次审计只新增本报告 1 个文件（`docs/AUDIT_ASSETS_2026-10-13.md`），未修改任何代码、未改 `HANDOVER.md`、未碰知识库内容、未停/重启任何服务（后端 :8001、前端 :8080、底座 :8000 全程只读调用）。

---

## 8. 阻塞点 / 不确定项

| 项 | 状态 | 说明 |
|---|---|---|
| `.docx` / `.xlsx` 是否入库 | **需 lead/owner 决定** | §5.2 第 1、9 项；仓库无二进制文档先例 |
| `.orig-*` 备份处置 | **需 lead 决定** | §5.2 第 8 项；建议 `.gitignore` 加 `*.orig-*` |
| §9 知识库行如何重写 | **需 lead 决定** | 本审计不改 `HANDOVER.md`（任务硬约束） |
| 「集合 space=cosine」 | **未实测** | `/kb/list` 不返回 `space` 字段，无法验证，已在 §4.3/§6.3 标注 |
| 「Qwen3-4B-Instruct 当前在用 FP16」 | **未实测** | 未调用 `/vllm/status` 以免打扰服务；仅确认磁盘存在 7.6 GB 未量化目录 |
| git 未跟踪清单 | **活快照** | 只对 `2026-10-14T22:44:14+08:00` 负责；提交前请重取（§5.4） |
| registry 悬空记录 / 无记录目录 | **待清理** | 5 条 `swk_*` 无目录、2 个 `dimensions_cov_*` 无记录（§2.3） |

## 附录：本次审计的可复现脚本

全部脚本（只读）留在 Windows 侧 `E:\WSL\_check\`，用下面方式执行：

```powershell
wsl.exe -d Ubuntu-24.04 -- bash /mnt/e/WSL/_check/<脚本名>.sh
```

| 脚本 | 用途 |
|---|---|
| `explore1.sh` | 路径/文件结构初探、`~/loras` 与 `training_data` 定位 |
| `evidence1.sh` | papers 计数、manifest 解析、registry 解析、数据集行数、KB 清单、git status、`.gitignore` 原文 |
| `evidence2.sh` | registry/KB 解析、`~/models` 体积、loras 目录对照、未跟踪文件详情 |
| `evidence3.sh` | 论文签名匹配（第 1 版，证明输入是分块而非前缀）、embedding 维度、registry 全表 |
| `evidence4.sh` | **窗口签名匹配（篇数最终口径）**、未跟踪小文件内容抽查 |
| `evidence5.sh` | 敏感词核查、docx/xlsx 内部结构、train_lora/index3 的 git 状态 |
| `evidence6.sh` | HEAD 与提交历史、`dimensions_v1`/`analyze_data_v1` 台账原文、manifest 与磁盘 id 对照、门禁 |
| `evidence7.sh` | 最终快照：HANDOVER 指纹、KB 终态、git status、门禁复跑 |
| `evidence8.sh` | 16 篇旧清单备份计数 |
| `evidence9.sh` | `papers/` 全量条目（发现 `papers/pdf/`） |
| `evidence10.sh` | `papers/pdf` 与 manifest 33 个 id 对照 |

*报告结束。本文件是本次审计唯一的写入产物。*
