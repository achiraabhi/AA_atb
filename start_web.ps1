# ATB Web Mode — Python-only startup (no Node.js required)
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " After-Assembling Test Bench — Web Mode" -ForegroundColor Cyan
Write-Host "  Python-only — no Node.js needed" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "Starting server on http://localhost:8000 ..." -ForegroundColor Green
Write-Host ""

python -m uvicorn backend.main:app --reload --port 8000 --app-dir "$root"
