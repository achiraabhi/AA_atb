# ATB Web Mode startup script (PowerShell)
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " After-Assembling Test Bench - Web Mode" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Start backend
Write-Host "Starting backend on http://localhost:8000 ..." -ForegroundColor Green
$backend = Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; python -m uvicorn backend.main:app --reload --port 8000" -PassThru

Start-Sleep -Seconds 2

# Start frontend
Write-Host "Starting frontend on http://localhost:5173 ..." -ForegroundColor Green
$frontend = Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\frontend'; npm run dev" -PassThru

Write-Host ""
Write-Host "Backend PID: $($backend.Id)" -ForegroundColor Gray
Write-Host "Frontend PID: $($frontend.Id)" -ForegroundColor Gray
Write-Host ""
Write-Host "Open http://localhost:5173 in your browser" -ForegroundColor Yellow
Write-Host ""
Write-Host "Press any key to stop both servers..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
Stop-Process -Id $backend.Id  -Force -ErrorAction SilentlyContinue
Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue
Write-Host "Stopped." -ForegroundColor Red
