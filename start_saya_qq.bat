@echo off
setlocal
cd /d "%~dp0"
title Saya + QQ
start "" powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_saya_qq.ps1" -Action panel
exit /b 0
