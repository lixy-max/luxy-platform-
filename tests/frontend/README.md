# `tests/frontend` — 前端行为测试（Windows/WSL 侧 node）

## 怎么跑

```bash
# WSL 里（node 在 Windows 侧）
node "\\wsl.localhost\Ubuntu-24.04\home\luxy\paper_lora_project\tests\frontend\run.js"

# 或 Windows 命令行
node "\\wsl.localhost\Ubuntu-24.04\home\luxy\paper_lora_project\tests\frontend\run.js"
```

## 为什么 CI 跳过

`run.js` 用 Node 内置 `vm` + 手写 DOM 桩件加载 `webui/index3.html` 的内联脚本。
它能精确模拟浏览器的大部分行为，但：

- 桩件里没有 `MutationObserver` / `IntersectionObserver` 等浏览器 API
- Linux CI 的 `vm` 全局作用域与真实浏览器存在差异（实测出现过 `let` 变量 TDZ 误报）
- 该 harness 还依赖 `\wsl.localhost` 路径读文件（Windows 侧 node 才能无缝读）

所以在 `.github/workflows/ci.yml` 里用 `SKIP_FE_TESTS=1` 跳过。
**本地开发时仍应跑**，它能抓到真实的前端回归（已抓到过 `sessionSaveQueue` /
`currentSessionId` 的 TDZ bug）。

## 手动跑全部

```bash
SKIP_FE_TESTS=0 bash run_tests.sh     # 若本机有 node
```
