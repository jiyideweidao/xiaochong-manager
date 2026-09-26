@echo off
chcp 65001 >nul
echo 正在关闭 小虫管理器（源码模式）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
timeout /t 2 >nul
exit
