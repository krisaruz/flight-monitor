@echo off
cd /d "%~dp0"
if not exist .env (
  echo 请先复制 .env.example 为 .env，填入 TRAVELPAYOUTS_TOKEN，并修改密码
  copy .env.example .env
)
set PYTHONPATH=%~dp0backend
echo 启动 Flight Monitor: http://127.0.0.1:8000
echo 需已配置 TRAVELPAYOUTS_TOKEN，并执行: playwright install chromium
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
