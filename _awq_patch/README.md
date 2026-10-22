# `_awq_patch` — AWQ 兼容 shim（可选，默认不启用）

## 它解决什么

某些 `autoawq` 版本引用了新版 `transformers` 已移除的激活函数类
（`transformers.activations` 里的旧名字），导致 AWQ 模型加载时报 `ImportError`。
`sitecustomize.py` 会在 Python 启动时打一个轻量补丁，把旧名字补回去。

## 何时需要

- 你用的是 **AWQ 量化权重**（如 `Qwen2.5-7B-Instruct-AWQ`）
- 且启动时报 `cannot import name 'xxx' from 'transformers.activations'`

## 如何启用

把本目录加进 `PYTHONPATH`（`sitecustomize.py` 会被 Python 自动导入）：

```bash
export PYTHONPATH="$PWD/_awq_patch:$PYTHONPATH"
python lora_manager.py
```

Docker 里：
```yaml
environment:
  - PYTHONPATH=/app/_awq_patch
```

## 注意

- **默认不启用**（未设置 `PYTHONPATH` 时 Python 不会加载它）
- 它只补齐缺失的名字，不改变计算逻辑
- 如果新版 transformers/autoawq 已修复该问题，可以删掉这个目录
