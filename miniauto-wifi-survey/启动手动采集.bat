@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist "%~dp0layout.json" (
    echo [错误] 未找到 layout.json。
    echo 请先双击 启动控制台.bat，设置房间、路由器和家具并保存布局。
    pause
    exit /b 2
)

set "TARGET=%~1"
if not defined TARGET set /p "TARGET=请输入采集节点地址（例如 192.168.1.50:62587）: "
if not defined TARGET (
    echo [错误] 没有输入采集节点地址。
    pause
    exit /b 2
)

echo.
echo 正在启动 ESP32-C6 手动搬移采集...
echo 节点地址：%TARGET%
echo 输出文件：manual_survey.csv / manual_survey.json
echo SSID 和 BSSID 将按隐私模式脱敏。
echo.

call "%~dp0tools\py.cmd" "%~dp0host\survey_manual.py" --target "%TARGET%" --layout "%~dp0layout.json" --redact
if errorlevel 1 pause
endlocal
