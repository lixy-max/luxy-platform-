# 安装与启动（Luxy 模型垂直开发平台 v4.9.7）

> 单机平台：数据全部留在本机，不上传云端（云端 API 仅在显式启用学习增强时使用）。

## 1. 环境要求
- Linux（Ubuntu 22.04+）或 WSL2
- **Python 3.10 ~ 3.12**
- （可选）NVIDIA GPU + CUDA 12.x —— 无 GPU 也能用知识库/规则/文档工具
- 磁盘 ≥ 20 GB（模型另计）
- **Node.js 20+**（仅「浏览自动化」功能需要；playwright-core 1.64.1 要求 Node≥20）

## 2. 装依赖
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# GPU 用户（可选，用于本地推理/训练）：
pip install "vllm==0.11.0"     # 实测运行版本 0.11.0（5060 Ti / 3060 可用）；版本需与 CUDA 匹配
```

浏览自动化（可选）：
```bash
cd browser_sidecar
npm install                    # playwright-core 1.64.1
npx playwright install chromium
node server.mjs                # 默认监听 127.0.0.1:9224；需 WSL 访问时加 HOST=0.0.0.0
```

## 3. 启动（推荐用一键脚本）
```bash
bash install/start.sh         # 后端 :8001 + 前端 :8080（默认）
bash install/smoke_test.sh    # 冒烟自检：7 项
bash install/stop.sh          # 停止
```

手动启动：
```bash
# 后端
BACKEND_PORT=8001 python lora_manager.py
# 前端（★ 推荐 serve.py，带 no-store 防缓存；不要用 python -m http.server）
python webui/serve.py 8080
```
打开 **http://127.0.0.1:8080/index3.html**

### 换端口 / 多实例
```bash
BACKEND_PORT=8002 python lora_manager.py
https://127.0.0.1:8080/index3.html?api=http://127.0.0.1:8002
```
> 多实例原理：`PROJECT_DIR = lora_manager.py 所在目录` ⇒ 复制到另一个目录即数据完全隔离。

## 4. 首次使用（空装流程）
1. **底座模型**：把模型放进 `models/`（或设 `MODELS_DIR`），在「底座管理」启动 vLLM
2. **知识库**：新建 → 上传 zip / 填文件夹路径 → **重建索引**（确认 chunks > 0）
3. **挂载知识库**：挂载后模型会**自动带上**知识库内容回答（两种对话模式都支持）
4. **工作流**：出厂带一个「文档要点提炼工作流」示例（4 节点，不依赖 LoRA，空装可跑）
5. （可选）**LoRA**：训练页上传 jsonl（**必须是 conversations 格式**；
   `messages` 格式会在上传时自动转换）→ 训练 → 挂载

## 5. 目录说明
```
data/kbs/          知识库（正文 + meta）
chroma_db/         向量索引
loras/             LoRA 适配器
training_data/     训练用 jsonl
uploads/           上传文件
models/            底座模型（MODELS_DIR 可覆盖）
logs/              日志
install/           启动/停止/冒烟脚本
```

## 6. 版本确认
```bash
curl http://127.0.0.1:8001/version
# {"version":"4.9.2","name":"Luxy 模型垂直开发平台","generation":"v4.9","build":"2026-10-22"}
curl http://127.0.0.1:8001/health     # 也带 version 字段
cat VERSION
```

## 7. 常见问题
| 现象 | 处理 |
|---|---|
| readiness 4/5 | 缺「云端 API Key」属正常（可选功能）|
| readiness 缺「预设工作流」| 检查根目录 `workflow_config.json` |
| vLLM 起不来 | 显存不足时调小 `--gpu-memory-utilization` 或换小模型 |
| 知识库查不到 | 确认「重建索引」成功（chunks>0）且已挂载；失效挂载会自动清理 |
| 浏览自动化状态灯红 | 确认 sidecar 在 **9224** 端口：`curl http://127.0.0.1:9224/health` |
| 页面某功能 undefined | 确认 `webui/pages/*.js` 与 `rolechat.js`/`reflect.js` 都在（v4.1 曾漏打）|
| 训练期间推理不可用 | 检查是否双卡模式：`gpu_mode=dual` + `train_gpu=0` + `inference_gpu=1` ⇒ 训练在 GPU0、vLLM 留 GPU1，互不影响 |

## 8. 版本历史
- **v4.9.7**：修 playwright-core 版本（^25.0.0→1.64.1）· sidecar 端口统一 9224 · 补 install/start/stop/smoke 脚本 ·
  补 VERSION 文件与 `/version` 端点 · INSTALL 升版 · 清 .pyc · 修硬编码 token 路径 ·
  **修学习增强被知识库短路（默认配置下 learn/云转手/敏感词闸门全失效）** ·
  **加学习增强能力判定（本地答完但数值算错 ⇒ 自动升级云）**
- v4.9.7：5 项 P0（kb 参数 / 去重白名单 / 训练数据格式转换 / 工作流环检测 / workflow 状态以结果为准）+ 双卡模式真生效
- v4.9：知识库中文分块三处修复 + 出厂工作流换成不依赖 LoRA 的 4 节点示例
- v4.8：底座下拉单一真源（不再两个"使用中"）
- v4.7：知识库失效不再阻塞对话
- v4.6：幻觉修复引擎（检测→定位→修复→验证→回滚）
- v4.5：仿真确定性兜底进 Agent 模式
- v4.4：sidecar 网关自动探测（零配置）
- v4.3：workflow 防覆盖 + 工具派发兜底
- v4.2：补齐 8 个前端 JS（修 v4.1 漏打）
- v4.1：workflow_config + 知识库自动挂载
- v4：首个发布包
