"""
WebSocket connection pool manager.
Maintains a set of active WebSocket connections and broadcasts messages to all.
Thread-safe: broadcast() can be called from sync threads via run_coroutine_threadsafe.
"""
import asyncio
import json
import logging
from typing import Set

from fastapi import WebSocket

log = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        log.info(f"[WS] Client connected — {len(self._connections)} active")

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)
        log.info(f"[WS] Client disconnected — {len(self._connections)} active")

    async def broadcast(self, event: dict) -> None:
        """Send JSON event to all connected clients; remove stale connections."""
        payload = json.dumps(event)
        dead: Set[WebSocket] = set()
        async with self._lock:
            targets = set(self._connections)
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                self._connections -= dead

    def broadcast_sync(self, event: dict, loop: asyncio.AbstractEventLoop) -> None:
        """Thread-safe wrapper: schedule broadcast on the given event loop."""
        asyncio.run_coroutine_threadsafe(self.broadcast(event), loop)

    @property
    def connection_count(self) -> int:
        return len(self._connections)
