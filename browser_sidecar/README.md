# 浏览器自动化 sidecar（Node · 运行在 Windows ✓）

> 依据 `docs/BROWSER_EXEC_PLAN.md` Step 1 实现 ✓
> 为什么跑在 Windows: Edge 和 node 只在 Windows ✓ WSL 没 node ✗

## 启动
```bash
# Windows 侧（任选其一）
node server.mjs              # 常驻服务（127.0.0.1:9223）
node server.mjs --selftest   # 自检: 打开 example.com → 快照 → 截图 ✓
```

## 连接策略（两级 ✓）
```
① 优先 connectOverCDP('http://127.0.0.1:9222') 连用户已开的 Edge（复用登录态 ✓）
② 连不上则 chromium.launch({channel:'msedge'}) 自己拉起 ✓
★ 只新开 tab ✓ 绝不碰用户已有标签 ✗
```

## 接口（12 个动作 ✓）
```
GET  /health · GET /actions
POST /act/<name>   {args}   → {ok, ...}   永远 200 ✓ 失败带 error ✓ 让模型看到原因 ✓
动作: open snapshot click fill read screenshot download upload move wait tabs close
```

## 设计要点（都是踩坑/借鉴来的 ✓）
- **元素清单只给可见+可交互 ≤80 个** ✓ role+name+ref ✓ 不返回整页 HTML ✗（省 token ✓）
- **页面文本包 `<<<EXTERNAL_CONTENT>>>` 标记** ✓（防提示注入 ✓ 借鉴 Nanobrowser injection-context ✓）
- **站点策略** `policy.json`: allow / ask / block ✓ 默认 ask ✓
- **上传只允许 `uploads/` 下文件** ✓（防任意读盘 ✗）
- **密码字段只标 `[password]` 不取值** ✓
- 动作串行执行 ✓（同一页面并发点会乱 ✓）

## 访问 token（安全机制）

sidecar 启动时会生成一个 48 位访问 token，**平台每次调用都要带上**（防 CSRF）。

**token 文件位置（2026-10-23 统一）**：
```
<项目>/browser_sidecar/.sidecar_token      ← 默认（Windows 与 WSL 看到同一个文件 ✓）
```
- `TOKEN_FILE` 环境变量可覆盖（特殊部署）
- **为什么不放 `~/.luxy/`**：sidecar 跑在 Windows（`C:\Users\<user>`），平台跑在 WSL
  （`/home/<user>`）—— 两个 home 不是同一个地方。放项目目录两边都能访问同一个文件 ✓

平台侧读取顺序（`lora_manager._sidecar_token_path`）：
```
① 环境变量 BROWSER_SIDECAR_TOKEN
② <项目>/browser_sidecar/.sidecar_token     ← 首选（由 __file__ 推导，不写死路径）
③ ~/.luxy/sidecar_token
④ ~/browser_sidecar/.sidecar_token
```

## 环境变量
```
PORT=9223  CDP_ENDPOINT=http://127.0.0.1:9222
PW_CORE=<playwright-core 路径>   # 默认复用 E:/WSL/_check/drill/node_modules/playwright-core ✓
SHOT_DIR / DL_DIR / UL_DIR · MAX_REFS=80
```

## 已验证（2026-10-19 ✓）
```
selftest: 拉起 Edge → example.com ✓ title/refs ✓ 截图 53,188 bytes ✓
HTTP: /health ✓ /actions(12) ✓ /act/open ✓ /act/screenshot ✓ /act/snapshot（含防注入标记 ✓）
```
