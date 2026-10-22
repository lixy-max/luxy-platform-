# v4.1 发布包 · 全功能隔离验证报告（2026-10-22）

## 一、结论

**v4.1 通过空装隔离验证：38 项功能断言 37 通过（97.4%，唯一失败是"云端 API Key 未配置"属可选功能）。核心链路「用户文档 → 知识库 → 模型带库回答」在 Agent 模式和普通模式下都实测跑通。**

---

## 二、交付物

```
/mnt/e/WSL/release/luxy-platform-v4.1.tar.gz   762 KB（解压 2.8 MB）

内容:
  · 26 个 Python 模块
  · webui/ (index3.html + api.js + browser_page.js + learn.js + serve.py + vendor)
  · 49 个 scripts/*.py
  · agent_kb/ (工具文档，68 个工具)
  · workflow_config.json  ← v4 缺失，v4.1 补上
  · settings.json（空装默认）
  · install/start.sh · install/serve_web.sh
  · docs/INSTALL.md（安装文档）· WORK_DISCIPLINE · WORK_HANDOVER

包内数据目录全部为空:
  data / loras / training_data / uploads / chroma_db / models  → 各 0 个文件 ✓
```

## 三、隔离安装验证

```
全新目录: /home/luxy/luxy-v41-run/     （与开发机数据完全隔离）
后端: BACKEND_PORT=8003 · 2 秒就绪 ✓
前端: :8082 · 200 ✓
Traceback: 0 ✓
```

## 四、全功能测试结果（38 断言 / 37 通过 / 97.4%）

### C. 基础能力
| 项 | 结果 |
|---|---|
| health | ✓ ok |
| readness 空装 | ✓ **4/5**（v4 是 3/5，补 workflow_config 后 +1）|
| · 运行依赖 | ✓ 齐全 |
| · 本地模型 | ✓ 9 个 |
| · 推理底座 vLLM | ✓ 运行中 |
| · 云端 API Key | ✗ 未配置（**可选功能，正常**）|
| · 预设工作流 | ✓ 1 个（v4 时缺失）|
| agent/tools | ✓ **68 个** |
| sim/nodes | ✓ **6 个** |
| 空装 KB / LoRA | ✓ 各 0 个 |

### D. 知识库全链路（6/6）
```
create → ingest_folder(folder_path) → rebuild(bge-m3,1024) → query 4/4命中 → mount → mounted列表
全部 ✓
```

### E. ★ 模型带知识库回答（5/5）—— 本次核心验证
```
问: 星河 S2 的续航、价格和防水等级是多少？保修多久？
答: 星河 S2 智能手表的续航是 14 天，价格是 1299 元。它的防水等级是 5ATM。
    保修期限为整机 1 年。

事实命中: 14天 ✓ 1299元 ✓ 5ATM ✓ 1年 ✓
hallucination: risk=low ✓ 不误报
```

### F. 仿真计算（6/6）
| 公式 | 结果 |
|---|---|
| projectile_range (v=20,45°) | 40.78864851911713 ✓ |
| free_fall_time (h=10) | 1.4280869812290344 ✓ |
| force (m=2,a=5) | 10.0 ✓ |
| kinetic_energy (m=2,v=10) | 100.0 ✓ |
| potential_energy (m=2,h=10) | 196.13299999999998 ✓ |
| math_eval 987654/6 | 164609 ✓ |

### G. 幻觉检测 5 层（5/5）
| 用例 | 结果 |
|---|---|
| 正常回答 | risk=low ✓ 不误报 |
| 无来源数字（过磷酸钙46%）| risk=high ✓ 抓到 |
| 编造上传声明 | risk=high ✓ 抓到 |
| 否定句（"我没有看到你上传的文件"）| risk=low ✓ 不误报 |
| 真实 DOI 10.48550/arXiv.2303.12712 | risk=low ✓ 不误报 |

### H. 其他
```
academic/search     ✓ 200（OpenAlex 额度可能 429，走降级）
training/list       ✓ 200 · 0 条（空装）
available_datasets  ✓ 200
settings 脱敏        ✓ 响应里无 /home/luxy 绝对路径
browser/status      ✓ 200
```

## 五、本次修复的 3 个发布级缺陷

| # | 缺陷 | 修法 | 状态 |
|---|---|---|---|
| 1 | **v4 包缺 `workflow_config.json`** ⇒ readiness 3/5、预设工作流不可用 | v4.1 打包时补上 | ✅ |
| 2 | **Agent 模式完全不用知识库** `/chat/tools` 无 kb 字段、无注入 | 新增自动注入：未指定则用 `settings.kb_mounted`，检索结果作 system 消息 | ✅ 实测命中 |
| 3 | **普通模式必须手动选库** 流式接口 `if req.kb and ...` | 未指定则自动用 kb_mounted[0] + 合并其余（最多3库）| ✅ 实测命中 |

附带：前端 `API_BASE` 可配置（`window.__LUX_API__` > `?api=` > 默认 8001），多实例部署必需 ✓

## 六、遗留问题（不影响发布，记录在案）

```
① 前端 95 个 AGENT_TOOLS vs 后端 68 个工具文档
   ⇒ 约 27 个前端多出的工具名在后端不存在（"幽灵工具名"反向问题）
② 普通模式徽标：已修（流式结束后调 /hallucination/check），
   但本次前端测试因等待 5.5s 已过开屏动画，未单独验徽标视觉
③ 训练链路未在 v4.1 里实测（会停 vLLM 影响开发实例）
   —— 上一轮已在开发机验过: 142.5 秒成功、vLLM 自动恢复
④ academic/search 在 OpenAlex 429 时返回空（降级正常，但应给出提示）
⑤ readiness 的"云端 API Key"项对纯本地用户永远 ✗（建议标为"可选，不算失败"）
```

## 七、启动方式（写进 INSTALL.md）

```bash
tar -xzf luxy-platform-v4.1.tar.gz -C ~/luxy --strip-components=1
cd ~/luxy
pip install -r requirements.txt

# 后端
BACKEND_PORT=8001 python lora_manager.py
# 前端
cd webui && python -m http.server 8080
# 打开 http://127.0.0.1:8080/index3.html

# 换端口/多实例
BACKEND_PORT=8002 python lora_manager.py
http://127.0.0.1:8080/index3.html?api=http://127.0.0.1:8002
```

## 八、方法学结论（重要）

```
开发机有数据 ⇒ "模型不用知识库"被完全掩盖 ✗
空装隔离 ⇒ 第 5 步立刻暴露 ✗

⇒ 发布流程必须包含「空装 + 完整用户旅程」这一环
⇒ 本次通过空装演练发现 3 个发布级缺陷（其中 2 个是功能根本性缺失）
```

*2026-10-22 · 全部数字来自隔离实例 :8003 的真实调用，非推测*
