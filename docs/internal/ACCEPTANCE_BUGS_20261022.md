# 验收缺陷台账（2026-10-22 · 三轮验收 2123 断言）

> 数据来源：三个验收子代理的真实测试，均含 raw.json 可追溯。
> 状态标记：✅ 已修 · ⏳ 待修 · 📝 记录

## 总账

| 子代理 | 断言 | 通过 | 失败 | 通过率 | 报告 |
|---|---|---|---|---|---|
| A · API+工具层 | 1326 | 1227 | 99 | 92.5% | acceptance_api.md |
| B · 逻辑层 | 519 | 497 | 22 | 95.7% | acceptance_logic.md |
| C · 前端+端到端 | 278 | 265 | 13 | 95.3% | acceptance_frontend.md |
| **合计** | **2123** | **1989** | **134** | **93.7%** | |

---

## ✅ 已修（3 项）

### 1. 兜底分发器 URL 缺前导斜杠（提交 2b8512e）
```
index3.html 两处: API_BASE + _path，_path 无前导 /
⇒ 'http://127.0.0.1:8001kb/list' ⇒ ERR_INVALID_URL
⇒ 56/56 兜底路由不可用，search_existing_models 100% 失效
修: 加归一化 if (_path.charAt(0) !== '/') _path = '/' + _path
```

### 2. 真实引用被误判为幻觉（提交 c6fc61e）
```
academic_sources.search() 吞 429 返回 [] ⇒ _openalex_find 当"查不到" ⇒ 判 fabricated
实测误判: 真实 DOI 10.48550/arXiv.2303.12712
         真实标题《Sparks of Artificial General Intelligence》
修: 空结果/异常 ⇒ 返回 None = 无法判定（绝不判编造）
```

### 3. 编造标题漏报 + 否定句误报（提交 c6fc61e）
```
漏报: OpenAlex 模糊检索任意标题都有命中 ⇒ bool(hits) 算"存在" ⇒ fabricated_citation 永不触发
误报: 「我没有看到你上传的文件」→ fabricated_upload_claim/high
修: DOI 精确匹配；标题 Jaccard ≥0.6；否定词窗口 ⇒ 不算声称
```

---

## ⏳ 待修（7 项）

### P0-1 · 24 次 5xx（参数校验系统性缺失）
| 触发 | 现象 | 位置 |
|---|---|---|
| `top_k <= 0` | 500 chromadb TypeError | kb.py:922 |
| `max_tokens`/`temperature` 越界 | 500（上游 400 被包成 500，9 条）| — |
| KB 名 ≥256 / 文件名 ≥255 | 500 OSError | — |
| `search_existing` 10 万字符 query | 500 argv too long | — |

### P0-2 · 普通模式低置信徽标失效
```
根因（比子代理初判更根本）: /chat/completions/stream **根本不返回**
     hallucination / confidence 字段
⇒ 不是前端 SSE 解析问题，是**后端流式接口没做检测** ✗
修法: 流式生成结束后做一次检测，通过 SSE 末尾发 {hallucination, confidence} 事件
```

### P0-3 · 缺参数物理题不反问
```
Q「43223N/45°/2kg 能飞多远」→ A「2205.30 米」✗（把 F/m 当 v²，量纲错）
对照: 「43223 N 的力以 45 度投掷 2kg 的球能飞多远」→ 正确反问 ✓
⇒ sim_router 关键词/参数抽取对"紧凑写法"失效 ✗
```

### P0-4 · `DELETE /history/{id}` 假成功
```
不存在的 id 返回 200 "已删除"，同资源 GET 却正确 404
位置: lora_manager.py:1901-1910（漏存在性检查）
```

### P1-5 · 仿真路由选错节点
```
模型不用 physics_calc.projectile_range，改用 math_eval 硬编码 g=9.8
⇒ 40.82 米 vs 正确 40.7886 米
```

### P1-6 · 训练历史未持久化
```
/training/list → {"tasks":[]}
但 agri_expert_v1 的 LoRA 产物还在
⇒ 训练任务记录只在内存，后端重启即丢 ✗
```

### P2-7 · 其他一致性/安全问题
```
· 分发表 3 处错: add_workflow_node:['GET','work'] 无此路由
                rebuild/delete_knowledge_base 值写成文档形状
· KB 缺失状态码 404/400/200 三套并存
· reflect/ingest 空 body 默认写 agent_use 库（危险默认值）
· /academic/search 的 limit 不生效；兜底路径 ~34s 且响应结构不一致
· confidence elapsed_norm 负值未裁剪
· 数值层"同数字不同主体"绕过（三环唑 7天 vs 吡虫啉 7天 → risk=low）
· 否定句类误报（已修 upload 类，其他类未排查）
· 94 工具中 list_sim_nodes/run_simulation 缺 dangerous 布尔
```

---

## 环境限制（影响后续测试）

```
· OpenAlex 免费日额度耗尽（429，UTC 零点重置）
· paper.edu.cn 返回滑块验证码 ⇒ 学术检索双源失效
· WSL 必须指定 -d Ubuntu-24.04（默认进 26.04，无 /home/luxy）
· 数据集实测 71 个
· GPU0 = 用户自用（PUBG）；平台全在 GPU1（后端起时 CUDA_VISIBLE_DEVICES=1）
```

---

## 未测（验收报告 §6 披露）

```
· 5 个禁调端点（含 vllm/stop、training/start、真实删除）
· 真实数据删除链路
· 训练完整链路（会停 vLLM）
· UI 实际点击操作（C 只做了渲染 + 部分交互）
· 100+ 并发压测
· 长时间稳定性（soak）
```

*更新于 2026-10-22*
