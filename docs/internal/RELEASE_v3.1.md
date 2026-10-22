# 发布包 v3.1（`v0.3.5`）· 2026-10-19

> 本版 = 前端优化 + 安全修复 + 评估方法论定案 的集成发布
> 产物：`release/paper-lora-workbench-v0.3.5.zip`（1.46 MB · 285 文件 · 闸门通过 ✓）
> 备份：`D:\paper-lora-backups\paper-lora-workbench-v0.3.5_2026-10-19.zip`（md5 已校验 ✓）

## 一、本版包含的优化（相对 v3 系列 v0.3.0）

### 🔒 安全（P0 清零 ✓）
| 项 | 内容 |
|---|---|
| S-1 | 真实密码泄露 5 处全部清理 ✓ + `redact_secrets()` 脱敏 ✓ |
| S-2 | sidecar 加 token 鉴权 ✓ + CORS 白名单 ✓（原来任意网页可驱动浏览器 ✗） |
| S-3 | 后端默认绑 `127.0.0.1` ✓（原来 `0.0.0.0` 无鉴权 ✗） |
| S-4 | `agent_use` 知识库写保护（约定级 ⚠） |

### ⚡ 前端性能（首屏快 18 倍 ✓）
| 项 | 前 | 后 |
|---|---|---|
| 首屏 DOMContentLoaded | 984 ms ✗ | **53 ms** ✓ |
| CDN 依赖 | 3 个（jsdelivr ✗ 弱网崩 ✗） | **0 个**（`webui/vendor/` 本地化 ✓） |
| 缓存策略 | 全站 no-store ✗ | vendor/js/css `immutable` ✓ · HTML `no-store` ✓ |
| vendor 最大耗时 | 879 ms ✗ | **5 ms** ✓ |

### 🛡️ XSS 防护（原来 0 防护 ✗）
- `marked` 输出经 **DOMPurify allowlist** 净化 ✓
- 链接强制 `rel="noopener noreferrer"` ✓ + 禁 `javascript:` ✓
- 无 DOMPurify 时**降级纯文本**✓（安全优先 ✓）
- **实测 4 个攻击载荷全部拦截** ✓（`<img onerror>` / `<script>` / `javascript:` / `<svg onload>`）

### 🧩 前端模块化（E-1 · index3.html 9,125 → 8,594 行）
| 模块 | 内容 |
|---|---|
| `webui/api.js` | 统一 API 层（超时/重试/统一错误 ✓ `tryGet/tryPost/tryPut/tryDel` ✓）· 已迁移 28 处 fetch ✓ |
| `webui/pages/history.js` | 历史记录页 ✓ |
| `webui/pages/settings.js` | 设置页 ✓ |
| `webui/pages/kb.js` | 知识库页（211 行 ✓） |
| `webui/pages/train.js` | 训练页 ✓ |
| `webui/pages/base.js` | 底座页 ✓ |
| `webui/pages/provider.js` | Provider 配置 ✓ |

### 🧪 测试（原来 0 个 E2E ✗）
- `tools/e2e_smoke.mjs`：**10 条 E2E** ✓（页面加载/路由/markdown/XSS/工具/主题 ✓）
- **headless + `--disable-gpu` ⇒ 零显存占用** ✓（实测显存不变 ✓）
- 前端断言 **375/375** ✓（含 `api-layer-loaded` / `page-modules-loaded` 等新断言 ✓）
- 后端 **828 tests OK** ✓

### 🐛 修掉的平台 bug
| Bug | 内容 |
|---|---|
| P-57 | sidecar 反复无声退出 → 崩溃兜底 + 断开自动重连 ✓ |
| P-60 | 跑批静默丢任务（占位行被当成绩 ✗）→ 占位标记 + 自动重跑器 ✓ |
| P-61 | vLLM 起不来 → 真因 Windows 应用占 8.8GB 显存 ✗ → 默认 `gpu_memory_utilization` 0.45/0.55 ✓ |
| P-62 | 跑手无自愈 → `run_retry.mjs` 自动重跑 ✓ |
| 时序 bug | 启动调用在 `pages/*.js` 之前 ⇒ ReferenceError ✗（E2E 抓到 ✓ 已修 ✓） |

### 📊 评估方法论（M-1 定案 ✓）
- 5 次重复跑 + **A/B 混跑**（同一次跑内每任务两臂 ✓ 漂移抵消 ✓）
- **两个独立方法互证：+5.28（t=2.03）/ +5.76（t=2.21）** ✓
- 结论：闸门⑤ 提升**真实存在**（约 +5 分 · 效应集中 · 不增加耗时）✓
- ❌ 不可声称：精确 +6.45 / +12pp / "稳定"（t 仅 2.0~2.2 临界 ⚠）
- 详见 `docs/EVAL_REPEATABILITY.md` · `docs/AB_RESULT.md`

## 二、新环境验证（干净目录 ✓ 备用端口 ✓ 未占 GPU ✓）

```
解压: 285 文件 ✓
测试: 828 tests OK (skipped=1) ✓
启动: bash scripts/start.sh --no-gpu（BACKEND_PORT=18001 FRONTEND_PORT=18080）
  后端 2 秒就绪 ✓
  ① /health → {"status":"ok"} ✓
  ② index3.html → 200 ✓
  ③ /docs/tool/list → 48 个工具 ✓
  ④ /kb/list → 空库（干净环境 ✓）
  ⑤ vendor/marked.min.js → 200 · 35,159 B ✓
  ⑥ pages/kb.js → 200 · 10,567 B ✓
  ⑦ 缓存 → public, max-age=31536000, immutable ✓
  ⑧ index3.html → 8,594 行 ✓
清理: 测试实例全停 ✓ 主服务未受影响 ✓
```

## 三、启动方式

```bash
# 解压后
bash scripts/setup.sh && bash scripts/run.sh
# 或
bash scripts/start.sh            # 含推理底座（需 GPU ✓）
bash scripts/start.sh --no-gpu   # 仅 KB/规则模式（不占 GPU ✓）
```

**端口可覆盖**：`BACKEND_PORT` / `FRONTEND_PORT` / `VLLM_PORT` ✓（多实例 ✓）
**局域网**：需显式 `WORKBENCH_HOST=0.0.0.0` ✓（默认仅本机 ✓ 安全 ✓）

## 四、已知限制（诚实标注 ✓）

1. **vLLM 显存**：默认 0.55（32K 上下文 ✓）；若 Windows 图形应用占用多 ⇒ 可能起不来 ⚠ 需关掉部分应用或用 0.45（上下文降为 ~8K）
2. **agent-module 未拆分**（3,600 行 IIFE ✗）：按行号抽取尝试失败 ✗ 已回滚 ✓ 正确做法见 `docs/AGENT_MODULE_SPLIT_PLAN.md` §七
3. **fetch 迁移 28/164**：剩余按页逐步迁移 ✓
4. **a11y 未做**（aria 属性 0 ✗）：Phase 2 计划中 ✓

## 五、备份

```
D:\paper-lora-backups\
  ├─ paper-lora-workbench-v0.3.5_2026-10-19.zip   （1.46 MB · md5 C5C9FEA7… ✓ 与源一致 ✓）
  └─ docs_2026-10-19\                             （6 份关键文档 ✓）
     ├─ AB_RESULT.md
     ├─ EVAL_REPEATABILITY.md
     ├─ AGENT_MODULE_SPLIT_PLAN.md
     ├─ FRONTEND_OPT_PLAN.md
     ├─ PLATFORM_ISSUES.md
     └─ HANDOVER.md
```
