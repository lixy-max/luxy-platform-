# KB 清理报告 —— 删除 0 块空知识库（task-3）

- 执行时间：2026-10-14 22:38 ~ 22:39（WSL Ubuntu-24.04，`/home/luxy/paper_lora_project`）
- 执行者：kb-curator
- 范围：只删探测遗留的空库；**未 ingest、未 rebuild、未改 embedding、未停/重启后端或 vLLM**
- 结论：**17 库 → 8 库，删除 9 个实测为空的库；宝贵库 agent_use（含 `90_learned.md` 18 块 / 7762 字符）逐项指纹一致，未受影响。**

---

## 1. 备份（删除前第一步）

| 项 | 值 |
|---|---|
| 备份文件 | `/home/luxy/paper_lora_project/data/kb_backup/agent_kb_before_tidy_20261014_2238.tgz` |
| 内容 | `data/kbs/`（17 个库目录）+ `agent_kb/`（12 个文件） |
| 大小 | 24,570 字节 |
| sha256 | `87d8d28653499251cded1e54842c46699c8ae77a0b96d8c1262f839eba8e817f` |
| tar 条目数 | 48 |

备份完整性抽检：tar 内确认存在 `data/kbs/agent_use/meta.json`、`data/kbs/xiyouji/meta.json`、`data/kbs/minimax_h3_prompt/meta.json`。
（`data/kb_backup/` 下另有历史全量索引备份 `chroma_20261013_2136.tgz` 82MB 可作二级保险。）

---

## 2. 删前指纹（/kb/agent_use + /kb/list 实测）

**agent_use**

| 指标 | 值 |
|---|---|
| 文档条目数 | 10 |
| `total_chunks`（meta） | 74 |
| 文档 chunks 求和 | 74 |
| 文档 chars 求和 | 29,605 |
| **索引真实块数**（chroma `kb_agent_use`.count()） | **74** |
| embedding | `{"model": "bge-m3", "dim": 1024}` |
| updated_at | `2026-10-13T21:53:18.096693` |
| **canonical hash** | `ae62bdf3089bbacd957426301dec0e6c7ce32dcfd4848a907a4309f1406f1518` |

文档名列表（顺序保留，用于删后逐项比对）：
`30_pitfalls.md, 90_learned.md, 00_overview.md, 10_tool_catalog.md, 60_synonyms.md, 70_verbs.md, 40_disambiguation.md, 20_playbooks.md, 80_office_mcp.md, 50_decision_rules.md`

**宝贵资产 90_learned.md**：**18 块 / 7762 字符** —— 与任务书预期完全一致。

**全库清点（删前 17 个）**：

| 库 | meta total_chunks | 真实 chroma count | 处置 |
|---|---|---|---|
| agent_use | 74 | 74 | 保留（受保护） |
| agent_use_ctrl | 56 | 56 | 保留 |
| learned_chat | 2 | 2 | 保留 |
| learned_chat2 | 12 | 12 | 保留 |
| learned_test | 2 | 2 | 保留 |
| minimax_h3_prompt | 820 | 820 | 保留（受保护） |
| xiyouji | 900 | 900 | 保留（受保护） |
| r2_lora_fs_probe | 2536 | 1268 | **保留**（受保护）；见 §6 异常观察 |
| probe_top3_r2b | 0 | 0 | **删除** |
| probe_top3_r2c | 0 | 0 | **删除** |
| probe_trainsets_r2 | 0 | 0 | **删除** |
| r2_embed_probe | 0 | 0 | **删除** |
| r2_size_xcheck | 0 | 0 | **删除** |
| r2b_top3_xcheck | 0 | 0 | **删除** |
| tmp_xlsx_probe | 0 | 0 | **删除** |
| verify_r2_xlsx | 0 | 0 | **删除** |
| verify_r3_ds | 0 | 0 | **删除** |

全库合集 hash（删前）：`c1eabff696d84aee421f236e49838cd9328d6a08248df63ff79e26734a8c7634`

---

## 3. 候选确认（删除前逐库实测，双重口径）

对 9 个候选库，在**删除动作发生前**现场重新读取（不是用缓存），三重口径全部为 0 才允许删：

