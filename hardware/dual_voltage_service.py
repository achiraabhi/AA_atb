"""
DualVoltageService — reads V1 and V2 from two independent serial ports.

V1 = energizing voltage (applied to primary winding).
V2 = measurement voltage (sensed on secondary winding).

Each port runs a VoltageMeterSerial background reader thread.
Call get_readings() at any time to get the latest averaged values.
"""
import logging
from typing import Optional

from hardware.voltage_meter_serial import VoltageMeterSerial

log = logging.getLogger(__name__)


class DualVoltageService:

    def __init__(self) -> None:
        self._v1 = VoltageMeterSerial()
        self._v2 = VoltageMeterSerial()
        self.v1_connected = False
        self.v2_connected = False

    def connect(
        self,
        v1_port: Optional[str], v1_baud: int = 9600,
        v2_port: Optional[str] = None, v2_baud: int = 9600,
    ) -> None:
        if v1_port:
            self.v1_connected = self._v1.connect(v1_port, v1_baud)
            if not self.v1_connected:
                log.warning(f"V1 meter failed to connect on {v1_port}")
        if v2_port:
            self.v2_connected = self._v2.connect(v2_port, v2_baud)
            if not self.v2_connected:
                log.warning(f"V2 meter failed to connect on {v2_port}")

    def set_meter_port(self, which: str, port: str, baud: int = 115200) -> bool:
        """
        Reconnect one meter ('v1' or 'v2') to a new serial port at runtime.
        Reuses the existing VoltageMeterSerial instance so references held by
        the hardware manager (TestEngine) stay valid.
        """
        which = which.lower()
        if which not in ("v1", "v2"):
            raise ValueError(f"unknown meter '{which}' — expected 'v1' or 'v2'")
        meter = self._v1 if which == "v1" else self._v2
        meter.disconnect()
        ok = meter.connect(port, baud)
        if which == "v1":
            self.v1_connected = ok
        else:
            self.v2_connected = ok
        if ok:
            log.info(f"{which.upper()} meter assigned to {port} @ {baud}")
        else:
            log.warning(f"{which.upper()} meter failed to connect on {port}")
        return ok

    def disconnect(self) -> None:
        self._v1.disconnect()
        self._v2.disconnect()

    def get_readings(self) -> dict:
        v1 = self._v1.get_latest() if self.v1_connected else None
        v2 = self._v2.get_latest() if self.v2_connected else None
        return {
            "v1":           round(v1, 3) if v1 is not None else None,
            "v2":           round(v2, 3) if v2 is not None else None,
            "v1_fresh":     self.v1_connected and self._v1.is_fresh(),
            "v2_fresh":     self.v2_connected and self._v2.is_fresh(),
            "v1_connected": self.v1_connected,
            "v2_connected": self.v2_connected,
        }
