@echo off
chcp 65001 >nul
title 飞书机票监控机器人

:: 在项目目录创建 .env（参考 .env.example）
:: Python 会加载 .env；若未配置 Amadeus 则会自动使用演示数据

set BOT_DIR=%~dp0

echo =========================================
echo   飞书机票监控机器人 - 守护进程
echo =========================================
echo   崩溃后 5 秒自动重启，Ctrl+C 停止
echo.

:loop
echo [%date% %time%] 启动机器人...
cd /d "%BOT_DIR%"
python feishu_flight_bot.py

echo.
echo [%date% %time%] 机器人已停止 (退出码: %ERRORLEVEL%^)
echo [%date% %time%] 5 秒后自动重启...
timeout /t 5 /nobreak >nul
goto loop
