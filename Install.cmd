@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1" %*
set "MONITOR_EXIT=%ERRORLEVEL%"
echo.
if not "%MONITOR_EXIT%"=="0" echo Command failed. Read the message above and README.md.
pause
exit /b %MONITOR_EXIT%
