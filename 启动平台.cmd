@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\platform.ps1" -Action Start
if errorlevel 1 pause
endlocal
