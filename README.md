# After-Assembling Test Bench (ATB)

Industrial-grade web application for automated no-load testing of assembled transformers.  
Built with Python + FastAPI (backend) and a browser front-end. Drives a physical relay
matrix (RL1–40) and dual voltage meters over serial, with a touchscreen-first UI meant to
run as a locked kiosk on a Raspberry Pi.

---

## Features

| Feature | Details |
|---------|---------|
| **Visual transformer builder** | Canvas editor — drag windings, click nodes to assign relays, pick wire colours from a swatch palette |
| **Relay matrix (RL1–40)** | A1 RL1–16 (voltmeter +), A2 RL17–32 (voltmeter −), gates RL33–36, Group B RL37–40 (energizing-winding taps) |
| **Ratio-based rule engine** | Segment-pair rules; expected voltage scales with the applied excitation. Auto-generate the full sweep from topology |
| **Wire-colour workflow** | Each lead's colour is pickable and verifiable; the step results show, per step, the exact leads measured (colour + lead number) |
| **Winding-polarity (phase) check** | Detects a reversed winding that reads the right magnitude — PASS/FAIL per step |
| **Dev Panel** | Manual per-relay toggle, diagnostic sequence, and a Fault Finder to locate a dead/stuck relay |
| **Step results** | Live pass/fail list with wire colours; the per-step measurement detail is revealed on tap |
| **Dual serial + UNI-T UT61B+** | Separate ports for the relay MCU and voltage meters; UT61B+ multimeters over USB-HID also supported |
| **Persistence** | SQLite database (results, batches, measurements) alongside CSV/JSON logs |
| **Kiosk mode** | Boots the Pi straight into a locked fullscreen browser showing only this app |

---

## Project Structure

