"""
Transformer Template Editor Window.

Layout:
  ┌─────────────────────────────────────────────────────────────────┐
  │  HEADER                                                         │
  ├─────────────────────────────┬───────────────────────────────────┤
  │  LEFT: scrollable form      │  RIGHT: live canvas preview       │
  │  ├─ Basic Info              │                                   │
  │  ├─ Primary Windings        │   (re-renders as you type)        │
  │  ├─ Secondary Windings      │                                   │
  │  └─ Test Steps              │                                   │
  ├─────────────────────────────┴───────────────────────────────────┤
  │  BOTTOM: Validate | Save | Load | Clear | Close                 │
  └─────────────────────────────────────────────────────────────────┘

No JSON editing required.  Everything through the form.
"""
from __future__ import annotations

import json
import re
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, Dict, List, Optional, Any

import customtkinter as ctk

from core.config_loader import ConfigLoader
from core.transformer_renderer import TransformerRenderer, C_PREVIEW_BG
from core.validator import TransformerValidator, Severity


# ──────────────────────────────────────────────────────────────────
#  Internal constants / helpers
# ──────────────────────────────────────────────────────────────────
_DARK = "#0a0a18"
_CARD = "#111128"
_HDR  = "#1a1a38"
_BLUE = "#2a3a7a"
_BTN  = "#1a2a5a"
_RED  = "#5a1010"
_GRN  = "#1a4a1a"
_MONO = ("Consolas", 10)
_MONO_BOLD = ("Consolas", 10, "bold")
_SM   = ("Consolas", 9)
_SM_B = ("Consolas", 9, "bold")

TRANSFORMER_TYPES = [
    "isolating_transformer",
    "step_down_transformer",
    "step_up_transformer",
    "multi_tap_transformer",
    "center_tap_transformer",
    "auto_transformer",
    "toroidal_transformer",
    "unknown",
]


def _entry(parent, var: tk.StringVar, width: int = 120) -> ctk.CTkEntry:
    return ctk.CTkEntry(parent, textvariable=var, width=width,
                         font=_MONO, fg_color=_DARK, border_color=_BLUE)


def _label(parent, text: str, bold: bool = False) -> ctk.CTkLabel:
    f = ctk.CTkFont("Consolas", 9, "bold" if bold else "normal")
    return ctk.CTkLabel(parent, text=text, font=f, text_color="#8090b0")


def _section_banner(parent, text: str) -> ctk.CTkFrame:
    f = ctk.CTkFrame(parent, fg_color=_HDR, corner_radius=0, height=22)
    ctk.CTkLabel(f, text=f"  {text}", font=ctk.CTkFont("Consolas", 9, "bold"),
                 text_color="#607abb").pack(side="left", padx=6, pady=2)
    return f


# ══════════════════════════════════════════════════════════════════
#  Tap Row  (single row inside a winding card)
# ══════════════════════════════════════════════════════════════════
class _TapRow(ctk.CTkFrame):
    """Pin | Voltage | Label | Relay Ch | Meas Ch | [×]"""

    def __init__(self, parent, on_change: Callable, on_remove: Callable,
                 tap_data: Optional[dict] = None):
        super().__init__(parent, fg_color="#080818", corner_radius=3)
        self._on_change = on_change
        self._on_remove = on_remove
        tap = tap_data or {}

        self.v_pin   = tk.StringVar(value=str(tap.get("pin",           "")))
        self.v_volt  = tk.StringVar(value=str(tap.get("voltage",       "")))
        self.v_lbl   = tk.StringVar(value=str(tap.get("label",         "")))
        self.v_relay = tk.StringVar(value=str(tap.get("relay_channel", "")))
        self.v_meas  = tk.StringVar(value=str(tap.get("meas_channel",  "")))

        self._build()
        for v in (self.v_pin, self.v_volt, self.v_lbl, self.v_relay, self.v_meas):
            v.trace_add("write", lambda *_: on_change())

    def _build(self) -> None:
        f = self
        cols = [("Pin", 46), ("Volt", 58), ("Label", 76), ("RlyCh", 48), ("MCh", 40)]
        for ci, (text, _w) in enumerate(cols):
            ctk.CTkLabel(f, text=text, font=ctk.CTkFont("Consolas", 8),
                          text_color="#506080").grid(row=0, column=ci, padx=1)

        for ci, (var, w) in enumerate([(self.v_pin, 46), (self.v_volt, 58),
                                        (self.v_lbl, 76), (self.v_relay, 48),
                                        (self.v_meas, 40)]):
            ctk.CTkEntry(f, textvariable=var, width=w,
                          font=ctk.CTkFont(*_MONO),
                          fg_color=_DARK, border_color=_BLUE
                          ).grid(row=1, column=ci, padx=1, pady=2)
        ctk.CTkButton(f, text="×", width=24, height=24,
                       font=ctk.CTkFont("Consolas", 11, "bold"),
                       fg_color=_RED, hover_color="#7a1010",
                       command=lambda: self._on_remove(self)
                       ).grid(row=1, column=5, padx=2, pady=2)

    def get_data(self) -> dict:
        try:   pin = int(self.v_pin.get())
        except: pin = 0
        try:   volt = float(self.v_volt.get())
        except: volt = 0.0
        lbl = self.v_lbl.get().strip() or (f"{volt:g}V" if volt else "")
        d = {"pin": pin, "voltage": volt, "label": lbl}
        try:
            d["relay_channel"] = int(self.v_relay.get())
            d["meas_channel"]  = int(self.v_meas.get())
        except (ValueError, TypeError):
            pass   # omit if blank — matrix engine skips points without relay_channel
        return d


