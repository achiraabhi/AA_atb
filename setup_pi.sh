#!/bin/bash
# ATB — Raspberry Pi one-shot setup script
# Run once after copying the project to the Pi:
#   bash setup_pi.sh

set -e

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="/etc/systemd/system/atb.service"
PI_USER="${SUDO_USER:-$(whoami)}"

echo "============================================"
echo " ATB — Raspberry Pi Setup"
echo " Install dir : $INSTALL_DIR"
echo " Running as  : $PI_USER"
echo "============================================"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/5] Installing system packages..."
apt-get update -qq
apt-get install -y python3-pip python3-venv

# ── 2. Serial port permission ─────────────────────────────────────────────────
echo "[2/5] Adding $PI_USER to dialout group (serial port access)..."
usermod -aG dialout "$PI_USER"

# ── 3. Python virtualenv + deps ───────────────────────────────────────────────
echo "[3/5] Creating Python virtualenv and installing dependencies..."
cd "$INSTALL_DIR"
python3 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q
echo "Dependencies installed."

# ── 4. Create .env if not present ────────────────────────────────────────────
echo "[4/5] Setting up .env config..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "  Created .env from template — edit it to set HARDWARE_MODE=real and SERIAL_PORT"
else
    echo "  .env already exists, skipping."
fi

# ── 5. Install systemd service ────────────────────────────────────────────────
echo "[5/5] Installing systemd service..."

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=ATB After-Assembling Test Bench
After=network.target

[Service]
Type=simple
User=$PI_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port \${PORT:-8000}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable atb
systemctl start atb

echo ""
echo "============================================"
echo " Setup complete!"
echo ""
echo " Service status : sudo systemctl status atb"
echo " Live logs      : journalctl -u atb -f"
echo " Stop           : sudo systemctl stop atb"
echo " Restart        : sudo systemctl restart atb"
echo ""

# Detect local IP
IP=$(hostname -I | awk '{print $1}')
echo " Open in browser: http://$IP:8000"
echo "============================================"
echo ""
echo " NOTE: Log out and back in for serial port"
echo " access to take effect (dialout group)."
echo "============================================"
