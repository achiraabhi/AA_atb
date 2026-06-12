# ATB — Deploy updates from Windows PC to Raspberry Pi
# Usage:  .\deploy_to_pi.ps1 -PiHost 192.168.1.100
# First run: .\deploy_to_pi.ps1 -PiHost 192.168.1.100 -FirstTime

param(
    [Parameter(Mandatory=$true)]
    [string]$PiHost,

    [string]$PiUser = "pi",
    [int]$Port = 22,
    [switch]$FirstTime
)

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$REMOTE = "${PiUser}@${PiHost}"
$REMOTE_DIR = "/home/$PiUser/atb"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " ATB Deploy → $REMOTE" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Files/folders to sync (excludes venv, logs, .env, __pycache__)
$SYNC_DIRS = @("web", "backend", "core", "hardware", "configs")
$SYNC_FILES = @("requirements.txt", ".env.example", "start.sh", "setup_pi.sh")

if ($FirstTime) {
    Write-Host "[FIRST TIME] Creating remote directory and running setup..." -ForegroundColor Yellow
    ssh -p $Port "${REMOTE}" "mkdir -p $REMOTE_DIR"
}

# Sync directories
foreach ($dir in $SYNC_DIRS) {
    $local = Join-Path $ROOT $dir
    if (Test-Path $local) {
        Write-Host "Syncing $dir/..." -ForegroundColor White
        scp -r -P $Port "$local" "${REMOTE}:${REMOTE_DIR}/"
    }
}

# Sync individual files
foreach ($file in $SYNC_FILES) {
    $local = Join-Path $ROOT $file
    if (Test-Path $local) {
        Write-Host "Syncing $file..." -ForegroundColor White
        scp -P $Port "$local" "${REMOTE}:${REMOTE_DIR}/"
    }
}

if ($FirstTime) {
    Write-Host ""
    Write-Host "Running setup_pi.sh on Pi (requires sudo)..." -ForegroundColor Yellow
    ssh -p $Port "${REMOTE}" "cd $REMOTE_DIR && chmod +x setup_pi.sh start.sh && sudo bash setup_pi.sh"
} else {
    Write-Host ""
    Write-Host "Restarting ATB service on Pi..." -ForegroundColor Green
    ssh -p $Port "${REMOTE}" "sudo systemctl restart atb"
    Start-Sleep -Seconds 2
    ssh -p $Port "${REMOTE}" "sudo systemctl status atb --no-pager -l"
}

Write-Host ""
Write-Host "Done. Open http://${PiHost}:8000" -ForegroundColor Green
