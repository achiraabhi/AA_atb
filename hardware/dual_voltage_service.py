"""
DualVoltageService — reads V1 and V2 from two independent meters.

V1 = energizing voltage (applied to primary winding).
V2 = measurement voltage (sensed on secondary winding).

Each channel runs its own background reader thread and is read via the same
API regardless of meter type:
  - "serial" — VoltageMeterSerial: an Arduino/MCU meter streaming over a serial
               port (needs a port name + baud).
  - "ut61"   — VoltageMeterUT61: a UNI-T UT61B+ polled over USB-HID. It is NOT a
               serial port and needs no port/baud (the device is auto-detected).

Call get_readings() at any time to get the latest values.
"""
import logging
from typing import Optional

from hardware.voltage_meter_serial import VoltageMeterSerial
from hardware.voltage_meter_ut61 import VoltageMeterUT61

log = logging.getLogger(__name__)


def _make_meter(driver: str):
    """Construct the meter implementation for a driver name ('serial' | 'ut61')."""
    return VoltageMeterUT61() if driver == "ut61" else VoltageMeterSerial()


class DualVoltageService:

    def __init__(self, v1_driver: str = "serial", v2_driver: str = "serial",
                 v1_serial: Optional[str] = None, v2_serial: Optional[str] = None) -> None:
        self.v1_driver = v1_driver
        self.v2_driver = v2_driver
        self.v1_serial = v1_serial   # UT61 meter serial pinned to V1 (optional)
        self.v2_serial = v2_serial   # UT61 meter serial pinned to V2 (optional)
        self._v1 = _make_meter(v1_driver)
        self._v2 = _make_meter(v2_driver)
        self.v1_connected = False
        self.v2_connected = False

    def connect(
        self,
        v1_port: Optional[str], v1_baud: int = 9600,
        v2_port: Optional[str] = None, v2_baud: int = 9600,
    ) -> None:
        # A UT61 (USB-HID) meter has no serial port to scan for; it claims a
        # distinct physical meter (by pinned serial when set, else the first
        # unclaimed one). V2 connects first so it gets the pinned/first meter.
        if self.v2_driver == "ut61" or v2_port:
            self.v2_connected = self._v2.connect(v2_port, v2_baud, serial=self.v2_serial)
            if not self.v2_connected:
                log.warning(f"V2 meter ({self.v2_driver}) failed to connect"
                            + (f" on {v2_port}" if v2_port else ""))
        if self.v1_driver == "ut61" or v1_port:
            self.v1_connected = self._v1.connect(v1_port, v1_baud, serial=self.v1_serial)
            if not self.v1_connected:
                log.warning(f"V1 meter ({self.v1_driver}) failed to connect"
                            + (f" on {v1_port}" if v1_port else ""))

    def set_meter_dmm(self, which: str, serial: str) -> bool:
        """
        Reassign a UT61 (HID) channel to a specific meter by serial number.
        If the other channel currently holds that meter, the two are swapped.
        """
        which = which.lower()
        if which not in ("v1", "v2"):
            raise ValueError(f"unknown meter '{which}' — expected 'v1' or 'v2'")
        driver = self.v1_driver if which == "v1" else self.v2_driver
        if driver != "ut61":
            log.warning(f"{which.upper()} is not a UT61 (HID) channel")
            return False

        other      = "v2" if which == "v1" else "v1"
        meter      = self._v1 if which == "v1" else self._v2
        omtr       = self._v2 if which == "v1" else self._v1
        odriver    = self.v2_driver if which == "v1" else self.v1_driver
        freed      = meter.serial            # meter this channel will release
        swap       = (odriver == "ut61" and getattr(omtr, "serial", None) == serial)

        meter.disconnect()
        if swap:
            omtr.disconnect()                # free the requested meter to reassign

        ok = meter.connect(serial=serial)
        self._set_state(which, ok, serial if ok else None)

        if swap:
            # Give the other channel the meter this one just released.
            ook = omtr.connect(serial=freed)
            self._set_state(other, ook, freed if ook else None)

        log.info(f"{which.upper()} meter -> sn={serial}: {'ok' if ok else 'FAILED'}"
                 + (" (swapped)" if swap else ""))
        return ok

    def _set_state(self, which: str, ok: bool, serial: Optional[str]) -> None:
        if which == "v1":
            self.v1_connected = ok
            if serial is not None:
                self.v1_serial = serial
        else:
            self.v2_connected = ok
            if serial is not None:
                self.v2_serial = serial

    def set_meter_port(self, which: str, port: str, baud: int = 115200) -> bool:
        """
        Reconnect one meter ('v1' or 'v2') to a new serial port at runtime.
        Reuses the existing VoltageMeterSerial instance so references held by
        the hardware manager (TestEngine) stay valid.
        """
        which = which.lower()
        if which not in ("v1", "v2"):
            raise ValueError(f"unknown meter '{which}' — expected 'v1' or 'v2'")
        driver = self.v1_driver if which == "v1" else self.v2_driver
        if driver == "ut61":
            # HID-only channel — it has no serial/tty port to assign.
            log.warning(f"{which.upper()} uses the UT61B+ over USB-HID — "
                        f"serial port assignment ignored")
            return False
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

    @staticmethod
    def _overload(meter, connected: bool) -> bool:
        """True when a meter currently reports overload (OL). Only the UT61
        meter exposes this; serial meters never do."""
        if not connected:
            return False
        is_resp = getattr(meter, "is_responding", None)
        return bool(getattr(meter, "last_overload", False) and (is_resp is None or is_resp()))

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
            "v1_overload":  self._overload(self._v1, self.v1_connected),
            "v2_overload":  self._overload(self._v2, self.v2_connected),
        }
