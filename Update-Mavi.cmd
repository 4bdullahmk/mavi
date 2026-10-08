@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
  echo Python 3.11 or newer is required to safely unpack a Mavi update.
  echo Install Python from https://www.python.org/downloads/windows/.
  pause
  exit /b 1
)
if "%~1"=="" (
  set /p "ZIP=Enter or paste the full path to the downloaded Mavi release ZIP: "
) else (
  set "ZIP=%~1"
)
if not exist "%ZIP%" (
  echo The ZIP file was not found: "%ZIP%"
  pause
  exit /b 1
)
%PYTHON% Update-Mavi.py "%ZIP%"
if errorlevel 1 pause
