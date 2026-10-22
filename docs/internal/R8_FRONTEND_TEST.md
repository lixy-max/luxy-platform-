# R-8 前端实测报告（2026-10-21）

## 被测项
`webui/index3.html` 的 `_path.replace` 修复（提交 `005d438`）
- 位置: L7300 + L9507（2 处 ✓）
- 修复: `if (typeof _path === 'function') _path = _path(args)` + `String()` 兜底

## 实测方法
Playwright 打开 `http://127.0.0.1:8080/index3.html`，在页面上下文执行 4 个测试用例：

| # | 用例 | 输入 | 期望 | 实际 | 结果 |
|---|---|---|---|---|---|
| 1 | 字符串路径 | `'/kb/x'` | `'/kb/x'` | `'/kb/x'` | ✓ |
| 2 | **函数路径(有参)** | `(a) => \`/kb/${a.kb_name}/rebuild\`` | `'/kb/test/rebuild'` | `'/kb/test/rebuild'` | ✓ |
| 3 | 函数路径(无参) | `(a) => '/training/start'` | `'/training/start'` | `'/training/start'` | ✓ |
| 4 | 数字路径(异常) | `12345` | `'12345'` | `'12345'` | ✓ |

## 验证修复代码存在
- `hasFix`: **true** ✓
- `fixCount`: **2** ✓（两处修复都在页面里）

## Console 检查
- Errors: **0** ✓
- Warnings: 0 ✓

## 结论
**PASS ✓** —— 修复代码在页面中生效，函数类型路径可被正确处理，
异常类型（数字）有 `String()` 兜底，无 console 错误。

## 影响范围（已验证可修）
所有带参数的控制类工具：
- `kb_rebuild` / `kb_info` / `kb_delete` / `kb_ingest_folder`
- `train_status` / `workflow_latest` / ...
