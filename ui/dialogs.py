"""
Application dialogs:
  - AddTransformerDialog: JSON editor to create a new transformer definition
  - ViewLogsDialog:       Browse saved test log files
  - AboutDialog:          Application info
"""
import json
import tkinter as tk
import customtkinter as ctk
from pathlib import Path
from typing import Optional, Callable

from core.config_loader import ConfigLoader


# ------------------------------------------------------------------ #
#  Helper: dark-themed scrolled text widget                          #
# ------------------------------------------------------------------ #
class _DarkText(tk.Text):
    def __init__(self, parent, **kwargs):
        defaults = dict(
            bg="#0d0d20", fg="#c0d0ff", insertbackground="#c0d0ff",
            selectbackground="#2a3a7a", relief="flat",
            font=("Consolas", 10), wrap="none"
        )
        defaults.update(kwargs)
        super().__init__(parent, **defaults)


# ------------------------------------------------------------------ #
#  Add / Edit Transformer Dialog                                      #
# ------------------------------------------------------------------ #
class AddTransformerDialog(ctk.CTkToplevel):
    """
    Modal dialog for creating or editing a transformer JSON config.
    Displays a JSON text editor with a validate & save button.
    """

    TEMPLATE = {
        "name": "New Transformer",
        "transformer_id": "new_transformer",
        "type": "unknown",
        "rated_power_va": 100,
        "rated_frequency_hz": 50,
        "notes": "",
        "primary": [
            {
                "id": "P1",
                "start_pin": 1,
                "end_pin": 2,
                "voltage": 230,
                "dot_polarity": True,
                "relay_id": 0,
                "taps": [],
                "coords": {}
            }
        ],
        "secondary": [
            {
                "id": "S1",
                "start_pin": 7,
                "end_pin": 8,
                "voltage": 115,
                "dot_polarity": True,
                "relay_id": 1,
                "taps": [],
                "coords": {}
            }
        ],
        "tests": [
            {
                "from": "P1",
                "to": "S1",
                "expected_voltage": 115,
                "tolerance_percent": 5,
                "measurement_channel": 0,
                "stabilization_delay_ms": 500,
                "relay_map": {},
                "description": "Primary to secondary isolation"
            }
        ]
    }

    def __init__(self, parent, config_loader: ConfigLoader,
                 on_saved: Optional[Callable] = None,
                 initial_json: Optional[dict] = None):
        super().__init__(parent)
        self._loader = config_loader
        self._on_saved = on_saved
        self.title("Transformer Editor")
        self.geometry("760x640")
        self.resizable(True, True)
        self.configure(fg_color="#12122a")
        self.grab_set()

        self._build_ui(initial_json or self.TEMPLATE)

    def _build_ui(self, template: dict) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header
        hdr = ctk.CTkFrame(self, fg_color="#1e1e3a", corner_radius=0, height=36)
        hdr.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(hdr, text="  TRANSFORMER CONFIGURATION EDITOR",
                     font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color="#607abb").pack(side="left", padx=8, pady=6)

        # Text editor
        editor_frame = ctk.CTkFrame(self, fg_color="#0a0a18", corner_radius=4)
        editor_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        editor_frame.grid_rowconfigure(0, weight=1)
        editor_frame.grid_columnconfigure(0, weight=1)

        self._text = _DarkText(editor_frame)
        scroll_y = tk.Scrollbar(editor_frame, command=self._text.yview,
                                 bg="#1a1a3a", troughcolor="#0a0a18")
        scroll_x = tk.Scrollbar(editor_frame, orient=tk.HORIZONTAL,
                                 command=self._text.xview,
                                 bg="#1a1a3a", troughcolor="#0a0a18")
        self._text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        self._text.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")

        self._text.insert("1.0", json.dumps(template, indent=2))

        # Validation message
        self._msg_label = ctk.CTkLabel(self, text="",
                                        font=ctk.CTkFont("Consolas", 10),
                                        text_color="#ff6060")
        self._msg_label.grid(row=2, column=0, padx=8)

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=3, column=0, pady=8)
        ctk.CTkButton(btn_frame, text="Validate", width=100,
                       font=ctk.CTkFont("Consolas", 11),
                       fg_color="#1a3a6a", command=self._validate).pack(side="left", padx=6)
        ctk.CTkButton(btn_frame, text="Save", width=100,
                       font=ctk.CTkFont("Consolas", 11, "bold"),
                       fg_color="#1a5a1a", command=self._save).pack(side="left", padx=6)
        ctk.CTkButton(btn_frame, text="Cancel", width=100,
                       font=ctk.CTkFont("Consolas", 11),
                       fg_color="#3a1a1a", command=self.destroy).pack(side="left", padx=6)

    def _validate(self) -> Optional[dict]:
        try:
            data = json.loads(self._text.get("1.0", tk.END))
            self._loader._validate(data, "editor")
            self._msg_label.configure(text="✓ Valid JSON", text_color="#00e676")
            return data
        except json.JSONDecodeError as e:
            self._msg_label.configure(text=f"JSON Error: {e}", text_color="#ff4444")
        except ValueError as e:
            self._msg_label.configure(text=f"Config Error: {e}", text_color="#ff9900")
        return None

    def _save(self) -> None:
        data = self._validate()
        if data is None:
            return
        try:
            path = self._loader.save_transformer(data)
            self._msg_label.configure(text=f"Saved: {Path(path).name}", text_color="#00e676")
            if self._on_saved:
                self._on_saved()
            self.after(800, self.destroy)
        except Exception as e:
            self._msg_label.configure(text=f"Save error: {e}", text_color="#ff4444")


