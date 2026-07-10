"""
Thread-safe serial driver for the relay MCU (SELECT/CLEAR/STATUS firmware).

The Arduino firmware accepts line-terminated commands:
    SELECT <n>   — close one relay (firmware enforces exclusivity + gates)
    CLEAR        — open all relays
    STATUS       — report active A1/A2/B relays
It replies with human-readable text ("Selected Relay: 5", "Matrix Cleared",
a STATUS block) rather than "OK"; a command is treated as successful unless its
reply contains "ERROR".

Because the firmware sets ONE relay per SELECT (and keeps a prior subgroup
selection), an exact relay set is applied as: CLEAR, then a SELECT per relay.

All I/O is protected by a lock so commands may safely be sent from any thread.
pyserial is an optional dependency — when not installed the driver operates in
software-only mode (all commands return True, nothing is sent to hardware).
"""
import threading
import logging
import time
from typing import List

from hardware.protocol import (
    build_select, build_clear, build_status,
    DEFAULT_BAUD_MCU, RESP_ERROR, RESP_STATUS_MARK,
)
from hardware import relay_comm_log

log = logging.getLogger(__name__)

try:
    import serial as _serial
    _SERIAL_OK = True
except ImportError:
    _serial = None  # type: ignore
    _SERIAL_OK = False


class RelaySerial:
    """
    Manages USB-serial communication with the relay MCU.

    Usage
    -----
    rs = RelaySerial()
    rs.connect("COM3")
    rs.apply([1, 17])      # CLEAR then SELECT 1, SELECT 17 (gates are automatic)
    rs.clear()
    rs.disconnect()
    """

    TIMEOUT_S      = 2.0   # read timeout waiting for an MCU response line
    BANNER_WAIT_S  = 1.6   # Arduino resets on open and prints a boot banner

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
                # Close any stale handle first (e.g. reconnecting after an I/O
                # error left the old port open), otherwise reopening can fail.
                if self._port is not None:
                    try:
                        if getattr(self._port, "is_open", False):
                            self._port.close()
                    except Exception:
                        pass
                    self._port = None
                self._port = _serial.Serial(
                    port, baud,
                    timeout=self.TIMEOUT_S,
                    write_timeout=self.TIMEOUT_S,
                )
                self._connected = True
                # The board resets on open (DTR) and prints its boot banner
                # ("Relay Matrix Ready" + command list). Wait past that and
                # drain it so it isn't mistaken for a command reply.
                time.sleep(self.BANNER_WAIT_S)
                self._port.reset_input_buffer()
            relay_comm_log.record("INFO", f"connected on {port} @ {baud}")
            log.info(f"Relay MCU connected on {port} @ {baud}")
            # Confirm the SELECT/CLEAR/STATUS firmware is alive (non-fatal).
            if not self._verify_alive():
                log.debug(f"Relay MCU on {port} did not return a STATUS block")
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

    def apply(self, relay_ids: List[int]) -> bool:
        """
        Make exactly `relay_ids` active: CLEAR, then one SELECT per relay.
        Gate relays must NOT be included — the firmware controls those.
        An empty list is equivalent to clear().
        """
        ids = [int(r) for r in relay_ids]
        if not ids:
            return self.clear()
        with self._lock:
            if not self._connected or self._port is None:
                return False
            ok = self._send_locked(build_clear())
            for r in ids:
                ok = self._send_locked(build_select(r)) and ok
            return ok

    def clear(self) -> bool:
        with self._lock:
            if not self._connected or self._port is None:
                return False
            return self._send_locked(build_clear())

    def query_status(self) -> str:
        """Send STATUS and return the raw multi-line reply (for diagnostics)."""
        with self._lock:
            if not self._connected or self._port is None:
                return ""
            return self._read_block_locked(build_status())

    # ── internal ──────────────────────────────────────────────────────────

    def _send_locked(self, cmd: str) -> bool:
        """
        Write one command and read its reply line. Success = a line came back
        and it does not contain ERROR. Caller must hold self._lock.
        """
        try:
            self._port.reset_input_buffer()
            self._port.write(cmd.encode("ascii"))
            self._port.flush()
            relay_comm_log.record("TX", cmd.strip())
            resp = self._port.readline().decode("ascii", errors="replace").strip()
            relay_comm_log.record("RX", resp or "(no reply)")
            if resp == "":
                log.warning(f"MCU no reply for {cmd.strip()!r}")
                return False
            if RESP_ERROR in resp.upper():
                log.warning(f"MCU error for {cmd.strip()!r}: {resp!r}")
                return False
            return True
        except Exception as exc:
            log.error(f"Relay serial I/O error: {exc}")
            self._connected = False
            return False

    def _read_block_locked(self, cmd: str, window_s: float = 1.0) -> str:
        """Write a command and collect reply lines for `window_s`. Holds lock."""
        try:
            self._port.reset_input_buffer()
            self._port.write(cmd.encode("ascii"))
            self._port.flush()
            relay_comm_log.record("TX", cmd.strip())
            lines, deadline = [], time.time() + window_s
            while time.time() < deadline:
                raw = self._port.readline()
                if not raw:
                    continue
                line = raw.decode("ascii", errors="replace").rstrip()
                lines.append(line)
                if line.strip():
                    relay_comm_log.record("RX", line.strip())
            return "\n".join(lines)
        except Exception as exc:
            log.error(f"Relay serial I/O error: {exc}")
            self._connected = False
            return ""

    def _verify_alive(self) -> bool:
        with self._lock:
            if not self._connected or self._port is None:
                return False
            block = self._read_block_locked(build_status())
        return RESP_STATUS_MARK in block.upper()
