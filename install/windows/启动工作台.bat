@echo off
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
title Luxy Workbench - Start

echo ==================================================
echo   Luxy Workbench - Start
echo ==================================================
echo.
where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo [ERR] wsl.exe not found. Please install WSL2.
  pause
  exit /b 1
)

rem ---- pick WSL distro: prefer Ubuntu-24.04, never fall back to bare wsl.exe
set "DISTRO="
wsl.exe -d Ubuntu-24.04 -- true >nul 2>nul
if not errorlevel 1 set "DISTRO=Ubuntu-24.04"
if not defined DISTRO (
  wsl.exe -d Ubuntu -- true >nul 2>nul
  if not errorlevel 1 set "DISTRO=Ubuntu"
)
if not defined DISTRO (
  echo [ERR] No usable WSL distro. Tried Ubuntu-24.04 and Ubuntu.
  echo       Run command: wsl.exe -l -v
  pause
  exit /b 1
)
echo   WSL distro : !DISTRO!

rem ---- locate project dir, do not hardcode username
set "PROJDIR="
for /f "usebackq delims=" %%p in (`wsl.exe -d !DISTRO! -- bash -c "ls -d /home/*/paper_lora_project 2>/dev/null | head -1"`) do (
  set "PROJDIR=%%p"
)
if not defined PROJDIR (
  echo [ERR] paper_lora_project not found inside !DISTRO!
  echo       Searched under /home/*/
  pause
  exit /b 1
)
echo   Project    : !PROJDIR!
echo   Ports      : backend 8001 / frontend 8080 / vLLM 8000
echo   vLLM loads asynchronously, ready in 1-3 min. No need to wait.
echo.

echo [start] bash scripts/start.sh ...
wsl.exe -d !DISTRO! --cd "!PROJDIR!" -- bash -lc "bash scripts/start.sh"
set "RC=!errorlevel!"

echo.
if "!RC!"=="0" (
  echo ==================================================
  echo   STARTED - open http://127.0.0.1:8080/index3.html
  echo ==================================================
) else (
  echo ==================================================
  echo   [ERR] start.sh exit code !RC!
  echo ==================================================
)

echo.
echo [keeper] starting background keeper. Minimized window, keep it open.
start "LuxyKeeper" /min wsl.exe -d !DISTRO! --cd "!PROJDIR!" -- bash -lc "if [ -f scripts/keeper.sh ]; then exec bash scripts/keeper.sh; else exec bash scripts/dev/keeper.sh; fi"

echo.
echo   Stop : double-click the stop-workbench bat file
echo   Logs : !PROJDIR!/logs/  inside WSL
echo.
pause
endlocal
