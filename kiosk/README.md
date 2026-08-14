# ATB Kiosk Mode

Turn the Raspberry Pi into a **single-purpose appliance**: on boot it goes
straight into a locked, fullscreen browser showing **only** the ATB web app —
no desktop, no tabs, no address bar, no way to browse anywhere else.

## Why a kiosk (and not the normal browser)

The dashboard is canvas-heavy. A full desktop browser session wastes RAM/CPU on
tabs, extensions, and window chrome, and lets an operator wander off the app.
Kiosk mode strips all of that: one locked window, one URL.

Two engines are supported:

| Browser | Engine | Notes |
|---------|--------|-------|
| **Chromium `--kiosk`** (default) | Blink | Same engine the app was built/tested against — guaranteed correct rendering. Locked down. |
| **`cog`** | WPE WebKit | Genuinely *lighter* (smaller RAM/CPU footprint), embedded-focused, GPU-accelerated on Pi. Different engine — do a quick visual check. |

Both run under **`cage`**, a tiny single-app Wayland compositor, so there is no
desktop environment at all (ideal on Raspberry Pi OS **Lite**).

## Install (run once, on the Pi)

```bash
# 1. Backend service first (if not already installed)
sudo bash setup_pi.sh

# 2. Kiosk — Chromium (safe default)
sudo bash setup_kiosk.sh

#    …or the lighter WPE WebKit engine:
sudo KIOSK_BROWSER=cog bash setup_kiosk.sh
```

Then `sudo reboot` — the Pi comes up directly in the app.

## Operate

| Action | Command |
|--------|---------|
| Status | `sudo systemctl status atb-kiosk` |
| Live logs | `journalctl -u atb-kiosk -f` |
| Stop the kiosk | `sudo systemctl stop atb-kiosk` |
| Disable boot-to-kiosk | `sudo systemctl disable atb-kiosk` |
| Drop to a console | `Ctrl+Alt+F2`, then log in |
| Change engine/URL | re-run `sudo KIOSK_BROWSER=cog KIOSK_URL=http://localhost:8000 bash setup_kiosk.sh` |

## How it works

- [`setup_kiosk.sh`](../setup_kiosk.sh) installs `cage` + the browser, disables
  screen blanking, and writes `atb-kiosk.service` (a systemd unit that owns
  `tty1`).
- [`atb-kiosk.sh`](atb-kiosk.sh) is the launcher `cage` runs: it waits for the
  backend to answer, then `exec`s the chosen browser in kiosk mode.
- The service starts **after** `atb.service`, so the backend is up first. Both
  auto-restart on failure.

## Notes / caveats

- Best on **Raspberry Pi OS Lite**. If you run the full desktop, its display
  manager also wants `tty1` — either use Lite, or disable the desktop autologin
  before enabling the kiosk service.
- If the screen still blanks, confirm `consoleblank=0` landed in
  `/boot/cmdline.txt` (the script appends it) and reboot.
- To verify GPU acceleration with Chromium kiosk, temporarily launch it
  non-kiosk and open `chrome://gpu` — "Canvas" should read *Hardware accelerated*.
- This does **not** replace remote access: any device on the LAN can still reach
  `http://<pi-ip>:8000`.
