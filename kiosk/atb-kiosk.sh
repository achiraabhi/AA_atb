#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# ATB kiosk launcher — opens ONLY the ATB web app, fullscreen and locked down.
#
# Run as the single client of the `cage` kiosk compositor (see atb-kiosk.service):
#     cage -- /path/to/kiosk/atb-kiosk.sh
#
# It waits for the backend to come up, then execs a browser in kiosk mode.
# Which browser is chosen by KIOSK_BROWSER:
#     auto      (default) — use cog if installed, else Chromium
#     chromium  — Chromium in --kiosk (same engine the app was built against)
#     cog       — cog / WPE WebKit (lighter, embedded-focused)
# The URL is KIOSK_URL (default http://localhost:8000).
# ─────────────────────────────────────────────────────────────────────────────
set -u

URL="${KIOSK_URL:-http://localhost:8000}"
BROWSER="${KIOSK_BROWSER:-auto}"

# ── wait for the ATB backend before opening the browser ──────────────────────
# (cage shows a blank screen until this returns; avoids a "can't connect" flash.)
for _ in $(seq 1 90); do
    if command -v curl >/dev/null 2>&1; then
        curl -sf -o /dev/null "$URL" && break
    else
        # No curl? fall back to a plain TCP check on the port.
        (echo > "/dev/tcp/localhost/${URL##*:}") >/dev/null 2>&1 && break
    fi
    sleep 1
done

# ── pick the browser binary ──────────────────────────────────────────────────
resolve() {
    case "$BROWSER" in
        cog)      command -v cog >/dev/null 2>&1 && { echo cog; return; } ;;
        chromium) for b in chromium-browser chromium; do command -v "$b" >/dev/null 2>&1 && { echo "$b"; return; }; done ;;
        *)        # auto: prefer the lighter cog, fall back to Chromium
                  command -v cog >/dev/null 2>&1 && { echo cog; return; }
                  for b in chromium-browser chromium; do command -v "$b" >/dev/null 2>&1 && { echo "$b"; return; }; done ;;
    esac
    echo none
}
BIN="$(resolve)"

echo "[atb-kiosk] browser=$BIN url=$URL"

case "$BIN" in
    cog)
        # WPE WebKit single-page kiosk. -O = open at URL.
        exec cog -O "$URL"
        ;;
    chromium|chromium-browser)
        # Locked-down Chromium: fullscreen, no chrome, no navigation, GPU on.
        exec "$BIN" \
            --kiosk "$URL" \
            --incognito \
            --noerrdialogs \
            --disable-infobars \
            --disable-session-crashed-bubble \
            --disable-features=Translate,TranslateUI \
            --disable-pinch \
            --overscroll-history-navigation=0 \
            --check-for-update-interval=31536000 \
            --enable-gpu-rasterization \
            --ignore-gpu-blocklist \
            --enable-zero-copy \
            --autoplay-policy=no-user-gesture-required
        ;;
    *)
        echo "[atb-kiosk] ERROR: no kiosk browser found (install 'cog' or 'chromium-browser')" >&2
        # Keep the service alive so systemd doesn't hot-loop; show the message.
        sleep 3600
        exit 1
        ;;
esac
