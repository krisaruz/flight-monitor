@echo off
chcp 65001 >nul
echo.
echo =========================================
echo   设置开机自启动
echo =========================================
echo.

set BOT_DIR=%~dp0
set SHORTCUT_NAME=飞书机票监控机器人
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup

echo   将在 Windows 启动目录创建快捷方式:
echo   %STARTUP_DIR%\%SHORTCUT_NAME%.lnk
echo.
echo   指向: %BOT_DIR%start_bot.bat
echo.

:: 使用 PowerShell 创建快捷方式
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%STARTUP_DIR%\%SHORTCUT_NAME%.lnk'); $sc.TargetPath = '%BOT_DIR%start_bot.bat'; $sc.WorkingDirectory = '%BOT_DIR%'; $sc.WindowStyle = 7; $sc.Description = '飞书机票监控机器人 - 开机自启'; $sc.Save()"

if %ERRORLEVEL%==0 (
    echo   [OK] 快捷方式已创建！电脑重启后会自动启动机器人。
    echo.
    echo   如需取消自启动，删除以下文件即可:
    echo   %STARTUP_DIR%\%SHORTCUT_NAME%.lnk
) else (
    echo   [!] 创建失败，请手动将 start_bot.bat 放入启动文件夹
    echo   启动文件夹: %STARTUP_DIR%
)

echo.
pause
