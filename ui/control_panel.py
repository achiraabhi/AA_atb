"""
ControlPanel — right-side panel containing test controls, relay status grid,
progress display, and system status banner.

Relay grid layout:
  Side A  — RL01–RL16  (cyan when active)   voltmeter + node selection
  Side B  — RL17–RL32  (green when active)  voltmeter − node selection
  Gates   — RL33–RL34  (yellow when active) voltmeter gate relays
"""
import tkinter as tk
import customtkinter as ctk
from typing import Callable, Dict, Optional

from core.state_manager import StateManager, AppState, TestMode
from hardware.hardware_interface import RELAY_COUNT, RL_A_MIN, RL_A_MAX, RL_B_MIN, RL_B_MAX, RL_GATE_A, RL_GATE_B


# Relay group colour scheme
_COL_A_IDLE   = "#1a2030"
_COL_A_TXT    = "#2a3a60"
_COL_A_ACTIVE = "#0a2040"
_COL_A_TXT_ON = "#00c8ff"   # cyan

_COL_B_IDLE   = "#1a2a1a"
_COL_B_TXT    = "#2a4a2a"
_COL_B_ACTIVE = "#0a2a0a"
_COL_B_TXT_ON = "#00e676"   # green

_COL_G_IDLE   = "#2a2010"
_COL_G_TXT    = "#4a3a10"
_COL_G_ACTIVE = "#2a1a00"
_COL_G_TXT_ON = "#ffcc00"   # yellow


