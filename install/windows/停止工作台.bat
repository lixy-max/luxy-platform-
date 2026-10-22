@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
title Luxy Workbench - Stop

echo ==================================================
echo   顿悟 luxy - 停止工作台
echo ==================================================
echo.

set "DISTRO="
wsl.exe -d Ubuntu-24.04 -- true >nul 2>nul
if not errorlevel 1 set "DISTRO=Ubuntu-24.04"
if not defined DISTRO (
  wsl.exe -d Ubuntu -- true >nul 2>nul
  if not errorlevel 1 set "DISTRO=Ubuntu"
)
if not defined DISTRO (
  echo [错误] 找不到可用 WSL 发行版
  pause
  exit /b 1
)

set "PROJDIR="
for /f "usebackq delims=" %%p in (`wsl.exe -d !DISTRO! -- bash -c "ls -d /home/*/paper_lora_project 2>/dev/null | head -1"`) do (
  set "PROJDIR=%%p"
)
if not defined PROJDIR (
  echo [错误] 找不到项目目录
  pause
  exit /b 1
)
echo   WSL: !DISTRO!
echo   项目: !PROJDIR!

echo.
echo [1/3] 停止 keeper ...
wsl.exe -d !DISTRO! -- bash -c "pkill -f 'scripts/keeper.sh' 2>/dev/null; pkill -f 'scripts/dev/keeper.sh' 2>/dev/null; true"
echo [2/3] 停止后端 / 前端 ...
wsl.exe -d !DISTRO! --cd "!PROJDIR!" -- bash -c "if [ -f install/stop.sh ]; then BACKEND_PORT=8001 WEB_PORT=8080 bash install/stop.sh; else pkill -f lora_manager.py 2>/dev/null; pkill -f 'serve.py 8080' 2>/dev/null; true; fi"
echo [3/3] 停止推理底座 vLLM ...
wsl.exe -d !DISTRO! -- bash -c "pkill -f vllm.entrypoints 2>/dev/null; true"
echo.
echo ==================================================
echo   已停止（底座下次启动需 1~3 分钟重新加载）
echo ==================================================
pause
endlocal