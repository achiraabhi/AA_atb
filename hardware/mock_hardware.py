"""
Mock hardware implementation for development and offline simulation.

MockRelayController — 34 relays, enforces safety rules, simulates contact delay.
MockVoltageReader   — multi-channel ADC simulation with Gaussian noise.
MockVoltageMeter    — single-channel serial-meter simulation (used by MeasurementManager).
MockHardwareManager — aggregates all mocks; provides simulate_test_voltages() hook.
"""
import time
import random
import threading
from typing import Dict, List, Optional

from hardware.hardware_interface import (
    RelayControllerInterface, VoltageReaderInterface, HardwareManagerInterface,
    RelayState, HardwareStatus, VoltageReading,
    RELAY_COUNT, RL_A_MIN, RL_A_MAX, RL_B_MIN, RL_B_MAX, RL_GATE_A, RL_GATE_B,
)
from hardware.protocol import is_group_a, is_group_b


# ── helpers ────────────────────────────────────────────────────────────────

def _enforce_group_safety(relay_ids: List[int]) -> List[int]:
    """Keep at most one A-relay and one B-relay; pass gates through."""
    a = [r for r in relay_ids if is_group_a(r)]
    b = [r for r in relay_ids if is_group_b(r)]
    g = [r for r in relay_ids if r in (RL_GATE_A, RL_GATE_B)]
    result: List[int] = []
    if a:
        result.append(a[0])
    if b:
        result.append(b[0])
    result.extend(x for x in g if x not in result)
    return result


def _add_gate_relays(relay_ids: List[int]) -> List[int]:
    result = list(relay_ids)
    if any(is_group_a(r) for r in relay_ids) and RL_GATE_A not in result:
        result.append(RL_GATE_A)
    if any(is_group_b(r) for r in relay_ids) and RL_GATE_B not in result:
        result.append(RL_GATE_B)
    return result


# ── mock relay controller ──────────────────────────────────────────────────

class MockRelayController(RelayControllerInterface):
    """
    Simulates RL1–RL34 with safety enforcement and realistic contact delay.
    Relay IDs are 1-based to match the hardware spec.
    """

    def __init__(self, fault_rate: float = 0.0) -> None:
        self._fault_rate = fault_rate
        self._states: Dict[int, bool] = {i: False for i in range(1, RELAY_COUNT + 1)}
        self._status = HardwareStatus.DISCONNECTED
        self._lock   = threading.Lock()

    def connect(self) -> bool:
        time.sleep(0.05)
        self._status = HardwareStatus.CONNECTED
        return True

    def disconnect(self) -> None:
        self.reset_all_relays()
        self._status = HardwareStatus.DISCONNECTED

    def set_relay(self, relay_id: int, state: bool) -> bool:
        if self._status != HardwareStatus.CONNECTED:
            return False
        if state:
            with self._lock:
                current = [r for r, s in self._states.items() if s]
            return self.set_all_relays({r: True for r in current + [relay_id]})
        else:
            with self._lock:
                self._states[relay_id] = False
            return True

    def set_all_relays(self, states: Dict[int, bool]) -> bool:
        if self._status != HardwareStatus.CONNECTED:
            return False
        if random.random() < self._fault_rate:
            return False
        active = [r for r, s in states.items() if s]
        safe   = _enforce_group_safety(active)
        final  = _add_gate_relays(safe)
        time.sleep(0.005)   # contact bounce simulation
        with self._lock:
            for i in range(1, RELAY_COUNT + 1):
                self._states[i] = (i in final)
        return True

    def reset_all_relays(self) -> bool:
        with self._lock:
            for k in self._states:
                self._states[k] = False
        return True

    def get_relay_state(self, relay_id: int) -> RelayState:
        with self._lock:
            if relay_id not in self._states:
                return RelayState.UNKNOWN
            return RelayState.CLOSED if self._states[relay_id] else RelayState.OPEN

    def get_all_states(self) -> Dict[int, bool]:
        with self._lock:
            return dict(self._states)

    def get_status(self) -> HardwareStatus:
        return self._status

    @property
    def relay_count(self) -> int:
        return RELAY_COUNT


# ── mock ADC voltage reader ────────────────────────────────────────────────

