@echo off
rem SightFlow launcher for Windows: double-click to start.
rem First run: creates a Python environment and installs the parts (takes a few minutes).
rem
rem Keep this file ASCII-only. cmd.exe misreads UTF-8 batch files (multi-byte lines get
rem split and run as commands), so the Japanese messages live in launcher\messages.txt.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem Keep the environment on a short path (deep folders break PyTorch's DLL loading).
set "VENV=%LOCALAPPDATA%\SightFlow\venv"

if exist "%VENV%\Scripts\python.exe" goto :ui

where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo.
  call :say nopython
  echo.
  pause
  exit /b 1
)

call :say setup
%PY% -m venv "%VENV%" || goto :fail
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul
rem GPU PyTorch must go in BEFORE requirements.txt: ultralytics would otherwise pull the
rem CPU build first, and the later GPU install would just say "already satisfied".
where nvidia-smi >nul 2>nul
if not errorlevel 1 (
  call :say gpu
  "%VENV%\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
)
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

:ui
if exist "webui\dist\index.html" goto :run
where npm >nul 2>nul
if errorlevel 1 (
  echo.
  call :say nodist
  echo.
  pause
  exit /b 1
)
call :say build
pushd webui
call npm ci && call npm run build
popd

:run
call :say run
"%VENV%\Scripts\python.exe" -m sightflow web %*
exit /b 0

:fail
echo.
call :say fail
echo   %VENV%
pause
exit /b 1

:say
rem Print every line of launcher\messages.txt whose key (before "|") is %1.
for /f "usebackq tokens=1* delims=|" %%a in ("%~dp0launcher\messages.txt") do if "%%a"=="%~1" echo(%%b
exit /b 0