# ------------------------------------------------------------------ #
#  View Logs Dialog                                                   #
# ------------------------------------------------------------------ #
class ViewLogsDialog(ctk.CTkToplevel):
    """Browse saved JSON log files."""

    def __init__(self, parent, log_dir: str):
        super().__init__(parent)
        self._log_dir = Path(log_dir)
        self.title("Test Logs")
        self.geometry("820x560")
        self.configure(fg_color="#12122a")
        self.grab_set()
        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        hdr = ctk.CTkFrame(self, fg_color="#1e1e3a", corner_radius=0, height=36)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        ctk.CTkLabel(hdr, text="  TEST LOG VIEWER",
                     font=ctk.CTkFont("Consolas", 11, "bold"),
                     text_color="#607abb").pack(side="left", padx=8, pady=6)

        # File list
        list_frame = ctk.CTkScrollableFrame(self, width=240, fg_color="#0d0d20")
        list_frame.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=8)
        self._list_frame = list_frame

        # Content viewer
        content_frame = ctk.CTkFrame(self, fg_color="#0a0a18", corner_radius=4)
        content_frame.grid(row=1, column=1, sticky="nsew", padx=(4, 8), pady=8)
        content_frame.grid_rowconfigure(0, weight=1)
        content_frame.grid_columnconfigure(0, weight=1)
        self._content = _DarkText(content_frame, state="disabled")
        sy = tk.Scrollbar(content_frame, command=self._content.yview)
        self._content.configure(yscrollcommand=sy.set)
        self._content.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")

        ctk.CTkButton(self, text="Close", command=self.destroy,
                       fg_color="#3a1a1a", font=ctk.CTkFont("Consolas", 11)
                       ).grid(row=2, column=0, columnspan=2, pady=8)

        self._populate_list()

    def _populate_list(self) -> None:
        files = sorted(self._log_dir.glob("*.json"), reverse=True)
        for path in files:
            btn = ctk.CTkButton(
                self._list_frame, text=path.name,
                font=ctk.CTkFont("Consolas", 9),
                fg_color="#151530", hover_color="#2a2a5a",
                anchor="w", command=lambda p=path: self._load_file(p)
            )
            btn.pack(fill="x", pady=1)

        if not files:
            ctk.CTkLabel(self._list_frame, text="No log files found",
                          font=ctk.CTkFont("Consolas", 10),
                          text_color="#404060").pack(pady=20)

    def _load_file(self, path: Path) -> None:
        try:
            with open(path) as f:
                content = json.dumps(json.load(f), indent=2)
        except Exception as e:
            content = f"Error reading file:\n{e}"
        self._content.configure(state="normal")
        self._content.delete("1.0", tk.END)
        self._content.insert("1.0", content)
        self._content.configure(state="disabled")


# ------------------------------------------------------------------ #
#  About Dialog                                                       #
# ------------------------------------------------------------------ #
class AboutDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("About")
        self.geometry("440x300")
        self.resizable(False, False)
        self.configure(fg_color="#12122a")
        self.grab_set()

        lines = [
            ("Transformer Test Bench", ctk.CTkFont("Consolas", 16, "bold"), "#607abb"),
            ("Industrial Post-Assembly Testing System", ctk.CTkFont("Consolas", 11), "#8090b0"),
            ("", None, None),
            ("Version 1.0.0", ctk.CTkFont("Consolas", 10), "#606080"),
            ("Python  |  CustomTkinter  |  Tkinter Canvas", ctk.CTkFont("Consolas", 10), "#606080"),
            ("", None, None),
            ("JSON-driven transformer configuration", ctk.CTkFont("Consolas", 10), "#808090"),
            ("Dynamic diagram rendering", ctk.CTkFont("Consolas", 10), "#808090"),
            ("Hardware abstraction layer", ctk.CTkFont("Consolas", 10), "#808090"),
        ]
        for text, font, color in lines:
            if text:
                ctk.CTkLabel(self, text=text, font=font, text_color=color).pack(pady=2)
            else:
                ctk.CTkLabel(self, text="").pack(pady=2)

        ctk.CTkButton(self, text="Close", command=self.destroy,
                       fg_color="#1a1a4a", font=ctk.CTkFont("Consolas", 11)
                       ).pack(pady=16)
