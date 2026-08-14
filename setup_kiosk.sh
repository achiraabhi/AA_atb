#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# ATB — Kiosk setup for Raspberry Pi
#
# Turns the Pi into a single-purpose appliance: on boot it goes straight into a
# locked, fullscreen browser showing ONLY the ATB web app. No desktop, no tabs,
# no address bar, no way to browse elsewhere.
#
# Run ONCE on the Pi, as root:
#     sudo bash setup_kiosk.sh                 # Chromium kiosk (default, safe)
#     sudo KIOSK_BROWSER=cog bash setup_kiosk.sh   # lighter WPE WebKit engine
#
# Best on Raspberry Pi OS **Lite** (no desktop) so tty1 is free for the kiosk.
# Requires the backend service (atb.service) — install it first with:
#     sudo bash setup_pi.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root:  sudo bash setup_kiosk.sh"
    exit 1
fi

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
PI_USER="${SUDO_USER:-pi}"
PI_UID="$(id -u "$PI_USER")"
KIOSK_BROWSER="${KIOSK_BROWSER:-chromium}"   # chromium (default) | cog | auto
KIOSK_URL="${KIOSK_URL:-http://localhost:8000}"

echo "============================================"
echo " ATB — Kiosk Setup"
echo " Install dir : $INSTALL_DIR"
echo " Kiosk user  : $PI_USER (uid $PI_UID)"
echo " Browser     : $KIOSK_BROWSER"
echo " URL         : $KIOSK_URL"
echo "============================================"

# ── 1. Install the kiosk compositor + browser ────────────────────────────────
echo "[1/4] Installing packages..."
apt-get update -qq
# cage = minimal single-app Wayland kiosk compositor; seatd/dbus for the seat.
apt-get install -y cage seatd dbus curl
case "$KIOSK_BROWSER" in
    cog)      apt-get install -y cog || { echo "  cog unavailable — falling back to Chromium"; apt-get install -y chromium-browser || apt-get install -y chromium; KIOSK_BROWSER=chromium; } ;;
    auto)     apt-get install -y cog || true; apt-get install -y chromium-browser || apt-get install -y chromium || true ;;
    *)        apt-get install -y chromium-browser || apt-get install -y chromium ;;
esac
systemctl enable seatd >/dev/null 2>&1 || true

chmod +x "$INSTALL_DIR/kiosk/atb-kiosk.sh"

# ── 2. Disable console/screen blanking (kiosk should never sleep) ─────────────
echo "[2/4] Disabling screen blanking..."
if ! grep -q "consoleblank=0" /boot/cmdline.txt 2>/dev/null && [ -f /boot/cmdline.txt ]; then
    sed -i 's/$/ consoleblank=0/' /boot/cmdline.txt || true
fi

# ── 3. Install the kiosk systemd service ──────────────────────────────────────
echo "[3/4] Installing atb-kiosk.service..."
SERVICE_FILE="/etc/systemd/system/atb-kiosk.service"
cat > "$SERVICE_FILE" << EOF
[Unit]
Description=ATB Kiosk (fullscreen locked browser)
# Start after the backend and the login/seat services are ready.
After=atb.service systemd-user-sessions.service seatd.service
Wants=atb.service

[Service]
Type=simple
User=$PI_USER
# A real login session so XDG_RUNTIME_DIR / seat access are set up (pam_systemd).
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
StandardError=journal
UtmpIdentifier=tty1
UtmpMode=user
Environment=XDG_RUNTIME_DIR=/run/user/$PI_UID
Environment=KIOSK_BROWSER=$KIOSK_BROWSER
Environment=KIOSK_URL=$KIOSK_URL
# cage runs our launcher as its single fullscreen client.
ExecStart=/usr/bin/cage -- $INSTALL_DIR/kiosk/atb-kiosk.sh
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOF

# ── 4. Boot to the kiosk ──────────────────────────────────────────────────────
echo "[4/4] Enabling boot-to-kiosk..."
# Boot to the multi-user (console) target; the service grabs tty1 for the kiosk.
systemctl set-default graphical.target >/dev/null 2>&1 || systemctl set-default multi-user.target
systemctl daemon-reload
systemctl enable atb-kiosk.service
systemctl restart atb-kiosk.service || true

echo ""
echo "============================================"
echo " Kiosk installed."
echo ""
echo " Status : sudo systemctl status atb-kiosk"
echo " Logs   : journalctl -u atb-kiosk -f"
echo " Stop   : sudo systemctl stop atb-kiosk"
echo " Disable: sudo systemctl disable atb-kiosk"
echo ""
echo " Switch engine later, e.g. to the lighter WPE browser:"
echo "   sudo KIOSK_BROWSER=cog bash setup_kiosk.sh"
echo ""
echo " Exit the kiosk to a console:  Ctrl+Alt+F2  (then login)"
echo " Reboot to test:               sudo reboot"
echo "============================================"
