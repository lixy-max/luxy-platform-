# 验收缺陷台账 · 第二轮更新（2026-10-22 晚）

> 承接 `ACCEPTANCE_BUGS_20261022.md`。本文件记录第二轮 3 个子代理的发现，合并为**待修总清单**。

## 两轮总账

| 轮次 | 子代理 | 断言 | 通过 | 通过率 | 报告 |
|---|---|---|---|---|---|
| 1 | A API+工具 | 1326 | 1227 | 92.5% | acceptance_api.md |
| 1 | B 逻辑层 | 519 | 497 | 95.7% | acceptance_logic.md |
| 1 | C 前端 | 278 | 265 | 95.3% | acceptance_frontend.md |
| 2 | D 回归+压测+soak | 913 | 896 | **98.1%** | acceptance2_stability.md |
| 2 | E 数据+训练 | 952 | 905 | 95.0% | acceptance2_data.md |
| 2 | F 交互+安全 | 458 | 420 | 91.7% | acceptance2_frontend_sec.md |
| **合计** | | **4446** | **4210** | **94.7%** | |

---

## ✅ 已修 + 已验证（3 项，D 独立回归确认）

| 项 | 提交 | 验证结果 |
|---|---|---|
| 兜底分发器缺 `/` | 2b8512e | 守卫 2 次 · **56/56 URL 合法**（修复前 56/56 非法）|
| 引用误报（真实 DOI/标题被判编造）| c6fc61e | 真实引用 **不判 fabricated**；桩注入证明源可用时**编造标题仍能抓** |
| 编造标题漏报 + 否定句误报 | c6fc61e | 5 句否定不报 · 2 个正例仍检出（未关检测）|

**基线无回归**：KB 64/64 · 语义路由 44/44 ✓

---

## 🔴 P0 待修（9 项）

### 1. 24+ 处 5xx（参数校验系统性缺失）
```
top_k<=0        → 500 chromadb TypeError (kb.py:922)
max_tokens/temperature 越界 → 500（上游 400 被包成 500，9 条）
KB名≥256/文件名≥255 → 500 OSError
search_existing 10万字符 query → 500 argv too long
POST /kb/create 1MB 名称 → 500 纯文本
POST /chat/completions 100KB → 500（包装 vLLM 400 上下文超限）
POST /kb/{n}/query 空query+top_k=0 → 500
POST /chat/tools {} → 502
```

### 2. 普通模式幻觉/置信度徽标失效
```
根因: /chat/completions/stream **根本不返回** hallucination/confidence
⇒ 不是前端解析问题，是后端流式接口没做检测 ✗
修: 流式结束后做一次检测，SSE 末尾发 {hallucination, confidence} 事件
```

### 3. 缺参数物理题不反问
```
「43223N/45°/2kg 能飞多远」
 F 实测: 给 **0.000227 m（0.227 毫米）**，并把抛射改成斜面模型
 且工具卡报「未知工具: physics_calc」→ 失败后不改调 run_simulation 而心算
 且与上一轮自相矛盾说「没有物理节点」
```

### 4. `GET /browser/sidecar-token` **无鉴权返回明文 48 位 token**
```
配合 sidecar 在 172.20.64.1:9224 可达 ⇒ 同网段任何人可控制浏览器 ✗
```

### 5. 会话恢复丢消息
```
服务端 16 条，UI 只渲染 8 条（Agent 两轮 + 2 张工具卡全丢）
```

### 6. 前端 29 个工具名后端不存在
```
/agent/tools 67 个 vs 前端 96 个 ⇒ 29 个是幽灵
（第一轮清理了 37 个后端幽灵，前端仍多出 29 个）
```

### 7. 分发表 6 条不可路由（均为真请求验证 404）
```
add_workflow_node      → GET /work（无此路由）
rebuild_knowledge_base → 条目是 AGENT_TOOL_DOCS 格式（结构非法）
delete_knowledge_base  → 同上
query_knowledge_base   → 'kb/${encodeURIComponent' 截断串
run_workflow           → 截断串 + 指向 workflow/result 而非 run
get_workflow_result    → 截断串
```

### 8. `physics_calc` 报"未知工具"，但 `list_sim_nodes` 宣称可用（自相矛盾）

### 9. 知识库索引严重不一致（E 实测）
```
30 库中 **15 个有 meta 无向量索引**
  （含声明 2537 块的 r2_lora_fs_probe、821 块的 minimax_h3_prompt，
    且无 docs/ 正文 ⇒ rebuild 也救不回）
**6 个块数虚高** —— xiyouji 声明 903 块，实际只有 **2** 块 ✗
3 个幽灵目录无 meta.json
总声明 6424 块 vs 实际 2144 块 ⇒ **67% 不可检索** ✗✗
```

---

## 🟠 P1 待修（10 项）

