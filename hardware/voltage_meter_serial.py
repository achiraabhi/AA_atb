"""
Continuous serial reader for the external voltage meter.

The meter independently streams voltage readings; this module runs a
background reader thread, buffers recent samples, and exposes a simple API.

Arduino/MCU boards trigger a reset when the serial port is first opened
(DTR pulse). INIT_DELAY_S waits past that reset before reading begins.
On disconnect/error the reader thread attempts to reopen the port automatically.
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
    Handles Arduino reset-on-open and automatic reconnection on error.
    """

    BUFFER_SIZE     = 200
    STALE_TIMEOUT   = 3.0
    INIT_DELAY_S    = 4.0   # wait past Arduino DTR-reset before first read
    RECONNECT_DELAY = 3.0   # seconds between reconnect attempts on error

    def __init__(self) -> None:
        self._port_name: Optional[str]     = None
        self._baud:      int               = DEFAULT_BAUD_METER
        self._port                         = None
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
        self._port_name = port
        self._baud      = baud
        try:
            self._port = _serial.Serial()
            self._port.port     = port
            self._port.baudrate = baud
            self._port.timeout  = 1.0
            self._port.dsrdtr   = False  # don't assert DTR (reduces reset issues)
            self._port.rtscts   = False
            self._port.open()
            self._connected = True
            self._stop_flag.clear()
            self._thread = threading.Thread(
                target=self._reader_loop,
                daemon=True,
                name=f"VoltageMeter-{port}",
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
        self._close_port()
        self._connected = False

    # ── reading API ───────────────────────────────────────────────────────

    def get_latest(self) -> Optional[float]:
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def get_samples(self, n: int = 10) -> List[float]:
        with self._lock:
            buf = list(self._buffer)
        return buf[-n:] if len(buf) >= n else buf

    def get_average(self, n: int = 10) -> Optional[float]:
        samples = self.get_samples(n)
        return sum(samples) / len(samples) if samples else None

    def is_stable(self, n: int = 10, max_spread_v: float = 0.2) -> bool:
        samples = self.get_samples(n)
        if len(samples) < 2:
            return False
        return (max(samples) - min(samples)) <= max_spread_v

    def is_fresh(self) -> bool:
        return (time.time() - self._last_ts) < self.STALE_TIMEOUT

    # ── VoltageReaderInterface shim ───────────────────────────────────────

    def read_voltage(self, channel: int = 0):
        from hardware.hardware_interface import VoltageReading
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

    # ── internal ──────────────────────────────────────────────────────────

    def _close_port(self) -> None:
        if self._port:
            try:
                if self._port.is_open:
                    self._port.close()
            except Exception:
                pass

    def _reader_loop(self) -> None:
        # Wait past the Arduino DTR-reset cycle before reading
        deadline = time.time() + self.INIT_DELAY_S
        while time.time() < deadline and not self._stop_flag.is_set():
            time.sleep(0.1)

        if self._port and self._port.is_open:
            try:
                self._port.reset_input_buffer()
            except Exception:
                pass

        while not self._stop_flag.is_set():
            try:
                if self._port and self._port.is_open:
                    raw = self._port.readline()
                    if not raw:
                        # Empty read — device may be momentarily resetting
                        time.sleep(0.2)
                        continue
                    line = raw.decode("ascii", errors="replace")
                    v = parse_voltage(line)
                    if v is not None:
                        with self._lock:
                            self._buffer.append(v)
                        self._last_ts = time.time()
                else:
                    time.sleep(0.1)
            except Exception as exc:
                log.warning(f"Voltage meter {self._port_name} error — reconnecting: {exc}")
                self._connected = False
                self._close_port()
                # Wait, then try to reopen
                for _ in range(int(self.RECONNECT_DELAY / 0.1)):
                    if self._stop_flag.is_set():
                        return
                    time.sleep(0.1)
                if self._stop_flag.is_set():
                    return
                try:
                    self._port.open()
                    time.sleep(self.INIT_DELAY_S)
                    if self._port.is_open:
                        self._port.reset_input_buffer()
                        self._connected = True
                        log.info(f"Voltage meter {self._port_name} reconnected")
                except Exception as reopen_exc:
                    log.error(f"Voltage meter {self._port_name} reopen failed: {reopen_exc}")