| 库 | meta total_chunks | meta documents | 真实 chroma count | 目录内容 |
|---|---|---|---|---|
| probe_top3_r2b | 0 | 0 | 0 | 仅 `meta.json` |
| probe_top3_r2c | 0 | 0 | 0 | 仅 `meta.json` |
| probe_trainsets_r2 | 0 | 0 | 0 | 仅 `meta.json` |
| r2_embed_probe | 0 | 0 | 0 | 仅 `meta.json` |
| r2_size_xcheck | 0 | 0 | 0 | 仅 `meta.json` |
| r2b_top3_xcheck | 0 | 0 | 0 | 仅 `meta.json` |
| tmp_xlsx_probe | 0 | 0 | 0 | 仅 `meta.json` |
| verify_r2_xlsx | 0 | 0 | 0 | 仅 `meta.json` |
| verify_r3_ds | 0 | 0 | 0 | 仅 `meta.json` |
| **r2_lora_fs_probe** | **2536** | 12 | **1268** | 非空 → **不删**（且属受保护名单） |

**没有任何候选库出现"块数不为 0"的情形**，故未触发中止条件；`r2_lora_fs_probe` 因非空且受保护而保留（本不在候选名单内，仅复核）。

---

## 4. 实际删除清单（9 个）

删除方式：读取 `/home/luxy/paper_lora_project/kb.py` 确认真实函数名后，用 `/home/luxy/.venv/bin/python` 调用 **`kb.delete_kb(name)`**（该函数会 `delete_collection(name=f"kb_{name}")` 并 `rmtree(data/kbs/<name>)`），未使用任何猜测的函数名，未走 HTTP 写接口（`/kb/{name}` 无 DELETE 接口）。

| # | 库名 | 删除时实测块数 | meta 目录 | chroma 集合 |
|---|---|---|---|---|
| 1 | probe_top3_r2b | 0 | 已删除 | 已删除 |
| 2 | probe_top3_r2c | 0 | 已删除 | 已删除 |
| 3 | probe_trainsets_r2 | 0 | 已删除 | 已删除 |
| 4 | r2_embed_probe | 0 | 已删除 | 已删除 |
| 5 | r2_size_xcheck | 0 | 已删除 | 已删除 |
| 6 | r2b_top3_xcheck | 0 | 已删除 | 已删除 |
| 7 | tmp_xlsx_probe | 0 | 已删除 | 已删除 |
| 8 | verify_r2_xlsx | 0 | 已删除 | 已删除 |
| 9 | verify_r3_ds | 0 | 已删除 | 已删除 |

删除脚本内置硬性护栏：名单硬编码且断言为 9 个唯一名、断言与受保护集合无交集、逐个现场复验（meta chunks=0 且 documents=0 且 chroma count=0）才执行；任一候选非空则整体中止、一个都不删。本次全部通过后才进入删除循环。

---

## 5. 删后复查（与删前指纹逐项对比）

**总共 41 项断言，0 失败。**

### 5.1 agent_use（必须完全一致）

| 指标 | 删前 | 删后 | 判定 |
|---|---|---|---|
| 文档条目数 | 10 | 10 | ✅ 一致 |
| total_chunks | 74 | 74 | ✅ 一致 |
| chunks 求和 / chars 求和 | 74 / 29,605 | 74 / 29,605 | ✅ 一致 |
| 索引真实块数 | 74 | 74 | ✅ 一致 |
| embedding | bge-m3 / 1024 | bge-m3 / 1024 | ✅ 一致 |
| updated_at | 2026-10-13T21:53:18.096693 | 同 | ✅ 未变动 |
| 文档名列表（顺序） | 10 个 | **完全相同** | ✅ 一致 |
| 每文档 chunks/chars | — | — | ✅ 逐条一致 |
| canonical hash | `ae62bdf3…f1518` | `ae62bdf3…f1518` | ✅ 相同 |

### 5.2 90_learned.md

删前 18 块 / 7762 字符 → 删后 **18 块 / 7762 字符**，条目字段完全一致。磁盘文件 `agent_kb/90_learned.md` 的 sha256 与备份包内同名成员**逐字节相同**（`231da2b31ba3f3144361287220494373ba0ee8c801d09e1d703fafee83cf2483`）。

### 5.3 其他库"一个不少、一个没变"

- 库数量 17 → **8**（恰好 17−9）；库名集合 = 删前集合减去 9 个删除项。
- 剩余 8 库的 meta 合并 hash：删前子集 `1d43a7300b5262b1…` = 删后 `1d43a7300b5262b1…` ✅
- **8 个剩余库的 `meta.json` 与备份包内成员 sha256 逐个相同**（META-OK ×8，见下）。
- Chroma 集合数 17 → 8；9 个被删库的集合全部消失，**无残留、无多余新集合**。
- 受保护库完好：agent_use 74、xiyouji 900、minimax_h3_prompt 820、r2_lora_fs_probe 1268（前后不变）。

