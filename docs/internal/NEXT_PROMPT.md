# 下个对话的提示词（复制这段给新会话的 agent ✓）

---

## 🎯 任务背景

你在 `E:\WSL`（Windows）+ WSL 的 **LoRA 工作台项目**（`/home/luxy/paper_lora_project`）里继续工作。
这是**续接会话**。上一轮已经**完成用户的原始任务**：平台 agent 自己读作业 → 完成 5 问 → 写出 Word
（`E:\zy.work\practical-2-answers.docx`，98 段 / 7 标题 / 20×7 表 / 诚实 AI Declaration）。

## 📌 第一步（必做）

```bash
cd /home/luxy/paper_lora_project
tail -120 HANDOVER.md                # 最后两节：practical-2 作业完成 + 复盘
tail -160 docs/PLATFORM_ISSUES.md    # 台账（P-32~P-44 补录、P-45~P-47、P-48~P-50）
cat docs/PRACTICAL2_AGENT_RUN.md     # ★ 作业端到端运行记录（4 次尝试 + API 证据 + 验收）
cat NEXT_PROMPT.md                   # 本文件
git log --oneline -6
```

**环境检查：**
```bash
curl -s -o /dev/null -w 'vLLM %{http_code}\n'     http://127.0.0.1:8000/v1/models
curl -s -o /dev/null -w '后端 %{http_code}\n'     http://127.0.0.1:8001/health
curl -s -o /dev/null -w '前端 %{http_code}\n'     http://127.0.0.1:8080/index3.html
curl -s -m 4 -w '\nsidecar %{http_code}\n'        http://127.0.0.1:9223/health
ls -la /mnt/e/zy.work/ /mnt/e/zy.work/from_agent/
```
- sidecar 挂了：Windows 侧 `cd E:\WSL\_check\browser_sidecar && node server.mjs`（keeper 也会拉）
- 门禁：`make test`（**828**）· Windows 侧 `node "\\wsl$\Ubuntu-24.04\home\luxy\paper_lora_project\tests\frontend\run.js"`（**372/372**）· `bash scripts/check_sync.sh`

## ✅ 上一轮已完成（不要重做）

| 项 | 状态 |
|---|---|
| 浏览器自动化 agent 下载 `practical-2.docx`（137,953 bytes · md5 一致） | ✅ 上一轮完成 |
| 平台 agent 读文件 → 完成 5 问 → write_docx 到 `E:\zy.work\practical-2-answers.docx` | ✅ **本轮完成**（`docs/PRACTICAL2_AGENT_RUN.md` 有完整证据） |
| P-45 / P-45b（agent 读写 E 盘文件） | ✅ 已修 |
| P-46（agent max_tokens 跟随页面设置 256~8192） | ✅ 已修 |
| P-47（主对话下拉可选云模型） | ✅ 已修（设计取舍待用户确认） |
| P-49（write_docx/write_sheet 参数 JSON 字符串静默丢弃） | ✅ 已修 + 2 回归测试（后端 828） |
| P-50（marked 走 CDN，弱网崩 agent） | ✅ 已修 + 前端断言（372） |
| 问题台账补录 P-32~P-44 + 新增 P-45~P-50 | ✅ 已入 `docs/PLATFORM_ISSUES.md` |

## 📋 下一轮要做（按优先级）

### 1. 交付收尾（用户侧，不需要平台）
- **学号未知**：`E:\zy.work\practical-2-answers.docx` 里 `Student Number: YOUR-STUDENT-NUMBER` 是占位符；
  提交要求文件名 `{StudentNumber}-Practical2.pdf`、上传 `student.zy.cdut.edu.cn`。
  ⇒ 问用户学号，填 header → Word 另存/转 PDF → 按学号命名。
- 提醒用户准备 **viva**（要能讲清：真因子定义、取模判断、循环、追踪表每一行、为什么 20 不是完美数而 28 是）。

### 2. 平台待办
- **P-48（建议修）**：`MAX_ITER=24` 偏紧 + system prompt「计算纪律」会诱导逐项 `run_simulation`
  ⇒ 本轮实测 24 轮被耗尽后进入**无工具的云接手**并输出"无法完成"。
  建议：`MAX_ITER` 40~48；system prompt 增"同一次计算合并、算术环节 ≤3 次"；escalation 无工具时如实说"迭代耗尽"。
