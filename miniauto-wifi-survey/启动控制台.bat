@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0runtime\python\python.exe"
if exist "%PY%" goto run

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto run

set "PY=%~dp0..\wifi-rss-toolkit\.venv\Scripts\python.exe"
if exist "%PY%" goto run

set "PY=python"

:run
"%PY%" "%~dp0host\gui_launcher.py"
if errorlevel 1 pause
