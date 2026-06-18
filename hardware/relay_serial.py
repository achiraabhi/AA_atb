"""
Thread-safe serial driver for the relay MCU.

The MCU accepts line-terminated commands and returns OK / ERROR responses.
All I/O is protected by a lock so commands may safely be sent from any thread.
pyserial is an optional dependency — when not installed the driver operates in
software-only mode (all commands return True, nothing is sent to hardware).
"""
import threading
import logging
from typing import Optional

from hardware.protocol import (
    build_set_relays, build_clear_all, build_estop, build_ping,
    DEFAULT_BAUD_MCU, RESP_OK,
)

log = logging.getLogger(__name__)

try:
    import serial as _serial
    _SERIAL_OK = True
except ImportError:
    _serial = None  # type: ignore
    _SERIAL_OK = False


class RelaySerial:
    """
    Manages RS-232/USB serial communication with the relay MCU.

    Usage
    -----
    rs = RelaySerial()
    rs.connect("COM3")
    rs.set_relays([1, 17, 33, 34])
    rs.clear_all()
    rs.disconnect()
    """

    TIMEOUT_S = 2.0   # read timeout waiting for MCU response

    def __init__(self) -> None:
        self._port = None          # serial.Serial instance
        self._lock = threading.Lock()
        self._connected = False

    # ── connection ────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, port: str, baud: int = DEFAULT_BAUD_MCU) -> bool:
        if not _SERIAL_OK:
            log.warning("pyserial not installed — relay MCU serial unavailable")
            return False
        try:
            with self._lock:
                self._port = _serial.Serial(
                    port, baud,
                    timeout=self.TIMEOUT_S,
                    write_timeout=self.TIMEOUT_S,
                )
                self._connected = True
                # Drain the MCU boot banner (e.g. "ATB_RELAY_READY\r\n")
                # before sending PING so the boot message isn't mistaken for
                # the PING response.
                import time as _time
                _time.sleep(0.3)
                self._port.reset_input_buffer()
            log.info(f"Relay MCU connected on {port} @ {baud}")
            # Verify MCU is alive (some firmware doesn't implement PING — not fatal)
            if not self._send_quiet(build_ping()):
                log.debug(f"Relay MCU PING no-OK on {port} (firmware may not implement PING)")
            return True
        except Exception as exc:
            log.error(f"Relay serial connect failed ({port}): {exc}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        with self._lock:
            if self._port and getattr(self._port, "is_open", False):
                try:
                    self._port.close()
                except Exception:
                    pass
            self._port = None
            self._connected = False

    # ── command API ───────────────────────────────────────────────────────

    def set_relays(self, relay_ids) -> bool:
        return self._send(build_set_relays(relay_ids))

    def clear_all(self) -> bool:
        return self._send(build_clear_all())

    def emergency_stop(self) -> bool:
        return self._send(build_estop())

    def ping(self) -> bool:
        return self._send_quiet(build_ping())

    # ── internal ──────────────────────────────────────────────────────────

    def _send(self, cmd: str) -> bool:
        """Send one command and return True if MCU replies OK."""
        with self._lock:
            if not self._connected or self._port is None:
                return False
            try:
                self._port.write(cmd.encode("ascii"))
                self._port.flush()
                raw = self._port.readline()
                resp = raw.decode("ascii", errors="replace").strip()
                ok = resp.upper().startswith(RESP_OK)
                if not ok:
                    log.warning(f"MCU non-OK response for {cmd.strip()!r}: {resp!r}")
                return ok
            except Exception as exc:
                log.error(f"Relay serial I/O error: {exc}")
                self._connected = False
                return False

    def _send_quiet(self, cmd: str) -> bool:
        """Like _send but logs non-OK at DEBUG (used for PING health-checks)."""
        with self._lock:
            if not self._connected or self._port is None:
                return False
            try:
                self._port.write(cmd.encode("ascii"))
                self._port.flush()
                raw = self._port.readline()
                resp = raw.decode("ascii", errors="replace").strip()
                ok = resp.upper().startswith(RESP_OK)
                if not ok:
                    log.debug(f"MCU non-OK response for {cmd.strip()!r}: {resp!r}")
                return ok
            except Exception as exc:
                log.error(f"Relay serial I/O error: {exc}")
                self._connected = False
                return False
