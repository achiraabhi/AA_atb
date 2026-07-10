"""
REST API routes for the ATB backend.
All endpoints delegate to the application objects stored in app.state.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.state_manager import AppState, TestMode
from hardware.hardware_interface import HardwareStatus

router = APIRouter(prefix="/api")


# ── helpers ──────────────────────────────────────────────────────────────────

def _get(request: Request, attr: str) -> Any:
    return getattr(request.app.state, attr)


# ── request/response models ───────────────────────────────────────────────────

class StartTestRequest(BaseModel):
    operator: str = ""
    mode: str = "AUTO"
    excitation_winding_id: Optional[str] = None
    applied_voltage: Optional[float] = None


class SelectTransformerRequest(BaseModel):
    transformer_id: str


class SerialConnectRequest(BaseModel):
    relay_port: Optional[str] = None
    relay_baud: int = 115200
    meter_port: Optional[str] = None
    meter_baud: int = 9600


class AssignVoltmeterRequest(BaseModel):
    target: str           # "v1" or "v2"
    port: str
    baud: int = 115200


class AssignRelayRequest(BaseModel):
    port: str
    baud: Optional[int] = None


class AssignDmmRequest(BaseModel):
    target: str           # "v1" or "v2"
    serial: str           # UNI-T meter serial number


class SaveTransformerRequest(BaseModel):
    data: Dict[str, Any]
    filename: Optional[str] = None


class GenerateRulesRequest(BaseModel):
    excitation_winding_id: str
    energize_tap_index: Optional[int] = None
    tolerance_percent: float = 5.0
    minimum_absolute_delta: float = 0.1
    save: bool = False


# ── transformer catalogue ─────────────────────────────────────────────────────

@router.get("/transformers")
def list_transformers(request: Request):
    cfg = _get(request, "config_loader")
    return [
        {
            "transformer_id": t.transformer_id,
            "name":           t.name,
            "type":           t.transformer_type,
            "rated_power_va": t.rated_power_va,
            "primary_count":  len(t.primary),
            "secondary_count": len(t.secondary),
            "auto_matrix":    t.auto_matrix.enabled,
        }
        for t in cfg.list_transformers()
    ]


@router.get("/transformers/{transformer_id}")
def get_transformer(transformer_id: str, request: Request):
    cfg = _get(request, "config_loader")
    raw = cfg.get_raw_json(transformer_id)
    if raw is None:
        raise HTTPException(404, f"Transformer '{transformer_id}' not found")
    return raw


@router.post("/transformers")
def save_transformer(body: SaveTransformerRequest, request: Request):
    cfg = _get(request, "config_loader")
    path = cfg.save_transformer(body.data, body.filename)
    return {"saved": True, "path": path}


# Relay groups for auto-assignment (mirror hardware.protocol ranges)
_RL_A_MIN, _RL_A_MAX = 1, 16     # Side-A relays → voltmeter + probe
_RL_B_MIN, _RL_B_MAX = 17, 32    # Side-B relays → voltmeter − probe


def _auto_assign_relays(raw: Dict[str, Any], ew_id: str) -> None:
    """
    Auto-assign relays to every winding/tap node, following the bench rules:

      • Energising winding:  NO measurement relays — its terminals are
        permanent wiring and it is never routed through Group A. (Excitation
        and its tap selection are handled externally.)
      • Measurement windings:  start → A1 (RL1-16), end → A2 (RL17-32).
      • Measurement-winding taps:  one A2 relay each (RL17-32),
        measured start→tap = winding.relay_a + tap.relay_b.

    Mutates ``raw`` in place. Raises HTTPException(400) if a group is exhausted.
    """
    counters = {"a": _RL_A_MIN, "b": _RL_B_MIN}

    def take(group: str) -> int:
        if group == "a":
            if counters["a"] > _RL_A_MAX:
                raise HTTPException(400, "Ran out of Group A relays (RL1–RL16)")
            v = counters["a"]; counters["a"] += 1
        else:
            if counters["b"] > _RL_B_MAX:
                raise HTTPException(400, "Ran out of Group B relays (RL17–RL32)")
            v = counters["b"]; counters["b"] += 1
        return v

    windings = (raw.get("primary") or []) + (raw.get("secondary") or [])
    # Energising winding first so it takes the lowest relay numbers.
    ordered = sorted(windings, key=lambda w: 0 if w.get("id") == ew_id else 1)

    for w in ordered:
        if w.get("id") == ew_id:
            # Energizing winding: its start/end are permanent dedicated wiring
            # (never switched) and it is NEVER routed through Group A. Its
            # excitation voltage is read by the V1 meter, and excitation-tap
            # selection (Group B, R37-40) is handled externally — so it takes
            # no measurement relays at all.
            w["relay_a"] = None
            w["relay_b"] = None
            for tap in (w.get("taps") or []):
                tap.pop("relay_a", None)
                tap["relay_b"] = None
        else:
            # Measurement winding: start → A1 (RL1-16), end → A2 (RL17-32),
            # each tap → A2 (RL17-32). Measured start→(end|tap).
            w["relay_a"] = take("a")
            w["relay_b"] = take("b")
            for tap in (w.get("taps") or []):
                tap.pop("relay_a", None)
                tap["relay_b"] = take("b")


@router.post("/transformers/{transformer_id}/generate-rules")
def generate_rules(transformer_id: str, body: GenerateRulesRequest, request: Request):
    import uuid
    from core.measurement_matrix_engine import MeasurementMatrixEngine

    cfg_loader = _get(request, "config_loader")
    config = cfg_loader.get_transformer(transformer_id)
    if not config:
        raise HTTPException(404, f"Transformer '{transformer_id}' not found")

    try:
        steps = MeasurementMatrixEngine().build_matrix(
            config,
            excitation_winding_id=body.excitation_winding_id,
            energize_tap_index=body.energize_tap_index,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    ew_id  = body.excitation_winding_id
    et_idx = body.energize_tap_index

    # Auto-assign relays onto the winding/tap nodes per the bench rules.
    raw = cfg_loader.get_raw_json(transformer_id)
    _auto_assign_relays(raw, ew_id)

    # Excitation segment node_b: tap reference or full winding end
    exc_node_b = f"{ew_id}:tap{et_idx}" if et_idx is not None else f"{ew_id}:end"

    rules = []
    for step in steps:
        to_wid  = step.to_winding_id
        to_tap  = step.to_tap_index
        meas_node_b = f"{to_wid}:tap{to_tap}" if to_tap is not None else f"{to_wid}:end"
        rules.append({
            "id": f"auto_{uuid.uuid4().hex[:8]}",
            "excitation_segment": {
                "node_a": ew_id,
                "node_b": exc_node_b,
                "nominal_voltage": step.nominal_input_voltage,
            },
            "measurement_segment": {
                "node_a": to_wid,
                "node_b": meas_node_b,
                "nominal_voltage": step.nominal_output_voltage,
            },
            "tolerance_percent":      body.tolerance_percent,
            "minimum_absolute_delta": body.minimum_absolute_delta,
            "measurement_type": "AC",
            "critical": True,
            "enabled":  True,
            "_label":   step.description,
        })

    # Persist rules + auto_matrix alongside the relay assignments already on `raw`.
    raw["ratio_rules"] = rules
    raw["auto_matrix"] = {
        "enabled": False,
        "energize_winding": ew_id,
        "energize_tap_index": et_idx,
    }
    if body.save:
        cfg_loader.save_transformer(raw)

    return {
        "rules":     rules,
        "count":     len(rules),
        "primary":   raw.get("primary", []),
        "secondary": raw.get("secondary", []),
    }


@router.delete("/transformers/{transformer_id}")
def delete_transformer(transformer_id: str, request: Request):
    cfg = _get(request, "config_loader")
    tc  = cfg.get_transformer(transformer_id)
    if tc is None:
        raise HTTPException(404, f"Transformer '{transformer_id}' not found")
    if tc.file_path and os.path.exists(tc.file_path):
        os.remove(tc.file_path)
    cfg.scan_and_load()
    return {"deleted": True}


# ── application state ─────────────────────────────────────────────────────────

@router.get("/state")
def get_state(request: Request):
    sm  = _get(request, "state_manager")
    session = sm.current_session
    return {
        "app_state":             sm.app_state.value,
        "test_mode":             sm.test_mode.value,
        "selected_transformer":  sm.selected_transformer_id,
        "operator":              sm.operator_name,
        "current_step_index":    sm.current_step_index,
        "total_steps":           sm.total_steps,
        "progress_pct":          sm.progress_pct,
        "active_primary":        sm.active_primary,
        "active_secondary":      sm.active_secondary,
        "current_measurement":   sm.current_measurement,
        "current_expected":      sm.current_expected,
        "current_tolerance":     sm.current_tolerance,
        "excitation_winding_id":      sm.excitation_winding_id,
        "applied_voltage":            sm.applied_voltage,
        "nominal_excitation_voltage": sm.nominal_excitation_voltage,
        "ratio_factor":               sm.ratio_factor,
        "session": {
            "transformer_id": session.transformer_id,
            "operator":       session.operator,
            "start_time":     session.start_time,
            "total_steps":    session.total_steps,
            "passed_steps":   session.passed_steps,
            "overall_pass":   session.overall_pass,
        } if session else None,
    }


@router.get("/transformers/{transformer_id}/windings")
def get_windings(transformer_id: str, request: Request):
    """Return the flat winding list for excitation winding selection."""
    cfg = _get(request, "config_loader")
    tc  = cfg.get_transformer(transformer_id)
    if tc is None:
        raise HTTPException(404, f"Transformer '{transformer_id}' not found")
    return [
        {
            "id":              w.id,
            "nominal_voltage": w.nominal_voltage,
            "can_energize":    w.can_energize,
            "side":            "primary" if w in tc.primary else "secondary",
        }
        for w in tc.windings
    ]


@router.get("/relays")
def get_relays(request: Request):
    sm = _get(request, "state_manager")
    return {"relays": {str(k): v for k, v in sm.relay_states.items()}}


@router.post("/relays/sequence")
def start_relay_sequence(request: Request):
    """
    Diagnostic click-test: energize the selected transformer's gates and each
    winding's assigned relays, one at a time, holding each closed for 1 second.

    Gates (RL33/34) are firmware-controlled and cannot be pulsed on their own —
    they close automatically with each winding relay's group and show CLOSED for
    that relay's 1-second window.
    """
    from core.relay_sequence import build_relay_steps

    sm  = _get(request, "state_manager")
    cfg = _get(request, "config_loader")
    seq = _get(request, "relay_sequencer")

    if sm.app_state in (AppState.TESTING, AppState.PAUSED, AppState.STOPPING):
        raise HTTPException(409, "Cannot run relay sequence while a test is active")
    if seq.running:
        raise HTTPException(409, "Relay sequence already running")

    tid = sm.selected_transformer_id
    if not tid:
        raise HTTPException(400, "No transformer selected")
    config = cfg.get_transformer(tid)
    if config is None:
        raise HTTPException(404, f"Transformer '{tid}' not found")

    steps = build_relay_steps(config)
    if not steps:
        raise HTTPException(400, "No relays are assigned to this transformer")

    seq.start(steps)
    return {
        "running":  True,
        "steps":    len(steps),
        "dwell_ms": 1000,
        "relays":   [rid for _, rid in steps],
    }


@router.post("/relays/sequence/stop")
def stop_relay_sequence(request: Request):
    seq = _get(request, "relay_sequencer")
    seq.stop()
    return {"running": False}


@router.post("/transformer/select")
def select_transformer(body: SelectTransformerRequest, request: Request):
    sm  = _get(request, "state_manager")
    cfg = _get(request, "config_loader")
    if not cfg.get_transformer(body.transformer_id):
        raise HTTPException(404, f"Transformer '{body.transformer_id}' not found")
    sm.selected_transformer_id = body.transformer_id
    return {"selected": body.transformer_id}


# ── test control ──────────────────────────────────────────────────────────────

@router.post("/test/start")
def start_test(body: StartTestRequest, request: Request):
    sm  = _get(request, "state_manager")
    eng = _get(request, "test_engine")
    if body.mode.upper() == "MANUAL":
        sm.test_mode = TestMode.MANUAL
    else:
        sm.test_mode = TestMode.AUTO
    if body.operator:
        sm.operator_name = body.operator
    eng.start(
        operator=body.operator,
        excitation_winding_id=body.excitation_winding_id,
        applied_voltage=body.applied_voltage,
    )
    return {"started": True}


@router.post("/test/stop")
def stop_test(request: Request):
    eng = _get(request, "test_engine")
    eng.stop()
    return {"stopped": True}


@router.post("/test/pause")
def pause_test(request: Request):
    eng = _get(request, "test_engine")
    eng.pause()
    return {"paused": True}


@router.post("/test/resume")
def resume_test(request: Request):
    eng = _get(request, "test_engine")
    eng.resume()
    return {"resumed": True}


@router.post("/test/next-step")
def next_step(request: Request):
    eng = _get(request, "test_engine")
    eng.next_step()
    return {"next": True}


@router.post("/test/emergency-stop")
def emergency_stop(request: Request):
    eng = _get(request, "test_engine")
    eng.emergency_stop()
    return {"emergency_stop": True}


class SkipUnitRequest(BaseModel):
    reason: str = ""


@router.post("/test/next-unit")
def next_unit(request: Request):
    eng = _get(request, "test_engine")
    eng.next_unit()
    return {"next_unit": True}


@router.post("/test/skip-unit")
def skip_unit(body: SkipUnitRequest, request: Request):
    eng = _get(request, "test_engine")
    eng.skip_unit(reason=body.reason)
    return {"skipped": True}


@router.post("/test/retry-unit")
def retry_unit(request: Request):
    eng = _get(request, "test_engine")
    eng.retry_unit()
    return {"retry": True}


@router.post("/test/complete-batch")
def complete_batch(request: Request):
    eng = _get(request, "test_engine")
    eng.complete_batch()
    return {"completed": True}


@router.get("/batch/state")
def get_batch_state(request: Request):
    sm = _get(request, "state_manager")
    batch = sm.batch_session
    if batch is None:
        return {"active": False}
    return batch.to_summary(active=True)


# ── hardware status ───────────────────────────────────────────────────────────

@router.get("/hardware/status")
def hardware_status(request: Request):
    hw = _get(request, "hardware")
    health = hw.health_check()
    return {k: v.value for k, v in health.items()}


@router.post("/hardware/connect")
def hardware_connect(body: SerialConnectRequest, request: Request):
    # Serial connection is established at startup from .env; report live status.
    hw = _get(request, "hardware")
    health = hw.health_check()
    connected = all(v == HardwareStatus.CONNECTED for v in health.values())
    return {"connected": connected, "mode": "real"}


@router.post("/hardware/relay/connect")
def relay_connect(request: Request):
    """
    (Re)connect the relay board on demand — e.g. after it was unplugged or hit a
    USB I/O error mid-session. Scans free serial ports for the relay MCU
    (skipping ports the voltage meters hold) and opens it.
    """
    from hardware import port_scanner

    hw    = _get(request, "hardware")
    relay = hw.relays
    if getattr(relay, "_serial", None) is not None and relay._serial.connected:
        return {"connected": True,
                "port": getattr(request.app.state, "relay_port", None),
                "message": "already connected"}

    # Don't probe ports a meter already holds open (it would steal their bytes).
    volt = getattr(request.app.state, "volt_service", None)
    held = set()
    for m in (getattr(volt, "_v1", None), getattr(volt, "_v2", None)):
        p = getattr(m, "_port_name", None)
        if p:
            held.add(p)

    baud = int(os.getenv("SERIAL_BAUD", "115200"))
    cfg_port = os.getenv("SERIAL_PORT")

    # Try the configured port first, then any other free port, classifying each.
    devices = [d["device"] for d in port_scanner.list_devices()]
    ordered = ([cfg_port] if cfg_port in devices else []) + [d for d in devices if d != cfg_port]

    relay_port = None
    for dev in ordered:
        if dev in held:
            continue
        if port_scanner.classify_port(dev, baud) == "relay":
            relay_port = dev
            break

    if not relay_port:
        raise HTTPException(404, "No relay MCU found on a free serial port")
    if not relay.connect(relay_port, baud):
        raise HTTPException(502, f"Failed to open relay MCU on {relay_port}")

    request.app.state.relay_port = relay_port
    return {"connected": True, "port": relay_port}


@router.get("/serial/ports")
def list_serial_ports(request: Request):
    """
    Scan the host and classify each serial port (voltmeter / relay / unknown)
    so the operator can assign V1/V2. Ports the app already holds open are
    reported from their known role instead of being re-probed.
    """
    from hardware import port_scanner

    volt       = _get(request, "volt_service")
    relay_port = getattr(request.app.state, "relay_port", None)
    readings   = volt.get_readings()

    v1_port = getattr(volt._v1, "_port_name", None)
    v2_port = getattr(volt._v2, "_port_name", None)

    # Map device → role for ports we hold open (don't probe these).
    assigned_map: Dict[str, str] = {}
    if readings.get("v1_connected") and v1_port:
        assigned_map[v1_port] = "v1"
    if readings.get("v2_connected") and v2_port:
        assigned_map[v2_port] = "v2"
    if relay_port:
        assigned_map[relay_port] = "relay"

    held = set(assigned_map)
    known = {dev: ("relay" if role == "relay" else "voltmeter")
             for dev, role in assigned_map.items()}

    ports = port_scanner.scan(skip=held, known=known)
    for p in ports:
        p["assigned"] = assigned_map.get(p["device"])
    return {"ports": ports}


@router.post("/serial/voltmeter")
def assign_voltmeter(body: AssignVoltmeterRequest, request: Request):
    """Manually assign a serial port to the V1 or V2 voltage meter at runtime."""
    if body.target.lower() not in ("v1", "v2"):
        raise HTTPException(status_code=400, detail="target must be 'v1' or 'v2'")
    # Keep the relay board on its own port — never let a meter take it.
    relay_port = getattr(request.app.state, "relay_port", None)
    if relay_port and body.port == relay_port:
        raise HTTPException(status_code=409,
                            detail=f"{body.port} is assigned to the relay board")
    volt = _get(request, "volt_service")
    ok = volt.set_meter_port(body.target, body.port, body.baud)
    if not ok:
        raise HTTPException(
            status_code=502,
            detail=f"Could not open {body.port} for {body.target.upper()}",
        )
    return {"connected": True, "target": body.target.lower(), "port": body.port}


@router.get("/serial/dmms")
def list_dmms(request: Request):
    """List the UNI-T UT61B+ meters on USB-HID and which channel each is on."""
    from hardware.voltage_meter_ut61 import VoltageMeterUT61
    volt = _get(request, "volt_service")
    v1s = getattr(volt._v1, "serial", None) if volt.v1_driver == "ut61" else None
    v2s = getattr(volt._v2, "serial", None) if volt.v2_driver == "ut61" else None
    meters = VoltageMeterUT61.list_meters()
    for m in meters:
        m["assigned"] = ("v1" if m["serial"] and m["serial"] == v1s
                         else "v2" if m["serial"] and m["serial"] == v2s
                         else None)
    return {"meters": meters, "v1": v1s, "v2": v2s,
            "v1_driver": volt.v1_driver, "v2_driver": volt.v2_driver}


@router.post("/serial/dmm")
def assign_dmm(body: AssignDmmRequest, request: Request):
    """Assign a specific UNI-T meter (by serial) to V1 or V2; swaps if needed."""
    if body.target.lower() not in ("v1", "v2"):
        raise HTTPException(status_code=400, detail="target must be 'v1' or 'v2'")
    volt = _get(request, "volt_service")
    ok = volt.set_meter_dmm(body.target, body.serial)
    if not ok:
        raise HTTPException(status_code=502,
                            detail=f"Could not open meter sn={body.serial} for {body.target.upper()}")
    return {"connected": True, "target": body.target.lower(), "serial": body.serial}


@router.post("/serial/relay")
def assign_relay(body: AssignRelayRequest, request: Request):
    """
    Manually assign a serial port to the relay board at runtime, keeping it
    separate from the voltage meters (a port a meter holds is rejected).
    """
    hw    = _get(request, "hardware")
    relay = hw.relays

    # Don't steal a port a meter currently holds open.
    volt = getattr(request.app.state, "volt_service", None)
    held = set()
    for m in (getattr(volt, "_v1", None), getattr(volt, "_v2", None)):
        p = getattr(m, "_port_name", None)
        if p:
            held.add(p)
    if body.port in held:
        raise HTTPException(status_code=409,
                            detail=f"{body.port} is in use by a voltage meter")

    baud = body.baud or int(os.getenv("SERIAL_BAUD", "115200"))
    if not relay.connect(body.port, baud):
        raise HTTPException(status_code=502,
                            detail=f"Could not open relay board on {body.port}")
    request.app.state.relay_port = body.port
    return {"connected": True, "port": body.port}


# ── logs ──────────────────────────────────────────────────────────────────────

@router.get("/logs")
def list_logs(request: Request):
    logs_dir = Path(__file__).parent.parent.parent / "logs"
    if not logs_dir.exists():
        return {"files": []}
    files = []
    for f in sorted(logs_dir.iterdir(), reverse=True):
        if f.suffix in (".csv", ".json"):
            files.append({
                "name":     f.name,
                "size":     f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
    return {"files": files}


@router.get("/logs/{filename}")
def get_log(filename: str, request: Request):
    logs_dir = Path(__file__).parent.parent.parent / "logs"
    path = (logs_dir / filename).resolve()
    if not str(path).startswith(str(logs_dir.resolve())):
        raise HTTPException(400, "Invalid filename")
    if not path.exists():
        raise HTTPException(404, "Log file not found")
    with open(path) as f:
        content = f.read()
    return {"filename": filename, "content": content}