```
AA_atb/
├── requirements.txt
│
├── backend/                        FastAPI web server
│   ├── main.py                     App entry point, lifespan wiring, static serving
│   ├── api/routes.py               REST endpoints (transformers, test, relays, db, logs)
│   └── websocket/                  Live state push (manager, broadcaster, events)
│
├── web/static/                     Browser front-end served by the backend
│   ├── index.html
│   ├── app.js                      Dashboard/UI logic + WebSocket client
│   ├── canvas.js                   Dashboard diagram + shared wire-colour palette
│   ├── editor.js                   Konva topology + rule editor
│   └── style.css, fonts/
│
├── frontend/                       Optional React/Vite client (TypeScript)
│
├── core/
│   ├── config_loader.py            JSON scanner / parser / dataclasses
│   ├── state_manager.py            Thread-safe observable state store
│   ├── sequence_manager.py         Resolves ExecutableSteps from ratio_rules / matrix
│   ├── test_engine.py              Background test orchestrator (threading)
│   ├── measurement_matrix_engine.py Auto-generates the measurement sweep from topology
│   ├── ratio_engine.py             Ratio maths + hybrid-tolerance validation
│   ├── relay_sequence.py           Diagnostic "click-test" sequencer
│   ├── validator.py                Config validation (errors / warnings / info)
│   └── logger.py                   CSV/JSON logging + console feed
│
├── db/                             SQLite persistence (SQLAlchemy)
│   ├── base.py                     Engine, session, init_db(), additive migrations
│   ├── models.py                   transformers / batches / units / measurements / …
│   └── store.py                    ResultStore — dual-writes results, read helpers
│
├── hardware/
│   ├── hardware_interface.py       Abstract base classes (ABCs) + relay constants
│   ├── protocol.py                 Serial builders + constants (RL1–40, groups, PHASE)
│   ├── relay_serial.py             Relay MCU serial driver (thread-safe)
│   ├── relay_controller.py         Real relay controller with group-safety enforcement
│   ├── routing_engine.py           Maps winding/tap pairs → relay lists (A + Group B)
│   ├── voltage_meter_serial.py     Continuous-stream voltage reader (background thread)
│   ├── voltage_meter_ut61.py       UNI-T UT61B+ polled meter (USB-HID)
│   ├── dual_voltage_service.py     V1 (energizing) + V2 (measurement) channel manager
│   ├── hybrid_hardware.py          Wires the relay controller + V2 meter to TestEngine
│   ├── port_scanner.py             Behaviour-based serial device auto-detection
│   ├── measurement_manager.py      relay-switch → stabilize → sample → average cycle
│   └── serial_manager.py           Lifecycle manager for the serial connections
│
├── scripts/import_logs.py          One-off backfill of logs/*.json into the database
├── kiosk/                          Kiosk launcher + docs (setup via setup_kiosk.sh)
├── transformers/*.json             Transformer configs (edit/save from the app)
├── data/                           Auto-created; SQLite database (atb.db) — gitignored
├── logs/                           Auto-created; CSV + JSON test results — gitignored
└── assets/, udev/, ut61eplus/, firmware/   icons · udev rules · UT61 driver · MCU sketch
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> `pyserial` is required — the app drives the relay board and voltage meters over serial.

### 2. Run the application

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Convenience launchers do the same thing:

- **Raspberry Pi / Linux**: `./start.sh`
- **Windows**: `start_web.bat` (double-click) or `.\start_web.ps1` (PowerShell)

Then open **http://localhost:8000** in a browser.

> **Industrial kiosk mode (Pi).** To make the Pi boot straight into a locked,
> fullscreen browser showing only this app — no desktop, no tabs, no way to
> browse elsewhere — run `sudo bash setup_kiosk.sh` once. Defaults to Chromium
> `--kiosk`; `sudo KIOSK_BROWSER=cog bash setup_kiosk.sh` uses the lighter WPE
> WebKit engine. See [kiosk/README.md](kiosk/README.md).

> **Hardware is optional at startup.** The backend auto-detects serial devices; if the relay board or meters are absent it logs a warning and starts anyway in a **disconnected** state — the UI loads and you can browse/edit transformers. Running an actual *test* requires the relay board. Configure serial ports in `.env` only when you attach real hardware (see [Connecting Real Hardware](#connecting-real-hardware)).

### Local development (no hardware)

For dev on a workstation, use an isolated virtual environment. The `venv/` folder shipped in the repo is built for the Raspberry Pi (Linux) — **create a fresh one on Windows**:

```powershell
# Windows PowerShell — from the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --port 8000
```

```bash
# macOS / Linux
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --port 8000
```

`--reload` restarts the server on code changes. The frontend served at port 8000 is the plain browser client in `web/static/` (HTML/CSS/JS edits show on refresh) — **no Node.js required**. The separate `frontend/` React app is optional and only needed if you are working on that client (`cd frontend && npm install && npm run dev`).

### 3. Run a test

1. Select a transformer with the **Transformer** picker (search by name/ID)
2. Enter the operator name
3. Click **▶ START TEST** (AUTO mode)
4. Steps stream into the **Step Results** list as they complete — each shows the
   measured node, the exact leads' **wire colours + numbers**, and PASS/FAIL. The
   test runs in the background; **tap any step** to reveal its measurement detail.

---

## The UI (tabs)

| Tab | Purpose |
|-----|---------|
| **Dashboard** | Select a transformer, run tests, watch the live diagram + Step Results |
| **Editor** | Build/edit a transformer visually and make measurement rules |
| **Dev Panel** | Manual relay control, diagnostic sequence, and Fault Finder |
| **Logs** | Browse the saved CSV/JSON reports in-app |

### Visual Transformer Builder (Editor tab)

The canvas IS the editor. Two modes toggle in the toolbar: **⬡ Topology** (build the
transformer) and **⚡ Validate** (make rules).

| Action | How |
|--------|-----|
| Add winding | Toolbar `[+ Primary]` / `[+ Secondary]` |
| Select & edit | Click a winding → property inspector on the right |
| Assign relay | Click a node's relay button → relay-picker popup (A1 / A2 / Group B by node) |
| Wire colour | Click a node's colour button → **swatch picker** (12 colours incl. Y/G stripe, White, Clear) |
| Add tap | Inspector `[+ Add Tap]` |
| Generate rules | Left panel: pick the energize winding → **Generate Rules** (auto-assigns relays + pins and builds the full sweep) |
| Make a rule by hand | Validate mode: set the **Active Excitation**, then click canvas nodes to define each measurement segment |
| Save | Toolbar `[💾 Save]` — writes JSON to `transformers/` |

Wire numbers shown on the diagram are the **relay numbers** (pin = relay); the
energizing winding's mains show `EN+` / `EN-`. See
[Wire numbers (pins) = relay numbers](#wire-numbers-pins--relay-numbers).

### Dev Panel

For bring-up and fault-finding (no test required):

- **Manual relay toggle** — tap any RL1–32 / RL37–40 relay to open/close it.
- **🔧 Diagnostic** — pulses each of the selected transformer's assigned relays in turn.
- **🔎 Fault Finder** — steps through every selectable relay one at a time; you mark
  each OK / Faulty, and it reports which relays are dead/stuck.
- **Relay MCU ⇄ PC console** — live view of the serial traffic.

---

## Hardware Architecture

### Relay Board

The relay groups have **functional** meaning — they are not generic banks.

| Group | Relays | Function |
|-------|--------|----------|
| **A1** | RL1 – RL16 | Measurement winding **START** nodes (voltmeter + probe) |
| **A2** | RL17 – RL32 | Measurement winding **END and TAP** nodes (voltmeter − probe) |
| Gate A | RL33 | Auto-closes when any A1-relay is active (firmware) |
| Gate B | RL34 | Auto-closes when any A2-relay is active (firmware) |
| **B** | RL37 – RL40 | **Energizing winding's TAP nodes** (excitation domain) |
| Gate B′ | RL35, RL36 | Auto-close when any Group-B relay is active (firmware) |

**Group A (RL1–32) measures normal windings. Group B (RL37–40) measures the energizing winding's taps.**

- A **normal winding** is routed through Group A: its start → A1, its end/taps → A2. A measurement step closes one A1 + one A2 relay.

#### Energizing winding — only its MAIN WIRES are external

This is the one asymmetric case, and it is easy to get wrong:

| Node | Relay | Why |
|------|-------|-----|
| Energizing winding **start_pin** | **none** | Carries mains; never switched. Permanently wired to the voltmeter **+ bus**. |
| Energizing winding **end_pin** | **none** | Carries mains; never switched. |
| Energizing winding **taps** | **one Group B relay each (RL37–40)** | Taps **are** measured — through **Group B**, *not* the A2 group. |

Because the energizing winding's start is *hard-wired* to the + bus, measuring one of its taps needs **no A relay** — the step closes **only that tap's Group B relay** (the firmware closes gates RL35/36 with it):

```
normal winding tap     →  [relay_a, relay_b, RL33, RL34]   (Group A)
energizing winding tap →  [relay_b, RL35, RL36]            (Group B — no A relay)
```

> ⚠️ **Group B has only 4 relays (RL37–40)** — so the energizing winding may carry at most **4 taps**. Auto-assign raises an error beyond that.

Its full excitation voltage is read by the **V1 meter**. Applying mains to a particular tap is still done **externally** — Group B is used to *measure* the taps, not to select which one is energized.

**Safety rule:** max ONE relay from A1, ONE from A2, ONE from Group B; **Groups A and B are mutually exclusive**. Enforced by the MCU firmware and mirrored in software (`RelayController._enforce_group_safety`, which drops Group A if both are ever requested).

The PC sends only selectable relays (RL1–32, RL37–40) via `SELECT`; the firmware closes the gate relays (RL33–36) automatically, so gates are **never sent** by the PC.

### Serial Communication

Two independent serial ports:

| Port | Direction | Protocol |
|------|-----------|----------|
| **Relay MCU** | PC → MCU | `SELECT <n>\r\n` / `CLEAR\r\n` / `STATUS\r\n` / `PHASE\r\n` (one relay per SELECT; firmware owns gates + exclusivity) |
| **Voltage Meter** | Meter → PC | Continuous `18.42\r\n` or `VOLTAGE:18.42\r\n` |

> The relay MCU runs the SELECT/CLEAR/STATUS firmware (Arduino Mega 2560 + 74HC595 expanders, active-low). It replies with text (`Selected Relay: 5`, `Matrix Cleared`, a STATUS block), not `OK`; the PC treats any reply without `ERROR` as success and detects the board by its STATUS response.

Configure via **Hardware → Serial Connections…** in the menu.

### Winding-polarity (phase) check

Each winding reads the correct RMS **magnitude** even if it is wound or connected
**backwards** — so magnitude alone can't catch a reversed winding. A separate
phase-detect circuit compares the energizing winding with the winding under test
and drives a **spare MCU pin** like an LED indicator:

- **signal present ⇒ IN-PHASE** (correct) — step PASSes
- **no signal ⇒ OUT-OF-PHASE** (polarity fault) — step **FAILs**, independent of the voltage

The check runs **only for a main winding's full measurement** (energizing ↔ main);
taps and the energizing winding itself are never phase-checked.

**Required MCU firmware — one command:** on receiving `PHASE\r\n`, reply with the
spare pin's state while the winding is energized:

```
PHASE\r\n   →   PHASE:1\r\n     // pin HIGH (signal present) → in-phase
PHASE\r\n   →   PHASE:0\r\n     // pin LOW  (no signal)      → out-of-phase
```

The PC sends `PHASE` inside the measurement window (relays closed, winding
energized) and parses the reply (`PHASE:1`/`1`/`IN` = in-phase; `PHASE:0`/`0`/`OUT`
= out-of-phase). If the board doesn't answer `PHASE` (older firmware) the step is
left **unchecked** — never a false fail. Disable the whole check with
`PHASE_CHECK=off` in `.env`.

The result appears in the **Phase** column of the Step Results table (`IN` / `OUT`
/ `—`), in the CSV/JSON logs (`phase` column), and in the database
(`measurements.phase_ok`).

### Energization

**Energization is external** — the operator applies mains voltage to the primary.  
The relay board routes only the **voltmeter probes**. No relay controls mains power.

---

## Transformer JSON Schema

### Wire numbers (pins) = relay numbers

**A wire's pin number IS its relay number.** There is nothing to cross-reference:
the wire landing on terminal `18` is driven by relay `18`. Pins are assigned with
the relays by *Generate Rules*.

The energizing winding's two **mains wires carry no relay** (they're external),
so they have no number — they are labelled **`EN+`** and **`EN-`** instead.

| Node | Wire # (pin) | Relay |
|------|--------------|-------|
| P1 start *(energizing)* | **EN+** | — none (mains, external) |
| P1 end *(energizing)*   | **EN-** | — none (mains, external) |
| P1 tap0 | 37 | 37 *(Group B)* |
| P1 tap1 | 38 | 38 *(Group B)* |
| S1 start | 1 | 1 *(A1)* |
| S1 end   | 17 | 17 *(A2)* |
| S1 tap0  | 18 | 18 *(A2)* |
| S2 start | 2 | 2 *(A1)* |

So `start_pin` / `end_pin` / `tap.pin` are **`int`, except** the energizing
winding's `start_pin`/`end_pin`, which are the strings `"EN+"` / `"EN-"`.

Pin fields are **read-only in the editor** — derived, not typed. Relay numbers
are shown as plain numbers everywhere (no `RL` prefix), and each wire's number is
printed **in line with the wire itself** on the diagram.

### Winding fields

```json
{
  "id": "S1",
  "winding_type": "basic_winding",
  "start_pin": 1,
  "end_pin": 17,
  "voltage": 115,
  "dot_polarity": true,
  "relay_a": 1,
  "relay_b": 17,
  "wire_color_start": "#2563eb",
  "wire_color_end": "#dc2626",
  "meas_channel": -1,
  "taps": []
}
```

| Field | Description |
|-------|-------------|
| `relay_a` | RL1–16 — connects `start_pin` to voltmeter **+** bus (`null` on the energizing winding) |
| `relay_b` | RL17–32 — connects `end_pin` to voltmeter **−** bus (`null` on the energizing winding) |
| `wire_color_start` / `wire_color_end` | Lead colour (hex from the palette) |
| `start_pin` / `end_pin` | Wire number = the relay number; `"EN+"` / `"EN-"` for the energizing winding's mains |
| `meas_channel` | Legacy ADC channel (`-1` = unused) |

> **Backward compatibility:** old fields `relay_id` → `relay_a`, `end_relay` → `relay_b` are still accepted.

### Tap fields

```json
{
  "pin": 19,
  "voltage": 12,
  "label": "CT (0V)",
  "relay_b": 19,
  "wire_color": "#16a34a",
  "meas_channel": 1
}
```

A tap takes a **single relay** on the B-side (− bus, RL17–32), stored in
`relay_b`. It is normally measured **start→tap**: `winding.relay_a` (start pin,
+ bus) paired with `tap.relay_b`. (Older configs may carry a `relay_a` on taps —
it is now ignored; the validator warns so you can remove it.)

**Exception — taps of the energizing winding.** They are still measured and
still stored in `relay_b`, but the value comes from **Group B (RL37–40)**, not
the A2 range. The winding itself has `relay_a: null` / `relay_b: null` (its main
wires are external, start hard-wired to the + bus), so the measurement closes
**only the tap's Group B relay**. Max 4 such taps. See
[Energizing winding](#energizing-winding--only-its-main-wires-are-external).

### Auto-matrix

When `auto_matrix.enabled = true`, the test engine auto-generates the full measurement sweep from the topology — no manual test steps required:

```json
"auto_matrix": {
  "enabled": true,
  "energize_winding": "P1",
  "energize_tap_index": null
}
```

---

## Wire colours & lead verification

A core job of the bench is confirming **each lead's wire colour is correct**. Every
node carries a colour, stored as hex:

- Winding: `wire_color_start`, `wire_color_end`
- Tap: `wire_color`

Colours are picked from a fixed **12-colour palette** (single source of truth in
[canvas.js](web/static/canvas.js)):

```
Black · Brown · Red · Orange · Yellow · Green · Blue · Violet · Grey · Y/G · White · Clear
```

Two colours render **truthfully** rather than as flat approximations — and are always
paired with their **name** (so verification survives colour-blindness):

- **Y/G** → green base with yellow stripes
- **White** → outlined so it's visible on the light editor canvas
- **Clear** → thin, pale, hatched (reads as transparent)

In the **Editor**, a node's colour is chosen from a touch **swatch picker**; the lead is
then drawn in that colour (taps drawn like main leads, slightly thinner).

In the **Step Results**, every step shows the exactly-two leads it measured on the
**measured** winding (the energizing winding is the reference and isn't shown), each as a
colour block **plus its lead number**:

- full winding → start + end leads (e.g. `S3` → `▉3  ▉21`)
- a tap → start + the tap (e.g. `S3 (T1)` → `▉3  ▨22`)

---

## Connecting Real Hardware

Configure the serial ports in `.env` (copied from `.env.example`):

1. **Relay MCU**: `SERIAL_PORT` + `SERIAL_BAUD` (default 115200)
2. **Voltage Meters**: `V1_PORT`/`V1_BAUD` (energizing) and `V2_PORT`/`V2_BAUD` (measurement)

At startup the backend probes every serial port and assigns devices by behaviour (voltmeters stream readings; the relay board answers a status query), so it does not depend on unstable `COM`/`/dev/ttyACM*` numbering. If the relay board is **not** found the server still starts — it logs `Relay board not detected — starting in disconnected state`, the UI reports DISCONNECTED, and tests stay disabled until the board is plugged in. Attach the board (and set the ports in `.env` if auto-detection needs a hint) to enable testing.

### UNI-T UT61B+ multimeter (USB-HID)

A UNI-T **UT61B+** can be used as a voltage meter instead of (or alongside) the
streaming serial meters. It is **not a serial port** — its cable is a WCH
HID-UART bridge (`1a86:e429`); other "+" models use a Silicon Labs CP2110
(`10c4:ea80`). Both are spoken to over USB-HID via the bundled
[`ut61eplus/`](ut61eplus/) driver. The meter does not stream — it is **polled**;
[hardware/voltage_meter_ut61.py](hardware/voltage_meter_ut61.py) runs a
background poll thread and exposes the same API as the serial meter, so it drops
straight into `DualVoltageService` / the test engine.

Select it per channel in `.env`:

```ini
V1_DRIVER=serial     # serial | ut61 | auto
V2_DRIVER=auto       # auto = UT61B+ if its HID bridge is plugged in, else serial
```

Setup (handled by `sudo bash setup_pi.sh`, or do it manually):

1. Python package `hidapi` (in `requirements.txt`) + system lib
   `libhidapi-hidraw0`.
2. udev rule for non-root access — copy [udev/99-unit-dmm.rules](udev/99-unit-dmm.rules)
   to `/etc/udev/rules.d/`, then
   `sudo udevadm control --reload-rules && sudo udevadm trigger`, and re-plug the
   meter. Without it you get `OSError: open failed`.

Reference scripts: [scan_usb.py](scan_usb.py) confirms the bridge is present;
[read_continuous.py](read_continuous.py) is a standalone polling loop.

---

## Database (persistence)

Results are persisted to an embedded **SQLite** database (SQLAlchemy) at
`data/atb.db`, in addition to the CSV/JSON logs. The schema **self-creates on first
run** — no migration step on the Pi. Single-bench deployment; the ORM keeps a later
move to a central PostgreSQL a connection-string change.

- **Tables:** `transformers`, `transformer_versions`, `operators`, `batches`,
  `units`, `measurements` (incl. `phase_ok`), `settings`, `audit_log`
  ([db/models.py](db/models.py)). Each unit stores a **config snapshot** for
  traceability.
- **Dual-write:** completed/skipped units + their per-step measurements are written
  by the test engine alongside the file logs — never blocking a test if the DB write
  fails ([db/store.py](db/store.py)).
- **Backfill existing logs:** `python -m scripts.import_logs` (idempotent).
- **Roll-up:** `GET /api/db/stats` returns units / pass / fail / yield% / batches.

Schema changes are handled by a lightweight additive migration in
[db/base.py](db/base.py); `alembic` is in `requirements.txt` for when the schema
grows further.

---

## Test Logs

Saved to `logs/` (auto-created):

- `<transformer_id>_<YYYYMMDD_HHMMSS>.csv`
- `<transformer_id>_<YYYYMMDD_HHMMSS>.json`

Browse them in the **Logs** tab.

---

## Development Notes

- **Thread safety** — serial drivers use `threading.Lock`; the test engine runs on a background thread; UI updates are pushed to the browser over WebSocket
- **Config-driven** — routing is derived purely from JSON topology; no hardcoded test sequences
- **Extensible** — subclass `RelayControllerInterface` / `VoltageReaderInterface` for custom hardware
- **Persistence is best-effort** — a DB write never blocks or fails a running test; file logs are always written too
- **Frontend has no build step** — `web/static/` is plain HTML/CSS/JS; the `?v=` query on the script tags in `index.html` is the cache-buster, bumped when JS/CSS change (hard-refresh to pick up edits)
- **Related docs** — [kiosk/README.md](kiosk/README.md) (kiosk setup) · `TRANSFORMER_EDITOR_AND_RULES_SPEC.md` (portable spec of the editor + rule engine)
