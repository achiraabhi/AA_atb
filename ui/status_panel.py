"""
StatusPanel — live measurement display and test history table.
Shows current winding pair, measured/expected voltage, tolerance, and PASS/FAIL.
"""
import tkinter as tk
import customtkinter as ctk
from typing import Optional, List

from core.state_manager import StateManager, AppState, TestStepResult


class MeasurementCard(ctk.CTkFrame):
    """Single measurement readout card."""

    def __init__(self, parent, title: str, unit: str = "V", **kwargs):
        super().__init__(parent, **kwargs)
        self.configure(fg_color="#0d0d20", corner_radius=6)
        font_sm   = ctk.CTkFont("Consolas", 9)
        font_big  = ctk.CTkFont("Consolas", 22, "bold")
        ctk.CTkLabel(self, text=title, font=font_sm, text_color="#5060a0").pack(pady=(6, 0))
        self._val_lbl = ctk.CTkLabel(self, text="---", font=font_big, text_color="#7080c0")
        self._val_lbl.pack()
        ctk.CTkLabel(self, text=unit, font=font_sm, text_color="#404060").pack(pady=(0, 6))

    def set_value(self, val: Optional[float], color: str = "#c0d0ff") -> None:
        if val is None:
            self._val_lbl.configure(text="---", text_color="#7080c0")
        else:
            self._val_lbl.configure(text=f"{val:.2f}", text_color=color)


