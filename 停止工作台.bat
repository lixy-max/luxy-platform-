@echo off
chcp 65001 >nul
title LoRA 工作台 - 停止
echo ==================================================
echo  LoRA 工作台 · 停止（Windows 双击即可）
echo ==================================================
echo.
echo  正在通过 WSL 停止：前端 + 后端（推理底座保留）
echo  （首次启动底座可能要几分钟，请耐心等待）
echo.

set "DISTRO=Ubuntu-24.04"
set "INNER=cd ~/paper_lora_project && bash scripts/stop.sh %*"
wsl.exe -d %DISTRO% -- bash -lc "%INNER%" 2>nul
if errorlevel 1 (
  echo.
  echo [提示] 没有名为 %DISTRO% 的 WSL 发行版，改用【默认发行版】重试...
  wsl.exe -- bash -lc "%INNER%"
)

echo.
echo ==================================================
echo  脚本已结束。若上面显示三个 ?，浏览器会自动打开。
echo  地址：http://127.0.0.1:8080/index3.html
echo  再次启动：双击「启动工作台.bat」
echo ==================================================
pause
