"""
MeasurementManager — high-level measurement orchestrator.

Full measurement cycle for a single step:
  1. Clear all relays  (safe break-before-make)
  2. Activate routing relays  [RL_a, RL_b, RL33, RL34]
  3. Wait stabilization delay
  4. Collect N voltage samples from the meter
  5. Noise-filter: flag if spread exceeds threshold
  6. Average the samples
  7. Clear relays
  8. Return a VoltageReading

Noise filtering:
  If (max − min) of samples > MAX_SPREAD_ACCEPT the reading is flagged
  in a warning log but still returned valid.  The calling layer decides
  whether to retry or accept.
"""
import time
import logging
from statistics import mean
from typing import List, Optional

from hardware.hardware_interface import VoltageReading

log = logging.getLogger(__name__)


class MeasurementManager:
    """
    Coordinates relay switching and voltage sampling.

    relay_ctrl  — any object with set_all_relays() / reset_all_relays()
    volt_meter  — VoltageMeterSerial  OR  legacy VoltageReaderInterface
    """

    DEFAULT_SAMPLES     = 10
    DEFAULT_STAB_MS     = 500
    MAX_SPREAD_ACCEPT   = 0.5    # V  — warn above this noise level
    INTER_SAMPLE_DELAY  = 0.02   # s  — pause between legacy ADC samples

    def __init__(self, relay_ctrl, volt_meter) -> None:
        self._relays = relay_ctrl
        self._meter  = volt_meter

    # ── public API ────────────────────────────────────────────────────────

    def measure(
        self,
        relay_ids: List[int],
        stabilization_ms: int = DEFAULT_STAB_MS,
        n_samples: int = DEFAULT_SAMPLES,
    ) -> VoltageReading:
        """
        Execute a full measurement cycle and return a VoltageReading.
        relay_ids — the relay IDs that must be closed for this measurement.
        """
        # 1. Safe clear
        self._relays.reset_all_relays()
        time.sleep(0.02)

        # 2. Set relays (safety enforced inside relay controller)
        relay_map = {rid: True for rid in relay_ids}
        ok = self._relays.set_all_relays(relay_map)
        if not ok:
            log.warning(f"Relay set may not have been fully acknowledged: {relay_ids}")

        # 3. Stabilise
        time.sleep(stabilization_ms / 1000.0)

        # 4. Collect samples
        samples = self._collect_samples(n_samples)

        # 5. Clear relays
        self._relays.reset_all_relays()

        # 6. Evaluate
        if not samples:
            return VoltageReading(
                channel=0, voltage=0.0, timestamp=time.time(),
                valid=False, error="No voltage samples collected",
            )

        spread = max(samples) - min(samples)
        avg    = mean(samples)

        if spread > self.MAX_SPREAD_ACCEPT:
            log.warning(
                f"High noise spread {spread:.3f} V over {len(samples)} samples "
                f"(avg={avg:.4f} V) for relays {relay_ids}"
            )

        return VoltageReading(
            channel=0,
            voltage=round(avg, 4),
            timestamp=time.time(),
            valid=True,
        )

    # ── sample collection ─────────────────────────────────────────────────

    def _collect_samples(self, n: int) -> List[float]:
        """
        Pull samples from whichever meter type is available.

        VoltageMeterSerial exposes get_samples(n) returning a list of floats
        that were collected by the background reader thread.

        Legacy VoltageReaderInterface exposes read_voltage(channel) and is
        polled synchronously.
        """
        if hasattr(self._meter, "get_samples"):
            # Serial meter — background-thread buffer
            raw = self._meter.get_samples(n)
            return [v for v in raw if isinstance(v, (int, float))]

        # Legacy ADC path
        samples: List[float] = []
        for _ in range(n):
            reading = self._meter.read_voltage(0)
            if reading.valid:
                samples.append(reading.voltage)
            time.sleep(self.INTER_SAMPLE_DELAY)
        return samples
