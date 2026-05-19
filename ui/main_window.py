"""
MainWindow — top-level application window.

Layout:
  ┌─────────────────────────────────────────────────────┐
  │  TOP BAR: transformer selector | operator | status  │
  ├──────────────────────────┬───────────┬──────────────┤
  │  TRANSFORMER CANVAS      │ CONTROL   │ STATUS       │
  │  (animated diagram)      │ PANEL     │ PANEL        │
  ├──────────────────────────┴───────────┴──────────────┤
  │  CONSOLE LOG                                        │
  └─────────────────────────────────────────────────────┘
"""
import tkinter as tk
import customtkinter as ctk
from typing import Optional

from core.config_loader import ConfigLoader
from core.state_manager import StateManager, AppState, TestMode
from core.sequence_manager import SequenceManager
from core.test_engine import TestEngine
from core.logger import TestLogger
from hardware.hardware_interface import HardwareManagerInterface
from hardware.serial_manager import SerialManager

from ui.transformer_canvas import TransformerCanvasWidget
from ui.control_panel import ControlPanel
from ui.status_panel import StatusPanel
from ui.dialogs import AddTransformerDialog, ViewLogsDialog, AboutDialog
from ui.visual_editor import VisualTransformerEditorWindow as TransformerEditorWindow


class MainWindow(ctk.CTk):
    """
    Root application window.
    Owns all subsystems and wires them together.
    """

    TITLE = "Transformer Test Bench  |  Industrial ATB"
    MIN_W, MIN_H = 1200, 720

    def __init__(self,
                 config_loader: ConfigLoader,
                 state_manager: StateManager,
                 logger: TestLogger,
                 hardware_manager: HardwareManagerInterface,
                 serial_manager: Optional[SerialManager] = None):
        super().__init__()

        self._configs     = config_loader
        self._state       = state_manager
        self._logger      = logger
        self._hw          = hardware_manager
        self._serial_mgr  = serial_manager or SerialManager()
        self._seq_mgr     = SequenceManager()
        self._engine      = TestEngine(
            state_manager=self._state,
            sequence_manager=self._seq_mgr,
            config_loader=self._configs,
            hardware=self._hw,
            logger=self._logger,
        )

        # Wire logger → console
        self._logger.add_console_callback(self._append_console_line)

        self.title(self.TITLE)
        self.minsize(self.MIN_W, self.MIN_H)
        self.configure(fg_color="#0d0d1a")

        self._build_menu()
        self._build_layout()
        self._init_hardware()
        self._load_transformers()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Subscribe to live measurement updates
        self._state.subscribe("measurement", self._on_measurement_update)

        # Start periodic live-voltage poll (for mock or serial meter)
        self._poll_live_voltage()

    # ------------------------------------------------------------------ #
    #  Menu bar                                                           #
    # ------------------------------------------------------------------ #

    def _build_menu(self) -> None:
        menubar = tk.Menu(self, bg="#1a1a3a", fg="#c0d0ff",
                           activebackground="#2a3a7a", activeforeground="white",
                           relief="flat", tearoff=False)

        file_menu = tk.Menu(menubar, tearoff=False, bg="#1a1a3a", fg="#c0d0ff",
                             activebackground="#2a3a7a", activeforeground="white")
        file_menu.add_command(label="New Transformer (Editor)…", command=self._open_editor)
        file_menu.add_command(label="Edit Current Transformer…", command=self._edit_current)
        file_menu.add_separator()
        file_menu.add_command(label="Add Transformer (JSON)…",  command=self._open_add_transformer)
        file_menu.add_command(label="Reload Transformers",       command=self._load_transformers)
        file_menu.add_separator()
        file_menu.add_command(label="View Logs…",                command=self._open_logs)
        file_menu.add_separator()
        file_menu.add_command(label="Exit",                      command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        hw_menu = tk.Menu(menubar, tearoff=False, bg="#1a1a3a", fg="#c0d0ff",
                           activebackground="#2a3a7a", activeforeground="white")
        hw_menu.add_command(label="Re-initialize Hardware",  command=self._init_hardware)
        hw_menu.add_command(label="Hardware Status",         command=self._show_hw_status)
        hw_menu.add_separator()
        hw_menu.add_command(label="Serial Connections…",     command=self._open_serial_dialog)
        menubar.add_cascade(label="Hardware", menu=hw_menu)

        help_menu = tk.Menu(menubar, tearoff=False, bg="#1a1a3a", fg="#c0d0ff",
                             activebackground="#2a3a7a", activeforeground="white")
        help_menu.add_command(label="About", command=lambda: AboutDialog(self))
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    # ------------------------------------------------------------------ #
    #  Layout                                                             #
    # ------------------------------------------------------------------ #

    def _build_layout(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=3)   # canvas: largest
        self.grid_columnconfigure(1, weight=1)   # control panel
        self.grid_columnconfigure(2, weight=1)   # status panel

        # Top bar
        self._build_topbar()

        # Centre panels
        self._canvas_widget = TransformerCanvasWidget(
            self, state_manager=self._state
        )
        self._canvas_widget.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=4)

        self._control_panel = ControlPanel(
            self, state_manager=self._state,
            on_start=self._on_start,
            on_stop=self._on_stop,
            on_pause=self._on_pause,
            on_resume=self._on_resume,
            on_reset=self._on_reset,
            on_next=self._on_next,
            on_retry=self._on_retry,
            on_emergency=self._on_emergency,
        )
        self._control_panel.grid(row=1, column=1, sticky="nsew", padx=4, pady=4)

        self._status_panel = StatusPanel(self, state_manager=self._state)
        self._status_panel.grid(row=1, column=2, sticky="nsew", padx=(4, 8), pady=4)

        # Bottom console
        self._build_console()

    def _build_topbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="#151530", corner_radius=0, height=52)
        bar.grid(row=0, column=0, columnspan=3, sticky="ew", padx=0, pady=0)
        bar.grid_propagate(False)
        bar.grid_columnconfigure(3, weight=1)

        # App icon / title
        ctk.CTkLabel(bar, text="⚡ ATB",
                      font=ctk.CTkFont("Consolas", 16, "bold"),
                      text_color="#607abb").grid(row=0, column=0, padx=(16, 8), pady=10)

        ctk.CTkLabel(bar, text="Transformer:",
                      font=ctk.CTkFont("Consolas", 11),
                      text_color="#8090b0").grid(row=0, column=1, padx=(8, 2))

        self._transformer_combo = ctk.CTkComboBox(
            bar, values=[], width=260,
            font=ctk.CTkFont("Consolas", 11),
            fg_color="#0d0d20", button_color="#2a3a7a",
            dropdown_fg_color="#1a1a3a",
            command=self._on_transformer_selected,
        )
        self._transformer_combo.grid(row=0, column=2, padx=4)

        ctk.CTkButton(bar, text="✎ Editor", width=80, height=30,
                       font=ctk.CTkFont("Consolas", 10, "bold"),
                       fg_color="#1a2a5a", hover_color="#2a3a7a",
                       command=self._open_editor
                       ).grid(row=0, column=3, padx=6)

        ctk.CTkLabel(bar, text="Operator:",
                      font=ctk.CTkFont("Consolas", 11),
                      text_color="#8090b0").grid(row=0, column=4, padx=(16, 2))

        self._operator_entry = ctk.CTkEntry(
            bar, width=160,
            font=ctk.CTkFont("Consolas", 11),
            fg_color="#0d0d20", border_color="#2a3a7a",
            placeholder_text="Operator name…",
        )
        self._operator_entry.grid(row=0, column=5, padx=4)
        self._operator_entry.bind("<FocusOut>", self._on_operator_change)
        self._operator_entry.bind("<Return>",   self._on_operator_change)

        # Serial / hardware connection indicator
        self._conn_label = ctk.CTkLabel(
            bar, text="● MOCK",
            font=ctk.CTkFont("Consolas", 11, "bold"),
            text_color="#ffcc00",
        )
        self._conn_label.grid(row=0, column=6, padx=4)

        # Live voltage display (updates from meter serial or mock)
        ctk.CTkLabel(bar, text="V:",
                     font=ctk.CTkFont("Consolas", 11),
                     text_color="#8090b0").grid(row=0, column=7, padx=(8, 2))
        self._volt_label = ctk.CTkLabel(
            bar, text="---.- V",
            font=ctk.CTkFont("Consolas", 12, "bold"),
            text_color="#00e676", width=80,
        )
        self._volt_label.grid(row=0, column=8, padx=(0, 16))

    def _build_console(self) -> None:
        console_frame = ctk.CTkFrame(self, fg_color="#0a0a18", corner_radius=0, height=130)
        console_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=0, pady=0)
        console_frame.grid_propagate(False)
        console_frame.grid_rowconfigure(1, weight=1)
        console_frame.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(console_frame, fg_color="#111128", corner_radius=0, height=22)
        hdr.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(hdr, text="  SYSTEM CONSOLE",
                      font=ctk.CTkFont("Consolas", 9, "bold"),
                      text_color="#607abb").pack(side="left", padx=6, pady=2)
        ctk.CTkButton(hdr, text="Clear", width=60, height=18,
                       font=ctk.CTkFont("Consolas", 9),
                       fg_color="#1a1a3a", command=self._clear_console
                       ).pack(side="right", padx=6)

        self._console_text = tk.Text(
            console_frame,
            bg="#080818", fg="#80c0a0", insertbackground="#80c0a0",
            font=("Consolas", 9), state="disabled",
            wrap="word", relief="flat", height=6,
        )
        sby = tk.Scrollbar(console_frame, command=self._console_text.yview,
                            bg="#1a1a3a", troughcolor="#0a0a18")
        self._console_text.configure(yscrollcommand=sby.set)
        self._console_text.grid(row=1, column=0, sticky="nsew", padx=0)
        sby.grid(row=1, column=1, sticky="ns")

        # Colour tags for different log levels
        self._console_text.tag_config("INFO",  foreground="#80c0a0")
        self._console_text.tag_config("STEP",  foreground="#60a0ff")
        self._console_text.tag_config("WARN",  foreground="#ffcc00")
        self._console_text.tag_config("ERROR", foreground="#ff6060")

    # ------------------------------------------------------------------ #
    #  Initialization                                                     #
    # ------------------------------------------------------------------ #

    def _init_hardware(self) -> None:
        self._logger.info("Initializing hardware…")
        ok = self._hw.initialize()
        relay_ok = self._serial_mgr.relay_connected
        meter_ok = self._serial_mgr.meter_connected
        if relay_ok and meter_ok:
            self._conn_label.configure(text="● SERIAL", text_color="#00e676")
            self._logger.info("Hardware initialized (SERIAL mode)")
        elif relay_ok or meter_ok:
            self._conn_label.configure(text="● PARTIAL", text_color="#ffcc00")
            self._logger.info("Hardware initialized (partial serial)")
        elif ok:
            self._conn_label.configure(text="● MOCK", text_color="#ffcc00")
            self._logger.info("Hardware initialized (MOCK mode)")
        else:
            self._logger.error("Hardware initialization failed")
            self._conn_label.configure(text="● ERROR", text_color="#ff4444")
        self._state.app_state = AppState.READY

    def _load_transformers(self) -> None:
        ids = self._configs.scan_and_load()
        names = self._configs.list_names()
        self._transformer_combo.configure(values=names)
        if names:
            self._transformer_combo.set(names[0])
            self._on_transformer_selected(names[0])
        self._logger.info(f"Loaded {len(ids)} transformer(s): {', '.join(ids)}")

    # ------------------------------------------------------------------ #
    #  Event handlers                                                     #
    # ------------------------------------------------------------------ #

    def _on_transformer_selected(self, name: str) -> None:
        configs = self._configs.list_transformers()
        match = next((c for c in configs if c.name == name), None)
        if match:
            self._state.selected_transformer_id = match.transformer_id
            self._canvas_widget.load_transformer(match)
            self._logger.info(f"Transformer selected: {match.name}")

    def _on_operator_change(self, _event=None) -> None:
        self._state.operator_name = self._operator_entry.get().strip()

    def _on_start(self) -> None:
        op = self._operator_entry.get().strip() or "Unknown"
        self._state.operator_name = op
        self._engine.start(operator=op)

    def _on_stop(self) -> None:
        self._engine.stop()

    def _on_pause(self) -> None:
        self._engine.pause()

    def _on_resume(self) -> None:
        self._engine.resume()

    def _on_reset(self) -> None:
        self._engine.stop()
        self._state.reset()
        self._logger.info("System reset")

    def _on_next(self) -> None:
        self._engine.next_step()

    def _on_retry(self) -> None:
        self._engine.retry_step()

    def _on_emergency(self) -> None:
        self._engine.emergency_stop()
        self._logger.error("EMERGENCY STOP — operator activated")

    def _open_editor(self, initial_config: Optional[dict] = None) -> None:
        """Open the full graphical template editor (new or edit)."""
        TransformerEditorWindow(
            self, self._configs,
            on_saved=self._load_transformers,
            initial_config=initial_config,
        )

    def _edit_current(self) -> None:
        """Open the editor pre-loaded with the currently selected transformer."""
        tid = self._state.selected_transformer_id
        if not tid:
            self._logger.warn("No transformer selected to edit")
            return
        raw = self._configs.get_raw_json(tid)
        self._open_editor(initial_config=raw)

    def _open_add_transformer(self) -> None:
        AddTransformerDialog(
            self, self._configs,
            on_saved=self._load_transformers,
        )

    def _open_logs(self) -> None:
        from pathlib import Path
        log_dir = str(Path(__file__).parent.parent / "logs")
        ViewLogsDialog(self, log_dir)

    def _show_hw_status(self) -> None:
        health = self._hw.health_check()
        serial = self._serial_mgr.status_dict()
        lines  = "\n".join(f"  {k}: {v.value}" for k, v in health.items())
        lines += "\n" + "\n".join(f"  {k}: {v}" for k, v in serial.items())
        self._logger.info(f"Hardware / serial status:\n{lines}")

    def _open_serial_dialog(self) -> None:
        _SerialConnectionDialog(self, self._serial_mgr,
                                on_change=self._init_hardware)

    # ── live voltage display ───────────────────────────────────────────────

    def _on_measurement_update(self, voltage: float) -> None:
        """Called by StateManager when a step measurement is recorded."""
        self.after(0, lambda: self._volt_label.configure(
            text=f"{voltage:7.3f} V", text_color="#00e676"))

    def _poll_live_voltage(self) -> None:
        """
        Periodically pull the latest voltage from the serial meter (if connected)
        or the mock meter, and display it in the topbar.
        """
        try:
            meter = self._serial_mgr.meter_serial
            if meter.connected:
                v = meter.get_latest()
                if v is not None:
                    self._volt_label.configure(text=f"{v:7.3f} V",
                                               text_color="#00e676")
            elif hasattr(self._hw, "meter"):
                v = self._hw.meter.get_latest()
                if v is not None and v != 0.0:
                    self._volt_label.configure(text=f"{v:7.3f} V",
                                               text_color="#90d0a0")
        except Exception:
            pass
        self.after(200, self._poll_live_voltage)

    # ------------------------------------------------------------------ #
    #  Console helpers                                                    #
    # ------------------------------------------------------------------ #

    def _append_console_line(self, line: str) -> None:
        """Thread-safe: schedule UI update on main thread."""
        self.after(0, lambda: self._do_append(line))

    def _do_append(self, line: str) -> None:
        self._console_text.configure(state="normal")
        # Determine tag from log level
        tag = "INFO"
        for level in ("STEP", "WARN", "ERROR"):
            if f"[{level}]" in line:
                tag = level
                break
        self._console_text.insert(tk.END, line + "\n", tag)
        self._console_text.see(tk.END)
        # Keep last 500 lines to avoid memory growth
        lines = int(self._console_text.index(tk.END).split(".")[0])
        if lines > 500:
            self._console_text.delete("1.0", "50.0")
        self._console_text.configure(state="disabled")

    def _clear_console(self) -> None:
        self._console_text.configure(state="normal")
        self._console_text.delete("1.0", tk.END)
        self._console_text.configure(state="disabled")

    # ------------------------------------------------------------------ #
    #  Close handler                                                      #
    # ------------------------------------------------------------------ #

    def _on_close(self) -> None:
        self._engine.stop()
        self._serial_mgr.disconnect_all()
        self._hw.shutdown()
        self.destroy()


