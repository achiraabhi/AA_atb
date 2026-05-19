"""
Visual Interactive Transformer Builder
ui/visual_editor.py

The canvas IS the primary editing interface.
Property panel is secondary (context-sensitive for selected item).

Interaction model:
  Left-click  winding body  → select, show properties
  Left-click  pin/tap node  → relay-assign popup
  Drag        drag-handle   → reorder winding vertically
  Right-click anything      → context menu
  Double-click winding      → force-select and open properties
"""
from __future__ import annotations

import json
import math
import re
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, Dict, List, Optional, Tuple, NamedTuple
from dataclasses import dataclass, field

import customtkinter as ctk

from core.config_loader import ConfigLoader
from core.validator import TransformerValidator, Severity


# ── palette ────────────────────────────────────────────────────────────────
_BG       = "#0f0f20"
_CORE_CLR = "#7a7a8a"
_P_IDLE   = "#4a90e2"
_S_IDLE   = "#e2913a"
_SEL_CLR  = "#00c8ff"
_HOV_CLR  = "#ffcc00"
_PIN_CLR  = "#5a6090"
_PIN_FILL = "#1a1a2e"
_TAP_CLR  = "#8888aa"
_WARN_CLR = "#ff9900"
_ERR_CLR  = "#ff4444"
_OK_CLR   = "#00e676"
_TEXT_CLR = "#c0c8e0"
_GRID_CLR = "#14142a"
_DARK     = "#0a0a18"
_CARD     = "#111128"
_HDR      = "#1a1a38"
_BLUE     = "#2a3a7a"
_BTN      = "#1a2a5a"
_RED      = "#5a1010"

WINDING_TYPES = [
    "basic_winding", "center_tap", "auto_winding",
    "auxiliary", "shielding", "toroidal",
]
TRANSFORMER_TYPES = [
    "isolating_transformer", "step_down_transformer",
    "step_up_transformer", "multi_tap_transformer",
    "center_tap_transformer", "auto_transformer",
    "toroidal_transformer", "unknown",
]

# ── selection tap codes (negative = non-tap selection) ─────────────────────
_SEL_WINDING   = -1   # whole winding body selected
_SEL_START_PIN = -2   # start_pin node selected
_SEL_END_PIN   = -3   # end_pin node selected


# ── data model ─────────────────────────────────────────────────────────────

@dataclass
class _VWinding:
    id:           str
    side:         str           # "primary" | "secondary"
    start_pin:    int
    end_pin:      int
    voltage:      float
    dot_polarity: bool = True
    relay_a:      Optional[int] = None   # RL1-16
    relay_b:      Optional[int] = None   # RL17-32
    meas_channel: int  = -1
    winding_type: str  = "basic_winding"
    taps:         List[dict] = field(default_factory=list)

    def to_json_dict(self) -> dict:
        return {
            "id":           self.id,
            "winding_type": self.winding_type,
            "start_pin":    self.start_pin,
            "end_pin":      self.end_pin,
            "voltage":      self.voltage,
            "dot_polarity": self.dot_polarity,
            "relay_a":      self.relay_a,
            "relay_b":      self.relay_b,
            "meas_channel": self.meas_channel,
            "taps":         [dict(t) for t in self.taps],
            "coords":       {},
        }


class _Hit(NamedTuple):
    """Hit-test result for a canvas element."""
    type:       str     # "winding_body"|"winding_drag"|"start_pin"|"end_pin"|"tap_pin"
    winding_id: str
    tap_index:  int     # -1/-2/-3 for non-tap; >=0 for tap
    x1: float
    y1: float
    x2: float
    y2: float


# ══════════════════════════════════════════════════════════════════════════ #
#  Main editor window                                                        #
# ══════════════════════════════════════════════════════════════════════════ #

