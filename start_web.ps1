# ATB Web Mode — Python-only startup (no Node.js required)
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " After-Assembling Test Bench — Web Mode" -ForegroundColor Cyan
Write-Host "  Python-only — no Node.js needed" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$localIP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch 'Loopback' -and $_.IPAddress -notmatch '^169' } | Select-Object -First 1).IPAddress
Write-Host "Starting server..." -ForegroundColor Green
Write-Host "  Local:   http://localhost:8000" -ForegroundColor White
Write-Host "  Network: http://${localIP}:8000" -ForegroundColor Yellow
Write-Host ""

python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000 --app-dir "$root"
