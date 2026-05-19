# Transformer Post-Assembly Test Bench (ATB)

Industrial-grade desktop application for automated no-load testing of assembled transformers.

## Features

- **JSON-driven transformer configuration** — add transformers without code changes
- **Dynamic diagram renderer** — canvas-based, fully programmatic coil drawings with polarity dots, pins, taps, voltage labels
- **Real-time animation** — active windings glow, current-flow particles animate, pins pulse, PASS/FAIL flash
- **Automated test sequencer** — reads test definitions, switches relays, waits stabilization, reads voltage, compares with tolerance
- **AUTO / MANUAL mode** — full auto sequence or step-by-step manual control
- **Hardware abstraction layer** — mock hardware included; swap in real drivers without touching UI
- **CSV + JSON test logging** — timestamped results per session
- **In-app transformer editor** — JSON editor dialog to add/edit transformer configs at runtime

---

## Project Structure

```
AA_atb/
├── main.py                     Entry point
├── requirements.txt
│
├── ui/
│   ├── main_window.py          Root CTk window, layout, menu
│   ├── transformer_canvas.py   Animated diagram widget
│   ├── control_panel.py        Buttons, relay grid, progress bar
│   ├── status_panel.py         Live measurement cards, history table
│   └── dialogs.py              Add transformer, view logs, about
│
├── core/
│   ├── config_loader.py        JSON scanner / validator / parser
│   ├── state_manager.py        Observable state store (thread-safe)
│   ├── sequence_manager.py     Resolves relay assignments per step
│   ├── test_engine.py          Background test orchestrator
│   ├── transformer_renderer.py Canvas drawing + animation engine
│   └── logger.py               CSV/JSON logging + console feed
│
├── hardware/
│   ├── hardware_interface.py   Abstract base classes (ABCs)
│   ├── mock_hardware.py        Simulated relay + ADC hardware
│   ├── relay_controller.py     (placeholder for real driver)
│   └── voltage_reader.py       (placeholder for real driver)
│
├── transformers/
│   ├── transformer_a.json      GE Healthcare 115/115V isolation
│   ├── transformer_b.json      Multi-tap 230/115/24V
│   └── transformer_c.json      Toroidal audio 240/2×12V
│
├── assets/                     (icons, fonts — future use)
└── logs/                       Auto-created; CSV + JSON results
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the application

```bash
python main.py
```

### 3. Run a test

1. Select a transformer from the top dropdown
2. Enter operator name
3. Click **▶ START TEST** (AUTO mode)
4. Watch the diagram animate and results populate

---

## Adding a New Transformer

### Option A — UI Editor (no code required)

1. Menu → **File → Add Transformer…**
2. Edit the JSON template in the editor
3. Click **Validate** then **Save**
4. The new transformer appears in the selector immediately

### Option B — JSON File

Create a `.json` file in the `transformers/` folder:

```json
{
  "name": "My Transformer",
  "transformer_id": "my_transformer_001",
  "type": "isolating_transformer",
  "rated_power_va": 250,
  "rated_frequency_hz": 50,

  "primary": [
    {
      "id": "P1",
      "start_pin": 1,
      "end_pin": 2,
      "voltage": 230,
      "dot_polarity": true,
      "relay_id": 0,
      "taps": []
    }
  ],

  "secondary": [
    {
      "id": "S1",
      "start_pin": 5,
      "end_pin": 6,
      "voltage": 24,
      "dot_polarity": true,
      "relay_id": 1,
      "taps": []
    }
  ],

  "tests": [
    {
      "from": "P1",
      "to": "S1",
      "expected_voltage": 24.0,
      "tolerance_percent": 5.0,
      "measurement_channel": 0,
      "stabilization_delay_ms": 500,
      "relay_map": {},
      "description": "Primary → Secondary 24V check"
    }
  ]
}
```

Then use **File → Reload Transformers**.

---

## Transformer JSON Schema

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Display name |
| `transformer_id` | string | Unique slug (auto-generated from name if omitted) |
| `type` | string | Free text type label |
| `rated_power_va` | number | Rated power in VA |
| `rated_frequency_hz` | number | Rated frequency |
| `primary` | array | Primary winding definitions |
| `secondary` | array | Secondary winding definitions |
| `tests` | array | Test step definitions |

### Winding object

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Winding ID, e.g. `"P1"`, `"S2"` |
| `start_pin` | int | Pin number at top of winding |
| `end_pin` | int | Pin number at bottom of winding |
| `voltage` | float | Rated voltage |
| `dot_polarity` | bool | Show IEC polarity dot on start_pin side |
| `relay_id` | int | Relay channel that energizes this winding |
| `taps` | array | Intermediate tap points (optional) |

### Tap object

```json
{ "pin": 2, "position_frac": 0.5, "voltage": 115 }
```

`position_frac`: 0.0 = top of winding, 1.0 = bottom.

### Test step object

| Field | Type | Description |
|-------|------|-------------|
| `from` | string | Winding ID to energize |
| `to` | string | Winding ID to measure |
| `expected_voltage` | float | Nominal output voltage |
| `tolerance_percent` | float | Acceptance band (±%) |
| `measurement_channel` | int | ADC channel to read |
| `stabilization_delay_ms` | int | Wait time after relay close (ms) |
| `relay_map` | object | Override relay states: `{"0": true, "1": false}` |
| `description` | string | Human-readable step description |

---

## Connecting Real Hardware

1. Create a class in `hardware/` that subclasses `RelayControllerInterface`
   and `VoltageReaderInterface` from `hardware/hardware_interface.py`
2. Implement all abstract methods
3. In `main.py`, replace `MockHardwareManager()` with your real manager

The UI and test engine require zero changes.

---

## Test Logs

Logs are saved to `logs/` as:
- `<transformer_id>_<YYYYMMDD_HHMMSS>.csv`
- `<transformer_id>_<YYYYMMDD_HHMMSS>.json`

View in-app: **File → View Logs…**

---

## Future Expansion

The architecture supports:
- Database backend (replace logger CSV with SQLAlchemy)
- Barcode scanner (add serial reader, set operator/transformer from scan)
- PDF reports (use ReportLab, attach to session end)
- Remote monitoring (WebSocket server on state_manager events)
- SCADA integration (Modbus TCP server wrapping relay/voltage interfaces)
- Calibration routines (new test type in sequence_manager)
