@echo off
title ATB Web Server
echo ============================================
echo  After-Assembling Test Bench - Web Mode
echo  Python-only — no Node.js needed
echo ============================================
echo.
echo Open http://localhost:8000 in your browser
echo Press Ctrl+C to stop.
echo.
cd /d %~dp0
python -m uvicorn backend.main:app --reload --port 8000
