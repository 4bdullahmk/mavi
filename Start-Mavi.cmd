@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

if not exist "portable\server.py" (
  echo Mavi's Windows server is missing: portable\server.py
  echo Download or extract a complete Mavi release, then try again.
  pause
  exit /b 1
)

set "PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
  echo Python 3.11 or newer is required.
  echo Install Python from https://www.python.org/downloads/windows/ and enable the Python launcher.
  pause
  exit /b 1
)

set "PY_MAJOR="
set "PY_MINOR="
for /f "tokens=2,3 delims= ." %%A in ('!PYTHON! --version 2^>^&1') do (
  set "PY_MAJOR=%%A"
  set "PY_MINOR=%%B"
)
if not defined PY_MAJOR (
  echo Could not determine the Python version. Install Python 3.11 or newer.
  pause
  exit /b 1
)
if not "!PY_MAJOR!"=="3" (
  echo Python 3.11 or newer is required. Found Python !PY_MAJOR!.!PY_MINOR!.
  pause
  exit /b 1
)
if !PY_MINOR! LSS 11 (
  echo Python 3.11 or newer is required. Found Python !PY_MAJOR!.!PY_MINOR!.
  echo Install Python from https://www.python.org/downloads/windows/.
  pause
  exit /b 1
)

where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is required for local model responses.
  echo Install it from https://ollama.com/download/windows, then run this launcher again.
  pause
  exit /b 1
)
ollama list >nul 2>nul
if errorlevel 1 (
  echo Ollama is installed but its local service is not responding.
  echo Open Ollama from the Start menu, then run this launcher again.
  echo If needed, open a separate Command Prompt and run: ollama serve
  pause
  exit /b 1
)

if not defined LOCALAPPDATA set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "MAVI_DATA_DIR=%LOCALAPPDATA%\Mavi"
set "MAVI_HOST=127.0.0.1"
set "MAVI_PORT=8769"
if not exist "%MAVI_DATA_DIR%" mkdir "%MAVI_DATA_DIR%"

if exist "portable\requirements.txt" (
  set "MAVI_VENV=%MAVI_DATA_DIR%\venv"
  if not exist "!MAVI_VENV!\Scripts\python.exe" (
    echo Creating Mavi's private Python environment under !MAVI_DATA_DIR! ...
    !PYTHON! -m venv "!MAVI_VENV!"
    if errorlevel 1 goto :failed
  )
  fc /b "portable\requirements.txt" "!MAVI_DATA_DIR!\requirements-installed.txt" >nul 2>nul
  if errorlevel 1 (
    echo Installing Mavi's Python packages. This may take a few minutes.
    "!MAVI_VENV!\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto :failed
    "!MAVI_VENV!\Scripts\python.exe" -m pip install -r "portable\requirements.txt"
    if errorlevel 1 goto :failed
    copy /y "portable\requirements.txt" "!MAVI_DATA_DIR!\requirements-installed.txt" >nul
  )
set "MAVI_VENV_PYTHON=!MAVI_VENV!\Scripts\python.exe"
) else (
  set "MAVI_VENV_PYTHON="
)

echo Starting Mavi at http://127.0.0.1:8769/
echo Keep this window open while using Mavi. Press Ctrl+C to stop the local server.
if defined MAVI_VENV_PYTHON (
  "!MAVI_VENV_PYTHON!" "portable\server.py"
) else (
  !PYTHON! "portable\server.py"
)
set "RESULT=!ERRORLEVEL!"
if not "!RESULT!"=="0" (
  echo Mavi exited with status !RESULT!.
  pause
)
exit /b !RESULT!

:failed
echo Mavi setup failed. Check the messages above, then try again.
pause
exit /b 1
