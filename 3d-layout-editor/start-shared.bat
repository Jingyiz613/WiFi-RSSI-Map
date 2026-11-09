@echo off
rem Start the 3D editor in LAN sharing mode.
rem Teammates on the same Wi-Fi/LAN open the "shared" URL printed below.
call "%~dp0start.bat" --host 0.0.0.0 %*
