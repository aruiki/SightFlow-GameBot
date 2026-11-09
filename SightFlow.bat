@echo off
rem SightFlow launcher for Windows: double-click to start.
rem First run: creates a Python environment and installs the parts (takes a few minutes).
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
  echo [SightFlow] Python が見つかりません。
  echo   https://www.python.org/downloads/ から Python 3.10 以上を入れてください。
  echo   インストール時に「Add python.exe to PATH」にチェックを入れてください。
  echo.
  pause
  exit /b 1
)

echo [SightFlow] 初回の準備をしています（数分かかります）...
%PY% -m venv "%VENV%" || goto :fail
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul
"%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

where nvidia-smi >nul 2>nul
if not errorlevel 1 (
  echo [SightFlow] NVIDIA GPU を見つけました。GPU 版 PyTorch を入れます...
  "%VENV%\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
)

:ui
if exist "webui\dist\index.html" goto :run
where npm >nul 2>nul
if errorlevel 1 (
  echo.
  echo [SightFlow] 画面部分（webui\dist）がありません。
  echo   GitHub の Releases から「SightFlow-...-windows.zip」をダウンロードして使ってください。
  echo   （ソースから使う場合は Node.js を入れてください）
  echo.
  pause
  exit /b 1
)
echo [SightFlow] 画面部分をビルドしています...
pushd webui
call npm ci && call npm run build
popd

:run
echo [SightFlow] 起動します。ブラウザが開かないときは http://127.0.0.1:8765 を開いてください。
echo   止めるときはこのウィンドウを閉じてください。
"%VENV%\Scripts\python.exe" -m sightflow web %*
exit /b 0

:fail
echo.
echo [SightFlow] 準備に失敗しました。上のメッセージを確認してください。
echo   やり直すときは %VENV% フォルダを消してから、もう一度ダブルクリックしてください。
pause
exit /b 1