# ══════════════════════════════════════════════════════════════════
#  Winding Card
# ══════════════════════════════════════════════════════════════════
class _WindingCard(ctk.CTkFrame):
    """
    Expandable card for one winding (primary or secondary).
    Contains all fields + dynamic tap list.
    """

    def __init__(self, parent, side: str, index: int,
                 on_change: Callable, on_remove: Callable,
                 winding_data: Optional[dict] = None):
        super().__init__(parent, fg_color=_CARD, corner_radius=6)
        self._side      = side
        self._on_change = on_change
        self._on_remove = on_remove
        self._tap_rows: List[_TapRow] = []

        prefix = "P" if side == "primary" else "S"
        w = winding_data or {}

        self.v_id        = tk.StringVar(value=w.get("id",        f"{prefix}{index}"))
        self.v_start_pin = tk.StringVar(value=str(w.get("start_pin", index * 2 - 1)))
        self.v_end_pin   = tk.StringVar(value=str(w.get("end_pin",   index * 2)))
        self.v_voltage   = tk.StringVar(value=str(w.get("voltage",   230 if side == "primary" else 115)))
        self.v_relay_id  = tk.StringVar(value=str(w.get("relay_id",  index - 1)))
        end_relay_val    = w.get("end_relay", "")
        self.v_end_relay = tk.StringVar(value="" if end_relay_val is None else str(end_relay_val))
        meas_ch_val      = w.get("meas_channel", -1)
        self.v_meas_ch   = tk.StringVar(value=str(meas_ch_val))
        self.v_dot       = tk.BooleanVar(value=w.get("dot_polarity", True))
        self.v_type      = tk.StringVar(value=w.get("winding_type", "basic_winding"))

        self._build(w.get("taps", []))

        for v in (self.v_id, self.v_start_pin, self.v_end_pin,
                  self.v_voltage, self.v_relay_id, self.v_end_relay,
                  self.v_meas_ch, self.v_type):
            v.trace_add("write", lambda *_: on_change())
        self.v_dot.trace_add("write", lambda *_: on_change())

    def _build(self, existing_taps: list) -> None:
        self.grid_columnconfigure(0, weight=1)

        # ── Header bar ──────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color="#1e1e40", corner_radius=4, height=28)
        hdr.grid(row=0, column=0, sticky="ew", padx=2, pady=(4, 0))
        hdr.grid_columnconfigure(0, weight=1)

        self._title_lbl = ctk.CTkLabel(hdr, textvariable=self.v_id,
                                        font=ctk.CTkFont("Consolas", 11, "bold"),
                                        text_color="#aabbff")
        self._title_lbl.grid(row=0, column=0, sticky="w", padx=8, pady=3)

        ctk.CTkButton(hdr, text="×", width=26, height=22,
                       font=ctk.CTkFont("Consolas", 11, "bold"),
                       fg_color=_RED, hover_color="#7a1010",
                       command=lambda: self._on_remove(self)
                       ).grid(row=0, column=1, padx=4)

        # ── Fields grid ─────────────────────────────────────────
        fg = ctk.CTkFrame(self, fg_color="transparent")
        fg.grid(row=1, column=0, sticky="ew", padx=6, pady=4)
        fg.grid_columnconfigure((1, 3), weight=1)

        def row(r, ltext, var, w=100):
            _label(fg, ltext).grid(row=r, column=0, sticky="e", padx=(4, 2), pady=2)
            _entry(fg, var, w).grid(row=r, column=1, sticky="ew", padx=(0, 8), pady=2)

        row(0, "ID:",           self.v_id,        70)
        row(1, "Start Pin:",    self.v_start_pin, 70)
        row(2, "End Pin:",      self.v_end_pin,   70)
        row(3, "Voltage (V):",  self.v_voltage,   80)
        row(4, "Relay ID:",     self.v_relay_id,  60)
        row(5, "End Relay:",    self.v_end_relay, 60)
        row(6, "Meas Channel:", self.v_meas_ch,   60)

        _label(fg, "Dot Polarity:").grid(row=7, column=0, sticky="e", padx=(4, 2), pady=2)
        ctk.CTkCheckBox(fg, text="", variable=self.v_dot, width=28,
                         checkbox_width=18, checkbox_height=18,
                         fg_color=_BLUE, border_color="#4a5a9a"
                         ).grid(row=7, column=1, sticky="w")

        # ── Tap section ─────────────────────────────────────────
        tap_hdr = ctk.CTkFrame(self, fg_color="#16163a", corner_radius=0, height=22)
        tap_hdr.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        tap_hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tap_hdr, text="  TAPS", font=ctk.CTkFont("Consolas", 8, "bold"),
                      text_color="#506090").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(tap_hdr, text="+ Add Tap", width=80, height=20,
                       font=ctk.CTkFont("Consolas", 9),
                       fg_color="#0a2a4a", hover_color="#1a3a6a",
                       command=self._add_tap
                       ).grid(row=0, column=1, padx=6, pady=1)

        self._tap_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._tap_frame.grid(row=3, column=0, sticky="ew", padx=4, pady=2)

        for td in existing_taps:
            self._add_tap(td)

    def _add_tap(self, tap_data: Optional[dict] = None) -> None:
        row = _TapRow(self._tap_frame, self._on_change,
                      self._remove_tap, tap_data)
        row.pack(fill="x", pady=1)
        self._tap_rows.append(row)
        self._on_change()

    def _remove_tap(self, row: _TapRow) -> None:
        row.pack_forget()
        row.destroy()
        if row in self._tap_rows:
            self._tap_rows.remove(row)
        self._on_change()

    def get_data(self) -> dict:
        try:   sp = int(self.v_start_pin.get())
        except: sp = 0
        try:   ep = int(self.v_end_pin.get())
        except: ep = 0
        try:   volt = float(self.v_voltage.get())
        except: volt = 0.0
        try:   relay = int(self.v_relay_id.get())
        except: relay = 0
        try:   end_relay = int(self.v_end_relay.get())
        except: end_relay = None
        try:   meas_ch = int(self.v_meas_ch.get())
        except: meas_ch = -1

        return {
            "id":           self.v_id.get().strip(),
            "winding_type": self.v_type.get(),
            "start_pin":    sp,
            "end_pin":      ep,
            "voltage":      volt,
            "dot_polarity": self.v_dot.get(),
            "relay_id":     relay,
            "end_relay":    end_relay,
            "meas_channel": meas_ch,
            "taps":         [t.get_data() for t in self._tap_rows],
            "coords":       {},
        }


