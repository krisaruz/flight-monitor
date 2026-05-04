@echo off
chcp 65001 >nul
title 机票搜索推送

:: ─── 从 .env 加载配置 ────────────────────────────
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%~dp0.env") do (
        set "%%a=%%b"
    )
)

:: ─── Webhook 地址 (必填) ─────────────────────────
:: 如果未从 .env 加载，请在此手动设置:
:: set FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/your-webhook-id

if not defined FEISHU_WEBHOOK (
    echo [错误] 未配置 FEISHU_WEBHOOK，请编辑此文件或创建 .env 文件
    echo 参考 .env.example
    pause
    exit /b 1
)

:: ─── 搜索参数 (按需修改) ──────────────────────────
set ORIGIN=香港
set DEST=大阪
set START=2026-04-20
set END=2026-06-30
set DAYS=5
set TOP=10

:: ─── 执行 ─────────────────────────────────────────
cd /d "%~dp0"
echo.
echo   正在搜索 %ORIGIN% → %DEST% 机票并推送到飞书群...
echo.

python -c "import sys; sys.path.insert(0,'.'); from feishu_flight_bot import *; CFG.use_demo=True; p=parse_flight_command('搜机票 %ORIGIN%到%DEST% %START%~%END% %DAYS%天'); r=run_search(p); c=build_flight_card(r,p['origin'],p['dest'],p['start'],p['end'],p['days'],top_n=%TOP%); push_to_feishu_webhook('%FEISHU_WEBHOOK%',c)"

echo.
if %ERRORLEVEL%==0 (echo   推送成功!) else (echo   推送失败，请检查网络)
echo.
pause
