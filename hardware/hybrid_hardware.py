"""
HybridHardwareManager — real relay board + real V2 voltage meter.

Relay commands go to the physical relay MCU.
Voltage reads (used by TestEngine during test steps) come from the real V2
VoltageMeterSerial instance owned by DualVoltageService.
"""
from typing import Dict, Optional

from hardware.hardware_interface import (
    HardwareManagerInterface, RelayControllerInterface,
    HardwareStatus,
)
from hardware.relay_controller import RelayController
from hardware.voltage_meter_serial import VoltageMeterSerial


class HybridHardwareManager(HardwareManagerInterface):
    """
    Combines a real RelayController with a real VoltageMeterSerial (V2).
    Implements the full HardwareManagerInterface expected by TestEngine.
    """

    def __init__(self, relay_controller: RelayController,
                 v2_meter: Optional[VoltageMeterSerial] = None) -> None:
        self._relay_ctrl = relay_controller
        self._v2         = v2_meter   # real serial meter — may be None if not connected

    @property
    def relays(self) -> RelayControllerInterface:
        return self._relay_ctrl

    @property
    def voltmeter(self) -> VoltageMeterSerial:
        return self._v2

    def initialize(self) -> bool:
        return True

    def shutdown(self) -> None:
        self._relay_ctrl.reset_all_relays()
        self._relay_ctrl.disconnect()

    def emergency_stop(self) -> None:
        self._relay_ctrl.emergency_stop()

    def health_check(self) -> Dict[str, HardwareStatus]:
        v2_status = (
            self._v2.get_status() if self._v2 else HardwareStatus.DISCONNECTED
        )
        return {
            "relay_controller": self._relay_ctrl.get_status(),
            "voltage_meter_v2": v2_status,
        }
