"""
ATB Backend — FastAPI entry point.

Run with:
    uvicorn backend.main:app --reload --port 8000

The backend wires together the existing Python core modules (unchanged) with:
  - REST API  → /api/...
  - WebSocket → /ws
  - Static files served from frontend/dist (production build)

CORS is enabled for local frontend development (http://localhost:5173).
"""
import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ── project root on path so core/ and hardware/ are importable ──────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from core.config_loader import ConfigLoader
from core.state_manager import StateManager
from core.sequence_manager import SequenceManager
from core.logger import TestLogger
from core.test_engine import TestEngine
from hardware.mock_hardware import MockHardwareManager

from backend.api.routes import router as api_router
from backend.websocket.manager import WebSocketManager
from backend.websocket.broadcaster import WsBroadcaster
from backend.websocket.events import (
    CMD_START_TEST, CMD_STOP_TEST, CMD_PAUSE_TEST, CMD_RESUME_TEST,
    CMD_NEXT_STEP, CMD_EMERGENCY_STOP, CMD_SELECT_TRANSFORMER, CMD_SET_OPERATOR,
    CMD_NEXT_UNIT, CMD_SKIP_UNIT, CMD_RETRY_UNIT, CMD_COMPLETE_BATCH,
    CMD_SET_EXCITATION,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


# ── application lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()

    # Core services
    config_loader   = ConfigLoader()
    ids = config_loader.scan_and_load()
    log.info(f"Loaded {len(ids)} transformer configs: {ids}")

    state_manager   = StateManager()

    # Hardware: set HARDWARE_MODE=real in .env to use physical relay board
    hardware_mode = os.getenv("HARDWARE_MODE", "mock").lower()
    if hardware_mode == "real":
        from hardware.relay_controller import RelayController
        serial_port = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
        baud        = int(os.getenv("SERIAL_BAUD", "115200"))
        hardware = RelayController()
        ok = hardware.connect(serial_port, baud)
        if not ok:
            log.warning(f"Real hardware connect failed on {serial_port} — falling back to mock")
            hardware = MockHardwareManager()
            hardware.initialize()
        else:
            log.info(f"Real relay board connected on {serial_port} @ {baud}")
    else:
        hardware = MockHardwareManager()
        hardware.initialize()
        log.info("Running with mock hardware (simulation mode)")

    logger          = TestLogger()
    seq_manager     = SequenceManager()
    test_engine     = TestEngine(state_manager, seq_manager, config_loader, hardware, logger)

    # WebSocket layer
    ws_manager      = WebSocketManager()
    broadcaster     = WsBroadcaster(state_manager, ws_manager, loop)

    # Store on app.state for route access
    app.state.config_loader   = config_loader
    app.state.state_manager   = state_manager
    app.state.hardware        = hardware
    app.state.test_engine     = test_engine
    app.state.ws_manager      = ws_manager
    app.state.broadcaster     = broadcaster
    app.state.event_loop      = loop

    log.info("ATB backend started — http://localhost:8000")
    yield

    # Cleanup
    test_engine.stop()
    hardware.shutdown()
    log.info("ATB backend shut down")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="After-Assembling Test Bench API",
    version="2.0.0",
    description="Industrial transformer test automation backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    ws_mgr  = app.state.ws_manager
    sm      = app.state.state_manager
    eng     = app.state.test_engine
    cfg     = app.state.config_loader

    await ws_mgr.connect(ws)

    # Send current state snapshot on connect
    try:
        session = sm.current_session
        batch   = sm.batch_session
        snapshot = {
            "type": "snapshot",
            "data": {
                "app_state":            sm.app_state.value,
                "test_mode":            sm.test_mode.value,
                "selected_transformer": sm.selected_transformer_id,
                "operator":             sm.operator_name,
                "relay_states":         {str(k): v for k, v in sm.relay_states.items()},
                "current_step":         sm.current_step_index,
                "total_steps":          sm.total_steps,
                "progress_pct":         sm.progress_pct,
                "transformers":         [
                    {"id": t.transformer_id, "name": t.name}
                    for t in cfg.list_transformers()
                ],
                "batch": batch.to_summary(active=True) if batch else None,
            }
        }
        await ws.send_json(snapshot)
    except Exception as e:
        log.warning(f"[WS] Snapshot send failed: {e}")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            cmd  = msg.get("type", "")
            data = msg.get("data", {})

            if cmd == CMD_SELECT_TRANSFORMER:
                tid = data.get("transformer_id", "")
                if cfg.get_transformer(tid):
                    sm.selected_transformer_id = tid

            elif cmd == CMD_SET_OPERATOR:
                sm.operator_name = data.get("operator", "")

            elif cmd == CMD_SET_EXCITATION:
                wid = data.get("excitation_winding_id")
                av  = data.get("applied_voltage")
                tid = sm.selected_transformer_id
                nominal = None
                if wid and tid:
                    tc = cfg.get_transformer(tid)
                    if tc:
                        w = tc.get_winding(wid)
                        if w:
                            nominal = w.nominal_voltage
                sm.set_excitation(wid, av, nominal)

            elif cmd == CMD_START_TEST:
                sm.operator_name = data.get("operator", sm.operator_name)
                eng.start(
                    operator=sm.operator_name,
                    excitation_winding_id=data.get("excitation_winding_id"),
                    applied_voltage=data.get("applied_voltage"),
                )

            elif cmd == CMD_STOP_TEST:
                eng.stop()

            elif cmd == CMD_PAUSE_TEST:
                eng.pause()

            elif cmd == CMD_RESUME_TEST:
                eng.resume()

            elif cmd == CMD_NEXT_STEP:
                eng.next_step()

            elif cmd == CMD_NEXT_UNIT:
                eng.next_unit()

            elif cmd == CMD_SKIP_UNIT:
                eng.skip_unit(reason=data.get("reason", ""))

            elif cmd == CMD_RETRY_UNIT:
                eng.retry_unit()

            elif cmd == CMD_COMPLETE_BATCH:
                eng.complete_batch()

            elif cmd == CMD_EMERGENCY_STOP:
                eng.emergency_stop()

    except WebSocketDisconnect:
        pass
    finally:
        await ws_mgr.disconnect(ws)


# ── serve Python web frontend (web/static/) ───────────────────────────────────

_web_static = PROJECT_ROOT / "web" / "static"

@app.get("/")
async def serve_index():
    return FileResponse(str(_web_static / "index.html"))

if _web_static.exists():
    app.mount("/static", StaticFiles(directory=str(_web_static)), name="static")


# ── dev entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(PROJECT_ROOT)],
    )
