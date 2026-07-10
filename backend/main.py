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

from backend.api.routes import router as api_router
from backend.websocket.manager import WebSocketManager
from backend.websocket.broadcaster import WsBroadcaster
from backend.websocket.events import (
    CMD_START_TEST, CMD_STOP_TEST, CMD_PAUSE_TEST, CMD_RESUME_TEST,
    CMD_NEXT_STEP, CMD_EMERGENCY_STOP, CMD_SELECT_TRANSFORMER, CMD_SET_OPERATOR,
    CMD_NEXT_UNIT, CMD_SKIP_UNIT, CMD_RETRY_UNIT, CMD_COMPLETE_BATCH,
    CMD_SET_EXCITATION, EVT_LIVE_VOLTAGES, EVT_RELAY_COMM,
)
from hardware import relay_comm_log
from hardware.dual_voltage_service import DualVoltageService

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

    # ── auto-detect serial devices by behaviour ─────────────────────────────
    # Probe every serial port: voltmeters stream VOLTAGE:, the relay answers
    # PING. This makes assignment independent of unstable /dev/ttyACM* numbers.
    from hardware.port_scanner import resolve_assignments
    from hardware.relay_controller import RelayController
    from hardware.hybrid_hardware import HybridHardwareManager

    baud = int(os.getenv("SERIAL_BAUD", "115200"))
    detected = resolve_assignments(
        relay_cfg=os.getenv("SERIAL_PORT"),
        v1_cfg=os.getenv("V1_PORT"),
        v2_cfg=os.getenv("V2_PORT"),
        baud=baud,
    )
    log.info(f"Serial scan: {detected['types']}")
    app.state.port_types = detected["types"]

    # Dual voltage meter service.
    # V1 = energizing voltage, V2 = measurement voltage (feeds TestEngine).
    # Both are now UNI-T UT61B+ meters over USB-HID by default. Since the two
    # meters share a VID/PID, each channel claims a DISTINCT physical meter; pin
    # which one is which by serial via V1_UT61_SERIAL / V2_UT61_SERIAL (else they
    # are auto-assigned by USB-port order). Set V*_DRIVER=serial to use a
    # streaming serial meter on a channel instead.
    v1_driver = os.getenv("V1_DRIVER", "ut61").strip().lower()
    v2_driver = os.getenv("V2_DRIVER", "ut61").strip().lower()
    volt_service = DualVoltageService(
        v1_driver=v1_driver, v2_driver=v2_driver,
        v1_serial=(os.getenv("V1_UT61_SERIAL") or None),
        v2_serial=(os.getenv("V2_UT61_SERIAL") or None),
    )
    volt_service.connect(
        v1_port=(detected["v1"] if v1_driver == "serial" else None),
        v1_baud=int(os.getenv("V1_BAUD", "115200")),
        v2_port=(detected["v2"] if v2_driver == "serial" else None),
        v2_baud=int(os.getenv("V2_BAUD", "115200")),
    )
    app.state.volt_service = volt_service
    _src = lambda drv, m, port: (f"UT61B+ sn={m.serial}" if drv == "ut61" else (port or "none"))
    log.info(f"Voltmeters → V1: {_src(v1_driver, volt_service._v1, detected['v1'])} "
             f"| V2: {_src(v2_driver, volt_service._v2, detected['v2'])}")

    # Relay board — only connect when one was actually detected, so we never
    # squat a voltmeter's port. Server still starts if absent (UI loads,
    # hardware reports DISCONNECTED, tests can't run until it's plugged in).
    relay_ctrl = RelayController()
    relay_port = detected["relay"]
    if relay_port and relay_ctrl.connect(relay_port, baud):
        log.info(f"Real relay board on {relay_port} @ {baud}")
        app.state.relay_port = relay_port
    else:
        log.warning("Relay board not detected — starting in disconnected state.")
        app.state.relay_port = None
    # Wire V2 meter into the hardware manager so TestEngine reads real voltages
    hardware = HybridHardwareManager(relay_ctrl, v2_meter=volt_service._v2)
    hardware.initialize()

    logger      = TestLogger()
    seq_manager = SequenceManager()
    test_engine = TestEngine(state_manager, seq_manager, config_loader, hardware, logger)

    from core.relay_sequence import RelaySequencer
    relay_sequencer = RelaySequencer(state_manager, hardware)

    # WebSocket layer
    ws_manager      = WebSocketManager()
    broadcaster     = WsBroadcaster(state_manager, ws_manager, loop)

    # Store on app.state for route access
    app.state.config_loader   = config_loader
    app.state.state_manager   = state_manager
    app.state.hardware        = hardware
    app.state.test_engine     = test_engine
    app.state.relay_sequencer = relay_sequencer
    app.state.ws_manager      = ws_manager
    app.state.broadcaster     = broadcaster
    app.state.event_loop      = loop

    # Background task: push V1/V2 immediately on change (1 s heartbeat when
    # steady) and stream relay MCU ⇄ PC serial traffic to the UI console.
    async def _voltage_broadcast_loop():
        TICK = 0.1
        HEARTBEAT = 1.0
        last_vals = None
        last_send = 0.0
        comm_seq = 0
        while True:
            readings = volt_service.get_readings()
            vals = (readings["v1"], readings["v2"],
                    readings.get("v1_overload"), readings.get("v2_overload"))
            now = loop.time()
            if vals != last_vals or (now - last_send) >= HEARTBEAT:
                await ws_manager.broadcast({"type": EVT_LIVE_VOLTAGES, "data": readings})
                last_vals = vals
                last_send = now
            entries, comm_seq = relay_comm_log.get_since(comm_seq)
            if entries:
                await ws_manager.broadcast({"type": EVT_RELAY_COMM, "data": {"entries": entries}})
            await asyncio.sleep(TICK)

    broadcast_task = asyncio.create_task(_voltage_broadcast_loop())

    log.info("ATB backend started — http://localhost:8000")
    yield

    # Cleanup
    broadcast_task.cancel()
    relay_sequencer.stop()
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
        # Also send current V1/V2 immediately so the bar isn't blank
        volt_readings = app.state.volt_service.get_readings()
        await ws.send_json({"type": EVT_LIVE_VOLTAGES, "data": volt_readings})
        # Seed the relay-comm console with recent traffic so it isn't blank.
        recent, _ = relay_comm_log.get_since(0)
        if recent:
            await ws.send_json({"type": EVT_RELAY_COMM, "data": {"entries": recent[-60:]}})
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