- **part2 判官重跑**（上轮云抖动全失败）：
  `python scripts/drill_score.py --runs /tmp/p2final.jsonl --tag p2final --tasks docs/DRILL_TASKS_PART2.md --pair-tasks docs/DRILL_TASKS_PART1.md --judge`
- **工具选择精度**（闸门⑤后 7 条下降 = "调了工具但选错"）→ ROADMAP M2 AST 参数级判分
- **网页 → 清洗 → 知识库闭环**（只进 `test_use_agent` ✓ 绝不碰 `agent_use` ✗）
- 前端 CDN 本地化（marked/highlight vendored）——P-50 已可降级，彻底离线仍需资源本地化
- 浏览器自动化剩余：浮窗形态、任务队列、多标签

### 3. 如果要重跑 agent 作业任务（一般不需要）
```bash
# 任务提示：E:\WSL\_check\r17\task_p2.txt（已含迭代预算约束，必须保留）
cd E:\WSL\_check\drill ; node run_p2.mjs          # 云 DeepSeek + 8192 tokens；输出在 E:\WSL\_check\r17\out\
```
验收脚本：`E:\WSL\_check\r17\finalize_docx.sh`（header / AI 声明 / 关键答案 / 追踪表逐行复算）。
⚠️ 重跑会在 `E:\zy.work` 生成 `_1` 后缀文件（平台不覆盖），记得清理中间版。

## ⚠ 铁律（违反会出事故）

```
✗ 绝不替 agent 做事来假装测试通过（答案必须由页面 agent 调工具产出；跑手只点按钮）
✗ 不复用旧产物当答案（uploads/cb067d75_practical-2-answers.docx、E:\zy.work\_lead_manual_backup\ 是旧的手动产物）
✗ 不在聊天里输入真实密码（上轮已泄露过一次 ✓ 用户已改 ✓ 别再犯）
✗ 不用 Get-Process node | Stop-Process（会杀跑批；用 Start-Process -PassThru 拿 PID）
✗ 不在 pwsh -Command 里写复杂 bash/中文引号（写 .sh 文件再跑）
✗ 不用系统 python3 跑测试（缺 fastapi 报 13 error）→ 用 make test（自动 ~/.venv）
✗ 文件改动写不了 \\wsl.localhost（EIO）→ 写补丁脚本到 E:\WSL\_check 再在 WSL 里跑（唯一匹配断言 + .bak）
✗ 工具返回 ok ≠ 结果正确（P-49 教训）⇒ 产物必须做**内容级/结构级验收**
✓ 每步验证: 后端 828 · 前端 372/372 · JS 3/3 · bash scripts/check_sync.sh
✓ 修完一个 bug 当场写进 docs/PLATFORM_ISSUES.md（编号只出现在提交信息 ≠ 入库 ✗）
```

## 🔧 关键命令

```bash
# 服务
bash scripts/start.sh --no-browser             # 拉起全部（幂等）
bash scripts/keeper.sh &                       # 看护（含 sidecar ✓）
tail -50 logs/backend.log

# 门禁
make test                                      # 后端 828
node "\\wsl$\Ubuntu-24.04\home\luxy\paper_lora_project\tests\frontend\run.js"   # 前端 372（Windows 侧）
bash scripts/check_sync.sh                     # 三处同步（不一致 → --fix）
```

## 📊 关键数字（本轮结束状态）

- 后端 **828 tests OK** · 前端 **372/372** · 服务 4 个 200（vLLM/后端/前端/sidecar）
- 作业交付：`E:\zy.work\practical-2-answers.docx`（40,962 B · 98 段 · 7 标题 · 20×7 表）
- 工具数 88（含文档 32 + 浏览器 13 + 仿真 + 工作流等）· 云 DeepSeek 可用（65k 窗口）

## ✅ 交付标准（用户任务已完成时的状态）

```
① 浏览器 agent 自己下载 practical-2.docx ✓
② 平台 agent 用 read_docx 读要求 → 5 问 → write_docx 到 E:\zy.work\ ✓
③ 产物经内容级验收（header/AI 声明/20→0 22/28→1 28/追踪表逐行复算）✓
④ 报告哪几步成功/失败/原因（不美化）✓ · 后端 828 / 前端 372 全绿 ✓ · 新问题当场入库 ✓
```
