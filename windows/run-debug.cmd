@echo off
setlocal
cd /d "%~dp0"
echo Starting AvayaGateway.exe from:
echo %CD%
echo.
if not exist AvayaGateway.exe (
  echo AvayaGateway.exe was not found in this folder.
  echo Run build-exe.cmd first, then use dist\AvayaGateway\run-debug.cmd.
  echo.
  pause
  exit /b 1
)
AvayaGateway.exe
echo.
echo AvayaGateway.exe exited with code %ERRORLEVEL%.
echo.
pause
exit /b %ERRORLEVEL%
