# 数据、模型与版权说明

## 本仓库**不包含**什么

- **不包含任何论文、文档或用户上传的数据**（`uploads/` 被 gitignore）
  - 说明：v4.9.7 已删除此前唯一的格式示例文件 `corrected_papers/example_paper_21_corrected.docx`
    与内部产物 `docs/artifacts/论文输入修复要点清单.docx`，仓库内**不再有任何 .docx/.pdf 文档**
  - 如需文档处理示例，请自行准备素材；工具支持用户本地文件
- **不包含任何模型权重**（`models/`、`*.safetensors`、`*.gguf` 等被 gitignore）
- **不包含任何 API 密钥**（`settings.json`、`.env` 被 gitignore；发布前由
  `scripts/check_no_secrets.sh` 扫描校验）
- **不包含**本地知识库、缓存、运行产物与历史库（`data/`、`cache/`、`outputs/` 被 gitignore）

仓库里与"数据"有关的只有**预设工作流配置**（`workflow_config.json`、
`doc_flows/*.json`），它们是**流程定义**，不含任何真实文献内容。

## 你运行时的数据归谁

| 数据 | 位置 | 归属 |
|---|---|---|
| 你上传的论文/表格/文档 | `uploads/` | **你自己的**，本软件不上传任何地方 |
| 转换出的文本、缓存、运行结果 | `outputs/`、`cache/` | 本地生成，随你处置 |
| 对话历史与任务记录 | `data/workbench.db` | 本地 SQLite，不上传 |
| 你的 API Key | `settings.json` | 仅存本地；请勿提交到任何仓库 |

**关于模型调用**：当你使用云端 provider（如 OpenAI 兼容接口）时，**被分析文档的相关内容
会随请求发送给你配置的服务商**——这是该 provider 的常规行为，请自行确认其数据政策。
使用本地 vLLM 时数据不离开本机。

## 引用的第三方模型

本项目**不打包**任何模型，只在其 README/脚本里说明如何自行获取（例如 Qwen 系列）。
这些模型各有自己的许可证，**以模型发布方的许可为准**；请在使用前自行阅读。

## 许可证

本仓库代码采用 **MIT**（见 [LICENSE](LICENSE)）。第三方依赖的许可证见
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
