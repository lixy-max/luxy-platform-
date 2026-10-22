# 架构图（云边分工 · LoRA 挂在哪 · 数据流）

## 一、总览

```mermaid
flowchart TB
    subgraph Browser["浏览器 · webui/index3.html（单文件）"]
        UI["界面 9 个页面"]
        AG["内置 Agent 循环<br/>（function calling）"]
        TK["工具注册表 AGENT_TOOLS<br/>（由后端注册表自动注册）"]
        UI --- AG --- TK
    end

    subgraph Edge["本机（WSL2 + RTX 5060 Ti）· 便宜、可控"]
        BE["后端 lora_manager.py（FastAPI :8001）<br/>路由 / 文档工具 / 知识库 / 工作流调度"]
        WE["工作流引擎 workflow_engine.py<br/>节点类型 / 拓扑 / 分块 / 缓存"]
        VL["vLLM :8000<br/>Qwen3-4B-Instruct（base）<br/>+ LoRA 适配器"]
        KB["知识库 agent_kb/ + 向量检索"]
        FS["uploads/ · outputs/ · doc_flows/"]
        BE --- WE --- VL
        BE --- KB
        BE --- FS
    end

    subgraph Cloud["云 API（贵，只在必要时用）"]
        CL["openai 兼容端点<br/>如 deepseek-chat"]
    end

    TK -->|HTTP| BE
    AG -->|"原生 tools（云模型）"| CL
    AG -->|"文本协议（本地小模型）"| VL
    BE -->|"工具内部那次模型调用<br/>跟随当前选中模型"| CL
    BE -->|本地推理| VL
```

## 二、云边分工（省钱的根据）

| 环节 | 放哪 | 为什么 |
|---|---|---|
| **抽取 / 维度归纳**（工作流主力） | **本地 LoRA**（`extract_v2`、`dimensions_v1`） | 量大、可缓存、不需要云端智能 |
| **格式规范化**（`format_fix`） | **规则**（零模型） | 确定性、毫秒级、可复现 |
| **校对 / 术语 / 逻辑 / 翻译** | 取决于**当前选中的模型** | 选了云就用云；选本地就本地 |
| **Agent 的决策与工具选择** | 当前选中的模型 | 云模型明显更强（本地 4B 会退化成复述原文）|
| **文档读写 / 表格 / 导出** | **纯本地代码**（python-docx / openpyxl） | 不需要模型 |

**实测**：云端 token 中位 **30896 → 8198（−73.5%）**（趋势对照，限界见 `docs/BASELINE.md`）。

## 三、LoRA 挂在哪

```
vLLM 启动: --enable-lora --max-loras 2 --max-lora-rank 128 --max-model-len 32768
                    │
                    ├── base: Qwen3-4B-Instruct（FP16）
                    └── LoRA 适配器（按需热插拔，最多同时 2 个）
                          · extract_v2 / dimensions_v1   ← 生产用
                          · 其余为实验版（见 /loras）
```

- LoRA 是**挂在 vLLM 上**，不是挂在 FastAPI 上：`/loras/load` 调 vLLM 的 LoRA 接口。
- 工作流的**节点**通过 `lora` 字段指定用哪个适配器（`extract` 类节点）。
- **节点换 LoRA 不用重跑整条链**：Agent 里就是 `update_workflow_node(id, {lora: ...})`。

## 四、一次"文档编辑任务"的数据流（实测链路）

```mermaid
sequenceDiagram
    participant U as 用户
    participant A as 内置 Agent（云模型）
    participant B as 后端 :8001
    participant F as uploads/ 文件
    participant V as vLLM :8000（仅在选本地时）

    U->>A: "把论文作者改成 luxy、单位改个人、校对后导出 Word"
    A->>B: list_uploads()
    A->>B: read_upload(test1.txt)
    A->>B: replace_in_file(test1.txt, 作者：XXX → 作者：luxy)
    B->>F: 就地写回（首次自动备份 .orig.txt）
    A->>B: replace_in_file(test1.txt, 单位：XX大学… → 单位：个人)
    A->>B: proofread_file(test1.txt)
    B->>（云 或 V）: 工具内部那次模型调用（跟随当前选中模型）
    B->>F: 就地写回
    A->>B: structure_fix_file(test1.txt)   → 输出 Markdown 标题
    A->>B: export_docx(source=test1.txt, out_dir=E:\..., name=论文-编辑版.docx)
    B->>F: 生成 docx（Markdown # → Word Heading 样式）
    A->>U: 汇报改动 + 完整路径
```

## 五、一次"论文分析工作流"的数据流

```mermaid
flowchart LR
    P["论文（uploads/）"] --> IN["input 节点<br/>读入 + 切块"]
    IN --> KB["retrieval 节点<br/>知识库检索"]
    KB --> EX["extract 节点<br/>LoRA: extract_v2"]
    EX --> DI["extract 节点<br/>LoRA: dimensions_v1"]
    DI --> AG["aggregate 节点<br/>汇总成报告"]
    AG --> OUT["outputs/result_*.json<br/>+ 质量台账"]
    OUT -.->|"build_dataset"| DS["训练数据集"]
    DS -.->|"start_training"| L2["新 LoRA"]
    L2 -.->|"update_workflow_node"| EX
```

## 六、部署形态与端口

| 组件 | 端口 | 启动方式 |
|---|---|---|
| vLLM 底座 | 8000 | `scripts/start.sh`（异步加载）或「🎛️ 底座管理」页 |
| 后端 | 8001 | `scripts/start.sh` |
| 前端 | 8080 | `scripts/start.sh`（`python -m http.server`，纯静态） |

```
一键启动: 双击 启动工作台.bat（桌面 / E:\WSL\ / 仓库根目录 各一份）
停止:     双击 停止工作台.bat（默认保留底座）；全停用 scripts/stop.sh --all
```