class ControlPanel(ctk.CTkFrame):

    _BTN_FONT  = None
    _LBL_FONT  = None
    _MONO_FONT = None

    @classmethod
    def _fonts(cls) -> None:
        if cls._BTN_FONT is None:
            cls._BTN_FONT  = ctk.CTkFont("Consolas", 12, "bold")
            cls._LBL_FONT  = ctk.CTkFont("Consolas", 10)
            cls._MONO_FONT = ctk.CTkFont("Consolas", 11)

    @property
    def BTN_FONT(self):  self._fonts(); return self._BTN_FONT
    @property
    def LBL_FONT(self):  self._fonts(); return self._LBL_FONT
    @property
    def MONO_FONT(self): self._fonts(); return self._MONO_FONT

    def __init__(self, parent, state_manager: StateManager,
                 on_start:     Callable,
                 on_stop:      Callable,
                 on_pause:     Callable,
                 on_resume:    Callable,
                 on_reset:     Callable,
                 on_next:      Callable,
                 on_retry:     Callable,
                 on_emergency: Callable,
                 **kwargs):
        super().__init__(parent, **kwargs)
        self._state = state_manager
        self._callbacks = {
            "start": on_start, "stop": on_stop,
            "pause": on_pause, "resume": on_resume,
            "reset": on_reset, "next": on_next,
            "retry": on_retry, "emergency": on_emergency,
        }
        # relay_id → CTkLabel  (1-based, RL1–RL34)
        self._relay_labels: Dict[int, ctk.CTkLabel] = {}

        self.configure(fg_color="#12122a", corner_radius=8)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Scrollable container — all content lives inside this
        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color="#12122a", corner_radius=0,
            scrollbar_button_color="#2a2a5a",
            scrollbar_button_hover_color="#3a3a7a",
        )
        self._scroll.grid(row=0, column=0, sticky="nsew")
        self._scroll.grid_columnconfigure(0, weight=1)

        self._build_ui()
        self._subscribe()

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self) -> None:
        S = self._scroll   # all widgets go into the scrollable frame
        row = 0

        # Mode
        row = self._section_header("TEST MODE", row)
        mode_frame = ctk.CTkFrame(S, fg_color="transparent")
        mode_frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4)
        mode_frame.grid_columnconfigure((0, 1), weight=1)
        row += 1

        self._btn_auto = ctk.CTkButton(
            mode_frame, text="AUTO", font=self.BTN_FONT,
            fg_color="#1a3a6a", hover_color="#2a5090",
            command=lambda: self._set_mode(TestMode.AUTO))
        self._btn_auto.grid(row=0, column=0, padx=3, pady=2, sticky="ew")

        self._btn_manual = ctk.CTkButton(
            mode_frame, text="MANUAL", font=self.BTN_FONT,
            fg_color="#1a1a3a", hover_color="#2a2a5a",
            command=lambda: self._set_mode(TestMode.MANUAL))
        self._btn_manual.grid(row=0, column=1, padx=3, pady=2, sticky="ew")

        # Controls
        row = self._section_header("TEST CONTROL", row)

        self._btn_start = self._big_btn(row, "▶  START TEST",
                                         "#1a5a1a", "#2a8a2a", self._callbacks["start"])
        row += 1
        self._btn_stop = self._big_btn(row, "■  STOP",
                                        "#5a1a1a", "#8a2a2a", self._callbacks["stop"])
        row += 1

        pause_row = ctk.CTkFrame(S, fg_color="transparent")
        pause_row.grid(row=row, column=0, sticky="ew", padx=8, pady=2)
        pause_row.grid_columnconfigure((0, 1), weight=1)
        row += 1

        self._btn_pause = ctk.CTkButton(
            pause_row, text="⏸ PAUSE", font=self.BTN_FONT,
            fg_color="#4a3a0a", hover_color="#6a5a10",
            command=self._callbacks["pause"])
        self._btn_pause.grid(row=0, column=0, padx=3, sticky="ew")

        self._btn_resume = ctk.CTkButton(
            pause_row, text="▶ RESUME", font=self.BTN_FONT,
            fg_color="#0a3a4a", hover_color="#105060",
            command=self._callbacks["resume"])
        self._btn_resume.grid(row=0, column=1, padx=3, sticky="ew")

        manual_row = ctk.CTkFrame(S, fg_color="transparent")
        manual_row.grid(row=row, column=0, sticky="ew", padx=8, pady=2)
        manual_row.grid_columnconfigure((0, 1), weight=1)
        row += 1

        self._btn_next = ctk.CTkButton(
            manual_row, text="⏭ NEXT", font=self.BTN_FONT,
            fg_color="#1a2a4a", hover_color="#2a3a6a",
            command=self._callbacks["next"])
        self._btn_next.grid(row=0, column=0, padx=3, sticky="ew")

        self._btn_retry = ctk.CTkButton(
            manual_row, text="↺ RETRY", font=self.BTN_FONT,
            fg_color="#3a2a0a", hover_color="#5a4010",
            command=self._callbacks["retry"])
        self._btn_retry.grid(row=0, column=1, padx=3, sticky="ew")

        self._btn_reset = self._big_btn(row, "↺  RESET",
                                         "#1a1a4a", "#2a2a7a", self._callbacks["reset"])
        row += 1

        self._btn_estop = ctk.CTkButton(
            S, text="⚠  EMERGENCY STOP",
            font=ctk.CTkFont("Consolas", 13, "bold"),
            fg_color="#7a0000", hover_color="#aa0000",
            height=48, corner_radius=6,
            command=self._callbacks["emergency"])
        self._btn_estop.grid(row=row, column=0, padx=8, pady=8, sticky="ew")
        row += 1

        # Progress
        row = self._section_header("PROGRESS", row)
        self._progress_bar = ctk.CTkProgressBar(S, height=14, corner_radius=4)
        self._progress_bar.grid(row=row, column=0, padx=8, pady=4, sticky="ew")
        self._progress_bar.set(0)
        row += 1

        self._progress_label = ctk.CTkLabel(
            S, text="Step: — / —",
            font=self.MONO_FONT, text_color="#8090b0")
        self._progress_label.grid(row=row, column=0, padx=8, pady=2)
        row += 1

        # Relay status grid
        row = self._section_header("RELAY STATUS  (A=cyan  B=green  Gate=yellow)", row)
        self._relay_frame = ctk.CTkFrame(S, fg_color="#0a0a18", corner_radius=4)
        self._relay_frame.grid(row=row, column=0, padx=8, pady=4, sticky="ew")
        row += 1
        self._build_relay_grid()

        # System status banner
        row = self._section_header("SYSTEM STATUS", row)
        self._status_banner = ctk.CTkLabel(
            S, text="IDLE",
            font=ctk.CTkFont("Consolas", 18, "bold"),
            text_color="#606080", height=44)
        self._status_banner.grid(row=row, column=0, padx=8, pady=6)

    def _section_header(self, title: str, row: int) -> int:
        S = self._scroll
        frame = ctk.CTkFrame(S, fg_color="#1e1e3a", corner_radius=0, height=22)
        frame.grid(row=row, column=0, sticky="ew", padx=0, pady=(8, 0))
        ctk.CTkLabel(frame, text=f"  {title}",
                     font=ctk.CTkFont("Consolas", 9, "bold"),
                     text_color="#607abb").pack(side="left", padx=4)
        return row + 1

    def _big_btn(self, row: int, text: str, fg: str, hover: str,
                 cmd: Callable) -> ctk.CTkButton:
        S = self._scroll
        btn = ctk.CTkButton(
            S, text=text, font=self.BTN_FONT,
            fg_color=fg, hover_color=hover, height=38, corner_radius=6,
            command=cmd)
        btn.grid(row=row, column=0, padx=8, pady=3, sticky="ew")
        return btn

    def _build_relay_grid(self) -> None:
        """
        Build the 34-relay status grid in three labelled groups:
          Side A : RL01–RL16  (4 columns)
          Side B : RL17–RL32  (4 columns)
          Gate   : RL33–RL34  (2 columns)
        """
        cols = 4
        r = 0

        # ── Group A label ────────────────────────────────────────────────
        lbl = ctk.CTkLabel(self._relay_frame, text=" SIDE A (RL01–RL16) ",
                           font=ctk.CTkFont("Consolas", 8, "bold"),
                           text_color=_COL_A_TXT_ON, fg_color="transparent")
        lbl.grid(row=r, column=0, columnspan=cols, sticky="w", padx=4, pady=(4, 0))
        r += 1

        for i, rid in enumerate(range(RL_A_MIN, RL_A_MAX + 1)):
            row_i, col_i = divmod(i, cols)
            self._relay_labels[rid] = self._make_relay_label(rid, "A")
            self._relay_labels[rid].grid(
                row=r + row_i, column=col_i, padx=2, pady=2)
        r += (RL_A_MAX - RL_A_MIN) // cols + 1

        # ── Group B label ────────────────────────────────────────────────
        lbl = ctk.CTkLabel(self._relay_frame, text=" SIDE B (RL17–RL32) ",
                           font=ctk.CTkFont("Consolas", 8, "bold"),
                           text_color=_COL_B_TXT_ON, fg_color="transparent")
        lbl.grid(row=r, column=0, columnspan=cols, sticky="w", padx=4, pady=(6, 0))
        r += 1

        for i, rid in enumerate(range(RL_B_MIN, RL_B_MAX + 1)):
            row_i, col_i = divmod(i, cols)
            self._relay_labels[rid] = self._make_relay_label(rid, "B")
            self._relay_labels[rid].grid(
                row=r + row_i, column=col_i, padx=2, pady=2)
        r += (RL_B_MAX - RL_B_MIN) // cols + 1

        # ── Gate relays label ─────────────────────────────────────────────
        lbl = ctk.CTkLabel(self._relay_frame, text=" GATE (RL33–RL34) ",
                           font=ctk.CTkFont("Consolas", 8, "bold"),
                           text_color=_COL_G_TXT_ON, fg_color="transparent")
        lbl.grid(row=r, column=0, columnspan=cols, sticky="w", padx=4, pady=(6, 0))
        r += 1

        for i, rid in enumerate((RL_GATE_A, RL_GATE_B)):
            self._relay_labels[rid] = self._make_relay_label(rid, "G")
            self._relay_labels[rid].grid(row=r, column=i, padx=2, pady=(2, 4))

    def _make_relay_label(self, relay_id: int, group: str) -> ctk.CTkLabel:
        if group == "A":
            fg, tc = _COL_A_IDLE, _COL_A_TXT
        elif group == "B":
            fg, tc = _COL_B_IDLE, _COL_B_TXT
        else:
            fg, tc = _COL_G_IDLE, _COL_G_TXT
        return ctk.CTkLabel(
            self._relay_frame,
            text=f"RL{relay_id:02d}",
            font=ctk.CTkFont("Consolas", 9),
            text_color=tc, fg_color=fg,
            corner_radius=3, width=44, height=22,
        )

    # ── subscriptions ─────────────────────────────────────────────────────

    def _subscribe(self) -> None:
        self._state.subscribe("app_state",    self._on_app_state)
        self._state.subscribe("test_step",    self._on_test_step)
        self._state.subscribe("relay_states", self._on_relay_states)
        self._state.subscribe("test_mode",    self._on_test_mode)

    def _on_app_state(self, state: AppState) -> None:
        self.after(0, lambda: self._apply_app_state(state))

    def _apply_app_state(self, state: AppState) -> None:
        banners = {
            AppState.IDLE:      ("IDLE",       "#606080"),
            AppState.READY:     ("READY",      "#4a8a4a"),
            AppState.TESTING:   ("TESTING...", "#00c8ff"),
            AppState.PAUSED:    ("PAUSED",     "#ffcc00"),
            AppState.PASS:      ("PASS ✓",     "#00e676"),
            AppState.FAIL:      ("FAIL ✗",     "#ff1744"),
            AppState.ERROR:     ("ERROR",      "#ff6d00"),
            AppState.EMERGENCY: ("E-STOP",     "#ff0000"),
        }
        text, color = banners.get(state, ("?", "#606080"))
        self._status_banner.configure(text=text, text_color=color)

    def _on_test_step(self, data: dict) -> None:
        self.after(0, lambda: self._apply_test_step(data))

    def _apply_test_step(self, data: dict) -> None:
        idx   = data.get("step_index", 0) + 1
        total = data.get("total", 0)
        self._progress_label.configure(text=f"Step: {idx} / {total}")
        self._progress_bar.set(idx / total if total else 0)

    def _on_relay_states(self, states: dict) -> None:
        self.after(0, lambda: self._apply_relay_states(states))

    def _apply_relay_states(self, states: dict) -> None:
        from hardware.hardware_interface import RL_A_MIN, RL_A_MAX, RL_B_MIN, RL_B_MAX, RL_GATE_A, RL_GATE_B
        for rid, lbl in self._relay_labels.items():
            active = states.get(rid, False)
            if active:
                if RL_A_MIN <= rid <= RL_A_MAX:
                    lbl.configure(text_color=_COL_A_TXT_ON, fg_color=_COL_A_ACTIVE)
                elif RL_B_MIN <= rid <= RL_B_MAX:
                    lbl.configure(text_color=_COL_B_TXT_ON, fg_color=_COL_B_ACTIVE)
                else:
                    lbl.configure(text_color=_COL_G_TXT_ON, fg_color=_COL_G_ACTIVE)
            else:
                if RL_A_MIN <= rid <= RL_A_MAX:
                    lbl.configure(text_color=_COL_A_TXT, fg_color=_COL_A_IDLE)
                elif RL_B_MIN <= rid <= RL_B_MAX:
                    lbl.configure(text_color=_COL_B_TXT, fg_color=_COL_B_IDLE)
                else:
                    lbl.configure(text_color=_COL_G_TXT, fg_color=_COL_G_IDLE)

    def _on_test_mode(self, mode: TestMode) -> None:
        self.after(0, lambda: self._apply_mode(mode))

    def _apply_mode(self, mode: TestMode) -> None:
        if mode == TestMode.AUTO:
            self._btn_auto.configure(fg_color="#1a5090")
            self._btn_manual.configure(fg_color="#1a1a3a")
        else:
            self._btn_auto.configure(fg_color="#1a1a3a")
            self._btn_manual.configure(fg_color="#1a5090")

    def _set_mode(self, mode: TestMode) -> None:
        self._state.test_mode = mode
