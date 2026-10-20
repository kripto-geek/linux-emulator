"""
Polished gaming-emulator GUI for Lemu (MEmu Play Linux Reconstruction).
Omarchy / Catppuccin Dark theme. Multi-instance manager + per-instance
control overlay with the touch keymapper, macros, fullscreen, screenshots, APK
install, ADB shell — modeled after Windows gaming emulators (MEmu/BlueStacks).

Key handling comes in two flavours, and the difference matters for gaming:

* the **touch keymapper** (:mod:`lemu.keymap`) turns host keys into Android touch
  *gestures*, which is the only thing a touch-only game reacts to; and
* the **key-event buttons** (F1-F10 → HOME/BACK/MENU/VOLUME/POWER) which inject
  Android keycodes for navigation and system actions.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from lemu.manager import LemuManager
from lemu.vm import LemuVM
from lemu.adb import LemuADB
from lemu.config import load_instance_config, save_instance_config
from lemu.keymap import (
    DEFAULT_BINDINGS,
    DEFAULT_PROFILES,
    KeymapperController,
    load_keymap,
    keymap_path,
    save_keymap,
)

# ── Palette: Catppuccin Mocha (Omarchy) ──────────────────────────────
BG_DARK = "#1e1e2e"
BG_CARD = "#313244"
BG_CARD_HOVER = "#45475a"
FG_LIGHT = "#cdd6f4"
FG_MUTED = "#a6adc8"
ACCENT_BLUE = "#89b4fa"
ACCENT_GREEN = "#a6e3a1"
ACCENT_RED = "#f38ba8"
ACCENT_YELLOW = "#f9e2af"
ACCENT_PINK = "#f5c2e7"
BORDER = "#45475a"

# Android keycode labels for the key-event picker
KEY_LABELS = {
    3: "HOME", 4: "BACK", 19: "DPAD_UP", 20: "DPAD_DOWN",
    21: "DPAD_LEFT", 22: "DPAD_RIGHT", 23: "ENTER", 24: "VOLUME_UP",
    25: "VOLUME_DOWN", 26: "POWER", 27: "CAMERA", 82: "MENU", 84: "SEARCH",
    164: "MEDIA_NEXT", 165: "MEDIA_PREV",
}


# ── One keymapper daemon per instance, shared by every window ─────────
_KM_CONTROLLERS: dict[int, KeymapperController] = {}
_KM_LISTENERS: dict[int, list] = {}
_KM_EVENTS: "queue.Queue[tuple[int, str, object]]" = queue.Queue()


def _km_emit(vmid: int, kind: str, payload) -> None:
    """Queue a keymapper event for the Tk thread.

    The daemon calls this from its own thread, and Tk widgets may only be
    touched from the one running the event loop, so events are queued and
    drained by :func:`pump_keymapper_events` instead of calling ``after()``
    off-thread (which silently fails outside ``mainloop()``).
    """
    _KM_EVENTS.put((vmid, kind, payload))


def pump_keymapper_events() -> None:
    """Deliver queued keymapper events on the Tk thread."""
    while True:
        try:
            vmid, kind, payload = _KM_EVENTS.get_nowait()
        except queue.Empty:
            return
        for listener in list(_KM_LISTENERS.get(vmid, [])):
            try:
                listener(kind, payload)
            except Exception:                                   # noqa: BLE001
                pass


def keymapper_controller(vmid: int) -> KeymapperController:
    """The shared start/stop handle for an instance's keymapper."""
    controller = _KM_CONTROLLERS.get(vmid)
    if controller is None:
        controller = KeymapperController(
            vmid,
            log=lambda message: _km_emit(vmid, "log", message),
            on_state=lambda running, rc: _km_emit(vmid, "state", (running, rc)),
        )
        _KM_CONTROLLERS[vmid] = controller
    return controller


def listen_keymapper(vmid: int, listener) -> None:
    _KM_LISTENERS.setdefault(vmid, []).append(listener)


def unlisten_keymapper(vmid: int, listener) -> None:
    listeners = _KM_LISTENERS.get(vmid, [])
    if listener in listeners:
        listeners.remove(listener)


def _fmt_point(point) -> str:
    if isinstance(point, (list, tuple)) and len(point) == 2:
        return f"({point[0]:g}, {point[1]:g})"
    return str(point)


def describe_action(action) -> str:
    """Human-readable one-liner for a binding (``None`` means un-bound)."""
    if action is None:
        return "(unbound)"
    kind = (action or {}).get("action")
    if kind == "swipe":
        return (f"swipe {_fmt_point(action.get('from'))} → {_fmt_point(action.get('to'))} "
                f"({action.get('duration_ms', 140)} ms)")
    if kind == "tap":
        return f"tap {_fmt_point(action.get('at'))}"
    if kind == "hold":
        return f"hold {_fmt_point(action.get('at'))} ({action.get('duration_ms', 600)} ms)"
    if kind == "keyevent":
        code = action.get("code")
        return f"key event {code} ({KEY_LABELS.get(code, 'custom')})"
    return str(kind)


