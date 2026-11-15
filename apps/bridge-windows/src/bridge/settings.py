"""The settings window: the relay, the PC's name and its PIN, then Save to install (or update) and start the service.
Like the Android bridge's one screen. Runs as administrator: the service and its settings are the machine's."""

import ctypes
import subprocess
import sys
import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk

from bridge import app, config, service, win32
from bridge.config import Relay, Settings

REFRESH_MS = 1000
STATES = {
    "connected": "Connected to the relay",
    "connecting": "Connecting…",
    "retrying": "Can't reach the relay",
    "unconfigured": "No relay set",
    "off": "Off",
}


def open_window() -> None:
    if not win32.is_admin():  # ask once, up front, rather than failing at Save
        arguments = subprocess.list2cmdline(service.command("settings")[1:])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, arguments, None, 1)
        return
    Window().mainloop()


class Window(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Archangel bridge")
        self.resizable(False, False)
        self._store = win32.settings_store(config.folder() / "bridge.json")
        self._settings = self._store.load()
        self._mode = tk.StringVar(value=self._settings.mode)
        self._url, self._token = tk.StringVar(), tk.StringVar()
        self._device = tk.StringVar(value=self._settings.device)
        self._secret = tk.StringVar(value=self._settings.secret)
        self._status = tk.StringVar()
        self._show_relay()

        frame = ttk.Frame(self, padding=16)
        frame.grid()
        row = 0

        def line(label: str, widget: tk.Widget, hint: str = "") -> None:
            nonlocal row
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
            widget.grid(row=row, column=1, sticky="we", pady=4)
            row += 1
            if hint:
                ttk.Label(frame, text=hint, foreground="#666").grid(row=row, column=1, sticky="w")
                row += 1

        ttk.Label(frame, textvariable=self._status, font=("Segoe UI", 11, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )
        row += 1
        modes = ttk.Frame(frame)
        for value, text in (("prod", "Prod"), ("dev", "Dev")):
            ttk.Radiobutton(modes, text=text, value=value, variable=self._mode, command=self._switch).pack(
                side="left", padx=(0, 12)
            )
        line("Relay", modes)
        line("URL", ttk.Entry(frame, textvariable=self._url, width=48), "wss://…/ws/device")
        line("Token", ttk.Entry(frame, textvariable=self._token, width=48, show="•"))
        line("This PC's name", ttk.Entry(frame, textvariable=self._device, width=48))
        line(
            "Lock screen PIN",
            ttk.Entry(frame, textvariable=self._secret, width=48, show="•"),
            "Or password. Kept on this PC, never sent. Blank: it won't unlock.",
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Stop and remove", command=self._remove).pack(side="right")
        ttk.Button(buttons, text="Save and start", command=self._save).pack(side="right", padx=(0, 8))
        self._refresh()

    def _show_relay(self) -> None:
        self._url.set(self._settings.relay.url)
        self._token.set(self._settings.relay.token)

    def _switch(self) -> None:
        """Keep what's typed for the relay being left, then show the other."""
        self._settings = replace(self._current(), mode=self._mode.get())
        self._show_relay()

    def _current(self) -> Settings:
        """The settings as typed, for the relay whose fields are showing."""
        relay = Relay(self._url.get().strip(), self._token.get().strip())
        device = self._device.get().strip() or config.default_device()
        return replace(self._settings.with_relay(relay), device=device, secret=self._secret.get())

    def _save(self) -> None:
        settings = self._current()
        if settings.relay.url and not settings.relay.url.startswith(("ws://", "wss://")):
            messagebox.showerror("Archangel bridge", "The relay's URL starts with wss:// (or ws:// for a local one).")
            return
        try:
            service.secure(config.folder())
            self._store.save(settings)
            self._settings = settings
            service.install()
        except Exception as error:
            messagebox.showerror("Archangel bridge", f"Couldn't start it: {error}")

    def _remove(self) -> None:
        if not messagebox.askyesno("Archangel bridge", "Stop the bridge and remove its service? Settings are kept."):
            return
        try:
            service.uninstall()
        except Exception as error:
            messagebox.showerror("Archangel bridge", f"Couldn't remove it: {error}")

    def _refresh(self) -> None:
        running = service.state()
        if running == "running":
            status = app.read_status(config.folder() / "status.json")
            text = STATES.get(status.state, status.state) if status else "Starting…"
            if status and status.reason and status.state == "retrying":
                text += f": {status.reason}"
        else:
            text = {"not installed": "Not installed: Save to start", "stopped": "Stopped"}.get(running, running.title())
        self._status.set(text)
        self.after(REFRESH_MS, self._refresh)
