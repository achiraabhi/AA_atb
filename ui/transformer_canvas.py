"""
TransformerCanvasWidget — CustomTkinter frame wrapping the Tkinter Canvas
that renders the transformer diagram.

Bridges between StateManager events (from background threads) and
the renderer via tk.Canvas.after() to keep UI updates on the main thread.
"""
import tkinter as tk
import customtkinter as ctk
from typing import Optional

from core.transformer_renderer import TransformerRenderer
from core.config_loader import TransformerConfig
from core.state_manager import StateManager, AppState


class TransformerCanvasWidget(ctk.CTkFrame):
    """
    Left-panel widget that contains the live animated transformer diagram.
    """

    def __init__(self, parent, state_manager: StateManager, **kwargs):
        super().__init__(parent, **kwargs)
        self._state = state_manager
        self._renderer: Optional[TransformerRenderer] = None
        self._current_config: Optional[TransformerConfig] = None
        self._pulse_ids = {}   # winding_id → after id for pulse animation
        self._pulse_state = {}
        self._active_from_tap: Optional[int] = None
        self._active_to_tap: Optional[int] = None

        self._build_ui()
        self._subscribe_to_state()

    # ------------------------------------------------------------------ #
    #  UI construction                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.configure(fg_color="#12122a", corner_radius=8)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header bar
        header = ctk.CTkFrame(self, fg_color="#1a1a3a", corner_radius=0, height=30)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        ctk.CTkLabel(header, text="TRANSFORMER DIAGRAM",
                     font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color="#607abb").pack(side="left", padx=12, pady=4)
        self._status_dot = ctk.CTkLabel(header, text="●", font=ctk.CTkFont(size=14),
                                         text_color="#404060")
        self._status_dot.pack(side="right", padx=12)

        # Canvas
        self._canvas = tk.Canvas(
            self, bg="#1a1a2e", highlightthickness=0,
            relief="flat"
        )
        self._canvas.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._canvas.bind("<Configure>", self._on_resize)

        self._renderer = TransformerRenderer(self._canvas)

        # Placeholder text
        self._placeholder = self._canvas.create_text(
            300, 225, text="Select a transformer to\ndisplay diagram",
            fill="#404070", font=("Consolas", 14), justify=tk.CENTER
        )

    def _subscribe_to_state(self) -> None:
        self._state.subscribe("test_step",   self._on_test_step)
        self._state.subscribe("step_result", self._on_step_result)
        self._state.subscribe("app_state",   self._on_app_state)
        self._state.subscribe("reset",       self._on_reset)

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def load_transformer(self, config: TransformerConfig) -> None:
        """Draw a new transformer. Safe to call from UI thread."""
        self._current_config = config
        self._canvas.delete("placeholder")
        # Render after layout is stable
        self._canvas.after(50, self._do_render)

    def _do_render(self) -> None:
        if self._current_config and self._renderer:
            self._renderer.render(self._current_config)
            self._set_status_dot("idle")

    # ------------------------------------------------------------------ #
    #  State callbacks (may arrive from background thread → use after()) #
    # ------------------------------------------------------------------ #

    def _on_test_step(self, data: dict) -> None:
        self._canvas.after(0, lambda: self._apply_test_step(data))

    def _apply_test_step(self, data: dict) -> None:
        if not self._renderer or not self._current_config:
            return
        from_w   = data.get("from_w", "")
        to_w     = data.get("to_w", "")
        from_tap = data.get("from_tap_index")
        to_tap   = data.get("to_tap_index")

        self._active_from_tap = from_tap
        self._active_to_tap   = to_tap

        self._renderer.reset_all_to_idle()
        self._renderer.highlight_winding(from_w, "active_primary")
        self._renderer.highlight_winding(to_w, "active_secondary")

        # Overlay active tap segments (highlight all segments 0..tap_index)
        if from_tap is not None:
            for i in range(from_tap + 1):
                self._renderer.highlight_tap_section(from_w, i, "active_primary")
        if to_tap is not None:
            for i in range(to_tap + 1):
                self._renderer.highlight_tap_section(to_w, i, "active_secondary")

        self._renderer.start_flow_animation(from_w, to_w)
        self._set_status_dot("testing")
        self._start_pin_pulse(from_w)
        self._start_pin_pulse(to_w)

    def _on_step_result(self, result) -> None:
        self._canvas.after(0, lambda: self._apply_step_result(result))

    def _apply_step_result(self, result) -> None:
        if not self._renderer:
            return
        state = "pass" if result.passed else "fail"
        self._renderer.highlight_winding(result.from_winding, state)
        self._renderer.highlight_winding(result.to_winding, state)
        # Reflect result on active tap segments
        if self._active_from_tap is not None:
            for i in range(self._active_from_tap + 1):
                self._renderer.highlight_tap_section(result.from_winding, i, state)
        if self._active_to_tap is not None:
            for i in range(self._active_to_tap + 1):
                self._renderer.highlight_tap_section(result.to_winding, i, state)
        self._renderer.flash_result(result.passed)
        self._set_status_dot("pass" if result.passed else "fail")

    def _on_app_state(self, state: AppState) -> None:
        self._canvas.after(0, lambda: self._apply_app_state(state))

    def _apply_app_state(self, state: AppState) -> None:
        if state in (AppState.IDLE, AppState.READY):
            if self._renderer:
                self._renderer.reset_all_to_idle()
                self._renderer._stop_animation()
            self._set_status_dot("idle")
        elif state == AppState.EMERGENCY:
            self._set_status_dot("emergency")

    def _on_reset(self, _) -> None:
        self._canvas.after(0, self._do_reset)

    def _do_reset(self) -> None:
        self._active_from_tap = None
        self._active_to_tap   = None
        if self._renderer:
            self._renderer.reset_all_to_idle()
            self._renderer._stop_animation()
        self._set_status_dot("idle")

    # ------------------------------------------------------------------ #
    #  Pin pulse animation                                               #
    # ------------------------------------------------------------------ #

    def _start_pin_pulse(self, winding_id: str) -> None:
        self._stop_pin_pulse(winding_id)
        self._pulse_state[winding_id] = True
        self._pulse_step(winding_id, 0)

    def _stop_pin_pulse(self, winding_id: str) -> None:
        aid = self._pulse_ids.pop(winding_id, None)
        if aid:
            try:
                self._canvas.after_cancel(aid)
            except Exception:
                pass

    def _pulse_step(self, winding_id: str, tick: int) -> None:
        if not self._renderer:
            return
        # Alternate pin colour between active and default
        from core.transformer_renderer import C_PIN_ACTIVE, C_PIN_OUTLINE
        color = C_PIN_ACTIVE if tick % 2 == 0 else C_PIN_OUTLINE
        for suffix in ("_start", "_end"):
            key = f"{winding_id}{suffix}"
            pid = self._renderer._pin_items.get(key)
            if pid:
                try:
                    self._canvas.itemconfig(pid, outline=color)
                except Exception:
                    pass
        aid = self._canvas.after(350, lambda: self._pulse_step(winding_id, tick + 1))
        self._pulse_ids[winding_id] = aid

    # ------------------------------------------------------------------ #
    #  Status dot                                                         #
    # ------------------------------------------------------------------ #

    def _set_status_dot(self, state: str) -> None:
        colors = {
            "idle":      "#404060",
            "testing":   "#00c8ff",
            "pass":      "#00e676",
            "fail":      "#ff1744",
            "emergency": "#ff6d00",
        }
        self._status_dot.configure(text_color=colors.get(state, "#404060"))

    # ------------------------------------------------------------------ #
    #  Resize handler                                                     #
    # ------------------------------------------------------------------ #

    def _on_resize(self, event) -> None:
        if self._current_config and self._renderer:
            # Debounce: only re-render after resize settles
            if hasattr(self, "_resize_after"):
                self._canvas.after_cancel(self._resize_after)
            self._resize_after = self._canvas.after(120, self._do_render)