剩余库 `meta.json` 完整性（tarball vs 磁盘，sha256）：
```
META-OK   agent_use         be90303fd331bfde15963160a1f46c97227ca455bd4ee5d331cd4af2d730eabe
META-OK   agent_use_ctrl    4131b28e92c8a04353e083d97598c4b69dd32fbec6e16460236cc735483c57a7
META-OK   learned_chat      dcca25a9e806217bcb217b07d0ce4ced1fff368e5e4e205288a61a65c047c08d
META-OK   learned_chat2     503d72a130da30a45444ebe49c3e1b4028e31b72aea3e24f146b742c54ca7c01
META-OK   learned_test      0e7235e2d07e82c4b08906367109ea0127030a0330fdd452a301235a8a1b5db6
META-OK   minimax_h3_prompt 4db75ea6240a7fcc8e734f9208f701ed20f8b5fae919e41f42e895a7f6fb0b8a
META-OK   r2_lora_fs_probe   b9b5e0d9a771fa876befe0261364c367f2bf25219cf4ab65ab591c147f35d8ee
META-OK   xiyouji           7a956f0532ca0b319e9f02067492112cf3c9115e2967dcab56bf8677570a843f
```
`agent_kb/` 下 12 个文件（含 `90_learned.md`、`_tools.json`、`_last_eval.json`）与备份包内成员 sha256 **全部相同**（AGENTKB-OK ×12，diff flag 0）。

### 5.4 对比结论

**删除仅影响被删的 9 个空库；其余 8 个库的元数据、索引块数、文档清单逐项不变，agent_use 与 90_learned.md 指纹完全一致。未发现任何非预期副作用。**

---

## 6. 异常观察（未处理，仅报告）

`r2_lora_fs_probe` 存在**先前就存在的** meta 与索引不一致：

- meta `total_chunks` = 2536，但 chroma 集合真实块数 = **1268**（恰好约一半）。
- 该不一致在删除前后**完全相同**（1268 → 1268），与本次清理无关，且该库属"先留着"名单，**本次未做任何改动**。
- 可能成因：该库 `documents` 列表里存在重名条目（`README.md` 出现 5 次、`merges.txt` 出现 2 次），meta 按条目累加 chunks，而 chroma 侧按内容哈希 id 写入会合并重复内容。
- 建议：由 lead 决定是否另开任务处理（修 meta 或重建索引），**本任务范围内不做 rebuild**（协议禁止）。

---

## 7. 门禁

```
cd /home/luxy/paper_lora_project && /home/luxy/.venv/bin/python -m unittest discover -s tests -t .
→ Ran 699 tests in 13.694s
→ OK
```

---

## 8. 备份路径与证据文件

备份包：
```
/home/luxy/paper_lora_project/data/kb_backup/agent_kb_before_tidy_20261014_2238.tgz
  sha256 87d8d28653499251cded1e54842c46699c8ae77a0b96d8c1262f839eba8e817f  24,570 bytes
```

同目录下的过程证据（均为本次生成，可复核）：

| 文件 | 内容 |
|---|---|
| `tidy_before_agent_use.json` / `tidy_before_kb_list.json` | 删前 `/kb/agent_use`、`/kb/list` 原始响应 |
| `tidy_after_agent_use.json` / `tidy_after_kb_list.json` | 删后原始响应 |
| `tidy_fingerprint_before.json` / `tidy_fingerprint_after.json` | 规范化指纹（含 canonical hash、真实 chroma 计数） |
| `tidy_delete_log.json` | 删前/删后逐库实测记录 |
| `tidy_compare.json` | 41 项对比断言明细（0 失败） |

复现用脚本（工作区外，`E:\WSL\_check\`）：`kb_recon.sh`、`kb_step1_backup.sh`、`kb_step2_peek.sh`、`kb_fp.py`、`kb_step2_fingerprint.sh`、`kb_step4_delete.py`、`kb_step5_compare.py`、`kb_step5_recheck.sh`。

---

## 9. 边界确认（做了什么、没做什么）

**做了**：打包备份 → 记录指纹 → 三重口径实测候选 → 只删确认过的 9 个空库 → 删后逐项复查指纹 + 按字节校验备份包。

**没做**（遵守硬约束）：未 ingest 任何文档；未 rebuild 任何索引；未修改 agent_use 内容或 embedding；未停/重启后端或 vLLM；未改任何代码、未改 `HANDOVER.md`；未碰 `webui/index3.html`、`lora_manager.py`、`vllm_manager.py`；未 `git add -A`、未提交。本任务唯一写入的仓库文件是 `docs/KB_TIDY_REPORT.md` 与 `data/kb_backup/` 下的备份/证据文件。
