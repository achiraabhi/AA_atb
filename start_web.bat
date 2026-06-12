@echo off
title ATB Web Server
echo ============================================
echo  After-Assembling Test Bench - Web Mode
echo  Python-only — no Node.js needed
echo ============================================
echo.
echo Open http://localhost:8000 in your browser
echo Other devices on this network: http://YOUR-PC-IP:8000
echo Press Ctrl+C to stop.
echo.
cd /d %~dp0
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
