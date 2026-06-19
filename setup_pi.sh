#!/bin/bash
# ATB — Raspberry Pi setup script
# Run once after copying the project to the Pi:
#   bash setup_pi.sh           (venv + deps only, no systemd)
#   sudo bash setup_pi.sh      (also installs systemd service for autostart)

set -e

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
PI_USER="${SUDO_USER:-$(whoami)}"

echo "============================================"
echo " ATB — Raspberry Pi Setup"
echo " Install dir : $INSTALL_DIR"
echo " Running as  : $PI_USER"
echo "============================================"
echo ""

# ── 1. System packages (requires sudo) ────────────────────────────────────────
if [ "$(id -u)" -eq 0 ]; then
    echo "[1/5] Installing system packages..."
    apt-get update -qq
    # libhidapi-hidraw0 — USB-HID backend for the UNI-T UT61B+ multimeter (hidapi)
    apt-get install -y python3-pip python3-venv libhidapi-hidraw0
    echo "[2/5] Adding $PI_USER to dialout group (serial port access)..."
    usermod -aG dialout "$PI_USER"

    # udev rule so the UT61B+ (USB-HID) opens without sudo. Without this the
    # backend gets "OSError: open failed" when V*_DRIVER=ut61/auto.
    echo "      Installing UNI-T DMM udev rule..."
    cp "$INSTALL_DIR/udev/99-unit-dmm.rules" /etc/udev/rules.d/99-unit-dmm.rules
    udevadm control --reload-rules && udevadm trigger
    echo "      udev rule installed — re-plug the meter for it to take effect."
else
    echo "[1/2] Skipping system packages (not root — run with sudo to install)"
    echo "      python3-venv must already be installed"
    echo ""
fi

# ── 2. Python virtualenv + deps ───────────────────────────────────────────────
echo "[$([ "$(id -u)" -eq 0 ] && echo "3" || echo "1")/$([ "$(id -u)" -eq 0 ] && echo "5" || echo "2")] Creating Python virtualenv and installing dependencies..."
cd "$INSTALL_DIR"

if [ -d venv ] && [ -f venv/bin/python ]; then
    echo "  venv already exists — upgrading packages..."
else
    python3 -m venv venv
fi

venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q
echo "  Dependencies installed."

# ── 3. Create .env if not present ────────────────────────────────────────────
echo "[$([ "$(id -u)" -eq 0 ] && echo "4" || echo "2")/$([ "$(id -u)" -eq 0 ] && echo "5" || echo "2")] Setting up .env config..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    echo "  Created .env from template — edit it to set SERIAL_PORT, V1_PORT and V2_PORT"
else
    echo "  .env already exists, skipping."
fi

# ── 4. Install systemd service (requires sudo) ────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo ""
    echo "============================================"
    echo " Basic setup complete!"
    echo ""
    echo " Run the server:"
    echo "   ./start.sh"
    IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    echo ""
    echo " Open in browser: http://$IP:8000"
    echo " (or http://localhost:8000 on the Pi itself)"
    echo ""
    echo " For autostart on boot, run: sudo bash setup_pi.sh"
    echo "============================================"
    exit 0
fi

SERVICE_FILE="/etc/systemd/system/atb.service"
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
systemctl restart atb

echo ""
echo "============================================"
echo " Full setup complete!"
echo ""
echo " Service status : sudo systemctl status atb"
echo " Live logs      : journalctl -u atb -f"
echo " Stop           : sudo systemctl stop atb"
echo " Restart        : sudo systemctl restart atb"
echo ""
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
echo " Open in browser: http://$IP:8000"
echo "============================================"
echo ""
echo " NOTE: Log out and back in for serial port"
echo " access to take effect (dialout group)."
echo "============================================"
