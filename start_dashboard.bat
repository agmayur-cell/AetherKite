@echo off
title AetherKite Terminal
cd /d "%~dp0"
echo ========================================================
echo   AETHERKITE TERMINAL (NSE / ZERODHA)
echo ========================================================
echo.

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if "%AETHERKITE_ENABLE_LIVE%"=="1" (
  echo   LIVE ORDER PLACEMENT IS ENABLED. Real money is at risk.
) else (
  echo   Paper mode only. LIVE orders are disabled.
)
echo.
echo Opening http://127.0.0.1:8000 in a few seconds...
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"

rem No --reload: a code edit must never restart the server mid-trade.
"%PY%" -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
