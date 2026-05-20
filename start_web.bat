@echo off
title ATB Web Server
echo ============================================
echo  After-Assembling Test Bench - Web Mode
echo ============================================
echo.
echo Starting Python backend on http://localhost:8000
echo Starting React frontend on http://localhost:5173
echo.
echo Press Ctrl+C to stop.
echo.

:: Start backend
start "ATB Backend" cmd /k "cd /d %~dp0 && python -m uvicorn backend.main:app --reload --port 8000"

:: Wait a moment then start frontend
timeout /t 2 /nobreak > nul

:: Start frontend dev server
start "ATB Frontend" cmd /k "cd /d %~dp0\frontend && npm run dev"

echo Servers started. Open http://localhost:5173 in your browser.
pause
