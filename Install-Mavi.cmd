@echo off
setlocal
cd /d "%~dp0"
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo Windows PowerShell is required to run the Mavi installer.
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -File "%~dp0Install-Mavi.ps1" %*
set "RESULT=%ERRORLEVEL%"
exit /b %RESULT%
