@echo off
title Kite-AI Trading Terminal
echo ========================================================
echo   STARTING KITE-AI TRADING TERMINAL (NSE / ZERODHA)
echo ========================================================
echo.
echo Opening browser at http://127.0.0.1:8000 ...
start http://127.0.0.1:8000
echo.
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
pause