class GestureDialog(tk.Toplevel):
    """Small editor for one swipe / tap / hold binding."""

    def __init__(self, parent, key: str):
        super().__init__(parent)
        self.title(f"Gesture for {key}")
        self.configure(bg=BG_DARK)
        self.transient(parent)
        self.grab_set()
        self.result: dict | None = None

        self.kind = tk.StringVar(value="swipe")
        self.fields = {}

        tk.Label(self, text=f"KEY: {key}", fg=ACCENT_BLUE, bg=BG_DARK,
                 font=("Sans", 11, "bold")).pack(pady=(12, 4))

        modes = tk.Frame(self, bg=BG_DARK)
        modes.pack(pady=4)
        for value, label in (("swipe", "Swipe"), ("tap", "Tap"), ("hold", "Hold")):
            tk.Radiobutton(modes, text=label, value=value, variable=self.kind,
                           command=self._sync, bg=BG_DARK, fg=FG_LIGHT,
                           selectcolor=BG_CARD, activebackground=BG_DARK,
                           activeforeground=FG_LIGHT, font=("Sans", 9),
                           highlightthickness=0).pack(side=tk.LEFT, padx=6)

        form = tk.Frame(self, bg=BG_CARD, padx=16, pady=12)
        form.pack(fill=tk.X, padx=16, pady=10)

        def row(label, initial):
            holder = tk.Frame(form, bg=BG_CARD)
            holder.pack(fill=tk.X, pady=3)
            tk.Label(holder, text=label, fg=FG_LIGHT, bg=BG_CARD, width=14,
                     anchor=tk.W, font=("Sans", 9)).pack(side=tk.LEFT)
            entry = tk.Entry(holder, bg=BG_DARK, fg=FG_LIGHT, relief="flat",
                             insertbackground=FG_LIGHT, font=("Sans", 9), width=20)
            entry.insert(0, initial)
            entry.pack(side=tk.LEFT, padx=6)
            return entry

        self.fields["from"] = row("From (x, y):", "0.78, 0.55")
        self.fields["to"] = row("To (x, y):", "0.18, 0.55")
        self.fields["at"] = row("At (x, y):", "0.50, 0.50")
        self.fields["duration_ms"] = row("Duration (ms):", "140")

        tk.Label(self, text="Coordinates are fractions (0-1) of the game screen.",
                 fg=FG_MUTED, bg=BG_DARK, font=("Sans", 8)).pack()

        buttons = tk.Frame(self, bg=BG_DARK, pady=10)
        buttons.pack(fill=tk.X)
        tk.Button(buttons, text="Cancel", command=self.destroy, bg=BG_CARD, fg=FG_LIGHT,
                  relief="flat", padx=14, pady=5, cursor="hand2", bd=0).pack(side=tk.RIGHT, padx=12)
        tk.Button(buttons, text="OK", command=self._ok, bg=ACCENT_GREEN, fg="#11111b",
                  font=("Sans", 9, "bold"), relief="flat", padx=14, pady=5,
                  cursor="hand2", bd=0).pack(side=tk.RIGHT)
        self._sync()
        self.wait_window(self)

    def _sync(self) -> None:
        kind = self.kind.get()
        state = tk.NORMAL if kind != "tap" else tk.DISABLED
        self.fields["from"].config(state=state)
        self.fields["to"].config(state=state)
        self.fields["at"].config(state=tk.DISABLED if kind != "tap" else tk.NORMAL)

    @staticmethod
    def _point(text: str) -> list[float] | None:
        try:
            x, y = [float(part) for part in text.replace(";", ",").split(",")[:2]]
        except ValueError:
            return None
        return [min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)]

    def _ok(self) -> None:
        kind = self.kind.get()
        try:
            duration = int(float(self.fields["duration_ms"].get()))
        except ValueError:
            messagebox.showerror("Invalid", "Duration must be a number.", parent=self)
            return
        if kind == "tap":
            at = self._point(self.fields["at"].get())
            if at is None:
                messagebox.showerror("Invalid", "At must look like 0.5, 0.5", parent=self)
                return
            self.result = {"action": "tap", "at": at}
        else:
            frm = self._point(self.fields["from"].get())
            to = self._point(self.fields["to"].get())
            if frm is None or to is None:
                messagebox.showerror("Invalid", "From/To must look like 0.5, 0.5", parent=self)
                return
            self.result = ({"action": "swipe", "from": frm, "to": to, "duration_ms": duration}
                           if kind == "swipe" else
                           {"action": "hold", "at": frm, "duration_ms": duration})
        self.destroy()


class JsonEditorDialog(tk.Toplevel):
    """Raw editor for keymap.json - the escape hatch for full profile control."""

    def __init__(self, parent, vmid: int, on_saved):
        super().__init__(parent)
        self.vmid = vmid
        self.on_saved = on_saved
        self.title(f"keymap.json - Instance {vmid}")
        self.geometry("720x560")
        self.configure(bg=BG_DARK)
        self.transient(parent)

        tk.Label(self, text=str(keymap_path(vmid)), fg=FG_MUTED, bg=BG_DARK,
                 font=("Sans", 8)).pack(anchor=tk.W, padx=12, pady=(10, 4))
        self.text = tk.Text(self, bg=BG_CARD, fg=FG_LIGHT, insertbackground=FG_LIGHT,
                            relief="flat", font=("Mono", 9), wrap=tk.NONE)
        self.text.pack(fill=tk.BOTH, expand=True, padx=12)
        self.text.insert("1.0", json.dumps(load_keymap(vmid), indent=2) + "\n")

        row = tk.Frame(self, bg=BG_DARK, pady=10)
        row.pack(fill=tk.X)
        tk.Button(row, text="Save", command=self._save, bg=ACCENT_GREEN, fg="#11111b",
                  font=("Sans", 9, "bold"), relief="flat", padx=14, pady=5,
                  cursor="hand2", bd=0).pack(side=tk.RIGHT, padx=12)
        tk.Button(row, text="Close", command=self.destroy, bg=BG_CARD, fg=FG_LIGHT,
                  relief="flat", padx=14, pady=5, cursor="hand2", bd=0).pack(side=tk.RIGHT)

    def _save(self) -> None:
        try:
            data = json.loads(self.text.get("1.0", tk.END))
        except ValueError as exc:
            messagebox.showerror("Invalid JSON", str(exc), parent=self)
            return
        if not isinstance(data, dict) or not isinstance(data.get("bindings"), dict):
            messagebox.showerror("Invalid Keymap",
                                 "Top level must be an object with a 'bindings' map.", parent=self)
            return
        save_keymap(self.vmid, data)
        self.on_saved(data)
        self.destroy()


