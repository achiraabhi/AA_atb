"""
Polling reader for a UNI-T UT61B+ (or compatible "+") digital multimeter.

Unlike the Arduino/MCU voltage meters (VoltageMeterSerial), the UT61B+ does
NOT stream on its own and is NOT a serial port. Its USB cable is a WCH HID-UART
bridge (id 1a86:e429, "WCH UART TO KB-MS"); other "+" models use a Silicon Labs
CP2110 bridge (10c4:ea80). Both are USB HID devices spoken to via `hidapi` — the
bundled `ut61eplus.UT61EPLUS` driver handles all the framing and auto-detects
either chip. The meter must be **polled**: each takeMeasurement() sends a request
and returns one reading.

This class wraps that driver behind the exact same public API as
VoltageMeterSerial, so it is a drop-in replacement anywhere the project expects
a meter (DualVoltageService, HybridHardwareManager, MeasurementManager). A
background thread polls the meter on a fixed interval and buffers recent samples;
the rest of the app reads from the buffer and never touches the HID device
directly (so test-time read_voltage() calls never race the poll thread).

USB permissions: on Linux a udev rule (see udev/99-unit-dmm.rules) is required so
the device opens without sudo, otherwise UT61EPLUS() raises OSError: open failed.
"""
import collections
import threading
import time
import logging
from typing import List, Optional

log = logging.getLogger(__name__)

# Known USB-HID bridges used by the UNI-T "+" DMM family.
_DMM_BRIDGES = {
    (0x1a86, 0xE429): "WCH HID-UART (UT61B+)",
    (0x10c4, 0xEA80): "Silicon Labs CP2110",
}

try:
    import hid  # provided by the `hidapi` package
    _HID_OK = True
except ImportError:
    hid = None  # type: ignore
    _HID_OK = False

try:
    from ut61eplus import UT61EPLUS
    _DRIVER_OK = True
except ImportError:
    UT61EPLUS = None  # type: ignore
    _DRIVER_OK = False


