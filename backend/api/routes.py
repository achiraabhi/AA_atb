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

router = APIRouter(prefix="/api")


# ── helpers ──────────────────────────────────────────────────────────────────

def _get(request: Request, attr: str) -> Any:
    return getattr(request.app.state, attr)


# ── request/response models ───────────────────────────────────────────────────

class StartTestRequest(BaseModel):
    operator: str = ""
    mode: str = "AUTO"


class SelectTransformerRequest(BaseModel):
    transformer_id: str


class SerialConnectRequest(BaseModel):
    relay_port: Optional[str] = None
    relay_baud: int = 115200
    meter_port: Optional[str] = None
    meter_baud: int = 9600


class SaveTransformerRequest(BaseModel):
    data: Dict[str, Any]
    filename: Optional[str] = None


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
        "session": {
            "transformer_id": session.transformer_id,
            "operator":       session.operator,
            "start_time":     session.start_time,
            "total_steps":    session.total_steps,
            "passed_steps":   session.passed_steps,
            "overall_pass":   session.overall_pass,
        } if session else None,
    }


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
    eng.start(operator=body.operator)
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


# ── hardware status ───────────────────────────────────────────────────────────

@router.get("/hardware/status")
def hardware_status(request: Request):
    hw = _get(request, "hardware")
    health = hw.health_check()
    return {k: v.value for k, v in health.items()}


@router.post("/hardware/connect")
def hardware_connect(body: SerialConnectRequest, request: Request):
    # In mock mode this is a no-op; real serial connection handled by serial_manager
    return {"connected": True, "mode": "mock"}


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
