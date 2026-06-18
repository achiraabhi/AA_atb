# After-Assembling Test Bench (ATB)

Industrial-grade web application for automated no-load testing of assembled transformers.  
Built with Python + FastAPI (backend) and a browser front-end. Drives a physical 34-relay board and dual voltage meters over serial.

---

## Features

| Feature | Details |
|---------|---------|
| **Visual transformer builder** | Canvas-primary editor — drag, right-click, click nodes to assign relays |
| **34-relay board support** | RL1–16 (voltmeter +), RL17–32 (voltmeter −), RL33/34 gates |
| **Dual serial communication** | Separate ports for relay MCU and external voltage meter |
| **Auto-matrix test engine** | Derives full measurement sweep from topology — no hardcoded sequences |
| **Animated diagram** | Live coil glow, current-flow particles, per-tap highlighting, PASS/FAIL flash |
| **AUTO / MANUAL / STEP mode** | Full auto, manual gate, or single-step advance |
| **CSV + JSON logging** | Timestamped results per session, viewable in-app |

---

## Project Structure

```
AA_atb/
├── requirements.txt
│
├── backend/                        FastAPI web server
│   ├── main.py                     App entry point + static file serving
│   ├── api/routes.py               REST endpoints
│   └── websocket/                  Live state push (manager, broadcaster, events)
│
├── web/static/                     Browser front-end served by the backend
│   ├── index.html
│   ├── app.js / canvas.js / editor.js
│   └── style.css, fonts/
│
├── frontend/                       Optional React/Vite client (TypeScript)
│
├── core/
│   ├── config_loader.py            JSON scanner / parser / dataclasses
│   ├── state_manager.py            Thread-safe observable state store
│   ├── sequence_manager.py         Resolves test steps from config
│   ├── test_engine.py              Background test orchestrator (threading)
│   ├── measurement_matrix_engine.py Auto-generates measurement sweep from topology
│   ├── validator.py                Config validation (errors / warnings / info)
│   └── logger.py                   CSV/JSON logging + console feed
│
├── hardware/
│   ├── hardware_interface.py       Abstract base classes (ABCs) + relay constants
│   ├── protocol.py                 Serial message builders + constants (RL1–RL34)
│   ├── relay_serial.py             Relay MCU serial driver (thread-safe)
│   ├── voltage_meter_serial.py     Continuous-stream voltage reader (background thread)
│   ├── relay_controller.py         Full real relay controller with safety enforcement
│   ├── routing_engine.py           Maps winding/tap pairs → [relay_a, relay_b, 33, 34]
│   ├── measurement_manager.py      relay-switch → stabilize → sample → average cycle
│   └── serial_manager.py           Lifecycle manager for both serial connections
│
├── transformers/
│   ├── transformer_a.json          GE Healthcare 115/115V isolation
│   ├── transformer_b.json          Multi-tap 200–250V / 115V
│   ├── transformer_c.json          Toroidal audio 240V / 2×12V
│   ├── transformer_d.json          Center-tap secondary 230V / 0-12-0V
│   └── new_transformer.json        Example / template
│
├── assets/                         Icons, fonts (future use)
└── logs/                           Auto-created; CSV + JSON test results
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

On the Raspberry Pi use `./start.sh`; on Windows use `start_web.bat`.
Then open **http://localhost:8000** in a browser. Set the serial ports in `.env` first — the relay board must be connected or the backend will refuse to start.

### 3. Run a test

1. Select a transformer from the top dropdown
2. Enter operator name
3. Click **▶ START TEST** (AUTO mode)
4. Watch the diagram animate and results populate in the status panel

---

## Visual Transformer Builder

Open via **File → New Transformer (Editor)** or the **✎ Editor** button in the toolbar.

The canvas IS the editor — no forms required:

| Action | How |
|--------|-----|
| Add winding | Toolbar `[+ Primary]` / `[+ Secondary]` buttons |
| Select & edit | Left-click any winding → property panel opens on right |
| Assign relay | Left-click any pin or tap node → relay picker popup |
| Add tap | Right-click winding → **Add Tap**, or use the property panel |
| Reorder windings | Drag the `≡` handle above each winding |
| Context menu | Right-click winding, tap, pin, or canvas background |
| Validate | Toolbar `[✔ Validate]` — shows overlay markers + popup |
| Simulate | Toolbar `[▶ Simulate]` — animates measurement paths |
| Save | Toolbar `[💾 Save]` — writes JSON to `transformers/` folder |

---

## Hardware Architecture

### Relay Board (34 relays)

| Group | Relays | Function |
|-------|--------|----------|
| Side A | RL1 – RL16 | Voltmeter **+** probe selection |
| Side B | RL17 – RL32 | Voltmeter **−** probe selection |
| Gate A | RL33 | Auto-closes when any RL1-16 is active |
| Gate B | RL34 | Auto-closes when any RL17-32 is active |

**Safety rule:** max ONE relay from RL1–16 and ONE from RL17–32 active simultaneously. Enforced in software (RelayController) and recommended in MCU firmware.

Each measurement step activates exactly **[relay_a, relay_b, RL33, RL34]**.

### Serial Communication

Two independent serial ports:

| Port | Direction | Protocol |
|------|-----------|----------|
| **Relay MCU** | PC → MCU | `SET_RELAYS:1,17,33,34\r\n` → `OK\r\n` |
| **Voltage Meter** | Meter → PC | Continuous `18.42\r\n` or `VOLTAGE:18.42\r\n` |

Configure via **Hardware → Serial Connections…** in the menu.

### Energization

**Energization is external** — the operator applies mains voltage to the primary.  
The relay board routes only the **voltmeter probes**. No relay controls mains power.

---

## Transformer JSON Schema

### Winding fields

```json
{
  "id": "P1",
  "winding_type": "basic_winding",
  "start_pin": 1,
  "end_pin": 2,
  "voltage": 230,
  "dot_polarity": true,
  "relay_a": 1,
  "relay_b": null,
  "meas_channel": -1,
  "taps": []
}
```

| Field | Description |
|-------|-------------|
| `relay_a` | RL1–16 — connects `start_pin` to voltmeter **+** bus |
| `relay_b` | RL17–32 — connects `end_pin` to voltmeter **−** bus |
| `meas_channel` | Legacy ADC channel (`-1` = unused) |

> **Backward compatibility:** old fields `relay_id` → `relay_a`, `end_relay` → `relay_b` are still accepted.

### Tap fields

```json
{
  "pin": 5,
  "voltage": 12,
  "label": "CT (0V)",
  "relay_a": 3,
  "relay_b": 19,
  "meas_channel": 1
}
```

`relay_b` on tap = start→tap measurement (winding.relay_a + tap.relay_b)  
`relay_a` on tap = tap→end measurement (tap.relay_a + winding.relay_b)

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

## Connecting Real Hardware

Configure the serial ports in `.env` (copied from `.env.example`):

1. **Relay MCU**: `SERIAL_PORT` + `SERIAL_BAUD` (default 115200)
2. **Voltage Meters**: `V1_PORT`/`V1_BAUD` (energizing) and `V2_PORT`/`V2_BAUD` (measurement)

The backend connects to the relay board at startup and **raises an error if the port is unavailable** — the physical board is required to run.

---

## Test Logs

Saved to `logs/` (auto-created):

- `<transformer_id>_<YYYYMMDD_HHMMSS>.csv`
- `<transformer_id>_<YYYYMMDD_HHMMSS>.json`

View in-app: **File → View Logs…**

---

## Development Notes

- **Thread safety** — serial drivers use `threading.Lock`; UI updates are pushed to the browser over WebSocket
- **Config-driven** — routing is derived purely from JSON topology; no hardcoded test sequences
- **Extensible** — subclass `RelayControllerInterface` / `VoltageReaderInterface` for custom hardware