class VoltageMeterUT61:
    """
    Background-polling reader for a UT61B+ (HID) multimeter.

    Drop-in compatible with VoltageMeterSerial: exposes connect/disconnect,
    connected, get_latest/get_samples/get_average, is_stable/is_fresh, and the
    VoltageReaderInterface shim (read_voltage/get_status).

    Only voltage readings are buffered: overload (open leads / over-range)
    samples are skipped because their value is infinite, and non-voltage meter
    modes (OHM/CAP/Hz/…) are ignored so a mis-set meter can't feed bogus
    "volts" into the test engine.
    """

    BUFFER_SIZE     = 200
    STALE_TIMEOUT   = 3.0
    POLL_INTERVAL   = 0.2   # s — meter updates ~2-5 Hz; faster polling gains nothing
    RECONNECT_DELAY = 3.0   # s — between reopen attempts after a device error

    # Meter modes whose value is a voltage in volts (after driver normalisation).
    _VOLTAGE_MODES = {"ACV", "DCV", "ACmV", "DCmV", "AC/DC", "LPF", "AC+DC", "LozV", "DIDOE"}

    def __init__(self) -> None:
        self._dmm                          = None
        self._name: Optional[str]          = None
        self._connected     = False
        self._stop_flag     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock          = threading.Lock()
        self._buffer: collections.deque = collections.deque(maxlen=self.BUFFER_SIZE)
        self._last_ts       = 0.0   # last buffered (valid) sample
        self._last_resp_ts  = 0.0   # last time the meter answered at all
        self._last_mode: Optional[str]  = None
        self._last_overload = False
        self._warned_skip   = False

    # ── availability probe ──────────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        """True if a known UNI-T DMM HID bridge is currently plugged in."""
        if not (_HID_OK and _DRIVER_OK):
            return False
        try:
            for d in hid.enumerate():
                if (d["vendor_id"], d["product_id"]) in _DMM_BRIDGES:
                    return True
        except Exception as exc:
            log.debug(f"UT61 HID enumerate failed: {exc}")
        return False

    # ── connection ──────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_read_time(self) -> float:
        return self._last_ts

    @property
    def name(self) -> Optional[str]:
        return self._name

    def connect(self, port: Optional[str] = None, baud: Optional[int] = None) -> bool:
        """
        Open the meter and start polling.

        `port`/`baud` are accepted for signature-compatibility with
        VoltageMeterSerial but ignored — the HID device is auto-detected.
        """
        if not _DRIVER_OK:
            log.warning("ut61eplus driver not importable — UT61 meter unavailable")
            return False
        if not _HID_OK:
            log.warning("hidapi (`hidapi` package) not installed — UT61 meter unavailable")
            return False
        try:
            self._dmm = UT61EPLUS()  # opens the device (auto-detects WCH or CP2110)
            try:
                self._name = self._dmm.getName()
            except Exception:
                self._name = "UT61+"
            bridge = "WCH" if getattr(self._dmm, "_wch", False) else "CP2110"
            self._connected = True
            self._stop_flag.clear()
            self._thread = threading.Thread(
                target=self._poll_loop,
                daemon=True,
                name=f"UT61-{self._name}",
            )
            self._thread.start()
            log.info(f"UT61 meter connected: {self._name} via {bridge} bridge")
            return True
        except OSError as exc:
            log.error(
                f"UT61 meter open failed ({exc}) — check the udev rule "
                f"(udev/99-unit-dmm.rules) and that the meter is powered on"
            )
            return False
        except Exception as exc:
            log.error(f"UT61 meter connect failed: {exc}")
            return False

    def disconnect(self) -> None:
        self._stop_flag.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._close_device()
        self._connected = False

    # ── reading API (matches VoltageMeterSerial) ──────────────────────────────

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

    def is_responding(self) -> bool:
        """True if the meter answered recently — even if the reading was OL."""
        return (time.time() - self._last_resp_ts) < self.STALE_TIMEOUT

    @property
    def last_overload(self) -> bool:
        """True when the most recent reading was overload (OL / over-range)."""
        return self._last_overload

    @property
    def last_mode(self) -> Optional[str]:
        return self._last_mode

    # ── VoltageReaderInterface shim ───────────────────────────────────────────

    def read_voltage(self, channel: int = 0):
        from hardware.hardware_interface import VoltageReading
        v = self.get_latest()
        if v is None:
            err = "Overload (open leads / over-range)" if self._last_overload \
                else "No samples from UT61 meter"
            return VoltageReading(
                channel=channel, voltage=0.0, timestamp=time.time(),
                valid=False, error=err,
            )
        return VoltageReading(channel=channel, voltage=v,
                              timestamp=self._last_ts, valid=True)

    def get_status(self):
        from hardware.hardware_interface import HardwareStatus
        return HardwareStatus.CONNECTED if self._connected else HardwareStatus.DISCONNECTED

    # ── internal ──────────────────────────────────────────────────────────────

    def _close_device(self) -> None:
        if self._dmm is not None:
            try:
                self._dmm.dev.close()
            except Exception:
                pass
            self._dmm = None

    def _poll_loop(self) -> None:
        while not self._stop_flag.is_set():
            try:
                m = self._dmm.takeMeasurement()
                if m is None:
                    # checksum mismatch / partial HID read — skip this sample
                    self._wait(self.POLL_INTERVAL)
                    continue

                # The meter answered — record liveness, mode and overload state
                # even when we don't buffer a numeric value.
                self._last_resp_ts  = time.time()
                prev_mode           = self._last_mode
                self._last_mode     = m.mode
                self._last_overload = m.overload

                if m.overload:
                    if prev_mode != m.mode or not self._warned_skip:
                        log.info("UT61 meter reading is OL (overload) in %s mode "
                                 "— check range / leads", m.mode)
                        self._warned_skip = True
                elif m.mode not in self._VOLTAGE_MODES:
                    if prev_mode != m.mode:
                        log.warning("UT61 meter in non-voltage mode %s — V2 will be "
                                    "blank until set to a voltage range", m.mode)
                else:
                    # valid voltage reading — buffer it
                    with self._lock:
                        self._buffer.append(float(m.value))
                    self._last_ts = time.time()
                    self._warned_skip = False

                self._wait(self.POLL_INTERVAL)
            except Exception as exc:
                log.warning(f"UT61 meter read error — reconnecting: {exc}")
                self._connected = False
                self._close_device()
                self._wait(self.RECONNECT_DELAY)
                if self._stop_flag.is_set():
                    return
                try:
                    self._dmm = UT61EPLUS()
                    self._connected = True
                    log.info("UT61 meter reconnected")
                except Exception as reopen_exc:
                    log.error(f"UT61 meter reopen failed: {reopen_exc}")

    def _wait(self, seconds: float) -> None:
        """Sleep in small slices so disconnect() stays responsive."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._stop_flag.is_set():
                return
            time.sleep(min(0.05, max(0.0, deadline - time.time())))
