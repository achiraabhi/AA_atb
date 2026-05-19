"""
Transformer Post-Assembly Test Bench
Entry point — bootstraps all subsystems and launches the UI.

Hardware mode selection:
  - Default: MockHardwareManager  (no serial hardware required)
  - Real:    SerialManager + RelayController + VoltageMeterSerial
             (connect ports via Hardware → Serial Connections… in the menu)
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import customtkinter as ctk

from core.config_loader import ConfigLoader
from core.state_manager import StateManager
from core.logger import TestLogger
from hardware.mock_hardware import MockHardwareManager
from hardware.serial_manager import SerialManager
from ui.main_window import MainWindow


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    config_loader    = ConfigLoader()
    state_manager    = StateManager()
    logger           = TestLogger()
    hardware_manager = MockHardwareManager()
    serial_manager   = SerialManager()

    app = MainWindow(
        config_loader=config_loader,
        state_manager=state_manager,
        logger=logger,
        hardware_manager=hardware_manager,
        serial_manager=serial_manager,
    )
    app.mainloop()


if __name__ == "__main__":
    main()
