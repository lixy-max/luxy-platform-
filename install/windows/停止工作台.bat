@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
title Luxy Workbench - Stop

echo ==================================================
echo   Luxy Workbench - Stop
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
  echo [ERR] No usable WSL distro.
  pause
  exit /b 1
)
set "PROJDIR="
for /f "usebackq delims=" %%p in (`wsl.exe -d !DISTRO! -- bash -c "ls -d /home/*/paper_lora_project 2>/dev/null | head -1"`) do (
  set "PROJDIR=%%p"
)
if not defined PROJDIR (
  echo [ERR] project dir not found.
  pause
  exit /b 1
)
echo   WSL     : !DISTRO!
echo   Project : !PROJDIR!
echo.
echo [1/3] stopping keeper ...
wsl.exe -d !DISTRO! -- bash -c "pkill -f scripts/keeper.sh 2>/dev/null; pkill -f scripts/dev/keeper.sh 2>/dev/null; true"
echo [2/3] stopping backend and frontend ...
wsl.exe -d !DISTRO! --cd "!PROJDIR!" -- bash -c "if [ -f install/stop.sh ]; then BACKEND_PORT=8001 WEB_PORT=8080 bash install/stop.sh; else pkill -f lora_manager.py 2>/dev/null; pkill -f serve.py 2>/dev/null; true; fi"
echo [3/3] stopping vLLM ...
wsl.exe -d !DISTRO! -- bash -c "pkill -f vllm.entrypoints 2>/dev/null; true"
echo.
echo ==================================================
echo   STOPPED. vLLM needs 1-3 min to reload on next start.
echo ==================================================
pause
endlocal
