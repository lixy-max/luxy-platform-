# 发布包 v3 系列（2026-10-19）

## 版本演进（都是本次测试驱动修出来的 ✓）
| 版本 | 大小 | 修复内容 |
|---|---|---|
| v0.2.0 | 1,393,176 | 旧版（无 BACKEND_PORT / 缺 _tools.json ✗） |
| **v0.3.0** | 1,393,176 | 第 3 版基线（用户要求的"v3" ✓） |
| v0.3.1 | 1,394,775 | F-2: `agent_kb/_tools.json` 纳入版本控制（原来被 .gitignore ✗ 包不自洽 ✗） |
| v0.3.2 | — | F-1 部分: 后端读 BACKEND_PORT |
| v0.3.3 | — | F-1 部分: start.sh export（位置错 ✗ 无效） |
| **v0.3.4** | **最终 ✓** | **F-1 完整: start.sh env 显式传 BACKEND_PORT + WORKBENCH_HOST ✓** |

## ✅ v0.3.4 新环境验证（★ 干净目录解压 + 备用端口 ✓）
```
解压: 261 文件 ✓
测试套件: 828 tests OK (skipped=1) ✓（干净环境 ✓ 说明包自洽 ✓）
启动: bash scripts/start.sh --no-gpu（BACKEND_PORT=18001 FRONTEND_PORT=18080 ✓）
  后端就绪 1-2 秒 ✓
  ① 后端 /health → {"status":"ok"} ✓
  ② 前端 index3.html → 200（title "LoRA 工作台" ✓）
  ③ /docs/tool/list → 48 个工具 ✓
  ④ /kb/list → {"knowledge_bases":[]}（干净环境 ✓ 正常 ✓）
  ⑤ /browser/sidecar-token → {"ok":true,...} ✓
  ⑥ 清理后主服务未受影响 ✓
```

## 启动包（安装目录 + 快照 ✓）
- `check_sync.sh` 三处一致: 发布包 / 安装目录 / 快照 ✓
- 启动脚本: `bash scripts/setup.sh && bash scripts/run.sh`（或 `scripts/start.sh` ✓）
- 端口可覆盖: `BACKEND_PORT` / `FRONTEND_PORT` / `VLLM_PORT` ✓（多实例部署 ✓）
- `WORKBENCH_HOST` 默认 127.0.0.1（局域网需显式 0.0.0.0 ✓）

## ⚠ 已知依赖（新环境必读）
1. **浏览器 sidecar** 不在包里（Windows 专用 ✓）→ 需在 Windows 侧 `cd browser_sidecar && node server.mjs` ✓
   token 会生成 `.sidecar_token` ✓ 后端 `/browser/sidecar-token` 转发给页面 ✓
2. KB 的 embedding 模型需与建索引时一致 ✓（`KB_EMBED_MODEL` ✓ 默认 bge-small-zh ✓）
3. 干净环境没有 KB（`data/kbs/` 不在包里 ✓）→ 首次需自行建库/入库 ✓