# ══════════════════════════════════════════════════════════════════
#  Test Step Card
# ══════════════════════════════════════════════════════════════════
class _TestStepCard(ctk.CTkFrame):
    """One test step: From → To, optional tap points, expected voltage, tolerance, etc."""

    _NO_TAP = "— (full winding)"

    def __init__(self, parent, index: int, winding_ids: List[str],
                 winding_taps: Dict[str, List[dict]],
                 on_change: Callable, on_remove: Callable,
                 test_data: Optional[dict] = None):
        super().__init__(parent, fg_color=_CARD, corner_radius=6)
        self._on_change    = on_change
        self._on_remove    = on_remove
        self._winding_ids  = winding_ids
        self._winding_taps = winding_taps
        t = test_data or {}

        self.v_from     = tk.StringVar(value=t.get("from",     ""))
        self.v_to       = tk.StringVar(value=t.get("to",       ""))
        self.v_from_tap = tk.StringVar(value=self._NO_TAP)
        self.v_to_tap   = tk.StringVar(value=self._NO_TAP)
        self.v_exp      = tk.StringVar(value=str(t.get("expected_voltage", "")))
        self.v_tol      = tk.StringVar(value=str(t.get("tolerance_percent", 5.0)))
        self.v_ch       = tk.StringVar(value=str(t.get("measurement_channel", 0)))
        self.v_delay    = tk.StringVar(value=str(t.get("stabilization_delay_ms", 500)))
        self.v_desc     = tk.StringVar(value=t.get("description", ""))

        self._build(index, t.get("from_tap_index"), t.get("to_tap_index"))

        for v in (self.v_from, self.v_to, self.v_exp, self.v_tol,
                  self.v_ch, self.v_delay, self.v_desc):
            v.trace_add("write", lambda *_: on_change())

    def _build(self, index: int, from_tap_idx=None, to_tap_idx=None) -> None:
        self.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(self, fg_color="#1e1e40", corner_radius=4, height=26)
        hdr.grid(row=0, column=0, sticky="ew", padx=2, pady=(4, 0))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text=f"  Test {index}",
                      font=ctk.CTkFont("Consolas", 10, "bold"),
                      text_color="#aabbff").grid(row=0, column=0, sticky="w", padx=4)
        ctk.CTkButton(hdr, text="×", width=26, height=20,
                       font=ctk.CTkFont("Consolas", 11, "bold"),
                       fg_color=_RED, hover_color="#7a1010",
                       command=lambda: self._on_remove(self)
                       ).grid(row=0, column=1, padx=4)

        fg = ctk.CTkFrame(self, fg_color="transparent")
        fg.grid(row=1, column=0, sticky="ew", padx=6, pady=4)

        # ── From / To winding row ────────────────────────────────
        row0 = ctk.CTkFrame(fg, fg_color="transparent")
        row0.pack(fill="x", pady=2)
        _label(row0, "From:").pack(side="left", padx=(0, 4))
        self._cb_from = ctk.CTkComboBox(row0, variable=self.v_from,
                                         values=self._winding_ids, width=80,
                                         font=ctk.CTkFont(*_MONO),
                                         fg_color=_DARK, button_color=_BLUE,
                                         dropdown_fg_color=_HDR,
                                         command=self._on_from_winding_changed)
        self._cb_from.pack(side="left", padx=2)
        _label(row0, "→").pack(side="left", padx=6)
        self._cb_to = ctk.CTkComboBox(row0, variable=self.v_to,
                                       values=self._winding_ids, width=80,
                                       font=ctk.CTkFont(*_MONO),
                                       fg_color=_DARK, button_color=_BLUE,
                                       dropdown_fg_color=_HDR,
                                       command=self._on_to_winding_changed)
        self._cb_to.pack(side="left", padx=2)

        # ── Tap selection row ────────────────────────────────────
        tap_row = ctk.CTkFrame(fg, fg_color="transparent")
        tap_row.pack(fill="x", pady=1)
        _label(tap_row, "From Tap:").pack(side="left", padx=(0, 4))
        self._cb_from_tap = ctk.CTkComboBox(
            tap_row, variable=self.v_from_tap, values=[self._NO_TAP], width=150,
            font=ctk.CTkFont(*_SM), fg_color=_DARK, button_color=_BLUE,
            dropdown_fg_color=_HDR,
            command=lambda v: self._on_change())
        self._cb_from_tap.pack(side="left", padx=2)
        _label(tap_row, "To Tap:").pack(side="left", padx=(8, 4))
        self._cb_to_tap = ctk.CTkComboBox(
            tap_row, variable=self.v_to_tap, values=[self._NO_TAP], width=150,
            font=ctk.CTkFont(*_SM), fg_color=_DARK, button_color=_BLUE,
            dropdown_fg_color=_HDR,
            command=self._on_to_tap_changed)
        self._cb_to_tap.pack(side="left", padx=2)

        # ── Measurement fields ───────────────────────────────────
        def inline_row(label, var, width=70, suffix=""):
            r = ctk.CTkFrame(fg, fg_color="transparent")
            r.pack(fill="x", pady=1)
            _label(r, label).pack(side="left", padx=(0, 4))
            _entry(r, var, width).pack(side="left")
            if suffix:
                _label(r, suffix).pack(side="left", padx=2)

        inline_row("Expected V:",   self.v_exp,   80, "V")
        inline_row("Tolerance:",    self.v_tol,   60, "%")
        inline_row("Channel:",      self.v_ch,    50)
        inline_row("Delay:",        self.v_delay, 60, "ms")
        inline_row("Description:",  self.v_desc,  180)

        # Populate tap dropdowns and pre-select from saved data
        self._refresh_tap_opts(self.v_from.get(), self._cb_from_tap, self.v_from_tap)
        self._refresh_tap_opts(self.v_to.get(),   self._cb_to_tap,   self.v_to_tap)
        if from_tap_idx is not None:
            opts = self._tap_options(self.v_from.get())
            if 0 <= from_tap_idx + 1 < len(opts):
                self.v_from_tap.set(opts[from_tap_idx + 1])
        if to_tap_idx is not None:
            opts = self._tap_options(self.v_to.get())
            if 0 <= to_tap_idx + 1 < len(opts):
                self.v_to_tap.set(opts[to_tap_idx + 1])

    # ── Tap option helpers ───────────────────────────────────────────

    def _tap_options(self, winding_id: str) -> List[str]:
        opts = [self._NO_TAP]
        for i, tap in enumerate(self._winding_taps.get(winding_id, [])):
            lbl  = tap.get("label", "")
            volt = tap.get("voltage", "")
            opts.append(f"Tap {i}: {lbl}  {volt}V" if lbl else f"Tap {i}: {volt}V")
        return opts

    @staticmethod
    def _parse_tap_index(val: str) -> Optional[int]:
        m = re.match(r"Tap (\d+)", val)
        return int(m.group(1)) if m else None

    def _refresh_tap_opts(self, winding_id: str, combo, var: tk.StringVar) -> None:
        opts = self._tap_options(winding_id)
        combo.configure(values=opts)
        if var.get() not in opts:
            var.set(self._NO_TAP)

    def _on_from_winding_changed(self, value: str) -> None:
        self._refresh_tap_opts(value, self._cb_from_tap, self.v_from_tap)
        self._on_change()

    def _on_to_winding_changed(self, value: str) -> None:
        self._refresh_tap_opts(value, self._cb_to_tap, self.v_to_tap)
        self._on_change()

    def _on_to_tap_changed(self, value: str) -> None:
        """Auto-fill expected voltage from the selected tap definition."""
        idx = self._parse_tap_index(value)
        if idx is not None:
            taps = self._winding_taps.get(self.v_to.get(), [])
            if idx < len(taps):
                volt = taps[idx].get("voltage")
                if volt is not None:
                    self.v_exp.set(str(volt))
        self._on_change()

    # ── Public update methods ────────────────────────────────────────

    def update_winding_ids(self, ids: List[str]) -> None:
        self._winding_ids = ids
        self._cb_from.configure(values=ids)
        self._cb_to.configure(values=ids)

    def update_winding_taps(self, taps_dict: Dict[str, List[dict]]) -> None:
        self._winding_taps = taps_dict
        self._refresh_tap_opts(self.v_from.get(), self._cb_from_tap, self.v_from_tap)
        self._refresh_tap_opts(self.v_to.get(),   self._cb_to_tap,   self.v_to_tap)

    def get_data(self) -> dict:
        try:   exp = float(self.v_exp.get())
        except: exp = 0.0
        try:   tol = float(self.v_tol.get())
        except: tol = 5.0
        try:   ch = int(self.v_ch.get())
        except: ch = 0
        try:   delay = int(self.v_delay.get())
        except: delay = 500
        d = {
            "from":                   self.v_from.get().strip(),
            "to":                     self.v_to.get().strip(),
            "expected_voltage":       exp,
            "tolerance_percent":      tol,
            "measurement_channel":    ch,
            "stabilization_delay_ms": delay,
            "relay_map":              {},
            "description":            self.v_desc.get().strip(),
        }
        fi = self._parse_tap_index(self.v_from_tap.get())
        ti = self._parse_tap_index(self.v_to_tap.get())
        if fi is not None:
            d["from_tap_index"] = fi
        if ti is not None:
            d["to_tap_index"] = ti
        return d