| # | 项 |
|---|---|
| 1 | `DELETE /history/{id}` 对不存在 id 返 200「已删除」假成功（lora_manager.py:1901-1910）|
| 2 | 仿真路由选错节点：用 math_eval 硬编码 g=9.8 → 40.82，正确应 physics_calc → 40.7886 |
| 3 | 训练历史只存内存，后端重启即丢（`/training/list` 重启后为空）|
| 4 | `clean` 不存在的库会**创建幽灵目录**，`mount` 把假库当有效库 |
| 5 | `/tools/search_existing` 裸 500 + 正常路径同步阻塞 **150s** |
| 6 | 未知 `provider:` 前缀静默 200 且**回显请求值**（来源不可信）|
| 7 | vLLM 不在时 `/chat/completions` 返 500（应 503）· `/health` 是"假健康" |
| 8 | `/tools/quality` 并发退化 **20.9×**（242ms→5045ms，同步串行化）|
| 9 | 敏感信息泄露：`/settings` `/models/list` `/readiness` `/vllm/status` `/agent/config` 泄露 `/home/luxy/...` 绝对路径；`/browser/status` 泄露内网 IP |
| 10 | 底座管理页 `#ms-model-list` 永久「加载中」（前端未处理 `{"state":"idle"}`）|

---

## 🟡 P2 待修（8 项）

```
· 错误码 3 套并存（KB 不存在: 404/400/200）· 错误体 3 种结构 · 500 返回 text/plain
· reflect/ingest 空 body 默认写 agent_use 库（危险默认值）
· 缺 nosniff / X-Frame-Options / CSP
· LoRA 页 41 项但点列表项无详情（无 onclick）
· /academic/search 的 limit 不生效；兜底路径 ~34s 且响应结构不一致
· confidence elapsed_norm 负值未裁剪
· 数值层"同数字不同主体"绕过（三环唑7天 vs 吡虫啉7天 → risk=low）
· create 无名称长度上限 · list_sim_nodes/run_simulation 缺 dangerous 布尔
· 2 个 LoRA 孤儿目录（ml_engineer / strict_json）· 3 个 KB 幽灵目录
```

---

## ✅ 安全面结论（F 实测，25 条断言）

```
无可执行 XSS: 对话/气泡/KB搜索/KB description 存储型 全部转义；canary=0；alert 拦截 0 次
路径穿越: 6 种全 404 · KB 名白名单 400
SQL/命令注入: 12 种无回显
CSRF: /browser/call 无 token 403、错 token 403、对 token 200 ✓
CORS: 错误 Origin 无 ACAO，预检 400，127.0.0.1:8080 显式放行 ✓
⇒ 注入类风险基本可控；**唯一真漏洞是 sidecar-token 无鉴权** ✗
```

## ✅ 性能/稳定性结论（D 实测）

```
压测: 300 请求 100% 成功 · 0 个 5xx · 轻端点 P95=130ms
推理: 5 并发 × 3 轮 15/15 成功 · 0 OOM · GPU1 显存恒定
soak: 10 分钟 · GPU1 显存极差 **4 MiB**（无泄漏）· RSS +19.4MB 非单调
错误恢复: 20 个 RST 强断不崩 · 404/405/422 一致
```

## ✅ 训练链路结论（E 实测）

```
12 条样本 / 1 epoch / rank8 → **142.5 秒** → 80.8MB 适配器
平台自动完成: 停 vLLM → 训练 → 自动拉起（pid 27257→39858）
中断约 2 分钟 · 恢复后 /v1/models=200 · 新 LoRA 4.6s 出答案
峰值显存 5171MiB（仅 GPU1）· 全程未碰 GPU0
```

## ✅ 数据完整性（E 实测）

```
数据集 71 个: 文件全在，0 僵尸
LoRA 41 个: adapter 全齐，0 断链（2 个非 LoRA 孤儿目录）
KB 30 个: **重灾区**（见 P0-9）
```

---

## ⚠️ 测试方法论教训

```
D 的 soak 期间 E 调了 POST /training/start 真训练
⇒ 训练停 vLLM 90s ⇒ 污染 D 的 soak 数据（D 已用干净窗口重算）
⇒ **教训: 稳定性/压测必须串行独占后端**，不能与其他测试并行
```

## 环境限制

```
· OpenAlex 免费日额度耗尽（429，UTC 零点重置）· paper.edu.cn 滑块验证码
· WSL 必须 `wsl -d Ubuntu-24.04`（默认进 26.04，无 /home/luxy）
· 数据集 71 个 · 后端/GPU 全在 GPU1（启动时 CUDA_VISIBLE_DEVICES=1）
· GPU0 = 用户自用（PUBG 占 88–96%），测试全程未碰
```

## 建议修复顺序

```
第 1 批（数据安全，最快见效）:
  sidecar-token 鉴权 · 敏感信息泄露 · 安全头
第 2 批（崩溃类）:
  参数校验统一（24+ 处 5xx）· search_existing 裸500 · 分发表 6 条 · physics_calc 注册
第 3 批（功能正确性）:
  流式徽标 · 物理题反问 · 知识库索引修复 · 训练历史持久化 · 会话恢复
第 4 批（体验/性能）:
  工具名对齐 · 错误码统一 · tools/quality 并发 · 底座页加载中 · LoRA 详情

修完必须: 跑第三轮回归验证（重点回归已修 3 + 新修各项）
```

*更新于 2026-10-22 · 两轮 4446 断言 · 4210 通过 (94.7%)*
