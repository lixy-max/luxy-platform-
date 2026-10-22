@echo off
chcp 65001 >nul
title LoRA 工作台 - 一键启动
echo ==================================================
echo  LoRA 工作台 · 一键启动
echo ==================================================
echo.

where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 wsl.exe —— 请确认已装 WSL2。
  echo.
  pause
  exit /b 1
)

echo 正在通过 WSL 启动：后端 + 前端 + 推理底座
echo （底座是异步加载的，1~3 分钟后才可用，不需要等在这里）
echo.

set "DISTRO=Ubuntu-24.04"
set "INNER=cd ~/paper_lora_project && bash scripts/start.sh"
wsl.exe -d %DISTRO% -- bash -lc "%INNER%"

rem ★ 再起一个 keeper 会话：WSL2 在最后一个会话结束时会回收发行版，
rem   把服务一起带走（实测：启动器跑完约 1 分钟后三端口全 000，dmesg 见 EXT4 unmount/mount）。
rem   keeper 常驻既能占住会话，又会在服务掉了时自动拉起 —— 它不是"开机自启"，
rem   只有你手动双击本文件时才会出现。
start "工作台 keeper（请勿关闭此窗口）" /min wsl.exe -d %DISTRO% -- bash -lc "cd ~/paper_lora_project && exec bash scripts/keeper.sh" 2>nul
if errorlevel 1 (
  echo.
  echo [提示] 没有名为 %DISTRO% 的 WSL 发行版，改用【默认发行版】重试...
  wsl.exe -- bash -lc "%INNER%"
)
set RC=%errorlevel%

echo.
echo ==================================================
if "%RC%"=="0" (
  echo  启动脚本执行完成（返回 0）
  echo.
  echo  地址：http://127.0.0.1:8080/index3.html
  echo  若浏览器显示"拒绝连接"：等 2 秒后刷新一次；
  echo  若仍打不开，请看上面的输出里"后端/前端"是否 200。
) else (
  echo  [错误] 启动脚本退出码 %RC% —— 请把上面的输出截图/复制给我。
)
echo.
echo  停止：双击「停止工作台.bat」
echo ==================================================
pause