# ── Serial Connection Dialog ───────────────────────────────────────────────

class _SerialConnectionDialog(ctk.CTkToplevel):
    """
    Modal dialog for configuring and connecting both serial ports:
      - Relay MCU
      - Voltage Meter
    """

    def __init__(self, parent, serial_mgr: SerialManager,
                 on_change=None) -> None:
        super().__init__(parent)
        self._mgr       = serial_mgr
        self._on_change = on_change

        self.title("Serial Connections")
        self.resizable(False, False)
        self.grab_set()

        self._build()

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 6}
        F = ctk.CTkFont("Consolas", 11)
        FB = ctk.CTkFont("Consolas", 11, "bold")

        ports = self._mgr.list_ports() or ["(no ports)"]

        frame = ctk.CTkFrame(self, fg_color="#12122a")
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        # ── Relay MCU ─────────────────────────────────────────────────────
        ctk.CTkLabel(frame, text="RELAY MCU", font=FB,
                     text_color="#607abb").grid(row=0, column=0, columnspan=3,
                                                sticky="w", **pad)

        ctk.CTkLabel(frame, text="Port:", font=F).grid(row=1, column=0, sticky="e", **pad)
        self._relay_port = ctk.CTkComboBox(frame, values=ports, width=140, font=F)
        self._relay_port.grid(row=1, column=1, sticky="ew", **pad)
        if self._mgr.config.relay_port:
            self._relay_port.set(self._mgr.config.relay_port)

        ctk.CTkLabel(frame, text="Baud:", font=F).grid(row=2, column=0, sticky="e", **pad)
        self._relay_baud = ctk.CTkEntry(frame, width=80, font=F)
        self._relay_baud.insert(0, str(self._mgr.config.relay_baud))
        self._relay_baud.grid(row=2, column=1, sticky="w", **pad)

        self._relay_status = ctk.CTkLabel(
            frame, font=FB,
            text="● CONNECTED" if self._mgr.relay_connected else "● DISCONNECTED",
            text_color="#00e676" if self._mgr.relay_connected else "#ff4444")
        self._relay_status.grid(row=1, column=2, rowspan=2, **pad)

        ctk.CTkButton(frame, text="Connect Relay MCU", font=F,
                      fg_color="#1a3a6a", hover_color="#2a5090",
                      command=self._connect_relay
                      ).grid(row=3, column=0, columnspan=3, sticky="ew", **pad)

        # ── Voltage Meter ─────────────────────────────────────────────────
        ctk.CTkLabel(frame, text="VOLTAGE METER", font=FB,
                     text_color="#607abb").grid(row=4, column=0, columnspan=3,
                                                sticky="w", padx=12, pady=(16, 6))

        ctk.CTkLabel(frame, text="Port:", font=F).grid(row=5, column=0, sticky="e", **pad)
        self._meter_port = ctk.CTkComboBox(frame, values=ports, width=140, font=F)
        self._meter_port.grid(row=5, column=1, sticky="ew", **pad)
        if self._mgr.config.meter_port:
            self._meter_port.set(self._mgr.config.meter_port)

        ctk.CTkLabel(frame, text="Baud:", font=F).grid(row=6, column=0, sticky="e", **pad)
        self._meter_baud = ctk.CTkEntry(frame, width=80, font=F)
        self._meter_baud.insert(0, str(self._mgr.config.meter_baud))
        self._meter_baud.grid(row=6, column=1, sticky="w", **pad)

        self._meter_status = ctk.CTkLabel(
            frame, font=FB,
            text="● CONNECTED" if self._mgr.meter_connected else "● DISCONNECTED",
            text_color="#00e676" if self._mgr.meter_connected else "#ff4444")
        self._meter_status.grid(row=5, column=2, rowspan=2, **pad)

        ctk.CTkButton(frame, text="Connect Voltage Meter", font=F,
                      fg_color="#1a3a6a", hover_color="#2a5090",
                      command=self._connect_meter
                      ).grid(row=7, column=0, columnspan=3, sticky="ew", **pad)

        # ── Disconnect All ────────────────────────────────────────────────
        ctk.CTkButton(frame, text="Disconnect All", font=F,
                      fg_color="#3a1a1a", hover_color="#5a2a2a",
                      command=self._disconnect_all
                      ).grid(row=8, column=0, columnspan=3, sticky="ew",
                             padx=12, pady=(12, 4))

        ctk.CTkButton(frame, text="Close", font=F,
                      fg_color="#1a1a3a", hover_color="#2a2a6a",
                      command=self.destroy
                      ).grid(row=9, column=0, columnspan=3, sticky="ew",
                             padx=12, pady=(4, 12))

    def _connect_relay(self) -> None:
        port = self._relay_port.get()
        baud = self._safe_int(self._relay_baud.get(), 115200)
        ok   = self._mgr.connect_relay(port, baud)
        self._relay_status.configure(
            text="● CONNECTED" if ok else "● FAILED",
            text_color="#00e676" if ok else "#ff4444")
        if self._on_change:
            self._on_change()

    def _connect_meter(self) -> None:
        port = self._meter_port.get()
        baud = self._safe_int(self._meter_baud.get(), 9600)
        ok   = self._mgr.connect_meter(port, baud)
        self._meter_status.configure(
            text="● CONNECTED" if ok else "● FAILED",
            text_color="#00e676" if ok else "#ff4444")
        if self._on_change:
            self._on_change()

    def _disconnect_all(self) -> None:
        self._mgr.disconnect_all()
        self._relay_status.configure(text="● DISCONNECTED", text_color="#ff4444")
        self._meter_status.configure(text="● DISCONNECTED", text_color="#ff4444")
        if self._on_change:
            self._on_change()

    @staticmethod
    def _safe_int(s: str, default: int) -> int:
        try:
            return int(s)
        except ValueError:
            return default