# ══════════════════════════════════════════════════════════════════
#  Main Editor Window
# ══════════════════════════════════════════════════════════════════
class TransformerEditorWindow(ctk.CTkToplevel):
    """
    Full graphical transformer template editor.
    Live diagram preview updates as you type.
    """

    def __init__(self, parent,
                 config_loader: ConfigLoader,
                 on_saved: Optional[Callable] = None,
                 initial_config: Optional[dict] = None):
        super().__init__(parent)
        self._loader    = config_loader
        self._on_saved  = on_saved
        self._validator = TransformerValidator()
        self._preview_after_id: Optional[str] = None

        self._winding_cards: Dict[str, List[_WindingCard]] = {
            "primary": [], "secondary": []
        }
        self._test_cards: List[_TestStepCard] = []

        self.title("Transformer Template Editor")
        self.geometry("1120x760")
        self.minsize(900, 620)
        self.configure(fg_color="#0d0d1a")
        self.grab_set()

        self._build_ui()

        data = initial_config or self._blank_template()
        self._populate(data)

    # ================================================================ #
    #  UI construction                                                  #
    # ================================================================ #

    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=0)   # form — fixed width
        self.grid_columnconfigure(1, weight=1)   # preview — expands

        # ── Header ──────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self, fg_color=_HDR, corner_radius=0, height=38)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        ctk.CTkLabel(hdr, text="  ⚡ TRANSFORMER TEMPLATE EDITOR",
                      font=ctk.CTkFont("Consolas", 12, "bold"),
                      text_color="#607abb").pack(side="left", padx=8, pady=8)
        ctk.CTkLabel(hdr, text="Live preview updates as you edit",
                      font=ctk.CTkFont("Consolas", 9),
                      text_color="#404060").pack(side="right", padx=12)

        # ── Left form panel ─────────────────────────────────────────
        form_outer = ctk.CTkFrame(self, fg_color="#0f0f22", corner_radius=0, width=460)
        form_outer.grid(row=1, column=0, sticky="nsew")
        form_outer.grid_propagate(False)
        form_outer.grid_rowconfigure(0, weight=1)
        form_outer.grid_columnconfigure(0, weight=1)

        self._form_scroll = ctk.CTkScrollableFrame(form_outer, fg_color="#0f0f22",
                                                    corner_radius=0)
        self._form_scroll.grid(row=0, column=0, sticky="nsew")
        self._form_scroll.grid_columnconfigure(0, weight=1)
        self._build_form(self._form_scroll)

        # ── Right preview panel ─────────────────────────────────────
        preview_frame = ctk.CTkFrame(self, fg_color="#0d0d20", corner_radius=0)
        preview_frame.grid(row=1, column=1, sticky="nsew", padx=0, pady=0)
        preview_frame.grid_rowconfigure(1, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)

        phdr = ctk.CTkFrame(preview_frame, fg_color=_HDR, corner_radius=0, height=26)
        phdr.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(phdr, text="  LIVE PREVIEW",
                      font=ctk.CTkFont("Consolas", 9, "bold"),
                      text_color="#607abb").pack(side="left", padx=8, pady=3)

        self._canvas = tk.Canvas(preview_frame, bg=C_PREVIEW_BG,
                                  highlightthickness=0)
        self._canvas.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._canvas.bind("<Configure>", lambda _: self._schedule_preview())
        self._renderer = TransformerRenderer(self._canvas)

        # ── Bottom bar ───────────────────────────────────────────────
        self._build_bottom_bar()

    def _build_form(self, parent: ctk.CTkScrollableFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        row = 0

        # ─── Basic Info ─────────────────────────────────────────────
        _section_banner(parent, "BASIC INFORMATION").grid(
            row=row, column=0, sticky="ew", pady=(4, 0)); row += 1

        info_frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
        info_frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4); row += 1
        info_frame.grid_columnconfigure(1, weight=1)

        self.v_name  = tk.StringVar()
        self.v_id    = tk.StringVar()
        self.v_type  = tk.StringVar(value="isolating_transformer")
        self.v_va    = tk.StringVar(value="100")
        self.v_hz    = tk.StringVar(value="50")
        self.v_notes = tk.StringVar()

        fields = [
            ("Name:",        self.v_name,  200),
            ("ID:",          self.v_id,    160),
            ("Notes:",       self.v_notes, 200),
            ("Rated VA:",    self.v_va,    80),
            ("Frequency Hz:", self.v_hz,   60),
        ]
        for fi, (lbl, var, w) in enumerate(fields):
            _label(info_frame, lbl).grid(row=fi, column=0, sticky="e",
                                          padx=(8, 4), pady=3)
            _entry(info_frame, var, w).grid(row=fi, column=1, sticky="ew",
                                             padx=(0, 8), pady=3)
        # Type dropdown
        _label(info_frame, "Type:").grid(row=len(fields), column=0,
                                          sticky="e", padx=(8, 4), pady=3)
        ctk.CTkComboBox(info_frame, variable=self.v_type,
                         values=TRANSFORMER_TYPES, width=200,
                         font=ctk.CTkFont(*_MONO), fg_color=_DARK,
                         button_color=_BLUE, dropdown_fg_color=_HDR,
                         command=lambda _: self._schedule_preview()
                         ).grid(row=len(fields), column=1, sticky="w",
                                padx=(0, 8), pady=3)

        # Auto-slugify name → ID
        self.v_name.trace_add("write", self._on_name_change)
        for v in (self.v_id, self.v_va, self.v_hz, self.v_notes):
            v.trace_add("write", lambda *_: self._schedule_preview())

        # ─── Primary Windings ──────────────────────────────────────
        row = self._winding_section(parent, "primary", row)

        # ─── Secondary Windings ────────────────────────────────────
        row = self._winding_section(parent, "secondary", row)

        # ─── Auto Matrix ───────────────────────────────────────────
        row = self._build_auto_matrix_section(parent, row)

        # ─── Test Steps ────────────────────────────────────────────
        _section_banner(parent, "TEST STEPS  (manual — ignored when Auto Matrix is enabled)").grid(
            row=row, column=0, sticky="ew", pady=(8, 0)); row += 1

        add_test_btn = ctk.CTkButton(parent, text="+ Add Test Step",
                                      font=ctk.CTkFont("Consolas", 10),
                                      fg_color=_BTN, hover_color="#2a4a8a", height=28,
                                      command=self._add_test_step)
        add_test_btn.grid(row=row, column=0, padx=8, pady=4, sticky="w"); row += 1

        self._test_container = ctk.CTkFrame(parent, fg_color="transparent")
        self._test_container.grid(row=row, column=0, sticky="ew", padx=8, pady=2); row += 1
        self._test_container.grid_columnconfigure(0, weight=1)

        # Validation output area
        _section_banner(parent, "VALIDATION").grid(
            row=row, column=0, sticky="ew", pady=(8, 0)); row += 1
        self._validation_frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
        self._validation_frame.grid(row=row, column=0, sticky="ew",
                                     padx=8, pady=4); row += 1
        self._val_placeholder = ctk.CTkLabel(
            self._validation_frame, text="Press Validate to check configuration",
            font=ctk.CTkFont("Consolas", 9), text_color="#404060")
        self._val_placeholder.pack(pady=8)

    def _build_auto_matrix_section(self, parent, row: int) -> int:
        _section_banner(parent, "AUTO MATRIX  — one energised winding → validate all outputs").grid(
            row=row, column=0, sticky="ew", pady=(8, 0)); row += 1

        am_frame = ctk.CTkFrame(parent, fg_color=_CARD, corner_radius=6)
        am_frame.grid(row=row, column=0, sticky="ew", padx=8, pady=4); row += 1
        am_frame.grid_columnconfigure(1, weight=1)

        # Enable toggle
        self.v_am_enabled = tk.BooleanVar(value=False)
        _label(am_frame, "Enable Auto Matrix:").grid(
            row=0, column=0, sticky="e", padx=(8, 4), pady=4)
        ctk.CTkCheckBox(am_frame, text="(generates full sweep automatically)",
                         variable=self.v_am_enabled,
                         font=ctk.CTkFont("Consolas", 9),
                         checkbox_width=18, checkbox_height=18,
                         fg_color=_BLUE, border_color="#4a5a9a",
                         command=self._schedule_preview
                         ).grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.v_am_enabled.trace_add("write", lambda *_: self._schedule_preview())

        # Energise winding
        _label(am_frame, "Energise Winding:").grid(
            row=1, column=0, sticky="e", padx=(8, 4), pady=4)
        self.v_am_winding = tk.StringVar(value="")
        self._cb_am_winding = ctk.CTkComboBox(
            am_frame, variable=self.v_am_winding, values=[], width=100,
            font=ctk.CTkFont(*_MONO), fg_color=_DARK, button_color=_BLUE,
            dropdown_fg_color=_HDR,
            command=self._on_am_winding_changed)
        self._cb_am_winding.grid(row=1, column=1, sticky="w", padx=(0, 8))

        # Energise tap
        _label(am_frame, "Energise Tap:").grid(
            row=2, column=0, sticky="e", padx=(8, 4), pady=4)
        self.v_am_tap = tk.StringVar(value=_TestStepCard._NO_TAP)
        self._cb_am_tap = ctk.CTkComboBox(
            am_frame, variable=self.v_am_tap,
            values=[_TestStepCard._NO_TAP], width=200,
            font=ctk.CTkFont(*_MONO), fg_color=_DARK, button_color=_BLUE,
            dropdown_fg_color=_HDR,
            command=lambda v: self._schedule_preview())
        self._cb_am_tap.grid(row=2, column=1, sticky="w", padx=(0, 8))
        self.v_am_tap.trace_add("write", lambda *_: self._schedule_preview())

        # Info label
        self._am_info_lbl = ctk.CTkLabel(
            am_frame, text="",
            font=ctk.CTkFont("Consolas", 9), text_color="#6080c0",
            wraplength=340, justify="left")
        self._am_info_lbl.grid(row=3, column=0, columnspan=2,
                                sticky="w", padx=8, pady=(0, 6))

        return row

    def _on_am_winding_changed(self, wid: str) -> None:
        taps = self._get_winding_taps()
        tap_list = taps.get(wid, [])
        opts = [_TestStepCard._NO_TAP] + [
            f"Tap {i}: {t.get('label', '')}  {t.get('voltage', '')}V"
            for i, t in enumerate(tap_list)
        ]
        self._cb_am_tap.configure(values=opts)
        if self.v_am_tap.get() not in opts:
            self.v_am_tap.set(_TestStepCard._NO_TAP)
        self._schedule_preview()

    def _update_am_dropdowns(self) -> None:
        """Refresh energise-winding dropdown; called whenever windings change."""
        ids = self._all_winding_ids()
        if hasattr(self, "_cb_am_winding"):
            self._cb_am_winding.configure(values=ids)
            wid = self.v_am_winding.get()
            if wid:
                self._on_am_winding_changed(wid)

    def _winding_section(self, parent, side: str, row: int) -> int:
        label = "PRIMARY WINDINGS" if side == "primary" else "SECONDARY WINDINGS"
        _section_banner(parent, label).grid(
            row=row, column=0, sticky="ew", pady=(8, 0)); row += 1

        add_btn = ctk.CTkButton(
            parent,
            text=f"+ Add {'Primary' if side == 'primary' else 'Secondary'} Winding",
            font=ctk.CTkFont("Consolas", 10),
            fg_color=_BTN, hover_color="#2a4a8a", height=28,
            command=lambda s=side: self._add_winding_card(s)
        )
        add_btn.grid(row=row, column=0, padx=8, pady=4, sticky="w"); row += 1

        container = ctk.CTkFrame(parent, fg_color="transparent")
        container.grid(row=row, column=0, sticky="ew", padx=8, pady=2); row += 1
        container.grid_columnconfigure(0, weight=1)
        setattr(self, f"_{side}_container", container)

        return row

    def _build_bottom_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=_HDR, corner_radius=0, height=48)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")

        buttons = [
            ("✔ Validate",    self._validate,    "#1a3a6a", "#2a5090"),
            ("💾 Save",        self._save,        "#1a4a1a", "#2a6a2a"),
            ("📂 Load JSON",   self._load_json,   "#2a2a0a", "#4a4a10"),
            ("🗑 Clear",       self._clear,        "#3a1a1a", "#5a2020"),
            ("✕ Close",        self.destroy,       "#2a1a2a", "#4a2a4a"),
        ]
        for text, cmd, fg, hv in buttons:
            ctk.CTkButton(bar, text=text, command=cmd, width=120, height=34,
                           font=ctk.CTkFont("Consolas", 10, "bold"),
                           fg_color=fg, hover_color=hv, corner_radius=4
                           ).pack(side="left", padx=6, pady=7)

    # ================================================================ #
    #  Winding card management                                          #
    # ================================================================ #

    def _add_winding_card(self, side: str,
                           winding_data: Optional[dict] = None) -> _WindingCard:
        container = getattr(self, f"_{side}_container")
        idx = len(self._winding_cards[side]) + 1
        card = _WindingCard(container, side, idx,
                             on_change=self._on_winding_changed,
                             on_remove=lambda c, s=side: self._remove_winding_card(s, c),
                             winding_data=winding_data)
        card.grid(row=idx - 1, column=0, sticky="ew", pady=4)
        self._winding_cards[side].append(card)
        self._update_test_dropdowns()
        self._update_test_tap_options()
        self._schedule_preview()
        return card

    def _remove_winding_card(self, side: str, card: _WindingCard) -> None:
        if card in self._winding_cards[side]:
            self._winding_cards[side].remove(card)
        card.grid_forget()
        card.destroy()
        self._update_test_dropdowns()
        self._update_test_tap_options()
        self._schedule_preview()

    def _on_winding_changed(self) -> None:
        """Called whenever any winding field or tap changes."""
        self._update_test_tap_options()
        self._update_am_dropdowns()
        self._schedule_preview()

    # ================================================================ #
    #  Test step management                                             #
    # ================================================================ #

    def _add_test_step(self, test_data: Optional[dict] = None) -> _TestStepCard:
        idx = len(self._test_cards) + 1
        ids  = self._all_winding_ids()
        taps = self._get_winding_taps()
        card = _TestStepCard(self._test_container, idx, ids, taps,
                              on_change=self._schedule_preview,
                              on_remove=self._remove_test_step,
                              test_data=test_data)
        card.grid(row=idx - 1, column=0, sticky="ew", pady=4)
        self._test_cards.append(card)
        return card

    def _remove_test_step(self, card: _TestStepCard) -> None:
        if card in self._test_cards:
            self._test_cards.remove(card)
        card.grid_forget()
        card.destroy()

    def _update_test_dropdowns(self) -> None:
        ids = self._all_winding_ids()
        for card in self._test_cards:
            card.update_winding_ids(ids)

    def _update_test_tap_options(self) -> None:
        taps = self._get_winding_taps()
        for card in self._test_cards:
            card.update_winding_taps(taps)

    def _all_winding_ids(self) -> List[str]:
        ids = []
        for side in ("primary", "secondary"):
            for card in self._winding_cards[side]:
                wid = card.v_id.get().strip()
                if wid:
                    ids.append(wid)
        return ids

    def _get_winding_taps(self) -> Dict[str, List[dict]]:
        result: Dict[str, List[dict]] = {}
        for side in ("primary", "secondary"):
            for card in self._winding_cards[side]:
                wid = card.v_id.get().strip()
                if wid:
                    result[wid] = [t.get_data() for t in card._tap_rows]
        return result

    # ================================================================ #
    #  Data collection                                                  #
    # ================================================================ #

    def _collect_data(self) -> dict:
        name = self.v_name.get().strip()
        tid  = self.v_id.get().strip() or self._slugify(name)
        try:   va = float(self.v_va.get())
        except: va = 0.0
        try:   hz = float(self.v_hz.get())
        except: hz = 50.0

        # Auto-matrix config
        am_tap_idx = _TestStepCard._parse_tap_index(
            self.v_am_tap.get() if hasattr(self, "v_am_tap") else "")

        return {
            "name":               name,
            "transformer_id":     tid,
            "type":               self.v_type.get(),
            "rated_power_va":     va,
            "rated_frequency_hz": hz,
            "notes":              self.v_notes.get().strip(),
            "primary":   [c.get_data() for c in self._winding_cards["primary"]],
            "secondary": [c.get_data() for c in self._winding_cards["secondary"]],
            "tests":     [c.get_data() for c in self._test_cards],
            "auto_matrix": {
                "enabled":            bool(self.v_am_enabled.get()) if hasattr(self, "v_am_enabled") else False,
                "energize_winding":   self.v_am_winding.get().strip() if hasattr(self, "v_am_winding") else "",
                "energize_tap_index": am_tap_idx,
            },
        }

    # ================================================================ #
    #  Live preview                                                     #
    # ================================================================ #

    def _schedule_preview(self, *_) -> None:
        if self._preview_after_id:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
        self._preview_after_id = self.after(250, self._update_preview)

    def _update_preview(self) -> None:
        data = self._collect_data()
        try:
            cfg = self._loader._parse(data, "preview")
            self._canvas.configure(bg=C_PREVIEW_BG)
            self._renderer.render(cfg)
        except Exception as e:
            self._canvas.delete("all")
            self._canvas.create_text(
                self._canvas.winfo_width() // 2 or 300,
                self._canvas.winfo_height() // 2 or 200,
                text=f"Preview error:\n{e}",
                fill="#ff6060", font=("Consolas", 10),
                justify=tk.CENTER
            )

    # ================================================================ #
    #  Populate from existing config                                    #
    # ================================================================ #

    def _populate(self, data: dict) -> None:
        self.v_name.set(data.get("name",  ""))
        self.v_id.set(data.get("transformer_id", ""))
        self.v_type.set(data.get("type", "isolating_transformer"))
        self.v_va.set(str(data.get("rated_power_va",     100)))
        self.v_hz.set(str(data.get("rated_frequency_hz", 50)))
        self.v_notes.set(data.get("notes", ""))

        for w in data.get("primary", []):
            self._add_winding_card("primary", w)
        for w in data.get("secondary", []):
            self._add_winding_card("secondary", w)
        for t in data.get("tests", []):
            self._add_test_step(t)

        # Auto-matrix
        am = data.get("auto_matrix", {})
        if hasattr(self, "v_am_enabled"):
            self.v_am_enabled.set(bool(am.get("enabled", False)))
            wid = am.get("energize_winding", "")
            self.v_am_winding.set(wid)
            if wid:
                self._on_am_winding_changed(wid)
            tap_idx = am.get("energize_tap_index")
            if tap_idx is not None:
                opts = self._cb_am_tap.cget("values") or []
                if 0 <= tap_idx + 1 < len(opts):
                    self.v_am_tap.set(opts[tap_idx + 1])

        self._schedule_preview()

    def _clear(self) -> None:
        for side in ("primary", "secondary"):
            for card in list(self._winding_cards[side]):
                self._remove_winding_card(side, card)
        for card in list(self._test_cards):
            self._remove_test_step(card)
        for v in (self.v_name, self.v_id, self.v_notes):
            v.set("")
        self.v_va.set("100")
        self.v_hz.set("50")
        self.v_type.set("isolating_transformer")
        if hasattr(self, "v_am_enabled"):
            self.v_am_enabled.set(False)
            self.v_am_winding.set("")
            self.v_am_tap.set(_TestStepCard._NO_TAP)
        self._schedule_preview()

    # ================================================================ #
    #  Validate                                                         #
    # ================================================================ #

    def _validate(self) -> bool:
        data   = self._collect_data()
        issues = self._validator.validate(data)
        self._show_validation(issues)
        return not self._validator.has_errors(issues)

    def _show_validation(self, issues) -> None:
        for w in self._validation_frame.winfo_children():
            w.destroy()

        if not issues:
            ctk.CTkLabel(self._validation_frame,
                          text="✓ Configuration is valid",
                          font=ctk.CTkFont("Consolas", 10, "bold"),
                          text_color="#00e676").pack(pady=8)
            return

        colors = {
            Severity.ERROR:   "#ff4444",
            Severity.WARNING: "#ffaa00",
            Severity.INFO:    "#6080ff",
        }
        icons  = {Severity.ERROR: "✗", Severity.WARNING: "⚠", Severity.INFO: "ℹ"}

        for issue in issues:
            row = ctk.CTkFrame(self._validation_frame, fg_color="transparent")
            row.pack(fill="x", padx=6, pady=1)
            ctk.CTkLabel(row, text=icons[issue.severity],
                          font=ctk.CTkFont("Consolas", 10, "bold"),
                          text_color=colors[issue.severity], width=20
                          ).pack(side="left")
            ctk.CTkLabel(row, text=issue.message,
                          font=ctk.CTkFont("Consolas", 9),
                          text_color=colors[issue.severity],
                          wraplength=360, justify="left"
                          ).pack(side="left", padx=4)

    # ================================================================ #
    #  Save                                                             #
    # ================================================================ #

    def _save(self) -> None:
        if not self._validate():
            messagebox.showwarning(
                "Validation Errors",
                "Fix all errors before saving.\nSee the Validation section.",
                parent=self
            )
            return
        data = self._collect_data()
        try:
            path = self._loader.save_transformer(data)
            ctk.CTkLabel(self._validation_frame,
                          text=f"✓ Saved: {path}",
                          font=ctk.CTkFont("Consolas", 9),
                          text_color="#00e676", wraplength=400
                          ).pack(pady=4)
            if self._on_saved:
                self._on_saved()
            self.after(1200, self.destroy)
        except Exception as e:
            messagebox.showerror("Save Error", str(e), parent=self)

    # ================================================================ #
    #  Load from JSON file                                              #
    # ================================================================ #

    def _load_json(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Load Transformer JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._clear()
            self._populate(data)
        except Exception as e:
            messagebox.showerror("Load Error", str(e), parent=self)

    # ================================================================ #
    #  Helpers                                                          #
    # ================================================================ #

    def _on_name_change(self, *_) -> None:
        name = self.v_name.get()
        slug = self._slugify(name)
        self.v_id.set(slug)
        self._schedule_preview()

    @staticmethod
    def _slugify(name: str) -> str:
        s = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
        return s or "transformer"

    @staticmethod
    def _blank_template() -> dict:
        return {
            "name":               "New Transformer",
            "transformer_id":     "new_transformer",
            "type":               "isolating_transformer",
            "rated_power_va":     100,
            "rated_frequency_hz": 50,
            "notes":              "",
            "primary":  [{"id": "P1", "start_pin": 1, "end_pin": 2,
                           "voltage": 230, "dot_polarity": True,
                           "relay_id": 0, "end_relay": None, "meas_channel": -1,
                           "taps": [], "coords": {}}],
            "secondary": [{"id": "S1", "start_pin": 7, "end_pin": 8,
                            "voltage": 115, "dot_polarity": True,
                            "relay_id": 1, "end_relay": None, "meas_channel": 0,
                            "taps": [], "coords": {}}],
            "tests": [{"from": "P1", "to": "S1",
                        "expected_voltage": 115, "tolerance_percent": 5,
                        "measurement_channel": 0,
                        "stabilization_delay_ms": 500,
                        "relay_map": {},
                        "description": "Primary → Secondary"}],
            "auto_matrix": {
                "enabled":            False,
                "energize_winding":   "P1",
                "energize_tap_index": None,
            },
        }
