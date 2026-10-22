#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 requirements.txt：**从真实 import 反推**（不靠手工列，避免漏依赖），
再查当前环境里这些包的实际版本并钉住。

为什么这么做：
- `pip freeze` 会把间接依赖也写死（升级困难、与别人的环境冲突）
- 手工列会漏（例如 docx 的包名是 python-docx、PIL 是 pillow）
"""
import ast
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "data", "uploads", "outputs", "loras", "training_output",
             "papers", "cache", "__pycache__", "venv", ".venv", "node_modules",
             "archive", "doc_flows"}

# import 名 -> pip 包名（只列与 import 名不同的）
IMPORT_TO_PIP = {
    "docx": "python-docx",
    "PIL": "pillow",
    "fitz": "pymupdf",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "pypdfium2": "pypdfium2",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime",
    "sentence_transformers": "sentence-transformers",
    "chromadb": "chromadb",
    "transformers": "transformers",
    "peft": "peft",
    "datasets": "datasets",
    "uvicorn": "uvicorn",
    "fastapi": "fastapi",
    "pdfplumber": "pdfplumber",
    "pypdf": "pypdf",
    "openpyxl": "openpyxl",
    "requests": "requests",
    "torch": "torch",
    "vllm": "vllm",
    "huggingface_hub": "huggingface-hub",
    "llamafactory": "llamafactory",
    "modelscope": "modelscope",
}

# 这些是"运行环境自带/可选"，不作为强制依赖
OPTIONAL = {"vllm", "torch", "llamafactory", "modelscope", "datasets"}


def top_level_imports(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                out.add(node.module.split(".")[0])
    return out


# 隐式运行时依赖：不被本项目直接 import，但缺了就跑不起来。
# 干净环境实测：缺 python-multipart 时 lora_manager 导入即失败（FastAPI 文件上传路由）。
IMPLICIT_DEPS = {
    "python-multipart": "FastAPI 的文件上传路由（/loras/upload、/chat/upload）需要",
}


def main():
    found = set()
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        found |= top_level_imports(p)

    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    local = {p.stem for p in ROOT.glob("*.py")} | {"tests", "webui", "scripts"}

    reqs, optional = [], []
    for name in sorted(found):
        if name in stdlib or name in local or name.startswith("_"):
            continue
        pip_name = IMPORT_TO_PIP.get(name, name)
        try:
            v = version(pip_name)
        except PackageNotFoundError:
            continue          # 没装 = 不是这个项目的直接依赖
        line = f"{pip_name}=={v}"
        (optional if name in OPTIONAL else reqs).append(line)
    return reqs, optional


if __name__ == "__main__":
    reqs, optional = main()
    print("# 本项目直接依赖（从真实 import 反推 + 当前环境实测版本钉住）")
    print("# 由 scripts/gen_requirements.py 生成")
    for r in reqs:
        print(r)
    if optional:
        print()
        print("# 可选：推理/训练栈（体积大，按需安装；见 README）")
        for r in optional:
            print(f"# {r}")
