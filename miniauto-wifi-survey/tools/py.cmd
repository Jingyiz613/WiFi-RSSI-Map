@echo off
rem Unified Python launcher.
rem Picks the first available interpreter:
rem   1. runtime\python  (bundled with this package, no install needed)
rem   2. .venv inside this project
rem   3. ..\wifi-rss-toolkit\.venv  (shared environment on the dev machine)
rem   4. python from PATH
setlocal
set "ROOT=%~dp0.."
set "PY="

if exist "%ROOT%\runtime\python\python.exe" set "PY=%ROOT%\runtime\python\python.exe"
if not defined PY if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if not defined PY if exist "%ROOT%\..\wifi-rss-toolkit\.venv\Scripts\python.exe" set "PY=%ROOT%\..\wifi-rss-toolkit\.venv\Scripts\python.exe"
if not defined PY set "PY=python"

if not defined PY (
  echo Python not found. Install Python 3.10 or newer first.
  exit /b 2
)

"%PY%" %*
