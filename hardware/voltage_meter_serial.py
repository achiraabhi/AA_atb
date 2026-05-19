"""
Continuous serial reader for the external voltage meter.

The meter independently streams voltage readings to the PC; this module runs a
background reader thread, buffers recent samples, and exposes a simple API for
the test engine to collect averaged, noise-filtered readings.

pyserial is optional — when unavailable the object stays disconnected and all
sample-collection methods return empty / None.
"""
import collections
import threading
import time
import logging
from typing import List, Optional

from hardware.protocol import parse_voltage, DEFAULT_BAUD_METER

log = logging.getLogger(__name__)

try:
    import serial as _serial
    _SERIAL_OK = True
except ImportError:
    _serial = None  # type: ignore
    _SERIAL_OK = False


class VoltageMeterSerial:
    """
    Background serial reader for a continuously-streaming voltage meter.

    Thread safety
    -------------
    The internal ring-buffer is protected by a lock.  All public methods
    (get_latest, get_samples, get_average, is_stable) are safe to call from
    any thread including the Tk UI thread.
    """

    BUFFER_SIZE    = 200    # maximum stored samples
    STALE_TIMEOUT  = 3.0    # seconds before a reading is considered stale

    def __init__(self) -> None:
        self._port          = None
        self._connected     = False
        self._stop_flag     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock          = threading.Lock()
        self._buffer: collections.deque = collections.deque(maxlen=self.BUFFER_SIZE)
        self._last_ts       = 0.0

    # ── connection ────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_read_time(self) -> float:
        return self._last_ts

    def connect(self, port: str, baud: int = DEFAULT_BAUD_METER) -> bool:
        if not _SERIAL_OK:
            log.warning("pyserial not installed — voltage meter serial unavailable")
            return False
        try:
            self._port = _serial.Serial(port, baud, timeout=1.0)
            self._connected = True
            self._stop_flag.clear()
            self._thread = threading.Thread(
                target=self._reader_loop,
                daemon=True,
                name="VoltageMeterSerial",
            )
            self._thread.start()
            log.info(f"Voltage meter connected on {port} @ {baud}")
            return True
        except Exception as exc:
            log.error(f"Voltage meter connect failed ({port}): {exc}")
            return False

    def disconnect(self) -> None:
        self._stop_flag.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._port and getattr(self._port, "is_open", False):
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None
        self._connected = False

    # ── reading API ───────────────────────────────────────────────────────

    def get_latest(self) -> Optional[float]:
        """Return the most recent voltage sample, or None if buffer is empty."""
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def get_samples(self, n: int = 10) -> List[float]:
        """Return up to the last n samples (oldest first)."""
        with self._lock:
            buf = list(self._buffer)
        return buf[-n:] if len(buf) >= n else buf

    def get_average(self, n: int = 10) -> Optional[float]:
        """Return the mean of the last n samples, or None if insufficient data."""
        samples = self.get_samples(n)
        if not samples:
            return None
        return sum(samples) / len(samples)

    def is_stable(self, n: int = 10, max_spread_v: float = 0.2) -> bool:
        """
        Return True when the last n samples span less than max_spread_v volts.
        Used for noise validation before accepting a reading.
        """
        samples = self.get_samples(n)
        if len(samples) < 2:
            return False
        return (max(samples) - min(samples)) <= max_spread_v

    def is_fresh(self) -> bool:
        """True if a sample arrived within STALE_TIMEOUT seconds."""
        return (time.time() - self._last_ts) < self.STALE_TIMEOUT

    # ── VoltageReaderInterface compatibility ──────────────────────────────

    def read_voltage(self, channel: int = 0):
        """
        Legacy compatibility shim for VoltageReaderInterface callers.
        channel is ignored — the meter has one output stream.
        """
        from hardware.hardware_interface import VoltageReading, HardwareStatus
        v = self.get_latest()
        if v is None:
            return VoltageReading(
                channel=channel, voltage=0.0, timestamp=time.time(),
                valid=False, error="No samples from meter",
            )
        return VoltageReading(channel=channel, voltage=v,
                              timestamp=self._last_ts, valid=True)

    def get_status(self):
        from hardware.hardware_interface import HardwareStatus
        return HardwareStatus.CONNECTED if self._connected else HardwareStatus.DISCONNECTED

    # ── background reader ─────────────────────────────────────────────────

    def _reader_loop(self) -> None:
        while not self._stop_flag.is_set():
            try:
                if self._port and self._port.is_open:
                    raw = self._port.readline()
                    line = raw.decode("ascii", errors="replace")
                    v = parse_voltage(line)
                    if v is not None:
                        with self._lock:
                            self._buffer.append(v)
                        self._last_ts = time.time()
                else:
                    time.sleep(0.05)
            except Exception as exc:
                log.error(f"Voltage meter read error: {exc}")
                time.sleep(0.1)