class KeymapDialog(tk.Toplevel):
    """The touch keymapper: bindings, per-game profiles, and the daemon itself."""

    DEFAULT_TARGET = "default (all games)"

    def __init__(self, parent, vmid: int, controller: KeymapperController | None = None):
        super().__init__(parent)
        self.vmid = vmid
        self.controller = controller or keymapper_controller(vmid)
        self.title(f"Keymapper - Instance {vmid}")
        self.geometry("880x640")
        self.minsize(760, 520)
        self.configure(bg=BG_DARK)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self.data = load_keymap(vmid)
        self.target = self.DEFAULT_TARGET
        self.injector_var = tk.StringVar(value="auto")
        self._build_ui()
        self._refresh()
        listen_keymapper(vmid, self._on_keymapper_event)
        self._sync_controls()

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        header = tk.Frame(self, bg=BG_DARK, pady=10)
        header.pack(fill=tk.X)
        tk.Label(header, text="TOUCH KEYMAPPER", fg=ACCENT_BLUE, bg=BG_DARK,
                 font=("Sans", 11, "bold")).pack()
        tk.Label(header,  text="Host keys become Android touch gestures - the only input "
                               "touch-only games react to.",
                 fg=FG_MUTED, bg=BG_DARK, font=("Sans", 8)).pack()

        # --- scheme row
        scheme = tk.Frame(self, bg=BG_DARK, padx=16)
        scheme.pack(fill=tk.X, pady=(4, 6))
        tk.Label(scheme, text="Scheme:", fg=FG_LIGHT, bg=BG_DARK,
                 font=("Sans", 9, "bold")).pack(side=tk.LEFT)
        self.scheme_var = tk.StringVar(value=self.DEFAULT_TARGET)
        self.scheme_box = ttk.Combobox(scheme, textvariable=self.scheme_var, state="readonly",
                                       width=34, values=[self.DEFAULT_TARGET])
        self.scheme_box.pack(side=tk.LEFT, padx=8)
        self.scheme_box.bind("<<ComboboxSelected>>", lambda e: self._on_scheme_change())
        self.active_lbl = tk.Label(scheme, text="", fg=ACCENT_YELLOW, bg=BG_DARK,
                                   font=("Sans", 8))
        self.active_lbl.pack(side=tk.LEFT, padx=10)

        # --- bindings table
        container = tk.Frame(self, bg=BG_DARK, padx=16)
        container.pack(fill=tk.BOTH, expand=True)
        cols = ("key", "action")
        self.tree = ttk.Treeview(container, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("key", text="Key")
        self.tree.heading("action", text="Gesture")
        self.tree.column("key", width=150, anchor=tk.W)
        self.tree.column("action", width=460, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        tk.Label(self, text="Profiles are merged over the default scheme while their game is "
                            "focused; remove a key in a profile to un-bind it there.",
                 fg=FG_MUTED, bg=BG_DARK, font=("Sans", 8), anchor=tk.W,
                 padx=16).pack(fill=tk.X, pady=(4, 6))

        # --- binding actions
        bind_row = tk.Frame(self, bg=BG_DARK, pady=2)
        bind_row.pack(fill=tk.X, padx=12)

        def bbtn(text, cmd, col, fg="#11111b"):
            return tk.Button(bind_row, text=text, command=cmd, bg=col, fg=fg,
                             font=("Sans", 9, "bold"), relief="flat", padx=12, pady=5,
                             cursor="hand2", bd=0)

        bbtn("Add Gesture…", self.on_add_gesture, ACCENT_BLUE).pack(side=tk.LEFT, padx=4)
        bbtn("Add Key Event…", self.on_add_keyevent, ACCENT_PINK).pack(side=tk.LEFT, padx=4)
        bbtn("Remove", self.on_remove, ACCENT_RED).pack(side=tk.LEFT, padx=4)
        bbtn("Reset Scheme", self.on_reset, ACCENT_YELLOW).pack(side=tk.LEFT, padx=4)
        bbtn("Edit JSON…", self.on_edit_json, BG_CARD, FG_LIGHT).pack(side=tk.LEFT, padx=4)

        # --- daemon row
        daemon = tk.Frame(self, bg=BG_CARD, padx=12, pady=8)
        daemon.pack(fill=tk.X, padx=12, pady=(10, 6))
        tk.Label(daemon, text="Injector:", fg=FG_LIGHT, bg=BG_CARD,
                 font=("Sans", 9, "bold")).pack(side=tk.LEFT)
        self.injector_box = ttk.Combobox(daemon, textvariable=self.injector_var, state="readonly",
                                         width=8, values=["auto", "touch", "input"])
        self.injector_box.pack(side=tk.LEFT, padx=8)
        self.toggle_btn = tk.Button(daemon, text="Start keymapper", command=self.on_toggle,
                                    bg=ACCENT_GREEN, fg="#11111b", font=("Sans", 9, "bold"),
                                    relief="flat", padx=14, pady=5, cursor="hand2", bd=0)
        self.toggle_btn.pack(side=tk.LEFT, padx=8)
        self.daemon_lbl = tk.Label(daemon, text="stopped", fg=FG_MUTED, bg=BG_CARD,
                                   font=("Sans", 9))
        self.daemon_lbl.pack(side=tk.LEFT, padx=10)

        self.log = tk.Text(self, height=8, bg="#181825", fg=FG_MUTED, relief="flat",
                           font=("Mono", 8), wrap=tk.NONE)
        self.log.pack(fill=tk.X, padx=12, pady=(0, 12))
        self.log.configure(state=tk.DISABLED)

    # -- model helpers -----------------------------------------------------
    def _bindings(self) -> dict:
        if self.target == self.DEFAULT_TARGET:
            return self.data.setdefault("bindings", {})
        profile = self.data.setdefault("profiles", {}).setdefault(self.target, {})
        return profile.setdefault("bindings", {})

    def _refresh(self) -> None:
        profiles = [name for name in (self.data.get("profiles") or {}) if not name.startswith("_")]
        self.scheme_box.configure(values=[self.DEFAULT_TARGET] + sorted(profiles))
        for item in self.tree.get_children():
            self.tree.delete(item)
        for key, action in sorted(self._bindings().items()):
            self.tree.insert("", tk.END, values=(key, describe_action(action)))

    def _on_scheme_change(self) -> None:
        self.target = self.scheme_var.get()
        self._refresh()

    def _save(self) -> None:
        save_keymap(self.vmid, self.data)

    def _on_keymapper_event(self, kind: str, payload) -> None:
        # Delivered on the Tk thread by the event pump.
        if not self.winfo_exists():
            return
        if kind == "log":
            self._append_log(payload)
        elif kind == "state":
            self._sync_controls(payload[0])

    def _append_log(self, message: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 400:
            self.log.delete("1.0", "2.0")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)
        self._sync_controls()

    def _sync_controls(self, running: bool | None = None) -> None:
        if running is None:
            running = self.controller.running
        self.toggle_btn.configure(
            text="Stop keymapper" if running else "Start keymapper",
            bg=ACCENT_RED if running else ACCENT_GREEN)
        self.injector_box.configure(state=tk.DISABLED if running else "readonly")
        active = self.controller.active_profile
        kind = self.controller.injector_kind
        state = f"running - injector: {kind or '…'}" if running else "stopped"
        if running and active:
            state += f" - scheme: {active}"
        self.daemon_lbl.configure(text=state, fg=ACCENT_GREEN if running else FG_MUTED)

    # -- binding actions ---------------------------------------------------
    def _selected_key(self) -> str | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Select a key", "Pick a binding in the list first.", parent=self)
            return None
        return self.tree.item(selection[0])["values"][0]

    def on_add_gesture(self) -> None:
        key = simpledialog.askstring("Key", "Host key name as the guest reports it\n"
                                            "(e.g. KEY_A, KEY_LEFT, KEY_SPACE):",
                                     parent=self)
        if not key:
            return
        key = key.strip().upper().replace(" ", "_")
        if not key.startswith("KEY_"):
            key = "KEY_" + key
        dialog = GestureDialog(self, key)
        if dialog.result:
            self._bindings()[key] = dialog.result
            self._refresh()
            self._save()

    def on_add_keyevent(self) -> None:
        key = simpledialog.askstring("Key", "Host key name (e.g. KEY_ESC, KEY_P):", parent=self)
        if not key:
            return
        key = key.strip().upper()
        if not key.startswith("KEY_"):
            key = "KEY_" + key
        labels = ", ".join(f"{code}={name}" for code, name in sorted(KEY_LABELS.items()))
        code = simpledialog.askinteger("Android keycode", f"Keycode to send:\n{labels}",
                                       parent=self, minvalue=0, maxvalue=300,
                                       initialvalue=4)
        if code is None:
            return
        self._bindings()[key] = {"action": "keyevent", "code": code}
        self._refresh()
        self._save()

    def on_remove(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        if self.target == self.DEFAULT_TARGET:
            self._bindings().pop(key, None)
        else:
            # An explicit null is what un-binds a key inherited from the default.
            self._bindings()[key] = None
        self._refresh()
        self._save()

    def on_reset(self) -> None:
        if not messagebox.askyesno("Reset", f"Reset '{self.target}' to its defaults?",
                                   parent=self):
            return
        if self.target == self.DEFAULT_TARGET:
            self.data["bindings"] = dict(DEFAULT_BINDINGS)
        else:
            defaults = (DEFAULT_PROFILES.get(self.target) or {}).get("bindings") or {}
            self.data.setdefault("profiles", {})[self.target] = {"bindings": dict(defaults)}
        self._refresh()
        self._save()

    def on_edit_json(self) -> None:
        JsonEditorDialog(self, self.vmid, self._on_json_saved)

    def _on_json_saved(self, data: dict) -> None:
        self.data = data
        self._refresh()

    # -- daemon ------------------------------------------------------------
    def on_toggle(self) -> None:
        if self.controller.running:
            self.controller.stop()
            self._append_log("[gui] keymapper stopped")
            self._sync_controls(False)
            return
        self.controller.injector = self.injector_var.get()
        if self.controller.start():
            self._append_log(f"[gui] starting keymapper (injector: {self.controller.injector})")
        else:
            messagebox.showinfo("Keymapper", "Already running.", parent=self)
        self._sync_controls()

    def destroy(self) -> None:
        unlisten_keymapper(self.vmid, self._on_keymapper_event)
        super().destroy()


class MacroDialog(tk.Toplevel):
    def __init__(self, parent, vmid: int, send_key_cb):
        super().__init__(parent)
        self.vmid = vmid
        self.send_key = send_key_cb
        self.title(f"Macros - Instance {vmid}")
        self.geometry("540x360")
        self.configure(bg=BG_DARK)
        self.transient(parent)
        self.grab_set()

        self.recording = False
        self.macro = []  # list of (keysym, keycode)

        self._build_ui()

    def _build_ui(self):
        header = tk.Frame(self, bg=BG_DARK, pady=10)
        header.pack(fill=tk.X)
        tk.Label(header, text="MACROS", fg=ACCENT_PINK, bg=BG_DARK,
                 font=("Sans", 11, "bold")).pack()

        body = tk.Frame(self, bg=BG_DARK, padx=16, pady=10)
        body.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(body, bg=BG_CARD, fg=FG_LIGHT,
                                  selectbackground=ACCENT_BLUE, selectforeground="#11111b",
                                  font=("Sans", 10), relief="flat", height=10)
        self.listbox.pack(fill=tk.BOTH, expand=True)

        btns = tk.Frame(body, bg=BG_DARK, pady=8)
        btns.pack(fill=tk.X)

        def btn(text, cmd, col):
            return tk.Button(btns, text=text, command=cmd, bg=col, fg="#11111b",
                             font=("Sans", 9, "bold"), relief="flat", padx=12, pady=5,
                             cursor="hand2", bd=0)

        self.btn_rec = btn("Record", self.toggle_record, ACCENT_RED)
        self.btn_rec.pack(side=tk.LEFT, padx=4)
        btn("Play Selected", self.on_play, ACCENT_BLUE).pack(side=tk.LEFT, padx=4)
        btn("Clear", self.on_clear, ACCENT_YELLOW).pack(side=tk.LEFT, padx=4)

        self.status = tk.Label(self, text="Ready.", fg=FG_MUTED, bg=BG_CARD,
                               font=("Sans", 9), anchor=tk.W, padx=12, pady=4)
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

    def toggle_record(self):
        self.recording = not self.recording
        self.macro = []
        self.btn_rec.config(text="Stop" if self.recording else "Record",
                            bg=ACCENT_GREEN if self.recording else ACCENT_RED)
        self.status.config(text="Recording... press keys, then click Stop" if self.recording else "Ready.")

    def add_key(self, keysym: str, keycode: int):
        if not self.recording:
            return
        self.macro.append((keysym, keycode))
        self.listbox.insert(tk.END, f"{keysym} → KEYCODE_{keycode}")
        self.status.config(text=f"Recorded {len(self.macro)} step(s)")

    def on_play(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        _, keycode = self.macro[sel[0]]
        self.send_key(keycode)

    def on_clear(self):
        self.listbox.delete(0, tk.END)
        self.macro = []


class VMWindow(tk.Toplevel):
    """Per-instance control overlay: fullscreen, keymapper, macros, shell, screenshots."""
    def __init__(self, parent, vmid: int):
        super().__init__(parent)
        self.vmid = vmid
        self.cfg = load_instance_config(vmid)
        self.title(f"Lemu - Instance {vmid} - {self.cfg.get('name', 'MEmu')}")
        self.geometry("940x700")
        self.configure(bg=BG_DARK)
        self.transient(parent)

        self.fullscreen = False
        self.macro_dlg = None
        self.keymapper = keymapper_controller(vmid)

        self.build_ui()
        self.bind_keys()
        self._km_listener = lambda kind, payload: self._on_keymapper_event(kind, payload)
        listen_keymapper(vmid, self._km_listener)
        self.sync_keymapper()
        self.focus_set()

    def build_ui(self):
        bar = tk.Frame(self, bg=BG_CARD, padx=10, pady=6)
        bar.pack(fill=tk.X)
        self.status_lbl = tk.Label(bar, text="Instance running", fg=ACCENT_GREEN, bg=BG_CARD,
                                   font=("Sans", 10, "bold"))
        self.status_lbl.pack(side=tk.LEFT)
        tk.Label(bar, text="  ·  F11 Fullscreen  ·  F12 Screenshot  ·  F1-F10 System keys",
                 fg=FG_MUTED, bg=BG_CARD, font=("Sans", 9)).pack(side=tk.LEFT, padx=10)

        body = tk.Frame(self, bg=BG_DARK, padx=20, pady=20)
        body.pack(fill=tk.BOTH, expand=True)

        tk.Label(body, text=f"CONTROL OVERLAY — INSTANCE {self.vmid}", fg=ACCENT_BLUE,
                 bg=BG_DARK, font=("Sans", 13, "bold")).pack(pady=(0, 16))

        info = tk.Frame(body, bg=BG_CARD, padx=16, pady=16)
        info.pack(fill=tk.X)
        tk.Label(info, text=f"Name: {self.cfg.get('name', 'MEmu')}", fg=FG_LIGHT, bg=BG_CARD,
                 font=("Sans", 10)).pack(anchor=tk.W)
        tk.Label(info, text=f"ADB Port: {self.cfg.get('adb_port', 21503 + self.vmid * 10)}",
                 fg=FG_LIGHT, bg=BG_CARD, font=("Sans", 10)).pack(anchor=tk.W)
        tk.Label(info, text=f"CPUs: {self.cfg.get('cpus', 4)}  ·  RAM: {self.cfg.get('memory_mb', 4096)} MB",
                 fg=FG_LIGHT, bg=BG_CARD, font=("Sans", 10)).pack(anchor=tk.W)

        actions = tk.Frame(body, bg=BG_DARK, pady=16)
        actions.pack(fill=tk.X)

        def abtn(text, cmd, col):
            tk.Button(actions, text=text, command=cmd, bg=col, fg="#11111b",
                      font=("Sans", 9, "bold"), relief="flat", padx=14, pady=7,
                      cursor="hand2", bd=0).pack(side=tk.LEFT, padx=6)

        abtn("Fullscreen (F11)", self.toggle_fullscreen, ACCENT_BLUE)
        abtn("Screenshot (F12)", self.take_screenshot, ACCENT_PINK)
        abtn("Keymapper…", self.open_keybindings, ACCENT_YELLOW)
        abtn("Macros", self.open_macros, ACCENT_YELLOW)
        abtn("ADB Shell", self.open_shell, BG_CARD)
        abtn("Volume +", lambda: self.send_key(24), BG_CARD)
        abtn("Volume −", lambda: self.send_key(25), BG_CARD)
        abtn("Power", lambda: self.send_key(26), ACCENT_RED)

        # --- touch keymapper: the part that actually drives touch-only games
        km = tk.Frame(body, bg=BG_CARD, padx=16, pady=12)
        km.pack(fill=tk.X, pady=(4, 0))
        tk.Label(km, text="TOUCH KEYMAPPER", fg=ACCENT_BLUE, bg=BG_CARD,
                 font=("Sans", 9, "bold")).pack(side=tk.LEFT)
        tk.Label(km, text="injector", fg=FG_MUTED, bg=BG_CARD,
                 font=("Sans", 8)).pack(side=tk.LEFT, padx=(12, 4))
        self.km_injector = tk.StringVar(value=self.keymapper.injector)
        self.km_injector_box = ttk.Combobox(km, textvariable=self.km_injector, state="readonly",
                                            width=7, values=["auto", "touch", "input"])
        self.km_injector_box.pack(side=tk.LEFT)
        self.km_toggle = tk.Button(km, text="Start", command=self.toggle_keymapper, bg=ACCENT_GREEN,
                                   fg="#11111b", font=("Sans", 9, "bold"), relief="flat",
                                   padx=14, pady=5, cursor="hand2", bd=0)
        self.km_toggle.pack(side=tk.LEFT, padx=10)
        self.km_status = tk.Label(km, text="stopped", fg=FG_MUTED, bg=BG_CARD,
                                  font=("Sans", 9))
        self.km_status.pack(side=tk.LEFT)
        tk.Button(km, text="Edit bindings…", command=self.open_keybindings, bg=BG_CARD_HOVER,
                  fg=FG_LIGHT, font=("Sans", 9), relief="flat", padx=12, pady=5,
                  cursor="hand2", bd=0).pack(side=tk.RIGHT)

        tk.Label(body,
                 text="Start the keymapper, then click the emulator window and play. "
                      "It reads the guest's own keyboard, so host keys become real touch gestures.",
                 fg=FG_MUTED, bg=BG_DARK, font=("Sans", 9), wraplength=860,
                 justify=tk.LEFT).pack(side=tk.BOTTOM, pady=10)

    # -- keymapper ---------------------------------------------------------
    def _on_keymapper_event(self, kind: str, payload) -> None:
        # Both kinds matter: "state" flips the button, "log" carries the profile
        # the daemon switched to, which the status line shows.  Called on the Tk
        # thread by the event pump, so widgets can be touched directly.
        if self.winfo_exists():
            self.sync_keymapper()

    def sync_keymapper(self) -> None:
        running = self.keymapper.running
        self.km_toggle.configure(text="Stop" if running else "Start",
                                 bg=ACCENT_RED if running else ACCENT_GREEN)
        self.km_injector_box.configure(state=tk.DISABLED if running else "readonly")
        if running:
            kind = self.keymapper.injector_kind or "…"
            scheme = self.keymapper.active_profile or "default"
            self.km_status.configure(text=f"running · {kind} · {scheme}", fg=ACCENT_GREEN)
        else:
            self.km_status.configure(text="stopped", fg=FG_MUTED)

    def toggle_keymapper(self) -> None:
        if self.keymapper.running:
            self.keymapper.stop()
        else:
            self.keymapper.injector = self.km_injector.get()
            self.keymapper.start()
        self.after(150, self.sync_keymapper)

    def bind_keys(self):
        for ks, kc in [("w", 19), ("s", 20), ("a", 21), ("d", 22),
                       ("Up", 19), ("Down", 20), ("Left", 21), ("Right", 22),
                       ("space", 23), ("Return", 23), ("Escape", 4), ("BackSpace", 4)]:
            self.bind(f"<{ks}>", lambda e, kc=kc: self.on_key(kc))
        for fk, kc in [("F1", 3), ("F2", 4), ("F3", 82), ("F4", 25), ("F5", 24),
                       ("F6", 164), ("F7", 165), ("F8", 26), ("F9", 27), ("F10", 84)]:
            self.bind(f"<{fk}>", lambda e, kc=kc: self.on_key(kc))
        self.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.bind("<F12>", lambda e: self.take_screenshot())

    def on_key(self, keycode: int):
        self.send_key(keycode)
        return "break"

    def send_key(self, keycode: int):
        res = LemuADB(self.vmid).run_adb("shell", "input", "keyevent", str(keycode))
        if res.returncode == 0:
            self.status_lbl.config(text=f"Sent keyevent {keycode}", fg=ACCENT_GREEN)
        else:
            self.status_lbl.config(text="ADB not ready (instance still booting)", fg=ACCENT_RED)

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.attributes("-fullscreen", self.fullscreen)
        self.status_lbl.config(text="Fullscreen ON" if self.fullscreen else "Windowed", fg=ACCENT_GREEN)

    def take_screenshot(self):
        import time
        vm = LemuVM(self.vmid)
        path = str(Path(BASE_DIR) / "instances" / str(self.vmid) /
                   f"screenshot_{int(time.time())}.png")
        if vm.screendump(path):
            messagebox.showinfo("Screenshot", f"Saved: {path}")
            self.status_lbl.config(text="Screenshot saved", fg=ACCENT_GREEN)
        else:
            messagebox.showerror("Screenshot", "Failed (VM not running)")

    def open_keybindings(self):
        KeymapDialog(self, self.vmid, self.keymapper)

    def open_macros(self):
        if self.macro_dlg is None or not self.macro_dlg.winfo_exists():
            self.macro_dlg = MacroDialog(self, self.vmid, self.send_key)
        else:
            self.macro_dlg.lift()

    def open_shell(self):
        adb = LemuADB(self.vmid)
        subprocess.Popen([sys.executable, "-c",
                          f"import os; os.system('adb -s {adb.target} shell')"])

    def destroy(self):
        unlisten_keymapper(self.vmid, self._km_listener)
        super().destroy()


class LemuApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Lemu - MEmu Play for Linux (Gaming Emulator)")
        self.geometry("1150x700")
        self.minsize(900, 560)
        self.configure(bg=BG_DARK)

        self.selected_vmid = None
        self.vm_windows = {}

        self.setup_styles()
        self.build_ui()
        self.refresh_instances()

        self.bind_all("<F11>", lambda e: self.fullscreen_selected())
        self.bind_all("<F12>", lambda e: self.screenshot_selected())

        self.after(3000, self.auto_refresh)
        self.after(120, self._pump_keymapper)

    def _pump_keymapper(self):
        """Deliver keymapper daemon events on the Tk thread."""
        pump_keymapper_events()
        self.after(120, self._pump_keymapper)

    def setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG_DARK, foreground=FG_LIGHT, font=("Sans", 10))
        style.configure("TFrame", background=BG_DARK)
        style.configure("Treeview", background=BG_CARD, foreground=FG_LIGHT,
                        fieldbackground=BG_CARD, rowheight=34, font=("Sans", 10), borderwidth=0)
        style.configure("Treeview.Heading", background=BORDER, foreground=FG_LIGHT,
                        font=("Sans", 10, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", ACCENT_BLUE)],
                  foreground=[("selected", "#11111b")])
        style.map("Treeview.Heading", background=[("active", BG_CARD_HOVER)])
        # ttk's default combobox is white-on-white with this palette: restyle both
        # the closed field and the dropdown list so the text stays readable.
        style.configure("TCombobox", fieldbackground=BG_CARD, background=BG_CARD,
                        foreground=FG_LIGHT, arrowcolor=FG_LIGHT, bordercolor=BORDER,
                        lightcolor=BG_CARD, darkcolor=BG_CARD,
                        selectbackground=ACCENT_BLUE, selectforeground="#11111b",
                        padding=3)
        style.map("TCombobox",
                  fieldbackground=[("readonly", BG_CARD), ("disabled", BG_DARK)],
                  foreground=[("readonly", FG_LIGHT), ("disabled", FG_MUTED)],
                  background=[("readonly", BG_CARD), ("active", BG_CARD_HOVER)],
                  arrowcolor=[("disabled", FG_MUTED)])
        self.option_add("*TCombobox*Listbox.background", BG_CARD)
        self.option_add("*TCombobox*Listbox.foreground", FG_LIGHT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT_BLUE)
        self.option_add("*TCombobox*Listbox.selectForeground", "#11111b")
        self.option_add("*TCombobox*Listbox.font", ("Sans", 9))

    def build_ui(self):
        header = tk.Frame(self, bg=BG_DARK, pady=12, padx=16)
        header.pack(fill=tk.X)
        tk.Label(header, text="LEMU", fg=ACCENT_BLUE, bg=BG_DARK,
                 font=("Sans", 17, "bold")).pack(side=tk.LEFT)
        tk.Label(header, text="MEmu Play reconstructed for Linux  ·  KVM gaming emulator",
                 fg=FG_MUTED, bg=BG_DARK, font=("Sans", 9)).pack(side=tk.LEFT, padx=12, pady=4)

        toolbar = tk.Frame(self, bg=BG_DARK, padx=16, pady=6)
        toolbar.pack(fill=tk.X)

        def btn(text, cmd, col, fg="#11111b"):
            return tk.Button(toolbar, text=text, command=cmd, bg=col, fg=fg,
                             activebackground=col, activeforeground=fg,
                             font=("Sans", 9, "bold"), relief="flat", padx=12, pady=6,
                             cursor="hand2", bd=0)

        self.btn_start = btn("▶ Start", self.on_start, ACCENT_GREEN); self.btn_start.pack(side=tk.LEFT, padx=4)
        self.btn_stop = btn("⏹ Stop", self.on_stop, ACCENT_RED); self.btn_stop.pack(side=tk.LEFT, padx=4)
        self.btn_fs = btn("⛶ Fullscreen", self.fullscreen_selected, ACCENT_BLUE); self.btn_fs.pack(side=tk.LEFT, padx=4)
        self.btn_ss = btn("📸 Screenshot", self.screenshot_selected, ACCENT_PINK); self.btn_ss.pack(side=tk.LEFT, padx=4)
        self.btn_keys = btn("⌨ Keymapper", self.on_keybindings, ACCENT_YELLOW); self.btn_keys.pack(side=tk.LEFT, padx=4)
        self.btn_macro = btn("🎹 Macros", self.on_macro, ACCENT_YELLOW); self.btn_macro.pack(side=tk.LEFT, padx=4)
        self.btn_clone = btn("📋 Clone", self.on_clone, ACCENT_BLUE); self.btn_clone.pack(side=tk.LEFT, padx=4)
        self.btn_new = btn("➕ New", self.on_new, BG_CARD, FG_LIGHT); self.btn_new.pack(side=tk.LEFT, padx=4)
        self.btn_apk = btn("📲 Install APK", self.on_install_apk, BG_CARD, FG_LIGHT); self.btn_apk.pack(side=tk.LEFT, padx=4)
        self.btn_shell = btn("💻 Shell", self.on_shell, BG_CARD, FG_LIGHT); self.btn_shell.pack(side=tk.LEFT, padx=4)
        self.btn_settings = btn("⚙ Settings", self.on_settings, BG_CARD, FG_LIGHT); self.btn_settings.pack(side=tk.LEFT, padx=4)
        self.btn_del = btn("🗑 Delete", self.on_delete, ACCENT_RED); self.btn_del.pack(side=tk.RIGHT, padx=4)

        container = tk.Frame(self, bg=BG_DARK, padx=16, pady=10)
        container.pack(fill=tk.BOTH, expand=True)

        cols = ("id", "name", "status", "pid", "port", "cores", "ram", "display")
        self.tree = ttk.Treeview(container, columns=cols, show="headings", selectmode="browse")
        headers = {"id": "ID", "name": "Name", "status": "Status", "pid": "PID",
                   "port": "ADB Port", "cores": "CPU", "ram": "RAM", "display": "Disp"}
        widths = {"id": 40, "name": 170, "status": 100, "pid": 60, "port": 70,
                  "cores": 60, "ram": 80, "display": 70}
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c],
                             anchor=tk.CENTER if c in ("id", "status", "pid", "port", "cores", "ram", "display") else tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())
        self.tree.bind("<Double-1>", lambda e: self.on_start())

        self.status_var = tk.StringVar(value="Ready.")
        status_bar = tk.Label(self, textvariable=self.status_var, font=("Sans", 9),
                              fg=FG_MUTED, bg=BG_CARD, anchor=tk.W, padx=12, pady=4)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def get_selected_vmid(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select Instance", "Select an instance first.")
            return None
        return int(self.tree.item(sel[0])["values"][0])

    def on_select(self):
        sel = self.tree.selection()
        self.selected_vmid = int(self.tree.item(sel[0])["values"][0]) if sel else None

    def refresh_instances(self):
        prev = self.selected_vmid
        for item in self.tree.get_children():
            self.tree.delete(item)
        for inst in LemuManager.list_instances():
            status_text = "🟢 Running" if inst["running"] else "⚪ Stopped"
            pid_text = str(inst["pid"]) if inst["pid"] else "-"
            iid = self.tree.insert("", tk.END, values=(
                inst["vmid"], inst["name"], status_text, pid_text,
                inst["adb_port"], inst["cpus"], f"{inst['memory_mb']} MB", inst["display"]))
            if prev == inst["vmid"]:
                self.tree.selection_set(iid)

    def auto_refresh(self):
        self.refresh_instances()
        self.after(3000, self.auto_refresh)

    def fullscreen_selected(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        if vmid in self.vm_windows:
            self.vm_windows[vmid].toggle_fullscreen()
        else:
            self.on_start()

    def screenshot_selected(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        if vmid in self.vm_windows:
            self.vm_windows[vmid].take_screenshot()
        else:
            messagebox.showinfo("Not Running", "Start the instance first.")

    def open_vm_window(self, vmid: int):
        if vmid in self.vm_windows:
            self.vm_windows[vmid].lift()
            return
        win = VMWindow(self, vmid)
        self.vm_windows[vmid] = win
        win.protocol("WM_DELETE_WINDOW", lambda: self.on_vm_close(vmid, win))

    def on_vm_close(self, vmid: int, win: VMWindow):
        win.destroy()
        self.vm_windows.pop(vmid, None)

    def on_start(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        self.status_var.set(f"Starting instance {vmid}...")
        vm = LemuVM(vmid)
        if vm.is_running():
            messagebox.showinfo("Already Running", f"Instance {vmid} is already running.")
            return
        ok = vm.start(headless=False)
        self.refresh_instances()
        if ok:
            self.status_var.set(f"Instance {vmid} started.")
            self.open_vm_window(vmid)
        else:
            self.status_var.set(f"Failed to start instance {vmid}.")

    def on_stop(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        self.status_var.set(f"Stopping instance {vmid}...")
        LemuVM(vmid).stop()
        self.refresh_instances()
        self.status_var.set(f"Instance {vmid} stopped.")

    def on_keybindings(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        KeymapDialog(self, vmid, keymapper_controller(vmid))

    def on_macro(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        if vmid not in self.vm_windows:
            messagebox.showinfo("Not Running", "Start the instance first.")
            return
        self.vm_windows[vmid].open_macros()

    def on_clone(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        try:
            new_id = LemuManager.clone_instance(vmid)
            self.refresh_instances()
            self.status_var.set(f"Cloned to instance {new_id}.")
        except Exception as e:
            messagebox.showerror("Clone Failed", str(e))

    def on_new(self):
        new_id = LemuManager.create_instance()
        self.refresh_instances()
        self.status_var.set(f"Created instance {new_id}.")

    def on_delete(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        if not messagebox.askyesno("Confirm Delete", f"Delete instance {vmid}?"):
            return
        LemuManager.delete_instance(vmid)
        self.refresh_instances()
        self.status_var.set(f"Deleted instance {vmid}.")

    def on_install_apk(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        apk = filedialog.askopenfilename(filetypes=[("APK", "*.apk")])
        if not apk:
            return
        vm = LemuVM(vmid)
        if not vm.is_running():
            if not messagebox.askyesno("Offline", "Start instance?"):
                return
            vm.start()
        ok = LemuADB(vmid).install_app(apk)
        messagebox.showinfo("APK Install", "Installed." if ok else "Failed.")
        self.status_var.set("APK installed." if ok else "APK failed.")

    def on_shell(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        if vmid in self.vm_windows:
            self.vm_windows[vmid].open_shell()
        else:
            messagebox.showinfo("Not Running", "Start the instance first.")

    def on_settings(self):
        vmid = self.get_selected_vmid()
        if vmid is None:
            return
        cfg = load_instance_config(vmid)
        dlg = tk.Toplevel(self)
        dlg.title(f"Settings - {vmid}")
        dlg.geometry("420x400")
        dlg.configure(bg=BG_CARD)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(dlg, text=f"Instance {vmid}", fg=ACCENT_BLUE, bg=BG_CARD,
                 font=("Sans", 11, "bold")).pack(pady=12)
        form = tk.Frame(dlg, bg=BG_CARD)
        form.pack(fill=tk.BOTH, expand=True, padx=20)

        def field(row, label, val):
            tk.Label(form, text=label, fg=FG_LIGHT, bg=BG_CARD, font=("Sans", 9)).grid(
                row=row, column=0, sticky=tk.W, pady=6)
            e = tk.Entry(form, bg=BG_DARK, fg=FG_LIGHT, insertbackground=FG_LIGHT,
                         relief="flat", font=("Sans", 9))
            e.insert(0, str(val))
            e.grid(row=row, column=1, sticky=tk.E, pady=6, padx=(10, 0))
            return e

        e_name = field(0, "Name:", cfg.get("name", f"MEmu_{vmid}"))
        e_cpus = field(1, "CPU Cores:", cfg.get("cpus", 4))
        e_ram = field(2, "RAM (MB):", cfg.get("memory_mb", 4096))
        e_port = field(3, "ADB Port:", cfg.get("adb_port", 21503 + vmid * 10))
        e_disp = field(4, "Display (sdl/gtk/none):", cfg.get("display", "gtk"))
        e_vga = field(5, "VGA (std/virtio):", cfg.get("vga", "std"))

        def save():
            try:
                cfg["name"] = e_name.get().strip()
                cfg["cpus"] = int(e_cpus.get())
                cfg["memory_mb"] = int(e_ram.get())
                cfg["adb_port"] = int(e_port.get())
                cfg["display"] = e_disp.get().strip()
                cfg["vga"] = e_vga.get().strip()
                save_instance_config(vmid, cfg)
                dlg.destroy()
                self.refresh_instances()
                self.status_var.set(f"Settings saved for {vmid}.")
            except ValueError as ex:
                messagebox.showerror("Invalid", str(ex))

        tk.Button(dlg, text="Save", command=save, bg=ACCENT_GREEN, fg="#11111b",
                  font=("Sans", 9, "bold"), relief="flat", pady=6).pack(pady=16, fill=tk.X, padx=30)


def main():
    app = LemuApp()
    app.mainloop()


if __name__ == "__main__":
    main()