class MockVoltageReader(VoltageReaderInterface):
    """
    Simulates a multi-channel ADC.
    The test engine calls simulate_test_voltages() to pre-load expected values;
    subsequent read_voltage() calls return them with Gaussian noise applied.
    """

    def __init__(self, channel_count: int = 8, noise_rms: float = 0.3) -> None:
        self._channel_count = channel_count
        self._noise_rms     = noise_rms
        self._voltages: Dict[int, float] = {i: 0.0 for i in range(channel_count)}
        self._status = HardwareStatus.DISCONNECTED
        self._lock   = threading.Lock()

    def connect(self) -> bool:
        time.sleep(0.02)
        self._status = HardwareStatus.CONNECTED
        return True

    def disconnect(self) -> None:
        self._status = HardwareStatus.DISCONNECTED

    def set_channel_voltage(self, channel: int, voltage: float) -> None:
        with self._lock:
            self._voltages[channel] = voltage

    def set_all_voltages(self, voltage_map: Dict[int, float]) -> None:
        with self._lock:
            self._voltages.update(voltage_map)

    def read_voltage(self, channel: int) -> VoltageReading:
        if self._status != HardwareStatus.CONNECTED:
            return VoltageReading(channel=channel, voltage=0.0,
                                  timestamp=time.time(), valid=False,
                                  error="Not connected")
        if channel < 0 or channel >= self._channel_count:
            return VoltageReading(channel=channel, voltage=0.0,
                                  timestamp=time.time(), valid=False,
                                  error=f"Invalid channel {channel}")
        time.sleep(0.008)   # ADC conversion time
        with self._lock:
            base = self._voltages.get(channel, 0.0)
        noise = random.gauss(0, self._noise_rms) if base > 0 else 0.0
        return VoltageReading(channel=channel,
                              voltage=round(base + noise, 3),
                              timestamp=time.time(), valid=True)

    def read_all_channels(self) -> List[VoltageReading]:
        return [self.read_voltage(ch) for ch in range(self._channel_count)]

    def get_status(self) -> HardwareStatus:
        return self._status

    @property
    def channel_count(self) -> int:
        return self._channel_count


# ── mock serial voltage meter ──────────────────────────────────────────────

class MockVoltageMeter:
    """
    Simulates a serial-streaming voltage meter.
    Exposes get_samples() so MeasurementManager can use it via duck typing.
    """

    def __init__(self, noise_rms: float = 0.1) -> None:
        self._noise_rms  = noise_rms
        self._base       = 0.0
        self._lock       = threading.Lock()
        self._connected  = False

    def connect(self) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def set_voltage(self, voltage: float) -> None:
        with self._lock:
            self._base = voltage

    def get_samples(self, n: int = 10) -> List[float]:
        with self._lock:
            base = self._base
        if base == 0.0:
            return [0.0] * n
        return [
            round(base + random.gauss(0, self._noise_rms), 4)
            for _ in range(n)
        ]

    def get_latest(self) -> Optional[float]:
        samples = self.get_samples(1)
        return samples[0] if samples else None

    def get_average(self, n: int = 10) -> Optional[float]:
        s = self.get_samples(n)
        return sum(s) / len(s) if s else None

    def is_stable(self, n: int = 10, max_spread_v: float = 0.2) -> bool:
        s = self.get_samples(n)
        return (max(s) - min(s)) <= max_spread_v if len(s) >= 2 else False

    def read_voltage(self, channel: int = 0) -> VoltageReading:
        v = self.get_latest() or 0.0
        return VoltageReading(channel=channel, voltage=v,
                              timestamp=time.time(), valid=True)

    def get_status(self) -> HardwareStatus:
        return HardwareStatus.CONNECTED if self._connected else HardwareStatus.DISCONNECTED

    @property
    def channel_count(self) -> int:
        return 1


# ── mock hardware manager ──────────────────────────────────────────────────

class MockHardwareManager(HardwareManagerInterface):
    """
    Aggregates MockRelayController + MockVoltageReader + MockVoltageMeter.
    The test engine uses the ADC reader (voltmeter property) for channel-based
    reads; MeasurementManager uses the meter for averaged serial-style reads.
    """

    def __init__(self) -> None:
        self._relay_ctrl  = MockRelayController()
        self._volt_reader = MockVoltageReader(channel_count=8)
        self._volt_meter  = MockVoltageMeter()
        self._initialized = False

    @property
    def relays(self) -> MockRelayController:
        return self._relay_ctrl

    @property
    def voltmeter(self) -> MockVoltageReader:
        return self._volt_reader

    @property
    def meter(self) -> MockVoltageMeter:
        """Serial-style meter used by MeasurementManager."""
        return self._volt_meter

    def initialize(self) -> bool:
        ok_r = self._relay_ctrl.connect()
        ok_v = self._volt_reader.connect()
        ok_m = self._volt_meter.connect()
        self._initialized = ok_r and ok_v and ok_m
        return self._initialized

    def shutdown(self) -> None:
        self._relay_ctrl.reset_all_relays()
        self._relay_ctrl.disconnect()
        self._volt_reader.disconnect()
        self._volt_meter.disconnect()
        self._initialized = False

    def emergency_stop(self) -> None:
        self._relay_ctrl.reset_all_relays()

    def health_check(self) -> Dict[str, HardwareStatus]:
        return {
            "relay_controller": self._relay_ctrl.get_status(),
            "voltage_reader":   self._volt_reader.get_status(),
            "voltage_meter":    self._volt_meter.get_status(),
        }

    def simulate_test_voltages(
        self,
        expected_voltage: float,
        channel: int = 0,
        deviation_pct: float = 1.0,
    ) -> None:
        """
        Pre-load both mock voltage sources with a near-nominal reading.
        Called by the test engine before each measurement step.
        """
        dev = expected_voltage * (deviation_pct / 100.0)
        simulated = expected_voltage + random.uniform(-dev, dev)
        self._volt_reader.set_channel_voltage(channel, simulated)
        self._volt_meter.set_voltage(simulated)
