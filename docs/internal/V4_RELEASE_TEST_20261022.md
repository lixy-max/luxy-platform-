# v4 发布包 · 空装隔离验证报告（2026-10-22）

## 一、结论

**发布包可构建、可空装、可启动；知识库建库链路可用；但"模型带知识库回答"这条关键链路不通。**

---

## 二、发布包

```
构建物:  /mnt/e/WSL/release/luxy-platform-v4.tar.gz   (755 KB)
解压后:  ~2.7 MB
内容:
  · 26 个顶层 Python 模块
  · webui/ (index3.html + api.js + browser_page.js + learn.js + serve.py + vendor)
  · 49 个 scripts/*.py
  · agent_kb/ (工具文档 68 个)
  · browser_sidecar/ (server.mjs + package.json + policy.json，不含 node_modules)
  · settings.json (空数据默认配置)
  · install/start.sh · install/serve_web.sh
  · docs/ (WORK_DISCIPLINE / WORK_HANDOVER)
不含: data/ loras/ training_data/ uploads/ chroma_db/ models/ .git/ 存档/调试页
```

关键前提已验证：`PROJECT_DIR = Path(__file__).resolve().parent`
⇒ **换目录运行 = 数据完全隔离** ✓

## 三、隔离环境安装

```
位置: /home/luxy/luxy-v4-run/
后端: BACKEND_PORT=8002  （端口可配 ✓ 已确认）
前端: python -m http.server 8081
数据: 全空（0 知识库 / 0 LoRA / 0 数据集）
```

## 四、空装冒烟（全部通过）

| 端点 | 结果 |
|---|---|
| /health | 200 · 2 秒就绪 |
| /readiness | 200 · ok_count=3（开发机是 5，空装少 2 项，需查是哪两项）|
| /agent/tools | 200 · **68 个工具** |
| /sim/nodes | 200 · 6 个节点 |
| /kb/list | 200 · **0 个**（符合空装预期）|
| /loras | 200 · **0 个** |
| /training/available_datasets | 200 · **0 个** |
| 前端 8081 | 200 |
| 后端日志 | 无 Traceback |

## 五、真实用户旅程（4/6 步通过）

| 步 | 动作 | 结果 |
|---|---|---|
| 1 | 建第一个知识库 `POST /kb/create` | ✅ 200 |
| 2 | 灌用户文档 `POST /kb/{n}/ingest_folder` | ⚠️ **参数名是 `folder_path`** 不是 `folder`（第一次试错，第二次 200，added_docs=1）|
| 3 | 重建索引 `POST /kb/{n}/rebuild` | ✅ 200 · chunks=1 · embedding=bge-m3(1024) |
| 4 | 查询 `POST /kb/{n}/query`（3 个问题）| ✅ **3/3 命中**，返回原文片段 |
| 5 | **挂载 + 模型带库回答** | ❌ **失败**（详见下）|
| 6 | 知识库状态 `GET /kb/list` | ✅ 元数据完整（documents/total_chunks/embedding）|

## 六、🔴 关键缺陷：挂载了 ≠ 用上了

```
现象:
  已挂载 xingchen_x1 · KB 检索 3/3 命中 · 但模型回答:
  「关于"星辰 X1"的具体信息，我没有找到确切的产品数据…可能是灯具或天文望远镜」
  关键事实 1200流明 / 2999元 / 保修2年 **全部未命中** ✗

根因（已定位）:
  /chat/tools 的 ToolChatRequest **没有 kb 字段**，也没有任何 KB 上下文注入逻辑
  ⇒ Agent 模式完全不使用知识库 ✗
  （普通模式 /chat/completions 前端会传 kb，所以那边是通的）

修复尝试:
  已在 v4 实例里写入自动注入逻辑（无 kb 参数时自动用 settings.kb_mounted）
  ⇒ 代码已写入且语法通过、服务正常启动
  ⇒ 但**日志没有打印 [chat/tools] KB 注入**，说明注入代码未被执行到
  ⇒ 插入位置错误（可能在 early-return 之后），**未修好** ✗

★ 值得记录的好消息:
  hallucination_guard **正确地把这个回答判为 risk=high**（接地率低）
  ⇒ 检测层是好的，它如实告诉我们"这个回答没有依据" ✓
  ⇒ 这正是"幻觉检测"该起的作用
```

## 七、待修（按优先级）

| # | 项 | 说明 |
|---|---|---|
| **1** | **/chat/tools 注入 KB 上下文** | 插入位置要修；这是"体系"的核心环节 |
| 2 | `/kb/{n}/ingest_folder` 参数名提示 | 报错信息应提示正确参数名（用户体验）|
| 3 | readiness 空装只 3/5 | 查缺的 2 项是什么，是否影响功能 |
| 4 | 安装依赖体验 | 用户要自己装 torch/vllm/chromadb 等，需给出明确 requirements 与版本约束 |
| 5 | 前端默认端口/后端地址配置 | 空装时前端硬编码 127.0.0.1:8001，多实例需可配 |

## 八、方法学价值

**空装隔离测试一次性暴露了"开发机有数据才不显形"的问题**：
```
· 开发机上 KB 有内容、LoRA 已挂，所以"模型不用库"这个问题被掩盖了 ✗
· 空装后走真实旅程，第 5 步立刻暴露 ✗
⇒ 结论: 每次发布前必须跑一遍「空装 + 完整用户旅程」，不能只在开发机上验 ✓
```

## 九、复跑方式

```bash
tar -xzf luxy-platform-v4.tar.gz -C /path/to/empty-dir --strip-components=1
cd /path/to/empty-dir
BACKEND_PORT=8002 python lora_manager.py      # 后端
cd webui && python -m http.server 8081        # 前端
# 然后在 UI 里: 建库 → 灌文档 → 重建 → 查询 → 提问
```

*2026-10-22 · 空装隔离环境实测，非推测*
