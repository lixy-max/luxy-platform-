# Windows 启动台（`启动工作台.bat` / `停止工作台.bat`）

## 为什么放这里

这两个 `.bat` 是 Windows 侧的入口，**随仓库版本走**。
以前它们被复制到桌面后就不再更新，导致"启动台没同步"——旧脚本引用了已移动的路径
（比如 `scripts/keeper.sh` 曾被移到 `scripts/dev/`），于是启动失败。

## 安装到桌面

在 Windows PowerShell 里：

```powershell
$repo = "\\wsl.localhost\Ubuntu-24.04\home\luxy\paper_lora_project\install\windows"
$desk = [Environment]::GetFolderPath('Desktop')
Copy-Item "$repo\启动工作台.bat" $desk -Force
Copy-Item "$repo\停止工作台.bat" $desk -Force
```

或者直接把这两个文件拖到桌面。

## 启动后会自动做什么

1. 选择 WSL 发行版（`Ubuntu-24.04` → `Ubuntu`，**绝不**用 `wsl.exe --` 裸调用）
2. 在 WSL 里自动定位项目目录（不写死用户名）
3. 执行 `bash scripts/start.sh --no-browser`（后端 + 前端 + 异步拉起 vLLM）
4. **在 Windows 侧打开浏览器** `http://127.0.0.1:8080/index3.html`
5. 拉起 `keeper.sh`（占住 WSL 会话 + 服务看护）

### 不想自动开浏览器

```bat
启动工作台.bat nobrowser
```

## 设计要点（全部是踩过的坑）

| 问题 | 处理 |
|---|---|
| **多个 WSL 发行版**时回退到默认发行版（用户是 `a`，项目在 `luxy`）| 显式按 `Ubuntu-24.04` → `Ubuntu` 顺序探测，**绝不**裸调 `wsl.exe --` |
| 写死 `/home/luxy` | 在 WSL 里搜 `/home/*/paper_lora_project` |
| **中文乱码 / rem 注释被当命令执行** | UTF-8 无 BOM + `chcp 65001` → cmd 多字节解析失败；<br>加 BOM 后又导致 `@echo off` / `chcp` 被跳过<br>⇒ **最终用纯 ASCII（0 个非 ASCII 字节）+ 无 BOM**，中文信息由 WSL 侧输出 |
| **`. was unexpected at this time.`** | cmd 在 `if (...)` 块内把未转义 `(` `)` 当块边界 ⇒ 所有 `echo` 行去掉括号 |
| **WSL 里打不开 Windows 浏览器** | 本机 WSL interop 不可用 ⇒ 由 `.bat` 在 Windows 侧 `start "" <url>` 打开 |
| 服务被 WSL 回收 | 启动后拉起 `keeper.sh`（占住会话 + 服务看护）|
| 停止误杀其他实例 | 调 `install/stop.sh`（强制显式端口）|

## keeper 单实例（`scripts/keeper.sh`）

踩过 3 轮才收敛，记录在此避免重犯：

| 尝试 | 失败原因 |
|---|---|
| pid 文件判断 | `stop` 杀进程后 trap 未执行，文件残留 ⇒ 误判"已在运行" |
| `pgrep -f keeper.sh` | `nohup`/`setsid` 的 wrapper cmdline 也含脚本名 ⇒ 新实例把父进程当已有实例，永远起不来 |
| `flock` 文件锁 | keeper 派生的子进程**继承锁 fd** ⇒ 父进程被 `kill -9` 后子进程仍持锁 ⇒ 重启误判 |
| **最终：pid 文件 + `/proc/<pid>/cmdline` 校验** | ✓ 无继承问题、无 wrapper 误判、可防 pid 复用 |

另外两处必要修正：
- `trap` 必须带 `exit 0`（旧写法只删文件不退出，导致 stop 杀不死）
- 主循环用 `sleep 20 & wait $!`（可被信号立即打断，否则 TERM 要等睡完 20 秒）

## 手动启动（不想用 .bat）

```bash
cd ~/paper_lora_project
bash scripts/start.sh        # 含推理底座
bash scripts/start.sh --no-gpu
bash install/stop.sh         # 或 bash scripts/stop.sh
```
