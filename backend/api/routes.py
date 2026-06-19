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

      • Energising winding:  start → Group A,  end → NO relay (common reference).
      • Other windings:  start → Group A,  end → Group B.
      • Taps (any winding):  a single relay each, from Group B
        (measured start→tap = winding.relay_a + tap.relay_b).

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
            w["relay_a"] = take("a")          # start → Group A
            w["relay_b"] = None               # end → no relay (common)
        else:
            w["relay_a"] = take("a")          # start → Group A
            w["relay_b"] = take("b")          # end → Group B
        # Taps always take a single Group B relay; routed start→tap with the
        # winding's start relay (relay_a). Drop any legacy A-side tap relay.
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
    volt = _get(request, "volt_service")
    ok = volt.set_meter_port(body.target, body.port, body.baud)
    if not ok:
        raise HTTPException(
            status_code=502,
            detail=f"Could not open {body.port} for {body.target.upper()}",
        )
    return {"connected": True, "target": body.target.lower(), "port": body.port}


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
