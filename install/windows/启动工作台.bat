@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
title Luxy Workbench - Start

echo ==================================================
echo   顿悟 luxy - 工作台启动
echo ==================================================
echo.

where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 wsl.exe，请确认已安装 WSL2
  pause
  exit /b 1
)

rem ---------- 选择 WSL 发行版（优先 Ubuntu-24.04，绝不回退默认发行版）----------
set "DISTRO="
wsl.exe -d Ubuntu-24.04 -- true >nul 2>nul
if not errorlevel 1 set "DISTRO=Ubuntu-24.04"
if not defined DISTRO (
  wsl.exe -d Ubuntu -- true >nul 2>nul
  if not errorlevel 1 set "DISTRO=Ubuntu"
)
if not defined DISTRO (
  echo [错误] 找不到可用 WSL 发行版（试过 Ubuntu-24.04 / Ubuntu）
  echo        请运行 wsl.exe -l -v 查看
  pause
  exit /b 1
)
echo   WSL 发行版: !DISTRO!

rem ---------- 定位项目（不写死用户名）----------
set "PROJDIR="
for /f "usebackq delims=" %%p in (`wsl.exe -d !DISTRO! -- bash -c "ls -d /home/*/paper_lora_project 2>/dev/null | head -1"`) do (
  set "PROJDIR=%%p"
)
if not defined PROJDIR (
  echo [错误] 在 !DISTRO! 里找不到 paper_lora_project（已搜 /home/*/）
  pause
  exit /b 1
)
echo   项目目录: !PROJDIR!
echo   端口: 后端 8001 / 前端 8080 / 底座 8000
echo   （底座异步加载，1~3 分钟后可用，不必等）
echo.

echo [启动] bash scripts/start.sh ...
wsl.exe -d !DISTRO! --cd "!PROJDIR!" -- bash -lc "bash scripts/start.sh"
set "RC=!errorlevel!"

echo.
if "!RC!"=="0" (
  echo ==================================================
  echo   启动完成  打开: http://127.0.0.1:8080/index3.html
  echo ==================================================
) else (
  echo ==================================================
  echo   [错误] 启动脚本退出码 !RC!
  echo ==================================================
)

echo.
echo [keeper] 启动后台看护（最小化窗口，请勿关闭）...
start "LuxyKeeper" /min wsl.exe -d !DISTRO! --cd "!PROJDIR!" -- bash -lc "if [ -f scripts/keeper.sh ]; then exec bash scripts/keeper.sh; else exec bash scripts/dev/keeper.sh; fi"

echo.
echo   停止: 双击「停止工作台.bat」
echo   日志: 见 WSL 里 !PROJDIR!/logs/
echo.
pause
endlocal