class VisualTransformerEditorWindow(ctk.CTkToplevel):
    """Visual canvas-primary transformer editor."""

    # ── layout constants ───────────────────────────────────────────────────
    _CORE_W  = 20
    _COIL_W  = 38
    _LEAD    = 48
    _TURN_H  = 13
    _TURNS   = 5
    _TAP_SP  = 34
    _PIN_R   = 8
    _DOT_R   = 5
    _GAP     = 26
    _PROP_W  = 300

    def __init__(self, parent,
                 config_loader: ConfigLoader,
                 on_saved: Optional[Callable] = None,
                 initial_config: Optional[dict] = None):
        super().__init__(parent)
        self._loader    = config_loader
        self._on_saved  = on_saved
        self._validator = TransformerValidator()

        # Editor state
        self._windings:   List[_VWinding] = []
        self._hit_areas:  List[_Hit]      = []
        self._selected:   Optional[Tuple[str, int]] = None   # (winding_id, tap_code)
        self._hover:      Optional[Tuple[str, int]] = None
        self._drag_state: Optional[dict]            = None
        self._sim_active: bool                      = False
        self._sim_states: Dict[str, str]            = {}     # winding_id → color state
        self._redraw_id:  Optional[str]             = None

        # Transformer-level tk vars
        self._v_name  = tk.StringVar()
        self._v_id    = tk.StringVar()
        self._v_type  = tk.StringVar(value="isolating_transformer")
        self._v_va    = tk.StringVar(value="100")
        self._v_hz    = tk.StringVar(value="50")
        self._v_notes = tk.StringVar()
        self._v_am_en = tk.BooleanVar(value=True)
        self._v_am_w  = tk.StringVar()
        self._tests:  List[dict] = []

        # Prop-panel widgets (cleared on each selection change)
        self._prop_widgets: List[tk.Widget] = []

        self.title("Visual Transformer Builder")
        self.geometry("1380x840")
        self.minsize(1100, 680)
        self.configure(fg_color=_DARK)
        self.grab_set()

        self._build_ui()

        data = initial_config if initial_config else self._blank_template()
        self._populate(data)

    # ══════════════════════════════════════════════════════════════════════ #
    #  UI construction                                                       #
    # ══════════════════════════════════════════════════════════════════════ #

    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self._build_toolbar()
        self._build_canvas_area()
        self._build_property_panel()
        self._build_status_bar()

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=_HDR, corner_radius=0, height=46)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)

        def _btn(text, cmd, fg=_BTN, hv="#2a3a7a"):
            return ctk.CTkButton(bar, text=text, command=cmd, height=32,
                                 font=ctk.CTkFont("Consolas", 10, "bold"),
                                 fg_color=fg, hover_color=hv, corner_radius=4)

        ctk.CTkLabel(bar, text="  ⚡ VISUAL TRANSFORMER BUILDER",
                     font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color="#607abb").pack(side="left", padx=(8, 12), pady=7)

        _btn("+ Primary",   lambda: self._add_winding("primary"),  "#1a3a1a", "#2a5a2a").pack(side="left", padx=2, pady=7)
        _btn("+ Secondary", lambda: self._add_winding("secondary"), "#1a1a3a", "#2a2a6a").pack(side="left", padx=2, pady=7)

        _sep = ctk.CTkFrame(bar, fg_color="#2a2a4a", width=1, height=30)
        _sep.pack(side="left", padx=8, pady=7)

        _btn("✔ Validate", self._validate_and_show,  "#1a3a6a", "#2a5090").pack(side="left", padx=2, pady=7)
        _btn("▶ Simulate", self._simulate_test,       "#1a3060", "#2a4080").pack(side="left", padx=2, pady=7)

        _sep2 = ctk.CTkFrame(bar, fg_color="#2a2a4a", width=1, height=30)
        _sep2.pack(side="left", padx=8, pady=7)

        _btn("💾 Save",    self._save,       "#1a4a1a", "#2a6a2a").pack(side="left", padx=2, pady=7)
        _btn("📂 Load",    self._load_json,  "#2a2a0a", "#4a4a10").pack(side="left", padx=2, pady=7)
        _btn("🗑 Clear",   self._clear,      "#3a1a1a", "#5a2020").pack(side="left", padx=2, pady=7)
        _btn("✕ Close",   self.destroy,     "#2a1a2a", "#4a2a4a").pack(side="right", padx=8, pady=7)

        # Inline name + type fields
        ctk.CTkLabel(bar, text="Name:", font=ctk.CTkFont("Consolas", 9),
                     text_color="#8090b0").pack(side="left", padx=(12, 2), pady=7)
        e_name = ctk.CTkEntry(bar, textvariable=self._v_name, width=180,
                              font=ctk.CTkFont("Consolas", 10),
                              fg_color=_DARK, border_color=_BLUE)
        e_name.pack(side="left", padx=2, pady=7)
        self._v_name.trace_add("write", self._on_name_change)

        ctk.CTkLabel(bar, text="Type:", font=ctk.CTkFont("Consolas", 9),
                     text_color="#8090b0").pack(side="left", padx=(6, 2), pady=7)
        ctk.CTkComboBox(bar, variable=self._v_type, values=TRANSFORMER_TYPES,
                        width=190, font=ctk.CTkFont("Consolas", 9),
                        fg_color=_DARK, button_color=_BLUE, dropdown_fg_color=_HDR,
                        command=lambda _: self._schedule_redraw()
                        ).pack(side="left", padx=2, pady=7)

    def _build_canvas_area(self) -> None:
        outer = ctk.CTkFrame(self, fg_color=_BG, corner_radius=0)
        outer.grid(row=1, column=0, sticky="nsew")
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(outer, bg=_BG, highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")

        self._canvas.bind("<ButtonPress-1>",  self._on_click)
        self._canvas.bind("<B1-Motion>",       self._on_drag_move)
        self._canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self._canvas.bind("<Button-3>",        self._on_right_click)
        self._canvas.bind("<Motion>",          self._on_hover)
        self._canvas.bind("<Double-Button-1>", self._on_double_click)
        self._canvas.bind("<Configure>",       lambda _: self._schedule_redraw())

    def _build_property_panel(self) -> None:
        outer = ctk.CTkFrame(self, fg_color="#0f0f22",
                             corner_radius=0, width=self._PROP_W)
        outer.grid(row=1, column=1, sticky="nsew")
        outer.grid_propagate(False)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        self._prop_scroll = ctk.CTkScrollableFrame(
            outer, fg_color="#0f0f22", corner_radius=0,
            width=self._PROP_W - 16)
        self._prop_scroll.grid(row=0, column=0, sticky="nsew")
        self._prop_scroll.grid_columnconfigure(0, weight=0, minsize=110)
        self._prop_scroll.grid_columnconfigure(1, weight=1)

        self._prop_placeholder = ctk.CTkLabel(
            self._prop_scroll,
            text="Click a winding,\ntap, or pin node\nto edit properties.",
            font=ctk.CTkFont("Consolas", 10),
            text_color="#404060", justify="center")
        self._prop_placeholder.grid(row=0, column=0, columnspan=2, pady=40)

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="#0a0a18", corner_radius=0, height=24)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        self._status_var = tk.StringVar(
            value="Right-click canvas for options  |  click any node to assign relay")
        ctk.CTkLabel(bar, textvariable=self._status_var,
                     font=ctk.CTkFont("Consolas", 9),
                     text_color="#506080").pack(side="left", padx=8, pady=3)
        self._val_label = ctk.CTkLabel(bar, text="",
                                       font=ctk.CTkFont("Consolas", 9, "bold"),
                                       text_color="#ffcc00")
        self._val_label.pack(side="right", padx=8, pady=3)

    # ══════════════════════════════════════════════════════════════════════ #
    #  Canvas drawing                                                        #
    # ══════════════════════════════════════════════════════════════════════ #

    def _schedule_redraw(self) -> None:
        if self._redraw_id:
            try:
                self._canvas.after_cancel(self._redraw_id)
            except Exception:
                pass
        self._redraw_id = self._canvas.after(45, self._draw_canvas)

    def _draw_canvas(self) -> None:
        cv = self._canvas
        cv.delete("all")
        self._hit_areas.clear()

        W = cv.winfo_width()  or 720
        H = cv.winfo_height() or 520
        cx = W / 2

        # Subtle grid
        for xi in range(0, W, 40):
            cv.create_line(xi, 0, xi, H, fill=_GRID_CLR, width=1)
        for yi in range(0, H, 40):
            cv.create_line(0, yi, W, yi, fill=_GRID_CLR, width=1)

        core_y1 = H * 0.08
        core_y2 = H * 0.92
        hw = self._CORE_W / 2

        # Core
        cv.create_rectangle(cx - hw, core_y1, cx + hw, core_y2,
                             fill=_CORE_CLR, outline="#444455", width=1, tags="core")

        # Column labels
        cv.create_text(cx / 2, core_y1 - 14, text="PRIMARY",
                       fill="#3a6090", font=("Consolas", 9, "bold"))
        cv.create_text(cx + (W - cx) / 2, core_y1 - 14, text="SECONDARY",
                       fill="#906040", font=("Consolas", 9, "bold"))

        # Transformer name
        name = self._v_name.get().strip() or "New Transformer"
        cv.create_text(cx, core_y1 - 28, text=name,
                       fill=_TEXT_CLR, font=("Consolas", 12, "bold"))

        # Build quick validation map for overlay markers
        issues_map = self._get_validation_issue_map()

        # Column geometry
        coil_x_left  = cx - hw - 2
        coil_x_right = cx + hw + 2
        pin_x_left   = coil_x_left  - (self._COIL_W + self._LEAD)
        pin_x_right  = coil_x_right + (self._COIL_W + self._LEAD)

        primaries   = [w for w in self._windings if w.side == "primary"]
        secondaries = [w for w in self._windings if w.side == "secondary"]

        self._draw_column(primaries,   "left",  coil_x_left,  pin_x_left,
                          core_y1, core_y2, W, H, issues_map)
        self._draw_column(secondaries, "right", coil_x_right, pin_x_right,
                          core_y1, core_y2, W, H, issues_map)

        if not self._windings:
            cv.create_text(cx, H / 2,
                           text="Use [+ Primary] and [+ Secondary] in the toolbar\n"
                                "to add windings to this transformer.\n\n"
                                "Right-click anywhere for more options.",
                           fill="#404070", font=("Consolas", 12),
                           justify=tk.CENTER)

    def _draw_column(self, windings: List[_VWinding], side: str,
                     coil_x: float, pin_x: float,
                     core_y1: float, core_y2: float,
                     W: float, H: float,
                     issues_map: Dict[str, List]) -> None:
        if not windings:
            hint_x = coil_x + (-self._LEAD if side == "left" else self._LEAD)
            cv = self._canvas
            cv.create_text(hint_x, (core_y1 + core_y2) / 2,
                           text=f"No {'primary' if side == 'left' else 'secondary'}\nwindings",
                           fill="#2a3a5a", font=("Consolas", 10), justify=tk.CENTER)
            return

        heights = [self._winding_height(w) for w in windings]
        total   = sum(heights) + (len(windings) - 1) * self._GAP
        avail   = core_y2 - core_y1
        y       = core_y1 + max(0, (avail - total) / 2)

        for w, h in zip(windings, heights):
            centre_y = y + h / 2
            self._draw_winding(w, side, coil_x, pin_x, centre_y, h, W,
                               issues_map.get(w.id, []))
            y += h + self._GAP

    def _winding_height(self, w: _VWinding) -> int:
        base = self._TURNS * self._TURN_H * 2
        n = len(w.taps)
        return max(base, (n + 1) * self._TAP_SP + 20) if n else base

    def _draw_winding(self, w: _VWinding, side: str,
                      coil_x: float, pin_x: float,
                      centre_y: float, h: float, W: float,
                      issues: List) -> None:
        cv  = self._canvas
        top = centre_y - h / 2
        bot = centre_y + h / 2
        direction = -1 if side == "left" else 1

        selected  = self._selected is not None and self._selected[0] == w.id
        sim_state = self._sim_states.get(w.id)

        if sim_state == "active":
            base_color = "#00d4ff"
        elif sim_state == "pass":
            base_color = "#00e676"
        elif selected:
            base_color = _SEL_CLR
        elif self._hover and self._hover[0] == w.id and not self._selected:
            base_color = _HOV_CLR
        else:
            base_color = _P_IDLE if w.side == "primary" else _S_IDLE

        n_taps = len(w.taps)
        seg_ys = self._tap_ys(top, bot, n_taps)

        # Coil arcs per segment
        for si in range(len(seg_ys)):
            seg_top = top if si == 0 else seg_ys[si - 1]
            seg_bot = seg_ys[si]
            self._draw_coil_seg(w.id, side, coil_x, direction,
                                seg_top, seg_bot, base_color)

        # Lead wires
        cv.create_line(coil_x, top, pin_x, top, fill=base_color, width=2, tags=w.id)
        cv.create_line(coil_x, bot, pin_x, bot, fill=base_color, width=2, tags=w.id)

        # Pin nodes (start / end)
        self._draw_pin_node(w, "start", side, coil_x, pin_x, top, base_color)
        self._draw_pin_node(w, "end",   side, coil_x, pin_x, bot, base_color)

        # Polarity dot
        if w.dot_polarity:
            dr, pr = self._DOT_R, self._PIN_R
            cv.create_oval(pin_x - dr, top + pr + 4,
                           pin_x + dr, top + pr + 4 + dr * 2,
                           fill="#e0e0ff", outline="", tags=w.id)

        # Winding label + relay summary
        lbl_dx = -56 if side == "left" else 56
        relay_txt = self._relay_summary(w.relay_a, w.relay_b)
        cv.create_text(pin_x + lbl_dx, centre_y - (8 if relay_txt else 0),
                       text=f"{w.id}  {w.voltage:.0f}V" if w.voltage else w.id,
                       fill=base_color, font=("Consolas", 10, "bold"),
                       anchor="center", tags=w.id)
        if relay_txt:
            cv.create_text(pin_x + lbl_dx, centre_y + 10,
                           text=relay_txt, fill="#8090c0",
                           font=("Consolas", 8), anchor="center", tags=w.id)

        # Tap nodes
        for ti, tap in enumerate(w.taps):
            tap_y = seg_ys[ti] if ti < len(seg_ys) - 1 else bot
            self._draw_tap_node(w, ti, tap, side, coil_x, pin_x, tap_y)

        # Drag handle (small grab bar above winding, in the column center region)
        gap_x = (coil_x + pin_x) / 2
        dh_x1, dh_y1 = gap_x - 18, top - 14
        dh_x2, dh_y2 = gap_x + 18, top - 4
        cv.create_rectangle(dh_x1, dh_y1, dh_x2, dh_y2,
                            fill="#1a2a4a", outline=base_color, width=1,
                            tags=(w.id, "drag_handle"))
        cv.create_text(gap_x, (dh_y1 + dh_y2) / 2,
                       text="≡", fill=base_color, font=("Consolas", 8),
                       tags=(w.id, "drag_handle"))
        self._hit_areas.append(_Hit("winding_drag", w.id, _SEL_WINDING,
                                    dh_x1, dh_y1, dh_x2, dh_y2))

        # Winding body hit area
        body_x1 = min(coil_x, pin_x) - self._COIL_W
        body_x2 = max(coil_x, pin_x) + self._COIL_W
        self._hit_areas.append(_Hit("winding_body", w.id, _SEL_WINDING,
                                    body_x1, top, body_x2, bot))

        # Issue markers
        for issue in issues[:3]:
            mc = _ERR_CLR if issue.severity == Severity.ERROR else _WARN_CLR
            cv.create_text(pin_x + lbl_dx, top - 6,
                           text="⚠", fill=mc, font=("Consolas", 10))

    def _draw_pin_node(self, w: _VWinding, pin_type: str, side: str,
                       coil_x: float, pin_x: float, y: float,
                       base_color: str) -> None:
        cv  = self._canvas
        r   = self._PIN_R
        tap_code = _SEL_START_PIN if pin_type == "start" else _SEL_END_PIN
        relay_id = w.relay_a if pin_type == "start" else w.relay_b
        pin_num  = w.start_pin if pin_type == "start" else w.end_pin

        sel = (self._selected == (w.id, tap_code))
        hov = (self._hover == (w.id, tap_code))
        if sel:
            outline = _SEL_CLR
        elif hov:
            outline = _HOV_CLR
        elif relay_id is not None:
            outline = "#ffcc00"
        else:
            outline = _PIN_CLR

        cv.create_oval(pin_x - r, y - r, pin_x + r, y + r,
                       fill=_PIN_FILL, outline=outline, width=2,
                       tags=(w.id, f"pin_{pin_type}"))

        # Pin number label
        lbl_dx = -20 if side == "left" else 20
        cv.create_text(pin_x + lbl_dx, y, text=str(pin_num),
                       fill=_TEXT_CLR, font=("Consolas", 9, "bold"),
                       anchor="center", tags=w.id)

        # Relay badge
        if relay_id is not None:
            cv.create_text(pin_x + lbl_dx * 2.8, y,
                           text=f"RL{relay_id}", fill="#00c8ff",
                           font=("Consolas", 8, "bold"),
                           anchor="center", tags=w.id)

        # Hit area (slightly larger than the circle for easier clicking)
        self._hit_areas.append(_Hit(f"{pin_type}_pin", w.id, tap_code,
                                    pin_x - r - 5, y - r - 5,
                                    pin_x + r + 5, y + r + 5))

    def _draw_tap_node(self, w: _VWinding, ti: int, tap: dict,
                       side: str, coil_x: float, pin_x: float,
                       tap_y: float) -> None:
        cv  = self._canvas
        pr  = self._PIN_R * 0.75
        sel = (self._selected == (w.id, ti))
        hov = (self._hover == (w.id, ti))

        relay_a = tap.get("relay_a")
        relay_b = tap.get("relay_b")

        if sel:
            color = _SEL_CLR
        elif hov:
            color = _HOV_CLR
        elif relay_a is not None or relay_b is not None:
            color = "#ffcc00"
        else:
            color = _TAP_CLR

        cv.create_line(coil_x, tap_y, pin_x, tap_y,
                       fill=color, width=1.5, dash=(5, 3),
                       tags=(w.id, f"tap_{ti}"))
        cv.create_oval(pin_x - pr, tap_y - pr, pin_x + pr, tap_y + pr,
                       fill=_PIN_FILL, outline=color, width=1.5,
                       tags=(w.id, f"tap_{ti}"))

        lbl_dx  = -20 if side == "left" else 20
        pin_num = tap.get("pin", "")
        voltage = tap.get("voltage", "")
        label   = tap.get("label") or (f"{voltage}V" if voltage else str(pin_num))

        cv.create_text(pin_x + lbl_dx, tap_y, text=str(pin_num),
                       fill=color, font=("Consolas", 8), anchor="center",
                       tags=(w.id, f"tap_{ti}"))
        cv.create_text(pin_x + lbl_dx * 3.5, tap_y, text=label,
                       fill=_TEXT_CLR, font=("Consolas", 9), anchor="center",
                       tags=(w.id, f"tap_{ti}"))

        relay_txt = self._relay_summary(relay_a, relay_b)
        if relay_txt:
            cv.create_text(pin_x + lbl_dx * 6, tap_y, text=relay_txt,
                           fill="#00c8ff", font=("Consolas", 8), anchor="center",
                           tags=(w.id, f"tap_{ti}"))

        self._hit_areas.append(_Hit("tap_pin", w.id, ti,
                                    pin_x - pr - 5, tap_y - pr - 5,
                                    pin_x + pr + 5, tap_y + pr + 5))

    def _draw_coil_seg(self, wid: str, side: str, coil_x: float,
                       direction: int, top_y: float, bot_y: float,
                       color: str) -> None:
        cv    = self._canvas
        h     = bot_y - top_y
        n     = max(2, int(round(h / self._TURN_H)))
        arc_h = h / n
        rx    = self._COIL_W / 2

        for t in range(n):
            y0 = top_y + t * arc_h
            y1 = y0 + arc_h
            start_ang = (270 if t % 2 == 0 else 90) if side == "left" else \
                        (90  if t % 2 == 0 else 270)
            cv.create_arc(coil_x + direction * rx * 2, y0,
                          coil_x, y1,
                          start=start_ang, extent=180,
                          style=tk.ARC, outline=color, width=2.5,
                          tags=(wid, "coil_arc"))

    def _tap_ys(self, top: float, bot: float, n: int) -> List[float]:
        if n == 0:
            return [bot]
        ys = [(top + (bot - top) * (i + 1) / (n + 1)) for i in range(n)]
        ys.append(bot)
        return ys

    @staticmethod
    def _relay_summary(ra: Optional[int], rb: Optional[int]) -> str:
        parts = []
        if ra is not None:
            parts.append(f"RL{ra}")
        if rb is not None:
            parts.append(f"RL{rb}")
        return "/".join(parts)

    # ══════════════════════════════════════════════════════════════════════ #
    #  Mouse event handlers                                                  #
    # ══════════════════════════════════════════════════════════════════════ #

    def _hit_test(self, x: float, y: float) -> Optional[_Hit]:
        priority = ["tap_pin", "start_pin", "end_pin", "winding_drag", "winding_body"]
        hits = [h for h in self._hit_areas
                if h.x1 <= x <= h.x2 and h.y1 <= y <= h.y2]
        for p in priority:
            for h in hits:
                if h.type == p:
                    return h
        return hits[0] if hits else None

    def _on_click(self, event: tk.Event) -> None:
        hit = self._hit_test(event.x, event.y)
        if hit is None:
            self._selected = None
            self._update_property_panel(None)
            self._schedule_redraw()
            return

        if hit.type == "winding_drag":
            self._drag_state = {
                "id":       hit.winding_id,
                "start_y":  event.y,
                "side":     self._get_winding(hit.winding_id).side,
            }
            return

        if hit.type == "start_pin":
            self._selected = (hit.winding_id, _SEL_START_PIN)
            self._schedule_redraw()
            self._show_relay_popup(hit.winding_id, "relay_a",
                                   event.x_root, event.y_root)
        elif hit.type == "end_pin":
            self._selected = (hit.winding_id, _SEL_END_PIN)
            self._schedule_redraw()
            self._show_relay_popup(hit.winding_id, "relay_b",
                                   event.x_root, event.y_root)
        elif hit.type == "tap_pin":
            self._selected = (hit.winding_id, hit.tap_index)
            self._schedule_redraw()
            self._update_property_panel(hit)
        else:
            self._selected = (hit.winding_id, _SEL_WINDING)
            self._update_property_panel(hit)
            self._schedule_redraw()

    def _on_double_click(self, event: tk.Event) -> None:
        hit = self._hit_test(event.x, event.y)
        if hit and hit.type in ("winding_body", "winding_drag"):
            self._selected = (hit.winding_id, _SEL_WINDING)
            self._update_property_panel(hit)
            self._schedule_redraw()

    def _on_drag_move(self, event: tk.Event) -> None:
        if self._drag_state is None:
            return
        ds   = self._drag_state
        dy   = event.y - ds["start_y"]
        side = ds["side"]
        col  = [w for w in self._windings if w.side == side]
        idx  = next((i for i, w in enumerate(col) if w.id == ds["id"]), -1)
        if idx < 0:
            return
        thresh = self._GAP + self._TURNS * self._TURN_H
        if abs(dy) > thresh:
            new_idx = max(0, min(len(col) - 1, idx + (1 if dy > 0 else -1)))
            if new_idx != idx:
                ds["start_y"] = event.y
                a = self._windings.index(col[idx])
                b = self._windings.index(col[new_idx])
                self._windings[a], self._windings[b] = self._windings[b], self._windings[a]
                self._schedule_redraw()

    def _on_drag_end(self, event: tk.Event) -> None:
        self._drag_state = None

    def _on_hover(self, event: tk.Event) -> None:
        hit = self._hit_test(event.x, event.y)
        new_hover = None
        if hit:
            new_hover = (hit.winding_id, hit.tap_index)
            if hit.type in ("start_pin", "end_pin", "tap_pin"):
                self._canvas.configure(cursor="hand2")
                self._set_status("Click to assign relay  |  right-click for options")
            elif hit.type == "winding_drag":
                self._canvas.configure(cursor="fleur")
                self._set_status(f"Drag to reorder  [{hit.winding_id}]")
            else:
                self._canvas.configure(cursor="")
                self._set_status(f"{hit.winding_id}  |  right-click for options  |  click pin to assign relay")
        else:
            self._canvas.configure(cursor="")
            self._set_status("Right-click for options  |  click any node to assign relay")

        if new_hover != self._hover:
            self._hover = new_hover
            self._schedule_redraw()

    def _on_right_click(self, event: tk.Event) -> None:
        hit  = self._hit_test(event.x, event.y)
        menu = tk.Menu(self, tearoff=False, bg="#1a1a3a", fg="#c0d0ff",
                       activebackground="#2a3a7a", activeforeground="white",
                       relief="flat")

        if hit is None:
            menu.add_command(label="+ Add Primary Winding",
                             command=lambda: self._add_winding("primary"))
            menu.add_command(label="+ Add Secondary Winding",
                             command=lambda: self._add_winding("secondary"))
            menu.add_separator()
            menu.add_command(label="✔ Validate",    command=self._validate_and_show)
            menu.add_command(label="▶ Simulate",    command=self._simulate_test)
            menu.add_separator()
            menu.add_command(label="💾 Save",        command=self._save)
            menu.add_command(label="📂 Load JSON",   command=self._load_json)

        elif hit.type in ("winding_body", "winding_drag"):
            w = self._get_winding(hit.winding_id)
            menu.add_command(label=f"  Edit Properties [{w.id}]",
                             command=lambda: self._select_winding(w.id))
            menu.add_separator()
            menu.add_command(label="  ↑ Move Up",
                             command=lambda: self._move_winding(w.id, -1))
            menu.add_command(label="  ↓ Move Down",
                             command=lambda: self._move_winding(w.id, +1))
            menu.add_separator()
            menu.add_command(label="  + Add Tap",
                             command=lambda: self._add_tap_to(w.id))
            menu.add_separator()
            menu.add_command(label=f"  ✕ Delete [{w.id}]",
                             command=lambda: self._delete_winding(w.id),
                             foreground="#ff6060")

        elif hit.type == "tap_pin":
            w   = self._get_winding(hit.winding_id)
            ti  = hit.tap_index
            menu.add_command(label=f"  Edit Tap {ti} Properties",
                             command=lambda: self._select_tap(w.id, ti))
            menu.add_separator()
            menu.add_command(label="  Assign Relay A (RL1–16)…",
                             command=lambda: self._show_relay_popup(
                                 w.id, "tap_relay_a", event.x_root, event.y_root, ti))
            menu.add_command(label="  Assign Relay B (RL17–32)…",
                             command=lambda: self._show_relay_popup(
                                 w.id, "tap_relay_b", event.x_root, event.y_root, ti))
            menu.add_separator()
            menu.add_command(label=f"  ✕ Delete Tap {ti}",
                             command=lambda t=ti: self._delete_tap(w.id, t),
                             foreground="#ff6060")

        elif hit.type in ("start_pin", "end_pin"):
            w  = self._get_winding(hit.winding_id)
            rk = "relay_a" if hit.type == "start_pin" else "relay_b"
            grp = "A (RL1–16)" if rk == "relay_a" else "B (RL17–32)"
            menu.add_command(label=f"  Assign Relay {grp}…",
                             command=lambda: self._show_relay_popup(
                                 w.id, rk, event.x_root, event.y_root))
            menu.add_command(label="  Clear Relay",
                             command=lambda k=rk: self._set_relay(w.id, k, None))
            menu.add_separator()
            menu.add_command(label="  Edit Winding Properties",
                             command=lambda: self._select_winding(w.id))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ══════════════════════════════════════════════════════════════════════ #
    #  Relay-assignment popup                                                #
    # ══════════════════════════════════════════════════════════════════════ #

    def _show_relay_popup(self, winding_id: str, relay_key: str,
                          rx: int, ry: int, tap_index: int = -1) -> None:
        w = self._get_winding(winding_id)
        if w is None:
            return

        is_tap   = relay_key.startswith("tap_")
        pure_key = relay_key.replace("tap_", "")
        is_a     = pure_key == "relay_a"
        rl_min   = 1  if is_a else 17
        rl_max   = 16 if is_a else 32
        grp_lbl  = "Side A  (RL 1 – 16)" if is_a else "Side B  (RL 17 – 32)"

        if is_tap and 0 <= tap_index < len(w.taps):
            current = w.taps[tap_index].get(pure_key)
        else:
            current = getattr(w, pure_key, None)

        popup = ctk.CTkToplevel(self)
        popup.geometry(f"+{rx + 6}+{ry + 6}")
        popup.resizable(False, False)
        popup.grab_set()
        popup.configure(fg_color=_CARD)
        popup.wm_overrideredirect(True)

        frame = ctk.CTkFrame(popup, fg_color=_CARD, border_width=1,
                             border_color=_BLUE, corner_radius=6)
        frame.pack(fill="both", expand=True, padx=2, pady=2)
        frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(frame, text=grp_lbl,
                     font=ctk.CTkFont("Consolas", 10, "bold"),
                     text_color="#607abb").grid(row=0, column=0, columnspan=4,
                                                padx=8, pady=(8, 2), sticky="w")
        context = winding_id + (f"  Tap {tap_index}" if is_tap else "")
        ctk.CTkLabel(frame, text=f"  {context}",
                     font=ctk.CTkFont("Consolas", 9),
                     text_color="#8090b0").grid(row=1, column=0, columnspan=4,
                                                padx=8, pady=(0, 6), sticky="w")

        n_rl = rl_max - rl_min + 1
        for i, rl in enumerate(range(rl_min, rl_max + 1)):
            cur = current == rl
            btn = ctk.CTkButton(
                frame, text=f"RL{rl}", width=54, height=28,
                font=ctk.CTkFont("Consolas", 9, "bold"),
                fg_color="#0a4a8a" if cur else "#101020",
                hover_color="#1a5a9a",
                border_width=2 if cur else 0,
                border_color=_SEL_CLR if cur else "#101020",
                command=lambda r=rl: self._assign_relay(
                    popup, winding_id, relay_key, tap_index, r))
            btn.grid(row=2 + i // 4, column=i % 4, padx=2, pady=2)

        btn_row = 2 + math.ceil(n_rl / 4)
        ctk.CTkButton(frame, text="✕ Clear", width=80, height=26,
                      font=ctk.CTkFont("Consolas", 9),
                      fg_color=_RED, hover_color="#7a1010",
                      command=lambda: self._assign_relay(
                          popup, winding_id, relay_key, tap_index, None)
                      ).grid(row=btn_row, column=0, columnspan=2, padx=4, pady=6)
        ctk.CTkButton(frame, text="Cancel", width=80, height=26,
                      font=ctk.CTkFont("Consolas", 9),
                      fg_color="#1a1a3a", hover_color="#2a2a5a",
                      command=popup.destroy
                      ).grid(row=btn_row, column=2, columnspan=2, padx=4, pady=6)

    def _assign_relay(self, popup, winding_id: str, relay_key: str,
                      tap_index: int, value: Optional[int]) -> None:
        popup.destroy()
        self._set_relay(winding_id, relay_key, value, tap_index)

    def _set_relay(self, winding_id: str, relay_key: str,
                   value: Optional[int], tap_index: int = -1) -> None:
        w = self._get_winding(winding_id)
        if w is None:
            return
        is_tap   = relay_key.startswith("tap_")
        pure_key = relay_key.replace("tap_", "")
        if is_tap and 0 <= tap_index < len(w.taps):
            if value is None:
                w.taps[tap_index].pop(pure_key, None)
            else:
                w.taps[tap_index][pure_key] = value
        else:
            setattr(w, pure_key, value)

        if self._selected and self._selected[0] == winding_id:
            ti = tap_index if is_tap else _SEL_WINDING
            ht = "tap_pin" if is_tap else "winding_body"
            self._update_property_panel(_Hit(ht, winding_id, ti, 0, 0, 0, 0))
        self._schedule_redraw()

    # ══════════════════════════════════════════════════════════════════════ #
    #  Property panel                                                        #
    # ══════════════════════════════════════════════════════════════════════ #

    def _clear_prop(self) -> None:
        for widget in self._prop_widgets:
            try:
                widget.destroy()
            except Exception:
                pass
        self._prop_widgets.clear()
        if self._prop_placeholder.winfo_exists():
            self._prop_placeholder.grid(row=0, column=0, columnspan=2, pady=40)

    def _update_property_panel(self, hit: Optional[_Hit]) -> None:
        self._clear_prop()
        if hit is None:
            return
        if self._prop_placeholder.winfo_exists():
            self._prop_placeholder.grid_forget()

        w = self._get_winding(hit.winding_id)
        if w is None:
            return

        S   = self._prop_scroll
        row = [0]   # mutable row counter

        def banner(text: str) -> None:
            f = ctk.CTkFrame(S, fg_color=_HDR, corner_radius=0, height=22)
            f.grid(row=row[0], column=0, columnspan=2, sticky="ew", pady=(6, 0))
            ctk.CTkLabel(f, text=f"  {text}",
                         font=ctk.CTkFont("Consolas", 9, "bold"),
                         text_color="#607abb").pack(side="left", padx=6, pady=2)
            self._prop_widgets.append(f)
            row[0] += 1

        def field(label: str, var: tk.Variable,
                  width: int = 130, etype: str = "entry",
                  values: Optional[List[str]] = None) -> None:
            lbl = ctk.CTkLabel(S, text=label,
                               font=ctk.CTkFont("Consolas", 9),
                               text_color="#8090b0", anchor="e")
            lbl.grid(row=row[0], column=0, sticky="e", padx=(8, 4), pady=2)
            if etype == "combo":
                e = ctk.CTkComboBox(S, variable=var, values=values or [],
                                    width=width, font=ctk.CTkFont("Consolas", 9),
                                    fg_color=_DARK, button_color=_BLUE,
                                    dropdown_fg_color=_HDR)
            elif etype == "check":
                e = ctk.CTkCheckBox(S, text="", variable=var,
                                    checkbox_width=18, checkbox_height=18,
                                    fg_color=_BLUE, border_color="#4a5a9a")
            else:
                e = ctk.CTkEntry(S, textvariable=var, width=width,
                                 font=ctk.CTkFont("Consolas", 10),
                                 fg_color=_DARK, border_color=_BLUE)
            e.grid(row=row[0], column=1, sticky="ew", padx=(0, 8), pady=2)
            self._prop_widgets += [lbl, e]
            row[0] += 1

        def del_btn(text: str, cmd: Callable) -> None:
            b = ctk.CTkButton(S, text=text, command=cmd, height=28,
                              font=ctk.CTkFont("Consolas", 9),
                              fg_color=_RED, hover_color="#7a1010")
            b.grid(row=row[0], column=0, columnspan=2,
                   padx=8, pady=8, sticky="ew")
            self._prop_widgets.append(b)
            row[0] += 1

        def rl_grid(label_txt: str, color: str,
                    rl_range: range, get_val: Callable,
                    set_cb: Callable) -> None:
            lbl = ctk.CTkLabel(S, text=label_txt,
                               font=ctk.CTkFont("Consolas", 8, "bold"),
                               text_color=color, anchor="w")
            lbl.grid(row=row[0], column=0, columnspan=2, sticky="w", padx=8, pady=(4, 1))
            self._prop_widgets.append(lbl)
            row[0] += 1
            for i, rl in enumerate(rl_range):
                cur = get_val() == rl
                c   = i % 4
                r_  = row[0] + i // 4
                b = ctk.CTkButton(S, text=f"RL{rl}", width=52, height=24,
                                  font=ctk.CTkFont("Consolas", 8),
                                  fg_color="#0a3a6a" if cur else "#101020",
                                  hover_color="#1a5a9a",
                                  command=lambda r=rl: (set_cb(r),
                                                        self._update_property_panel(hit),
                                                        self._schedule_redraw()))
                b.grid(row=r_, column=0, columnspan=2,
                       padx=(6 + c * 60, 0), pady=1, sticky="w")
                self._prop_widgets.append(b)
            row[0] += math.ceil(len(rl_range) / 4)

        # ──────────────────────────────────────────────────────────────── #
        #  TAP PROPERTIES                                                   #
        # ──────────────────────────────────────────────────────────────── #
        if hit.type == "tap_pin" and 0 <= hit.tap_index < len(w.taps):
            ti  = hit.tap_index
            tap = w.taps[ti]

            banner(f"TAP {ti}  ─  {w.id}")

            v_pin  = tk.StringVar(value=str(tap.get("pin", "")))
            v_volt = tk.StringVar(value=str(tap.get("voltage", "")))
            v_lbl  = tk.StringVar(value=str(tap.get("label", "")))
            v_ra   = tk.StringVar(value=str(tap.get("relay_a", "") or ""))
            v_rb   = tk.StringVar(value=str(tap.get("relay_b", "") or ""))
            v_mc   = tk.StringVar(value=str(tap.get("meas_channel", "")))

            def _tap_upd(*_):
                try: tap["pin"] = int(v_pin.get())
                except: pass
                try: tap["voltage"] = float(v_volt.get())
                except: pass
                tap["label"] = v_lbl.get().strip()
                try: tap["relay_a"] = int(v_ra.get()) if v_ra.get().strip() else None
                except: tap["relay_a"] = None
                try: tap["relay_b"] = int(v_rb.get()) if v_rb.get().strip() else None
                except: tap["relay_b"] = None
                try: tap["meas_channel"] = int(v_mc.get())
                except: pass
                self._schedule_redraw()

            for v in (v_pin, v_volt, v_lbl, v_ra, v_rb, v_mc):
                v.trace_add("write", _tap_upd)

            field("Pin #:",       v_pin)
            field("Voltage (V):", v_volt)
            field("Label:",       v_lbl)
            field("Relay A:",     v_ra)
            field("Relay B:",     v_rb)
            field("Meas Ch:",     v_mc)

            banner("QUICK RELAY ASSIGN")
            rl_grid("Side A  (RL 1–16):", "#00c8ff", range(1, 17),
                    lambda: tap.get("relay_a"),
                    lambda r: tap.__setitem__("relay_a", r))
            rl_grid("Side B  (RL 17–32):", "#00e676", range(17, 33),
                    lambda: tap.get("relay_b"),
                    lambda r: tap.__setitem__("relay_b", r))

            del_btn(f"✕  Delete Tap {ti}",
                    lambda t=ti: self._delete_tap(w.id, t))

        # ──────────────────────────────────────────────────────────────── #
        #  WINDING PROPERTIES                                              #
        # ──────────────────────────────────────────────────────────────── #
        else:
            banner(f"WINDING  {w.id}  ({w.side.upper()})")

            v_id   = tk.StringVar(value=w.id)
            v_sp   = tk.StringVar(value=str(w.start_pin))
            v_ep   = tk.StringVar(value=str(w.end_pin))
            v_volt = tk.StringVar(value=str(w.voltage))
            v_ra   = tk.StringVar(value=str(w.relay_a) if w.relay_a is not None else "")
            v_rb   = tk.StringVar(value=str(w.relay_b) if w.relay_b is not None else "")
            v_mc   = tk.StringVar(value=str(w.meas_channel))
            v_type = tk.StringVar(value=w.winding_type)
            v_dot  = tk.BooleanVar(value=w.dot_polarity)

            def _w_upd(*_):
                nid = v_id.get().strip()
                if nid:
                    w.id = nid
                try: w.start_pin = int(v_sp.get())
                except: pass
                try: w.end_pin = int(v_ep.get())
                except: pass
                try: w.voltage = float(v_volt.get())
                except: pass
                try: w.relay_a = int(v_ra.get()) if v_ra.get().strip() else None
                except: w.relay_a = None
                try: w.relay_b = int(v_rb.get()) if v_rb.get().strip() else None
                except: w.relay_b = None
                try: w.meas_channel = int(v_mc.get())
                except: pass
                w.winding_type = v_type.get()
                w.dot_polarity = v_dot.get()
                self._schedule_redraw()

            for v in (v_id, v_sp, v_ep, v_volt, v_ra, v_rb, v_mc, v_type):
                v.trace_add("write", _w_upd)
            v_dot.trace_add("write", _w_upd)

            field("ID:",          v_id)
            field("Start Pin:",   v_sp)
            field("End Pin:",     v_ep)
            field("Voltage (V):", v_volt)
            field("Relay A:",     v_ra)
            field("Relay B:",     v_rb)
            field("Meas Ch:",     v_mc)
            field("Type:",        v_type, etype="combo", values=WINDING_TYPES)
            field("Dot Polarity:", v_dot, etype="check")

            banner("QUICK RELAY ASSIGN")
            rl_grid("Side A  (RL 1–16):", "#00c8ff", range(1, 17),
                    lambda: w.relay_a,
                    lambda r: (setattr(w, "relay_a", r), v_ra.set(str(r))))
            rl_grid("Side B  (RL 17–32):", "#00e676", range(17, 33),
                    lambda: w.relay_b,
                    lambda r: (setattr(w, "relay_b", r), v_rb.set(str(r))))

            # Tap list
            banner(f"TAPS  ({len(w.taps)})")
            if w.taps:
                for ti, tap in enumerate(w.taps):
                    rl_info = self._relay_summary(tap.get("relay_a"), tap.get("relay_b"))
                    lbl_txt = (f"Tap {ti}:  pin {tap.get('pin','?')}  "
                               f"{tap.get('voltage','?')}V  {rl_info}")
                    tb = ctk.CTkButton(S, text=lbl_txt, height=24,
                                       font=ctk.CTkFont("Consolas", 8),
                                       fg_color="#0a0a20", hover_color="#1a1a3a",
                                       anchor="w",
                                       command=lambda i=ti: self._select_tap(w.id, i))
                    tb.grid(row=row[0], column=0, columnspan=2,
                            sticky="ew", padx=8, pady=1)
                    self._prop_widgets.append(tb)
                    row[0] += 1
            else:
                no_tap = ctk.CTkLabel(S, text="  no taps",
                                      font=ctk.CTkFont("Consolas", 9),
                                      text_color="#404060")
                no_tap.grid(row=row[0], column=0, columnspan=2, sticky="w", padx=12)
                self._prop_widgets.append(no_tap)
                row[0] += 1

            add_btn = ctk.CTkButton(S, text="+ Add Tap", height=28,
                                    font=ctk.CTkFont("Consolas", 9),
                                    fg_color="#0a2a4a", hover_color="#1a3a6a",
                                    command=lambda: self._add_tap_to(w.id))
            add_btn.grid(row=row[0], column=0, columnspan=2,
                         sticky="ew", padx=8, pady=4)
            self._prop_widgets.append(add_btn)
            row[0] += 1

            del_btn(f"✕  Delete Winding  {w.id}",
                    lambda: self._delete_winding(w.id))

    # ══════════════════════════════════════════════════════════════════════ #
    #  Winding / tap management                                              #
    # ══════════════════════════════════════════════════════════════════════ #

    def _add_winding(self, side: str) -> None:
        idx       = len([w for w in self._windings if w.side == side]) + 1
        prefix    = "P" if side == "primary" else "S"
        used_pins = {p for w in self._windings
                     for p in [w.start_pin, w.end_pin]
                     + [t.get("pin", 0) for t in w.taps]}
        sp = max(used_pins or {0}) + 1
        ep = sp + 1

        used_ra = {w.relay_a for w in self._windings if w.relay_a}
        used_rb = {w.relay_b for w in self._windings if w.relay_b}
        next_ra = next((r for r in range(1, 17)  if r not in used_ra), None)
        next_rb = next((r for r in range(17, 33) if r not in used_rb), None)

        w = _VWinding(
            id=f"{prefix}{idx}", side=side,
            start_pin=sp, end_pin=ep,
            voltage=230.0 if side == "primary" else 115.0,
            relay_a=next_ra, relay_b=next_rb,
            meas_channel=-1 if side == "primary" else idx - 1,
        )
        self._windings.append(w)
        self._selected = (w.id, _SEL_WINDING)
        self._update_property_panel(_Hit("winding_body", w.id, _SEL_WINDING,
                                         0, 0, 0, 0))
        self._schedule_redraw()
        self._set_status(f"Added {w.id}  —  configure properties in the panel →")

    def _delete_winding(self, wid: str) -> None:
        self._windings = [w for w in self._windings if w.id != wid]
        if self._selected and self._selected[0] == wid:
            self._selected = None
            self._update_property_panel(None)
        self._schedule_redraw()

    def _move_winding(self, wid: str, direction: int) -> None:
        w = self._get_winding(wid)
        if w is None:
            return
        col     = [x for x in self._windings if x.side == w.side]
        idx     = col.index(w)
        new_idx = max(0, min(len(col) - 1, idx + direction))
        if new_idx == idx:
            return
        a = self._windings.index(col[idx])
        b = self._windings.index(col[new_idx])
        self._windings[a], self._windings[b] = self._windings[b], self._windings[a]
        self._schedule_redraw()

    def _add_tap_to(self, wid: str) -> None:
        w = self._get_winding(wid)
        if w is None:
            return
        used     = {w.start_pin, w.end_pin} | {t.get("pin", 0) for t in w.taps}
        tp       = max(used) + 1
        used_rb  = {w2.relay_b for w2 in self._windings if w2.relay_b}
        for t in w.taps:
            if t.get("relay_b"):
                used_rb.add(t["relay_b"])
        next_rb = next((r for r in range(17, 33) if r not in used_rb), None)
        ti = len(w.taps)
        w.taps.append({
            "pin":          tp,
            "voltage":      round(w.voltage * (ti + 1) / (len(w.taps) + 2), 1),
            "label":        "",
            "relay_a":      None,
            "relay_b":      next_rb,
            "meas_channel": ti,
        })
        self._select_tap(wid, ti)

    def _delete_tap(self, wid: str, tap_index: int) -> None:
        w = self._get_winding(wid)
        if w and 0 <= tap_index < len(w.taps):
            w.taps.pop(tap_index)
        if self._selected == (wid, tap_index):
            self._selected = (wid, _SEL_WINDING)
            self._update_property_panel(_Hit("winding_body", wid, _SEL_WINDING,
                                             0, 0, 0, 0))
        self._schedule_redraw()

    def _select_winding(self, wid: str) -> None:
        self._selected = (wid, _SEL_WINDING)
        self._update_property_panel(_Hit("winding_body", wid, _SEL_WINDING,
                                         0, 0, 0, 0))
        self._schedule_redraw()

    def _select_tap(self, wid: str, ti: int) -> None:
        self._selected = (wid, ti)
        self._update_property_panel(_Hit("tap_pin", wid, ti, 0, 0, 0, 0))
        self._schedule_redraw()

    # ══════════════════════════════════════════════════════════════════════ #
    #  Validation                                                            #
    # ══════════════════════════════════════════════════════════════════════ #

    def _get_validation_issue_map(self) -> Dict[str, List]:
        try:
            data   = self._collect_data()
            issues = self._validator.validate(data)
        except Exception:
            return {}
        result: Dict[str, List] = {}
        for issue in issues:
            for w in self._windings:
                if w.id in issue.field or w.id in issue.message:
                    result.setdefault(w.id, []).append(issue)
                    break
        return result

    def _validate_and_show(self) -> None:
        data   = self._collect_data()
        issues = self._validator.validate(data)
        errors   = sum(1 for i in issues if i.severity == Severity.ERROR)
        warnings = sum(1 for i in issues if i.severity == Severity.WARNING)

        if errors:
            self._val_label.configure(
                text=f"✗ {errors} error(s)  ⚠ {warnings} warning(s)",
                text_color="#ff4444")
        elif warnings:
            self._val_label.configure(
                text=f"✓ OK  ⚠ {warnings} warning(s)", text_color="#ffcc00")
        else:
            self._val_label.configure(text="✓ Valid", text_color=_OK_CLR)

        self._schedule_redraw()

        if issues:
            self._show_issue_popup(issues)

    def _show_issue_popup(self, issues) -> None:
        pop = ctk.CTkToplevel(self)
        pop.title("Validation Results")
        pop.geometry("500x380")
        pop.configure(fg_color=_CARD)
        pop.grab_set()
        sf = ctk.CTkScrollableFrame(pop, fg_color=_CARD)
        sf.pack(fill="both", expand=True, padx=8, pady=8)
        colors = {Severity.ERROR: "#ff4444", Severity.WARNING: "#ffaa00",
                  Severity.INFO: "#6080ff"}
        icons  = {Severity.ERROR: "✗", Severity.WARNING: "⚠", Severity.INFO: "ℹ"}
        for issue in issues:
            rf = ctk.CTkFrame(sf, fg_color="transparent")
            rf.pack(fill="x", pady=2)
            ctk.CTkLabel(rf, text=icons[issue.severity], width=20,
                         font=ctk.CTkFont("Consolas", 10, "bold"),
                         text_color=colors[issue.severity]).pack(side="left")
            ctk.CTkLabel(rf, text=issue.message,
                         font=ctk.CTkFont("Consolas", 9),
                         text_color=colors[issue.severity],
                         wraplength=420, justify="left").pack(side="left", padx=4)
        ctk.CTkButton(pop, text="Close", command=pop.destroy,
                      font=ctk.CTkFont("Consolas", 10),
                      fg_color=_BTN).pack(pady=8)

    # ══════════════════════════════════════════════════════════════════════ #
    #  Test simulation                                                       #
    # ══════════════════════════════════════════════════════════════════════ #

    def _simulate_test(self) -> None:
        primaries   = [w for w in self._windings if w.side == "primary"]
        secondaries = [w for w in self._windings if w.side == "secondary"]
        if not primaries or not secondaries:
            self._set_status("Simulation needs at least one primary AND one secondary winding.")
            return

        pairs = [(p, s) for p in primaries for s in secondaries]

        def run_pair(idx: int) -> None:
            if idx >= len(pairs):
                self._sim_states = {}
                self._sim_active = False
                self._schedule_redraw()
                self._set_status("▶ Simulation complete.")
                return

            p, s = pairs[idx]
            self._set_status(f"▶ Simulating:  {p.id}  →  {s.id}")

            def _step1():
                self._sim_states = {p.id: "active"}
                self._schedule_redraw()
                self._canvas.after(500, _step2)

            def _step2():
                self._sim_states = {p.id: "active", s.id: "active"}
                self._schedule_redraw()
                self._canvas.after(500, _step3)

            def _step3():
                self._sim_states = {p.id: "pass", s.id: "pass"}
                self._schedule_redraw()
                self._canvas.after(700, lambda: run_pair(idx + 1))

            _step1()

        self._sim_active = True
        run_pair(0)

    # ══════════════════════════════════════════════════════════════════════ #
    #  Data serialization / deserialization                                  #
    # ══════════════════════════════════════════════════════════════════════ #

    def _collect_data(self) -> dict:
        name = self._v_name.get().strip()
        tid  = self._v_id.get().strip() or self._slugify(name)
        try:   va = float(self._v_va.get())
        except: va = 0.0
        try:   hz = float(self._v_hz.get())
        except: hz = 50.0

        primaries   = [w for w in self._windings if w.side == "primary"]
        secondaries = [w for w in self._windings if w.side == "secondary"]
        am_winding  = self._v_am_w.get().strip() or (
            primaries[0].id if primaries else "")

        return {
            "name":               name,
            "transformer_id":     tid,
            "type":               self._v_type.get(),
            "rated_power_va":     va,
            "rated_frequency_hz": hz,
            "notes":              self._v_notes.get().strip(),
            "primary":   [w.to_json_dict() for w in primaries],
            "secondary": [w.to_json_dict() for w in secondaries],
            "tests":     self._tests,
            "auto_matrix": {
                "enabled":            bool(self._v_am_en.get()),
                "energize_winding":   am_winding,
                "energize_tap_index": None,
            },
        }

    def _populate(self, data: dict) -> None:
        self._v_name.set(data.get("name", ""))
        self._v_id.set(data.get("transformer_id", ""))
        self._v_type.set(data.get("type", "isolating_transformer"))
        self._v_va.set(str(data.get("rated_power_va", 100)))
        self._v_hz.set(str(data.get("rated_frequency_hz", 50)))
        self._v_notes.set(data.get("notes", ""))
        self._tests = data.get("tests", [])

        am = data.get("auto_matrix", {})
        self._v_am_en.set(bool(am.get("enabled", False)))
        self._v_am_w.set(am.get("energize_winding", ""))

        self._windings.clear()
        for wd in data.get("primary", []):
            self._windings.append(self._winding_from_dict(wd, "primary"))
        for wd in data.get("secondary", []):
            self._windings.append(self._winding_from_dict(wd, "secondary"))

        self._selected = None
        self._update_property_panel(None)
        self._schedule_redraw()

    @staticmethod
    def _winding_from_dict(d: dict, side: str) -> _VWinding:
        relay_a = d.get("relay_a", d.get("relay_id"))
        relay_b = d.get("relay_b", d.get("end_relay"))
        return _VWinding(
            id=d.get("id", "W?"), side=side,
            start_pin=d.get("start_pin", 1), end_pin=d.get("end_pin", 2),
            voltage=d.get("voltage", 0.0),
            dot_polarity=d.get("dot_polarity", True),
            relay_a=relay_a, relay_b=relay_b,
            meas_channel=d.get("meas_channel", -1),
            winding_type=d.get("winding_type", "basic_winding"),
            taps=[dict(t) for t in d.get("taps", [])],
        )

    def _clear(self) -> None:
        self._windings.clear()
        self._tests.clear()
        self._selected = None
        for v in (self._v_name, self._v_id, self._v_notes):
            v.set("")
        self._v_va.set("100")
        self._v_hz.set("50")
        self._v_type.set("isolating_transformer")
        self._update_property_panel(None)
        self._schedule_redraw()

    # ══════════════════════════════════════════════════════════════════════ #
    #  Save / Load                                                           #
    # ══════════════════════════════════════════════════════════════════════ #

    def _save(self) -> None:
        data   = self._collect_data()
        issues = self._validator.validate(data)
        errors = [i for i in issues if i.severity == Severity.ERROR]
        if errors:
            self._show_issue_popup(issues)
            messagebox.showwarning("Validation Errors",
                                   "Fix all errors before saving.",
                                   parent=self)
            return
        try:
            path = self._loader.save_transformer(data)
            self._val_label.configure(text="✓ Saved", text_color=_OK_CLR)
            self._set_status(f"Saved → {path}")
            if self._on_saved:
                self._on_saved()
            self.after(1400, self.destroy)
        except Exception as e:
            messagebox.showerror("Save Error", str(e), parent=self)

    def _load_json(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Load Transformer JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._populate(data)
        except Exception as e:
            messagebox.showerror("Load Error", str(e), parent=self)

    # ══════════════════════════════════════════════════════════════════════ #
    #  Helpers                                                               #
    # ══════════════════════════════════════════════════════════════════════ #

    def _get_winding(self, wid: str) -> Optional[_VWinding]:
        return next((w for w in self._windings if w.id == wid), None)

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    def _on_name_change(self, *_) -> None:
        self._v_id.set(self._slugify(self._v_name.get()))
        self._schedule_redraw()

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
            "primary": [{
                "id": "P1", "start_pin": 1, "end_pin": 2,
                "voltage": 230, "dot_polarity": True,
                "relay_a": 1, "relay_b": None,
                "meas_channel": -1, "taps": [], "coords": {},
            }],
            "secondary": [{
                "id": "S1", "start_pin": 7, "end_pin": 8,
                "voltage": 115, "dot_polarity": True,
                "relay_a": None, "relay_b": 17,
                "meas_channel": 0, "taps": [], "coords": {},
            }],
            "tests": [{
                "from": "P1", "to": "S1",
                "expected_voltage": 115, "tolerance_percent": 5,
                "measurement_channel": 0, "stabilization_delay_ms": 500,
                "relay_map": {"2": True, "18": True, "33": True, "34": True},
                "description": "Primary → Secondary",
            }],
            "auto_matrix": {
                "enabled": True,
                "energize_winding": "P1",
                "energize_tap_index": None,
            },
        }
