@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY (
  if exist "%~dp0..\runtime\python\python.exe" set "PY=%~dp0..\runtime\python\python.exe"
)
if not defined PY (
  echo.
  echo   Python 3 not found.
  echo   Install Python 3.9+ or put the bundled runtime at ..\runtime\python\python.exe
  echo.
  pause
  exit /b 1
)

echo Starting miniAuto 3D preview...
%PY% server.py %*
if errorlevel 1 pause
