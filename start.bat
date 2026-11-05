@echo off
rem ===========================================================================
rem  SightFlow launcher for Windows. Double-click to start.
rem
rem  First run : finds Python 3.10+, creates a virtual environment at a short
rem              path, installs PyTorch (CUDA if an NVIDIA GPU is found) and the
rem              rest of requirements.txt. This downloads a few GB once.
rem  Every run : starts "python -m sightflow web" and opens the browser.
rem
rem  Arguments are passed to "python -m sightflow web", e.g.
rem      start.bat projects\mygame.json
rem      start.bat projects\mygame.json --port 9000
rem      start.bat --template brawl_like      (new project from templates\brawl_like.json)
rem
rem  Optional settings (set them in cmd first, or in a small wrapper .bat):
rem      SIGHTFLOW_VENV        where the venv lives (default %USERPROFILE%\.venvs\sightflow)
rem      SIGHTFLOW_PYTHON      python.exe used to create the venv (default: auto)
rem      SIGHTFLOW_TORCH       cu128 / cpu  (default: cu128 if nvidia-smi works, else cpu)
rem      SIGHTFLOW_NO_BROWSER  set to 1 to not open the browser
rem ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title SightFlow

if not defined SIGHTFLOW_VENV set "SIGHTFLOW_VENV=%USERPROFILE%\.venvs\sightflow"
set "VENV=%SIGHTFLOW_VENV%"
set "VPY=%VENV%\Scripts\python.exe"

rem ---- 1. virtual environment ---------------------------------------------
if exist "%VPY%" goto :have_venv

call :find_python
if not defined PYEXE (
    echo.
    echo [SightFlow] Python 3.10 or newer was not found.
    echo   Install it from https://www.python.org/downloads/windows/
    echo   and tick "Add python.exe to PATH" in the installer, then run start.bat again.
    echo   ^(Or set SIGHTFLOW_PYTHON to the full path of python.exe.^)
    goto :fail
)
echo [SightFlow] Creating virtual environment: %VENV%
echo             using: %PYEXE%
if not exist "%VENV%\.." mkdir "%VENV%\.."
%PYEXE% -m venv "%VENV%"
if errorlevel 1 (
    echo [SightFlow] Could not create the virtual environment at %VENV%
    goto :fail
)

:have_venv
call "%VENV%\Scripts\activate.bat"
if errorlevel 1 goto :fail

rem ---- 2. PyTorch (once) ----------------------------------------------------
if exist "%VENV%\.sightflow-torch" goto :have_torch
if not defined SIGHTFLOW_TORCH (
    set "SIGHTFLOW_TORCH=cpu"
    nvidia-smi -L >nul 2>&1 && set "SIGHTFLOW_TORCH=cu128"
)
python -m pip install --upgrade pip
if /i "%SIGHTFLOW_TORCH%"=="cpu" (
    echo [SightFlow] No NVIDIA GPU found: installing CPU PyTorch ^(detection will be slower^).
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
) else (
    echo [SightFlow] NVIDIA GPU found: installing CUDA PyTorch ^(%SIGHTFLOW_TORCH%, about 3 GB^).
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/%SIGHTFLOW_TORCH%
)
if errorlevel 1 (
    echo [SightFlow] PyTorch install failed. Check your internet connection and run start.bat again.
    goto :fail
)
> "%VENV%\.sightflow-torch" echo %SIGHTFLOW_TORCH%

:have_torch
rem ---- 3. requirements.txt (again whenever the file changes) ---------------
fc /b "requirements.txt" "%VENV%\.sightflow-requirements.txt" >nul 2>&1
if not errorlevel 1 goto :have_reqs
echo [SightFlow] Installing requirements.txt
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [SightFlow] Installing requirements.txt failed.
    goto :fail
)
copy /y "requirements.txt" "%VENV%\.sightflow-requirements.txt" >nul

:have_reqs
rem ---- 4. web UI (prebuilt in webui\dist; build only if missing) -----------
if not exist "webui\dist\index.html" (
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [SightFlow] webui\dist is missing and Node.js is not installed.
        echo   Re-download SightFlow, or install Node.js and run start.bat again.
        goto :fail
    )
    echo [SightFlow] Building the web UI
    pushd webui
    call npm install && call npm run build
    popd
    if not exist "webui\dist\index.html" goto :fail
)

rem ---- 5. start ---------------------------------------------------------
set "PORT=8765"
set "NEXT="
for %%A in (%*) do (
    if defined NEXT (set "PORT=%%~A" & set "NEXT=")
    if /i "%%~A"=="--port" set "NEXT=1"
)
set "URL=http://127.0.0.1:%PORT%/"
if not "%SIGHTFLOW_NO_BROWSER%"=="1" (
    rem wait in the background until the server answers, then open the browser
    start "" /b powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$u='%URL%'; for($i=0;$i -lt 240;$i++){ try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $u | Out-Null; Start-Process $u; break } catch { Start-Sleep -Milliseconds 500 } }"
)
echo [SightFlow] Starting at %URL%   ^(close this window or press Ctrl+C to stop^)
python -m sightflow web %*
if errorlevel 1 goto :fail
exit /b 0

rem ---------------------------------------------------------------------------
:find_python
set "PYEXE="
if defined SIGHTFLOW_PYTHON (
    "%SIGHTFLOW_PYTHON%" -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set PYEXE="%SIGHTFLOW_PYTHON%"
    exit /b 0
)
rem "py" launcher first; plain "python" may be the Microsoft Store stub
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && (set "PYEXE=py -3" & exit /b 0)
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && (set "PYEXE=python" & exit /b 0)
exit /b 0

:fail
echo.
echo [SightFlow] Stopped because of the error above.
pause
exit /b 1