class StatusPanel(ctk.CTkFrame):
    """
    Right-side status panel.
    Sections: live measurement, last result, step history table.
    """

    def __init__(self, parent, state_manager: StateManager, **kwargs):
        super().__init__(parent, **kwargs)
        self._state = state_manager
        self.MONO = ctk.CTkFont("Consolas", 11)
        self.H2   = ctk.CTkFont("Consolas", 10, "bold")
        self.configure(fg_color="#12122a", corner_radius=8)
        self._results: List[TestStepResult] = []
        self._build_ui()
        self._subscribe()

    # ------------------------------------------------------------------ #
    #  UI construction                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        row = 0

        # Active test section
        row = self._section_hdr("LIVE MEASUREMENT", row)
        live_frame = ctk.CTkFrame(self, fg_color="#0d0d20", corner_radius=6)
        live_frame.grid(row=row, column=0, padx=8, pady=4, sticky="ew")
        live_frame.grid_columnconfigure((0,1,2), weight=1)
        row += 1

        self._card_measured = MeasurementCard(live_frame, "MEASURED")
        self._card_measured.grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        self._card_expected = MeasurementCard(live_frame, "EXPECTED")
        self._card_expected.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        self._card_tolerance = MeasurementCard(live_frame, "TOLERANCE", unit="%")
        self._card_tolerance.grid(row=0, column=2, padx=4, pady=4, sticky="ew")

        # Winding pair label
        self._pair_label = ctk.CTkLabel(
            self, text="— → —",
            font=ctk.CTkFont("Consolas", 14, "bold"),
            text_color="#607abb"
        )
        self._pair_label.grid(row=row, column=0, pady=4)
        row += 1

        # Result badge
        self._result_badge = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont("Consolas", 20, "bold"),
            text_color="#404060", height=36,
        )
        self._result_badge.grid(row=row, column=0, pady=4)
        row += 1

        # Deviation bar
        row = self._section_hdr("DEVIATION", row)
        self._dev_bar = ctk.CTkProgressBar(self, height=10, corner_radius=3,
                                            progress_color="#00c8ff")
        self._dev_bar.grid(row=row, column=0, padx=8, pady=4, sticky="ew")
        self._dev_bar.set(0)
        row += 1
        self._dev_label = ctk.CTkLabel(self, text="0.00 %", font=self.MONO,
                                        text_color="#8090b0")
        self._dev_label.grid(row=row, column=0)
        row += 1

        # Step history table
        row = self._section_hdr("TEST HISTORY", row)
        tbl_frame = ctk.CTkFrame(self, fg_color="#0d0d20", corner_radius=6)
        tbl_frame.grid(row=row, column=0, padx=8, pady=4, sticky="nsew")
        self.grid_rowconfigure(row, weight=1)
        row += 1

        # Header row
        headers = ["Step", "Pair", "Meas.", "Exp.", "Result"]
        widths   = [5,     10,    8,       8,      7]
        for ci, (h, w) in enumerate(zip(headers, widths)):
            ctk.CTkLabel(tbl_frame, text=h, font=ctk.CTkFont("Consolas", 9, "bold"),
                         text_color="#5060a0", width=w*8).grid(
                row=0, column=ci, padx=2, pady=2
            )

        # Scrollable history list
        self._history_scroll = ctk.CTkScrollableFrame(tbl_frame, fg_color="#0d0d20",
                                                        corner_radius=0, height=140)
        self._history_scroll.grid(row=1, column=0, columnspan=len(headers),
                                   sticky="nsew", padx=2, pady=2)
        tbl_frame.grid_columnconfigure(0, weight=1)
        tbl_frame.grid_rowconfigure(1, weight=1)

    def _section_hdr(self, title: str, row: int) -> int:
        f = ctk.CTkFrame(self, fg_color="#1e1e3a", corner_radius=0, height=22)
        f.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(f, text=f"  {title}",
                     font=ctk.CTkFont("Consolas", 9, "bold"),
                     text_color="#607abb").pack(side="left", padx=4)
        return row + 1

    # ------------------------------------------------------------------ #
    #  State subscriptions                                               #
    # ------------------------------------------------------------------ #

    def _subscribe(self) -> None:
        self._state.subscribe("test_step",    self._on_test_step)
        self._state.subscribe("measurement",  self._on_measurement)
        self._state.subscribe("step_result",  self._on_step_result)
        self._state.subscribe("session_started", self._on_session_started)
        self._state.subscribe("reset",        self._on_reset)

    def _on_test_step(self, data: dict) -> None:
        self.after(0, lambda: self._apply_test_step(data))

    def _apply_test_step(self, data: dict) -> None:
        from_w = data.get("from_w", "?")
        to_w   = data.get("to_w", "?")
        exp    = data.get("expected", 0.0)
        tol    = data.get("tolerance", 5.0)
        self._pair_label.configure(text=f"{from_w} → {to_w}")
        self._card_expected.set_value(exp, "#c0d0ff")
        self._card_tolerance.set_value(tol, "#c0d0ff")
        self._card_measured.set_value(None)
        self._result_badge.configure(text="", text_color="#404060")
        self._dev_bar.set(0)
        self._dev_label.configure(text="0.00 %")

    def _on_measurement(self, voltage: float) -> None:
        self.after(0, lambda: self._card_measured.set_value(voltage, "#00c8ff"))

    def _on_step_result(self, result: TestStepResult) -> None:
        self.after(0, lambda: self._apply_result(result))

    def _apply_result(self, result: TestStepResult) -> None:
        self._results.append(result)
        # Update badge
        if result.passed:
            self._result_badge.configure(text="✓  PASS", text_color="#00e676")
            self._card_measured.set_value(result.measured_voltage, "#00e676")
        else:
            self._result_badge.configure(text="✗  FAIL", text_color="#ff1744")
            self._card_measured.set_value(result.measured_voltage, "#ff1744")

        # Deviation bar (cap at 20%)
        dev = min(result.deviation_pct / 20.0, 1.0)
        bar_color = "#00e676" if result.passed else "#ff1744"
        self._dev_bar.configure(progress_color=bar_color)
        self._dev_bar.set(dev)
        self._dev_label.configure(text=f"{result.deviation_pct:.2f} %")

        # Add history row
        self._add_history_row(result)

    def _add_history_row(self, result: TestStepResult) -> None:
        color = "#00e676" if result.passed else "#ff1744"
        row_f = ctk.CTkFrame(self._history_scroll, fg_color="transparent")
        row_f.pack(fill="x", pady=1)
        cells = [
            str(result.step_index + 1),
            f"{result.from_winding}→{result.to_winding}",
            f"{result.measured_voltage:.2f}",
            f"{result.expected_voltage:.1f}",
            "PASS" if result.passed else "FAIL",
        ]
        cell_colors = ["#8090b0", "#8090b0", "#8090b0", "#8090b0", color]
        for ci, (text, tc) in enumerate(zip(cells, cell_colors)):
            ctk.CTkLabel(row_f, text=text,
                         font=ctk.CTkFont("Consolas", 9),
                         text_color=tc, width=60).grid(row=0, column=ci, padx=3)

    def _on_session_started(self, _) -> None:
        self.after(0, self._clear_history)

    def _clear_history(self) -> None:
        self._results.clear()
        for w in self._history_scroll.winfo_children():
            w.destroy()
        self._pair_label.configure(text="— → —")
        self._card_measured.set_value(None)
        self._card_expected.set_value(None)
        self._card_tolerance.set_value(None)
        self._result_badge.configure(text="", text_color="#404060")
        self._dev_bar.set(0)
        self._dev_label.configure(text="0.00 %")

    def _on_reset(self, _) -> None:
        self.after(0, self._clear_history)
