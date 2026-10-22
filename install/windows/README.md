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

## 设计要点（都是踩过的坑）

| 问题 | 处理 |
|---|---|
| **多个 WSL 发行版**时回退到默认发行版（用户是 `a`，项目在 `luxy`）| 显式按 `Ubuntu-24.04` → `Ubuntu` 顺序探测，**绝不**用 `wsl.exe --` 裸调用 |
| 写死 `/home/luxy` | 在 WSL 里搜 `$HOME/paper_lora_project` 与 `/home/*/paper_lora_project` |
| 中文在 cmd 里变乱码 | 文件用 **GBK(936)** 编码 + `chcp 936`；`start "标题"` 只用 ASCII |
| 服务被 WSL 回收 | 启动后拉起 `keeper.sh`（占住会话 + 服务看护）|
| 停止误杀其他实例 | 调 `install/stop.sh`（强制显式端口）|

## 手动启动（不想用 .bat）

```bash
cd ~/paper_lora_project
bash scripts/start.sh        # 含推理底座
bash scripts/start.sh --no-gpu
bash install/stop.sh         # 或 bash scripts/stop.sh
```
