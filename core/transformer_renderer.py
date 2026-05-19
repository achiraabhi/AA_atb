"""
Dynamic transformer diagram renderer — enhanced edition.
Draws transformer diagrams on a Tkinter Canvas entirely from JSON config.

Key capabilities:
  • Variable winding count (primary + secondary)
  • Full tap support: pin numbers, voltage labels, even auto-spacing
  • Per-segment coil colouring for tap-section animation
  • Polarity dots, lead wires, pin circles
  • Smooth flow-particle animation between winding paths
  • All items tag-based → single itemconfig call changes an entire winding

Coordinate system:
  - Iron core: vertical centre of canvas
  - Primary:   windings stacked left of core
  - Secondary: windings stacked right of core
"""
from __future__ import annotations
import math
import tkinter as tk
from typing import Dict, List, Tuple, Optional

from core.config_loader import TransformerConfig, WindingConfig


# ──────────────────────────── palette ────────────────────────────────
C_CORE            = "#7a7a8a"
C_CORE_HIGHLIGHT  = "#aaaabb"
C_PRIMARY_IDLE    = "#4a90e2"
C_SECONDARY_IDLE  = "#e2913a"
C_WINDING_ACTIVE  = "#00d4ff"
C_SEC_ACTIVE      = "#00ff99"
C_PASS            = "#00e676"
C_FAIL            = "#ff1744"
C_WIRE            = "#7a8090"
C_WIRE_ACTIVE     = "#00d4ff"
C_PIN_FILL        = "#1a1a2e"
C_PIN_OUTLINE     = "#5a6090"
C_PIN_ACTIVE      = "#ffcc00"
C_DOT             = "#e0e0ff"
C_DOT_ACTIVE      = "#ffcc00"
C_TAP_IDLE        = "#8888aa"
C_TAP_ACTIVE      = "#ffcc00"
C_TEXT            = "#c0c8e0"
C_TEXT_ACTIVE     = "#ffffff"
C_BG              = "#1a1a2e"
C_SEG_ACTIVE      = "#ff9900"    # active tap segment colour
C_PREVIEW_BG      = "#0f0f20"


