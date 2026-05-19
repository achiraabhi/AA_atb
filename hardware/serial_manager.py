"""
SerialManager — lifecycle manager for both serial connections.

Manages:
  relay_serial  — RelaySerial  (relay MCU)
  meter_serial  — VoltageMeterSerial  (external voltage meter)

Provides convenience methods for connecting / disconnecting each port,
querying connection state, and listing available COM ports.
"""
import logging
from typing import List, Optional

from hardware.relay_serial import RelaySerial
from hardware.voltage_meter_serial import VoltageMeterSerial
from hardware.protocol import DEFAULT_BAUD_MCU, DEFAULT_BAUD_METER

log = logging.getLogger(__name__)


class SerialConfig:
    """Mutable serial port configuration — updated when ports are (re-)connected."""

    def __init__(self) -> None:
        self.relay_port: Optional[str] = None
        self.relay_baud: int           = DEFAULT_BAUD_MCU
        self.meter_port: Optional[str] = None
        self.meter_baud: int           = DEFAULT_BAUD_METER


class SerialManager:
    """
    Owns both serial drivers and exposes a unified connection / status API.

    Usage
    -----
    sm = SerialManager()
    sm.connect_relay("COM3")
    sm.connect_meter("COM4", baud=9600)
    print(sm.status_dict())
    sm.disconnect_all()
    """

    def __init__(self) -> None:
        self.relay_serial = RelaySerial()
        self.meter_serial = VoltageMeterSerial()
        self.config       = SerialConfig()

    # ── relay MCU ─────────────────────────────────────────────────────────

    def connect_relay(self, port: str, baud: Optional[int] = None) -> bool:
        self.config.relay_port = port
        if baud is not None:
            self.config.relay_baud = baud
        ok = self.relay_serial.connect(port, self.config.relay_baud)
        log.info(f"Relay MCU {'CONNECTED' if ok else 'FAILED'} on {port} @ {self.config.relay_baud}")
        return ok

    def disconnect_relay(self) -> None:
        self.relay_serial.disconnect()
        log.info("Relay MCU disconnected")

    # ── voltage meter ─────────────────────────────────────────────────────

    def connect_meter(self, port: str, baud: Optional[int] = None) -> bool:
        self.config.meter_port = port
        if baud is not None:
            self.config.meter_baud = baud
        ok = self.meter_serial.connect(port, self.config.meter_baud)
        log.info(f"Voltage meter {'CONNECTED' if ok else 'FAILED'} on {port} @ {self.config.meter_baud}")
        return ok

    def disconnect_meter(self) -> None:
        self.meter_serial.disconnect()
        log.info("Voltage meter disconnected")

    # ── combined ──────────────────────────────────────────────────────────

    def disconnect_all(self) -> None:
        self.disconnect_relay()
        self.disconnect_meter()

    # ── status ────────────────────────────────────────────────────────────

    @property
    def relay_connected(self) -> bool:
        return self.relay_serial.connected

    @property
    def meter_connected(self) -> bool:
        return self.meter_serial.connected

    def status_dict(self) -> dict:
        return {
            "relay_mcu":     "CONNECTED" if self.relay_connected else "DISCONNECTED",
            "voltage_meter": "CONNECTED" if self.meter_connected else "DISCONNECTED",
        }

    @staticmethod
    def list_ports() -> List[str]:
        """Return available serial port device names."""
        try:
            import serial.tools.list_ports  # type: ignore
            return [p.device for p in serial.tools.list_ports.comports()]
        except Exception:
            return []
