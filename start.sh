#!/bin/bash
# ATB — manual start (use this for testing; use systemd service for production)

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$INSTALL_DIR"

if [ ! -f venv/bin/python ]; then
    echo "ERROR: virtualenv not found. Run setup_pi.sh first."
    exit 1
fi

[ -f .env ] && export $(grep -v '^#' .env | xargs)

PORT="${PORT:-8000}"
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo "============================================"
echo " ATB After-Assembling Test Bench"
echo " http://localhost:$PORT"
echo " http://$IP:$PORT  (network)"
echo " Ctrl+C to stop"
echo "============================================"
echo ""

exec venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
