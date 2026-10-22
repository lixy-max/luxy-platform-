# 第三方依赖与其许可证

本项目直接依赖下列库（见 `requirements.txt`）。许可证信息由**已安装包的元数据**读取生成，
不是手工填写；若某个包未标注许可证，请以该包自身的 LICENSE 文件为准。

| 包 | 版本约束 | 许可证 | 已安装版本 |
|---|---|---|---|
| `pillow` | `pillow==12.3.0` | 未标注 | 12.3.0 |
| `chromadb` | `chromadb==1.5.9` | Apache Software License | 1.5.9 |
| `python-docx` | `python-docx==1.2.0` | MIT | 1.2.0 |
| `fastapi` | `fastapi==0.136.3` | 未标注 | 0.136.3 |
| `openpyxl` | `openpyxl==3.1.5` | MIT | 3.1.5 |
| `pdfplumber` | `pdfplumber==0.11.10` | MIT License | 0.11.10 |
| `pydantic` | `pydantic==2.12.3` | 未标注 | 2.12.3 |
| `pypdf` | `pypdf==6.19.0` | 未标注 | 6.19.0 |
| `pypdfium2` | `pypdfium2==5.13.0` | BSD-3-Clause, Apache-2.0, dependency licenses | 5.13.0 |
| `rapidocr-onnxruntime` | `rapidocr-onnxruntime==1.4.4` | Apache-2.0 | 1.4.4 |
| `requests` | `requests==2.34.2` | Apache-2.0 | 2.34.2 |
| `safetensors` | `safetensors==0.8.0` | Apache Software License | 0.8.0 |
| `sentence-transformers` | `sentence-transformers==4.1.0` | Apache 2.0 | 4.1.0 |
| `transformers` | `transformers==4.56.2` | Apache 2.0 License | 4.56.2 |
| `uvicorn` | `uvicorn==0.54.0` | 未标注 | 0.54.0 |

## 说明

- 这些都是**运行期直接依赖**，它们的传递依赖未逐条列出；
  发布二进制/容器前建议用专门的 SBOM 工具再生成一份完整清单。
- 若某条许可证缺失，属于该上游包元数据不全，不代表没有许可证。

## 运行时可选依赖（不在 requirements.txt 内，需自行安装）

| 组件 | 用途 | 许可证 | 说明 |
|---|---|---|---|
| **vLLM** | 本地推理服务（OpenAI 兼容 API）| **Apache-2.0** | 可选。GPU 推理需自行 `pip install vllm==0.11.0`；不装则本地推理不可用，其余功能正常 |
| **LLaMA-Factory** | QLoRA 训练执行器 | **Apache-2.0** | 可选。训练功能依赖；不装则「训练」页不可用 |
| **Playwright / playwright-core** | 浏览器自动化 sidecar | **Apache-2.0** | 可选。`browser_sidecar/` 下 Node 侧；不装则浏览器工具不可用 |
| **BGE 中文嵌入模型**（`BAAI/bge-small-zh-v1.5` 等）| 知识库向量化 | **MIT**（BAAI 模型卡声明）| 首次建库时自动下载；模型本身不入仓库。使用前请核对具体模型卡的许可 |

## 前端 vendor（`webui/vendor/`，已随仓库分发）

| 库 | 用途 | 许可证 | 许可证文件 |
|---|---|---|---|
| **marked** | Markdown 渲染 | MIT | `webui/vendor/LICENSE-marked.txt` |
| **highlight.js** | 代码高亮 | BSD-3-Clause | `webui/vendor/LICENSE-highlightjs.txt` |
| **DOMPurify** | HTML 消毒 | Apache-2.0 OR MPL-2.0 | `webui/vendor/LICENSE-dompurify.txt` |
| **Lucide** | 图标 | ISC | `webui/vendor/LICENSE-lucide.txt` |

以上四个库均为**未修改**的原样分发（minify 版本），许可证文件随目录一起保留。

> 本表由脚本自动生成初版 + 人工补全可选依赖。如有遗漏，请提 PR。
