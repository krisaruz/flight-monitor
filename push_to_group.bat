@echo off
chcp 65001 >nul
title 机票搜索推送

:: 在项目目录创建 .env，填写 FEISHU_WEBHOOK（配置由 Python 的 load_env 加载）
cd /d "%~dp0"

:: 可选：覆盖默认搜索条件（亦可在命令行传参，见 python push_to_group.py -h）
set PUSH_ORIGIN=香港
set PUSH_DEST=大阪
set PUSH_START=2026-04-20
set PUSH_END=2026-06-30
set PUSH_DAYS=5
set PUSH_TOP=10
:: set USE_DEMO=0
:: set AMADEUS_CLIENT_ID=
:: set AMADEUS_CLIENT_SECRET=

echo.
echo   正在搜索并推送到飞书群...
echo.

python push_to_group.py

echo.
if %ERRORLEVEL%==0 (echo   完成) else (echo   失败，错误码 %ERRORLEVEL%)
echo.
pause
