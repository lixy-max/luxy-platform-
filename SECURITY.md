# 安全策略

## 报告漏洞

**请勿公开开 issue。** 用 [GitHub Security Advisory](https://github.com/lixy-max/luxy-platform-/security/advisories/new)（仓库 → Security → Report a vulnerability），
或私下联系维护者。我们会在 7 天内回应。

请在报告中包含：影响范围、复现步骤、版本（`GET /version`）、是否已在公开环境暴露。

## 设计边界（请先读）

本平台是**单机自托管**工具，默认假设：

- 只监听 `127.0.0.1`，**不面向公网**
- **没有账号体系 / RBAC** —— 谁访问到端口就等于拥有全部权限
- 数据（知识库、LoRA、数据集、对话）全部留在本机

⇒ **不要直接把端口暴露到公网**。需要多用户时，请在前面加一层反向代理 + 认证。

## 密钥处理

- 所有 API Key 支持环境变量注入，优先级：
  `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `OLLAMA_API_KEY` / `VLLM_API_KEY` > `settings.json`
- `settings.json` 已被 `.gitignore` 忽略，**不要提交**
- 发布包内的 `settings.json` 已清空全部 Key
- CI 会跑 `scripts/check_no_secrets.sh`，命中即失败

## 已知的可接受风险

| 项目 | 说明 |
|---|---|
| 无鉴权 | 见上：单机自托管定位，不加鉴权是设计选择 |
| 浏览器 sidecar | 默认仅本机可用；如需跨机，请自行加 token（`X-Sidecar-Token`）与来源白名单 |
| 代码执行类工具 | `run_python` 等工具可在本机执行代码 —— 本质是本地自动化助手 |
