@echo off
chcp 65001 >nul
title LoRA 工作台 - 一键安装
echo ==================================================
echo  LoRA 工作台 · 一键安装
echo ==================================================
echo.
where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 wsl.exe —— 请先安装 WSL2。
  pause
  exit /b 1
)
echo 正在检测环境并安装依赖（首次可能几分钟，取决于网络）...
echo.
set "DISTRO=Ubuntu-24.04"
set "INNER=cd ~/paper_lora_project && bash scripts/install.sh"
wsl.exe -d %DISTRO% -- bash -lc "%INNER%" 2>nul
if errorlevel 1 (
  echo.
  echo [提示] 没有名为 %DISTRO% 的 WSL 发行版，改用【默认发行版】重试...
  wsl.exe -- bash -lc "%INNER%"
)
set RC=%errorlevel%
echo.
echo ==================================================
if "%RC%"=="0" (
  echo  安装完成 OK  接下来双击「启动工作台.bat」
) else (
  echo  [错误] 安装退出码 %RC% —— 请把上面的输出发我。
  echo  也可以试：wsl bash scripts/install.sh --minimal
)
echo ==================================================
pause
