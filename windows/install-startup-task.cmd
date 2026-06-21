@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-startup-task.ps1" %*
exit /b %ERRORLEVEL%
