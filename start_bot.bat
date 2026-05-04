@echo off
chcp 65001 >nul
title 飞书机票监控机器人

:: ─── 配置区域 (请填入你的凭证) ─────────────────────
:: 方式1: 直接在此填写
:: set FEISHU_APP_ID=cli_xxxx
:: set FEISHU_APP_SECRET=your_secret_here

:: 方式2: 从 .env 文件加载 (推荐)
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%~dp0.env") do (
        set "%%a=%%b"
    )
)

:: Amadeus 航班API (可选，不填则用 --demo 模式)
:: set AMADEUS_CLIENT_ID=your_client_id
:: set AMADEUS_CLIENT_SECRET=your_client_secret

:: ─── 以下不用修改 ─────────────────────────────────
set BOT_DIR=%~dp0
set DEMO_FLAG=--demo

if not defined FEISHU_APP_ID (
    echo [错误] 未配置 FEISHU_APP_ID，请编辑此文件或创建 .env 文件
    echo 参考 .env.example
    pause
    exit /b 1
)

:: 如果配置了Amadeus，去掉demo标记
if defined AMADEUS_CLIENT_ID (
    if not "%AMADEUS_CLIENT_ID%"=="" (
        set DEMO_FLAG=
    )
)

echo =========================================
echo   飞书机票监控机器人 - 守护进程
echo =========================================
echo.
echo   如果机器人崩溃，将在 5 秒后自动重启
echo   按 Ctrl+C 停止
echo.

:loop
echo [%date% %time%] 启动机器人...
cd /d "%BOT_DIR%"
python feishu_flight_bot.py %DEMO_FLAG%

echo.
echo [%date% %time%] 机器人已停止 (退出码: %ERRORLEVEL%)
echo [%date% %time%] 5 秒后自动重启...
timeout /t 5 /nobreak >nul
goto loop