class TransformerRenderer:
    """
    Renders a complete transformer diagram on a tk.Canvas.

    Public API
    ----------
    render(config)                  — draw complete diagram from config
    highlight_winding(wid, state)   — colour a whole winding
    highlight_tap_section(wid, tap_index, state)  — colour one tap segment
    reset_all_to_idle()             — restore all items to default colours
    start_flow_animation(from, to)  — animate current-flow particles
    flash_result(passed)            — full-screen pass/fail flash
    """

    # ── Layout constants ─────────────────────────────────────────────
    CORE_W_FRAC  = 0.07    # core width  / canvas width
    CORE_H_FRAC  = 0.72    # core height / canvas height
    COIL_TURNS   = 5       # half-turn arcs per unit
    TURN_H       = 13      # px per half-turn arc
    COIL_W       = 36      # horizontal width of coil bumps
    PIN_R        = 7       # pin circle radius
    DOT_R        = 5       # polarity dot radius
    LEAD_LEN     = 34      # straight wire from coil edge to pin
    WINDING_GAP  = 24      # vertical gap between stacked windings
    TAP_SPACING  = 30      # minimum vertical px between adjacent taps

    def __init__(self, canvas: tk.Canvas):
        self._cv = canvas
        self._cfg: Optional[TransformerConfig] = None

        # item tracking
        self._winding_items: Dict[str, List[int]] = {}
        self._tap_items:     Dict[str, List[int]] = {}   # "P1_tap0" → [line,pin,pin_lbl,volt_lbl]
        self._seg_items:     Dict[str, List[int]] = {}   # "P1_seg0" → [arc,arc,...] coil arcs
        self._pin_items:     Dict[str, int]       = {}   # "P1_start" → oval id
        self._dot_items:     Dict[str, int]       = {}   # "P1" → oval id
        self._winding_paths: Dict[str, List[Tuple[float,float]]] = {}

        self._anim_particles: List[int] = []
        self._anim_after_id:  Optional[str] = None

    # ================================================================ #
    #  Public API                                                       #
    # ================================================================ #

    def render(self, config: TransformerConfig) -> None:
        self._cv.delete("all")
        self._winding_items.clear()
        self._tap_items.clear()
        self._seg_items.clear()
        self._pin_items.clear()
        self._dot_items.clear()
        self._winding_paths.clear()
        self._stop_animation()
        self._cfg = config

        W = self._cv.winfo_width()  or 600
        H = self._cv.winfo_height() or 450

        cx, cy   = W / 2, H / 2
        core_w   = max(18, W * self.CORE_W_FRAC)
        core_h   = max(100, H * self.CORE_H_FRAC)
        core_x1  = cx - core_w / 2
        core_x2  = cx + core_w / 2
        core_y1  = cy - core_h / 2
        core_y2  = cy + core_h / 2

        self._draw_core(core_x1, core_y1, core_x2, core_y2)
        self._draw_title(config.name, W)

        self._layout_windings(config.primary,   "left",
                              core_x1, core_y1, core_y2, W, H)
        self._layout_windings(config.secondary, "right",
                              core_x2, core_y1, core_y2, W, H)

    def highlight_winding(self, winding_id: str, state: str) -> None:
        """
        state: "idle" | "active_primary" | "active_secondary" | "pass" | "fail"
        """
        color_map = {
            "idle":             C_PRIMARY_IDLE if self._is_primary(winding_id) else C_SECONDARY_IDLE,
            "active_primary":   C_WINDING_ACTIVE,
            "active_secondary": C_SEC_ACTIVE,
            "pass":             C_PASS,
            "fail":             C_FAIL,
        }
        self._set_winding_color(winding_id, color_map.get(state, C_TEXT))

    def highlight_tap_section(self, winding_id: str, tap_index: int,
                               state: str = "active") -> None:
        """
        Highlight the coil segment at tap_index.
        state: "active" | "active_primary" | "active_secondary" | "pass" | "fail" | "idle"
        tap_index=-1 highlights the full winding.
        """
        color_map = {
            "active":           C_SEG_ACTIVE,
            "active_primary":   C_WINDING_ACTIVE,
            "active_secondary": C_SEC_ACTIVE,
            "pass":             C_PASS,
            "fail":             C_FAIL,
            "idle":             C_PRIMARY_IDLE if self._is_primary(winding_id) else C_SECONDARY_IDLE,
        }
        color = color_map.get(state, C_SEG_ACTIVE)
        if tap_index < 0:
            self._set_winding_color(winding_id, color)
            return
        seg_key = f"{winding_id}_seg{tap_index}"
        for iid in self._seg_items.get(seg_key, []):
            try:
                self._cv.itemconfig(iid, outline=color)
            except Exception:
                pass
        # Also highlight the tap indicator itself
        tap_key = f"{winding_id}_tap{tap_index}"
        for iid in self._tap_items.get(tap_key, []):
            try:
                t = self._cv.type(iid)
                if t == "oval":
                    self._cv.itemconfig(iid, outline=C_TAP_ACTIVE, fill=C_TAP_ACTIVE)
                elif t in ("line",):
                    self._cv.itemconfig(iid, fill=C_TAP_ACTIVE)
                elif t == "text":
                    self._cv.itemconfig(iid, fill=C_TAP_ACTIVE)
            except Exception:
                pass

    def reset_all_to_idle(self) -> None:
        if not self._cfg:
            return
        for w in self._cfg.primary + self._cfg.secondary:
            self.highlight_winding(w.id, "idle")

    def start_flow_animation(self, from_id: str, to_id: str) -> None:
        self._stop_animation()
        path = self._build_flow_path(from_id, to_id)
        if len(path) < 4:
            return
        self._anim_particles = []
        for _ in range(3):
            p = self._cv.create_oval(0, 0, 1, 1, fill=C_WINDING_ACTIVE,
                                      outline="", tags="flow_particle")
            self._anim_particles.append(p)
        spacing = max(1, len(path) // 3)
        self._anim_tick(path, 0, spacing)

    def _anim_tick(self, path: List[Tuple], tick: int, spacing: int) -> None:
        n = len(path)
        for i, pid in enumerate(self._anim_particles):
            idx = (tick + i * spacing) % n
            x, y = path[idx]
            r = 5
            self._cv.coords(pid, x - r, y - r, x + r, y + r)
        self._anim_after_id = self._cv.after(
            28, lambda: self._anim_tick(path, tick + 2, spacing)
        )

    def _stop_animation(self) -> None:
        if self._anim_after_id:
            try:
                self._cv.after_cancel(self._anim_after_id)
            except Exception:
                pass
            self._anim_after_id = None
        for pid in self._anim_particles:
            try:
                self._cv.delete(pid)
            except Exception:
                pass
        self._anim_particles = []

    def flash_result(self, passed: bool, cycles: int = 4) -> None:
        color = C_PASS if passed else C_FAIL
        self._flash(color, cycles * 2)

    # ================================================================ #
    #  Core & title                                                     #
    # ================================================================ #

    def _draw_core(self, x1: float, y1: float, x2: float, y2: float) -> None:
        mid = (x1 + x2) / 2
        half_gap = (x2 - x1) * 0.06
        # Left limb
        self._cv.create_rectangle(x1, y1, mid - half_gap, y2,
                                   fill=C_CORE, outline="#444455", width=1, tags="core")
        # Right limb
        self._cv.create_rectangle(mid + half_gap, y1, x2, y2,
                                   fill=C_CORE, outline="#444455", width=1, tags="core")

    def _draw_title(self, name: str, W: float) -> None:
        self._cv.create_text(W / 2, 14, text=name, fill=C_TEXT,
                              font=("Consolas", 11, "bold"), tags="title")

    # ================================================================ #
    #  Winding layout                                                   #
    # ================================================================ #

    def _layout_windings(self, windings: List[WindingConfig], side: str,
                          core_x: float, core_y1: float, core_y2: float,
                          canvas_w: float, canvas_h: float) -> None:
        if not windings:
            return
        # Pre-compute heights
        heights = [self._winding_height(w) for w in windings]
        total   = sum(heights) + (len(windings) - 1) * self.WINDING_GAP
        start_y = (core_y1 + core_y2) / 2 - total / 2

        y = start_y
        for w, h in zip(windings, heights):
            centre_y = y + h / 2
            self._draw_winding(w, side, core_x, centre_y, h, canvas_w)
            y += h + self.WINDING_GAP

    def _winding_height(self, w: WindingConfig) -> int:
        base = self.COIL_TURNS * self.TURN_H * 2
        n = len(w.taps) if w.taps else 0
        if n > 0:
            needed = (n + 1) * self.TAP_SPACING
            return max(base, needed)
        return base

    # ================================================================ #
    #  Winding drawing                                                  #
    # ================================================================ #

    def _draw_winding(self, winding: WindingConfig, side: str,
                       core_x: float, centre_y: float, h: float,
                       canvas_w: float) -> None:
        wid    = winding.id
        top_y  = centre_y - h / 2
        bot_y  = centre_y + h / 2
        n_taps = len(winding.taps) if winding.taps else 0

        direction = -1 if side == "left" else 1

        # Coil anchored at core edge
        coil_x = core_x - 2 if side == "left" else core_x + 2

        # Pin column x
        wire_x = (coil_x + direction * (self.COIL_W + self.LEAD_LEN))

        items: List[int] = []
        path_pts: List[Tuple[float, float]] = []

        # ── Coil arcs ───────────────────────────────────────────────
        # Distribute arcs across segments (between tap breakpoints)
        seg_ys = self._tap_y_positions(top_y, bot_y, n_taps)
        # seg_ys[i] → bottom y of segment i  (seg_ys[0]=tap0_y, last=bot_y)

        for seg_idx in range(len(seg_ys)):
            seg_top = top_y if seg_idx == 0 else seg_ys[seg_idx - 1]
            seg_bot = seg_ys[seg_idx]
            seg_arcs, seg_pts = self._draw_coil_segment(
                wid, seg_idx, side, coil_x, direction,
                seg_top, seg_bot
            )
            items    += seg_arcs
            path_pts += seg_pts

        # ── Lead wires & pins ───────────────────────────────────────
        # Top lead (start_pin)
        w_top = self._cv.create_line(coil_x, top_y, wire_x, top_y,
                                      fill=C_WIRE, width=2, tags=(wid, "wire"))
        # Bottom lead (end_pin)
        w_bot = self._cv.create_line(coil_x, bot_y, wire_x, bot_y,
                                      fill=C_WIRE, width=2, tags=(wid, "wire"))
        items += [w_top, w_bot]

        pr = self.PIN_R
        p_s = self._cv.create_oval(wire_x-pr, top_y-pr, wire_x+pr, top_y+pr,
                                    fill=C_PIN_FILL, outline=C_PIN_OUTLINE, width=2,
                                    tags=(wid, "pin"))
        p_e = self._cv.create_oval(wire_x-pr, bot_y-pr, wire_x+pr, bot_y+pr,
                                    fill=C_PIN_FILL, outline=C_PIN_OUTLINE, width=2,
                                    tags=(wid, "pin"))
        items += [p_s, p_e]
        self._pin_items[f"{wid}_start"] = p_s
        self._pin_items[f"{wid}_end"]   = p_e

        # Pin number labels
        lbl_dx = -16 if side == "left" else 16
        for pin_num, pin_y in [(winding.start_pin, top_y), (winding.end_pin, bot_y)]:
            lbl = self._cv.create_text(wire_x + lbl_dx, pin_y,
                                        text=str(pin_num), fill=C_TEXT,
                                        font=("Consolas", 9, "bold"),
                                        tags=(wid, "pin_label"))
            items.append(lbl)

        # ── Polarity dot ────────────────────────────────────────────
        if winding.dot_polarity:
            dr  = self.DOT_R
            dot = self._cv.create_oval(wire_x - dr, top_y + pr + 4,
                                        wire_x + dr, top_y + pr + 4 + dr * 2,
                                        fill=C_DOT, outline="", tags=(wid, "dot"))
            items.append(dot)
            self._dot_items[wid] = dot

        # ── Voltage / ID label (only for un-tapped windings) ────────
        if not n_taps and winding.voltage:
            lx  = wire_x - 40 if side == "left" else wire_x + 40
            vlbl = self._cv.create_text(lx, centre_y,
                                         text=f"{wid}\n{winding.voltage}V",
                                         fill=C_TEXT, font=("Consolas", 9),
                                         justify=tk.CENTER, tags=(wid, "volt_label"))
            items.append(vlbl)
        elif not n_taps:
            lx  = wire_x - 28 if side == "left" else wire_x + 28
            vlbl = self._cv.create_text(lx, centre_y, text=wid,
                                         fill=C_TEXT, font=("Consolas", 9, "bold"),
                                         tags=(wid, "id_label"))
            items.append(vlbl)

        # Winding ID label for tapped windings (above top pin)
        if n_taps:
            lx = wire_x - 28 if side == "left" else wire_x + 28
            id_lbl = self._cv.create_text(lx, top_y - 14, text=wid,
                                            fill=C_TEXT, font=("Consolas", 9, "bold"),
                                            tags=(wid, "id_label"))
            items.append(id_lbl)

        # ── Taps ────────────────────────────────────────────────────
        tap_item_lists = self._draw_taps(winding, side, coil_x, top_y, bot_y,
                                          wire_x, lbl_dx, seg_ys, items)

        # Collect flow path: wire_top → coil path → wire_bot
        full_path = ([(wire_x, top_y)] + [(coil_x, top_y)] +
                     path_pts +
                     [(coil_x, bot_y)] + [(wire_x, bot_y)])
        self._winding_paths[wid] = full_path
        self._winding_items[wid] = items

    def _draw_coil_segment(self, wid: str, seg_idx: int, side: str,
                            coil_x: float, direction: int,
                            top_y: float, bot_y: float
                            ) -> Tuple[List[int], List[Tuple[float, float]]]:
        """
        Draw coil arcs filling the vertical range [top_y, bot_y].
        Tags each arc with both the winding id and a segment tag for per-tap highlighting.
        Returns (item_ids, path_points).
        """
        seg_tag = f"{wid}_seg{seg_idx}"
        h       = bot_y - top_y
        # Adapt turn count to available height
        n_arcs  = max(2, int(round(h / self.TURN_H)))
        arc_h   = h / n_arcs

        color   = C_PRIMARY_IDLE if side == "left" else C_SECONDARY_IDLE

        arc_ids: List[int]                 = []
        pts:     List[Tuple[float, float]] = []

        for t in range(n_arcs):
            y0 = top_y + t * arc_h
            y1 = y0 + arc_h
            cy_arc = (y0 + y1) / 2
            rx = self.COIL_W / 2
            ry = arc_h / 2

            if side == "left":
                start_ang = 270 if t % 2 == 0 else 90
            else:
                start_ang = 90  if t % 2 == 0 else 270

            aid = self._cv.create_arc(
                coil_x + direction * rx * 2, y0,
                coil_x, y1,
                start=start_ang, extent=180,
                style=tk.ARC, outline=color, width=2.5,
                tags=(wid, seg_tag, "coil_arc")
            )
            arc_ids.append(aid)
            self._seg_items.setdefault(seg_tag, []).append(aid)

            # Build path points along arc
            for a in range(0, 181, 20):
                ang = math.radians(start_ang + a)
                px  = coil_x + direction * rx + direction * rx * math.cos(ang)
                py  = cy_arc + ry * math.sin(ang)
                pts.append((px, py))

        return arc_ids, pts

    # ================================================================ #
    #  Tap drawing                                                      #
    # ================================================================ #

    def _tap_y_positions(self, top_y: float, bot_y: float,
                          n_taps: int) -> List[float]:
        """
        Evenly distribute n_taps breakpoints between top_y and bot_y.
        Returns list of n_taps y-values (one per tap).
        The last segment ends at bot_y.
        Segments: [top_y..ys[0]], [ys[0]..ys[1]], ..., [ys[-1]..bot_y]
        """
        if n_taps == 0:
            return [bot_y]
        result = []
        for i in range(n_taps):
            frac = (i + 1) / (n_taps + 1)
            result.append(top_y + (bot_y - top_y) * frac)
        result.append(bot_y)   # final segment closes at bottom
        return result

    def _draw_taps(self, winding: WindingConfig, side: str,
                   coil_x: float, top_y: float, bot_y: float,
                   wire_x: float, lbl_dx: int,
                   seg_ys: List[float],
                   items: List[int]) -> None:
        taps = winding.taps or []
        n    = len(taps)
        if n == 0:
            return

        wid  = winding.id
        pr   = self.PIN_R * 0.7
        volt_dx = (lbl_dx * 1.0) + (-36 if side == "left" else 36)

        for i, tap in enumerate(taps):
            # y-position of this tap (use stored seg breakpoints, exclude last=bot_y)
            tap_y   = seg_ys[i] if i < len(seg_ys) - 1 else bot_y
            tap_key = f"{wid}_tap{i}"

            pin_num = tap.get("pin", "")
            voltage = tap.get("voltage", "")
            label   = tap.get("label", f"{voltage}V" if voltage else str(pin_num))

            # Dashed tap line from coil to pin column
            ln = self._cv.create_line(
                coil_x, tap_y, wire_x, tap_y,
                fill=C_TAP_IDLE, width=1.5, dash=(5, 3),
                tags=(wid, tap_key, "tap_line")
            )
            # Tap pin circle
            pin = self._cv.create_oval(
                wire_x - pr, tap_y - pr, wire_x + pr, tap_y + pr,
                fill=C_PIN_FILL, outline=C_TAP_IDLE, width=1.5,
                tags=(wid, tap_key, "tap_pin")
            )
            # Pin number label
            p_lbl = self._cv.create_text(
                wire_x + lbl_dx, tap_y,
                text=str(pin_num), fill=C_TAP_IDLE,
                font=("Consolas", 8),
                tags=(wid, tap_key, "tap_pin_label")
            )
            # Voltage label
            v_lbl = self._cv.create_text(
                wire_x + volt_dx, tap_y,
                text=label, fill=C_TEXT,
                font=("Consolas", 9, "bold"),
                tags=(wid, tap_key, "tap_volt_label")
            )

            tap_items = [ln, pin, p_lbl, v_lbl]
            items.extend(tap_items)
            self._tap_items[tap_key] = tap_items

    # ================================================================ #
    #  Colour helpers                                                   #
    # ================================================================ #

    def _set_winding_color(self, winding_id: str, color: str) -> None:
        for iid in self._cv.find_withtag(winding_id):
            t = self._cv.type(iid)
            try:
                if t == "arc":
                    self._cv.itemconfig(iid, outline=color)
                elif t == "line":
                    self._cv.itemconfig(iid, fill=color)
                elif t == "oval":
                    self._cv.itemconfig(iid, outline=color)
                elif t == "text":
                    self._cv.itemconfig(iid, fill=color)
            except Exception:
                pass

    def _is_primary(self, winding_id: str) -> bool:
        if self._cfg is None:
            return True
        return any(w.id == winding_id for w in self._cfg.primary)

    # ================================================================ #
    #  Animation path                                                   #
    # ================================================================ #

    def _build_flow_path(self, from_id: str, to_id: str) -> List[Tuple]:
        p1 = self._winding_paths.get(from_id, [])
        p2 = self._winding_paths.get(to_id,   [])
        if not p1 or not p2:
            return []
        return p1 + list(reversed(p2))

    # ================================================================ #
    #  Flash animation                                                  #
    # ================================================================ #

    def _flash(self, color: str, remaining: int) -> None:
        if remaining <= 0:
            try:
                self._cv.configure(bg=C_BG)
            except Exception:
                pass
            return
        new_bg = color if remaining % 2 == 0 else C_BG
        try:
            self._cv.configure(bg=new_bg)
        except Exception:
            pass
        self._cv.after(200, lambda: self._flash(color, remaining - 1))
