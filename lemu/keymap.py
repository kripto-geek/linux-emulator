"""
Host-keyboard to guest-touch keymapper for Lemu instances.

Android games are driven by touch gestures, but QEMU forwards host key presses
straight into the guest as *keyboard* events, which a touch-only game ignores.
This daemon closes that gap: it watches the guest's input devices (through
``getevent``) for the keys the player presses on the host, and for every bound
key injects the matching synthetic touch gesture (tap / swipe / hold) or Android
key event.

Two injectors are available
---------------------------
``touch`` (default)
    A *genuine* multitouch touchscreen created inside the guest with uinput
    (see :mod:`lemu.touch`).  Gestures are written straight into evdev, so a tap
    costs ~1 ms of round-trip; anything that listens to ``SOURCE_TOUCHSCREEN``
    sees a real finger, and multi-touch gestures work.
``input``
    Plain ``adb shell input tap|swipe|keyevent``.  Slower (~80-90 ms per gesture
    because it starts an app_process) and single-touch, but needs no root and no
    helper binary, so it is the automatic fallback.

Bindings and per-game profiles
------------------------------
``instances/<id>/keymap.json`` holds a default ``bindings`` map plus an optional
``profiles`` map.  Every second the daemon asks the guest which window has focus
and switches to the matching profile, so one instance can carry a different
control scheme per game::

    {
      "_comment": "...",
      "bindings":  { "KEY_A": {...}, ... },            // the default scheme
      "profiles": {
        "com.kiloo.subwaysurf": { "bindings": { ... } },  // merged over the default
        "com.dts.freefireth/*": { "bindings": { ... } }   // globs allowed
      }
    }

A profile's ``bindings`` are *merged* over the default ones; set a key to
``null`` in a profile to un-bind it for that game.

Coordinates
-----------
Bindings use *normalised* coordinates (0.0-1.0) over the **logical display**,
which is exactly the space ``adb shell input`` and ``screencap`` use, so a
binding keeps working when the guest resolution changes: the launcher runs at
1024x768 while Subway Surfers is presented through a 590x768 compatibility
display.

The two injectors reach that space differently, and the distinction matters:

* ``input`` takes logical display pixels directly.
* the uinput touchscreen reports raw 0..32767 that Android scales onto the
  **whole physical panel**, and only then does the letterbox transform place the
  touch inside the app.  So its fraction is
  ``(viewport_origin + logical_pixel) / panel_size`` - i.e. a touch in the
  middle of a letterboxed game is *not* at fraction 0.5 of the panel unless the
  letterbox happens to be centred.  :class:`DisplayGeometry` derives both from
  ``dumpsys display`` (``mOverrideDisplayInfo`` for the logical size,
  ``mBaseDisplayInfo`` for the panel, ``mCurrentDisplayRect`` for the offset).
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from lemu.adb import LemuADB
from lemu.config import get_instance_dir
from lemu.touch import TouchDevice, TouchUnavailable

# ---------------------------------------------------------------------------
# Default keymap
# ---------------------------------------------------------------------------
# Coordinates are normalised over the logical display: [0.0, 0.0] is its
# top-left and [1.0, 1.0] its bottom-right.  The defaults below target Subway
# Surfers (swipe to change lane, up to jump, down to roll) and leave Space/Enter
# as a "tap the middle of the screen" for menus.
_LANE_LEFT = {"action": "swipe", "from": [0.78, 0.55], "to": [0.18, 0.55],
              "duration_ms": 140}
_LANE_RIGHT = {"action": "swipe", "from": [0.22, 0.55], "to": [0.82, 0.55],
               "duration_ms": 140}
_JUMP = {"action": "swipe", "from": [0.50, 0.70], "to": [0.50, 0.26],
         "duration_ms": 140}
_ROLL = {"action": "swipe", "from": [0.50, 0.28], "to": [0.50, 0.72],
         "duration_ms": 140}
_CENTRE_TAP = {"action": "tap", "at": [0.50, 0.50]}

DEFAULT_BINDINGS = {
    "KEY_A":     dict(_LANE_LEFT),
    "KEY_LEFT":  dict(_LANE_LEFT),
    "KEY_D":     dict(_LANE_RIGHT),
    "KEY_RIGHT": dict(_LANE_RIGHT),
    "KEY_W":     dict(_JUMP),
    "KEY_UP":    dict(_JUMP),
    "KEY_S":     dict(_ROLL),
    "KEY_DOWN":  dict(_ROLL),
    "KEY_SPACE": dict(_CENTRE_TAP),
    "KEY_ENTER": dict(_CENTRE_TAP),
}

_KEYMAP_COMMENT = (
    "Coordinates are normalised (0..1) over the logical display. "
    "Actions: swipe {from,to,duration_ms}, tap {at}, hold {at,duration_ms}, "
    "keyevent {code} (Android key code). 'bindings' is the default scheme; "
    "'profiles' adds per-game schemes keyed by package, package/activity or glob, "
    "merged over the default (use null to un-bind a key). "
    "Run 'lemuc keymap --list' to see what is active."
)

#: A worked example that also documents the profile mechanism: the generic
#: defaults plus a pause key.  Subway Surfers itself drives the default scheme.
_DEFAULT_FF_JOY = [0.164, 0.814]          # left-thumb joystick centre
_FF_SWIPE = {"duration_ms": 160}

#: Free Fire MAX (com.dts.freefiremax): WASD drive the dynamic joystick on the
#: left, the right-hand cluster is taped where the buttons live on the
#: 1024x768 frame (normalised, so resolution-independent).  Measured against
#: the in-match layout; re-tune on the Visual Editor if the UI changes.
DEFAULT_PROFILES = {
    "com.kiloo.subwaysurf": {
        "_comment": "Subway Surfers: generic lane swipes + P/Esc to pause.",
        "bindings": {
            "KEY_P":   {"action": "keyevent", "code": 4},
            "KEY_ESC": {"action": "keyevent", "code": 4},
        },
    },
    "com.dts.freefiremax": {
        "_comment": "Free Fire MAX: WASD joystick swipes + action cluster. "
                    "P = Android BACK (opens the game menu).",
        "bindings": {
            "KEY_W":     {**_FF_SWIPE, "action": "swipe", "from": _DEFAULT_FF_JOY,
                          "to": [0.164, 0.634]},
            "KEY_A":     {**_FF_SWIPE, "action": "swipe", "from": _DEFAULT_FF_JOY,
                          "to": [0.024, 0.814]},
            "KEY_S":     {**_FF_SWIPE, "action": "swipe", "from": _DEFAULT_FF_JOY,
                          "to": [0.164, 0.914]},
            "KEY_D":     {**_FF_SWIPE, "action": "swipe", "from": _DEFAULT_FF_JOY,
                          "to": [0.294, 0.814]},
            "KEY_UP":    {**_FF_SWIPE, "action": "swipe", "from": _DEFAULT_FF_JOY,
                          "to": [0.164, 0.634]},
            "KEY_SPACE": {"action": "tap", "at": [0.949, 0.777]},   # jump
            "KEY_C":     {"action": "tap", "at": [0.896, 0.926]},   # crouch
            "KEY_Z":     {"action": "tap", "at": [0.949, 0.926]},   # prone
            "KEY_F":     {"action": "hold", "at": [0.828, 0.781],
                          "duration_ms": 260},                      # fire press
            "KEY_Q":     {"action": "tap", "at": [0.739, 0.820]},   # quick items
            "KEY_E":     {"action": "tap", "at": [0.159, 0.483]},   # sprint toggle
            "KEY_P":     {"action": "keyevent", "code": 4},         # game menu
        },
    },
}

DEFAULT_KEYMAP = {
    "_comment": _KEYMAP_COMMENT,
    "bindings": DEFAULT_BINDINGS,
    "profiles": DEFAULT_PROFILES,
}

# `getevent -lq` prints e.g. "/dev/input/event2: EV_KEY       KEY_A  DOWN"
_KEY_LINE_RE = re.compile(r"\bEV_KEY\s+(\S+)\s+(DOWN|UP|REPEAT)\b")
# `dumpsys display` reports the logical display an app is presented through.
# With an override that line reads '..., app 590 x 720, real 590 x 768, ...';
# `real` is the space screencap and `input` use.
_OVERRIDE_SIZE_RE = re.compile(
    r"mOverrideDisplayInfo=DisplayInfo\{[^\n]*?\breal (\d+) x (\d+)")
_BASE_SIZE_RE = re.compile(
    r"mBaseDisplayInfo=DisplayInfo\{[^\n]*?\breal (\d+) x (\d+)")
_CURRENT_RECT_RE = re.compile(
    r"mCurrentDisplayRect=Rect\((-?\d+), (-?\d+) - (-?\d+), (-?\d+)\)")
# `dumpsys window windows` names the focused window's package/activity.
_FOCUS_RE = re.compile(
    r"mCurrentFocus=Window\{[^}]*?\s([A-Za-z][A-Za-z0-9_.]*)/([A-Za-z0-9_.$]+)")


def keymap_path(vmid: int):
    return get_instance_dir(vmid) / "keymap.json"


def load_keymap(vmid: int) -> dict:
    """Load (creating on first use) the keymap for an instance."""
    path = keymap_path(vmid)
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_KEYMAP, indent=2) + "\n", encoding="utf-8")
        return json.loads(json.dumps(DEFAULT_KEYMAP))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"[-] Cannot read keymap {path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("bindings"), dict):
        raise SystemExit(f"[-] Malformed keymap {path}: expected an object with a 'bindings' map.")
    if not isinstance(data.get("profiles", {}), dict):
        raise SystemExit(f"[-] Malformed keymap {path}: 'profiles' must be an object.")
    return data


def save_keymap(vmid: int, data: dict) -> None:
    keymap_path(vmid).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Display geometry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DisplayGeometry:
    """Where the app's logical display sits on the guest's physical panel."""

    logical_w: int
    logical_h: int
    panel_w: int
    panel_h: int
    left: int
    top: int
    right: int
    bottom: int

    #: Space `adb shell input` / `screencap` use.
    def logical_px(self, nx: float, ny: float) -> tuple[int, int]:
        x = min(max(nx, 0.0), 1.0) * (self.logical_w - 1)
        y = min(max(ny, 0.0), 1.0) * (self.logical_h - 1)
        return int(round(x)), int(round(y))

    #: Fraction of the *physical panel* the uinput touchscreen wants.
    def panel_fraction(self, nx: float, ny: float) -> tuple[float, float]:
        nx, ny = min(max(nx, 0.0), 1.0), min(max(ny, 0.0), 1.0)
        frame_w = max(self.right - self.left, 1)
        frame_h = max(self.bottom - self.top, 1)
        px = self.left + nx * (frame_w - 1)
        py = self.top + ny * (frame_h - 1)
        return px / max(self.panel_w, 1), py / max(self.panel_h, 1)


DEFAULT_GEOMETRY = DisplayGeometry(1024, 768, 1024, 768, 0, 0, 1024, 768)


def probe_display(adb: LemuADB) -> DisplayGeometry:
    """Read the current logical size, panel size and letterbox offset."""
    out = adb.run_adb("shell", "dumpsys", "display", timeout=5.0).stdout or ""

    m = _OVERRIDE_SIZE_RE.search(out) or _BASE_SIZE_RE.search(out)
    logical = (int(m.group(1)), int(m.group(2))) if m else DEFAULT_GEOMETRY.logical_px(1, 1)
    b = _BASE_SIZE_RE.search(out)
    panel = (int(b.group(1)), int(b.group(2))) if b else logical
    r = _CURRENT_RECT_RE.search(out)
    rect = tuple(int(g) for g in r.groups()) if r else (0, 0, panel[0], panel[1])
    return DisplayGeometry(logical[0], logical[1], panel[0], panel[1], *rect)


class GeometryCache:
    """``DisplayGeometry`` behind a cache, so gestures never pay for the probe.

    ``dumpsys display`` costs ~20 ms over ADB, which would dwarf the ~1 ms the
    touchscreen itself needs.  A background tick (:meth:`refresh`, called from
    the profile loop) therefore keeps it warm and :meth:`get` only falls back to
    probing on its own when nothing has refreshed it for ``TTL`` seconds.
    """

    TTL = 3.0

    def __init__(self, adb: LemuADB):
        self.adb = adb
        self._geometry: DisplayGeometry | None = None
        self._at = 0.0

    def _probe(self) -> DisplayGeometry | None:
        try:
            return probe_display(self.adb)
        except (subprocess.SubprocessError, OSError, ValueError):
            return None

    def refresh(self) -> DisplayGeometry:
        """Unconditionally re-read the geometry (letterboxes change at will)."""
        geometry = self._probe()
        if geometry is not None:
            self._geometry, self._at = geometry, time.time()
        elif self._geometry is None:
            self._geometry, self._at = DEFAULT_GEOMETRY, time.time()
        return self._geometry

    def get(self) -> DisplayGeometry:
        now = time.time()
        if self._geometry is not None and now - self._at < self.TTL:
            return self._geometry
        return self.refresh()


# ---------------------------------------------------------------------------
# Foreground app + profiles
# ---------------------------------------------------------------------------
def foreground_component(adb: LemuADB) -> tuple[str | None, str | None]:
    """Return ``(package, activity)`` of the focused window, or ``(None, None)``."""
    try:
        out = adb.run_adb("shell", "dumpsys", "window", "windows", timeout=5.0).stdout or ""
    except (subprocess.SubprocessError, OSError):
        return None, None
    m = _FOCUS_RE.search(out)
    if not m:
        return None, None
    package, activity = m.group(1), m.group(2)
    if activity.startswith("."):
        activity = package + activity
    return package, activity


def resolve_profile(profiles: dict, package: str | None,
                    activity: str | None) -> tuple[str | None, dict | None]:
    """Pick the profile whose key matches the foreground component."""
    if not profiles or not package:
        return None, None
    component = f"{package}/{activity}" if activity else package
    exact = [component, package, f"{package}/*"]
    for key in exact:
        if key in profiles:
            return key, profiles[key]
    for pattern, profile in profiles.items():
        if pattern.startswith("_"):
            continue
        if fnmatch.fnmatchcase(component, pattern) or fnmatch.fnmatchcase(package, pattern):
            return pattern, profile
    return None, None


def merge_bindings(default: dict, profile: dict | None) -> dict:
    """A profile's bindings layered over the default scheme (null un-binds)."""
    merged = dict(default)
    for key, value in ((profile or {}).get("bindings") or {}).items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Injectors
# ---------------------------------------------------------------------------
class InputInjector:
    """``adb shell input`` - portable, no guest changes, ~80-90 ms per gesture."""

    kind = "input"

    def __init__(self, vmid: int, geometry: GeometryCache, source: str | None = None,
                 log=None):
        self.vmid = vmid
        self.geometry = geometry
        self.source = source
        self.adb = LemuADB(vmid)

    def _run(self, *args) -> None:
        cmd = ["adb", "-s", self.adb.target, "shell", "input"]
        if self.source:
            cmd.append(self.source)
        cmd += [str(a) for a in args]
        subprocess.run(cmd, capture_output=True, timeout=10)

    def tap(self, at, duration_ms: int | None = None) -> str:
        x, y = self.geometry.get().logical_px(*at)
        self._run("tap", x, y)
        return f"tap ({x},{y})"

    def swipe(self, frm, to, duration_ms: int = 140) -> str:
        x1, y1 = self.geometry.get().logical_px(*frm)
        x2, y2 = self.geometry.get().logical_px(*to)
        self._run("swipe", x1, y1, x2, y2, int(duration_ms))
        return f"swipe ({x1},{y1})->({x2},{y2}) {int(duration_ms)}ms"

    def hold(self, at, duration_ms: int = 600) -> str:
        x, y = self.geometry.get().logical_px(*at)
        self._run("swipe", x, y, x, y, int(duration_ms))
        return f"hold ({x},{y}) {int(duration_ms)}ms"

    def keyevent(self, code) -> str:
        self._run("keyevent", code)
        return f"keyevent {code}"

    def close(self) -> None:
        pass


class TouchInjector:
    """The native uinput touchscreen - ~1 ms per gesture, real multi-touch."""

    kind = "touch"

    def __init__(self, vmid: int, geometry: GeometryCache, log=None,
                 device: TouchDevice | None = None):
        self.vmid = vmid
        self.geometry = geometry
        self.adb = LemuADB(vmid)
        self.device = device or TouchDevice(vmid, log=log).start()

    def _where(self, *at) -> tuple[float, float]:
        return self.geometry.get().panel_fraction(*at)

    def tap(self, at, duration_ms: int | None = None) -> str:
        fx, fy = self._where(*at)
        self.device.tap(fx, fy, duration_ms or 40)
        return f"tap {tuple(at)} -> panel ({fx:.3f},{fy:.3f})"

    def swipe(self, frm, to, duration_ms: int = 140) -> str:
        x1, y1 = self._where(*frm)
        x2, y2 = self._where(*to)
        self.device.swipe(x1, y1, x2, y2, int(duration_ms))
        return (f"swipe {tuple(frm)}->{tuple(to)} -> panel "
                f"({x1:.3f},{y1:.3f})->({x2:.3f},{y2:.3f}) {int(duration_ms)}ms")

    def hold(self, at, duration_ms: int = 600) -> str:
        fx, fy = self._where(*at)
        self.device.tap(fx, fy, int(duration_ms))
        return f"hold {tuple(at)} -> panel ({fx:.3f},{fy:.3f}) {int(duration_ms)}ms"

    def keyevent(self, code) -> str:
        # The virtual device is a touchscreen, not a keyboard: key events still
        # have to go through `input keyevent`.
        subprocess.run(["adb", "-s", self.adb.target, "shell", "input", "keyevent",
                        str(code)], capture_output=True, timeout=10)
        return f"keyevent {code}"

    def close(self) -> None:
        self.device.close()


def make_injector(kind: str | None, vmid: int, geometry: GeometryCache, log,
                  source: str | None = None):
    """Build the requested injector, falling back to ``input`` when asked to."""
    kind = (kind or "auto").lower()
    if kind in ("auto", "touch"):
        try:
            return TouchInjector(vmid, geometry, log=log)
        except TouchUnavailable as exc:
            if kind == "touch":
                raise SystemExit(f"[-] touch injector unavailable: {exc}")
            log(f"touch injector unavailable ({exc}) - using `adb shell input`")
    if kind in ("auto", "input"):
        return InputInjector(vmid, geometry, source=source, log=log)
    raise SystemExit(f"[-] Unknown injector {kind!r} (expected auto, touch or input).")


# ---------------------------------------------------------------------------
# The daemon
# ---------------------------------------------------------------------------
class Keymapper:
    """Watches guest key input and replays bound keys as synthetic touches."""

    #: How long a detected display size stays valid before we re-query it.
    FRAME_TTL = 0.5
    #: How often the foreground app is re-checked for a profile switch.
    PROFILE_TTL = 1.0

    def __init__(self, vmid: int = 0, injector: str = "auto", source: str | None = None,
                 profile: str | None = None, verbose: bool = False, log=None):
        self.vmid = vmid
        self.adb = LemuADB(vmid)
        self.injector_kind = injector
        self.source = source
        self.profile_override = profile
        self.verbose = verbose
        self._log_sink = log

        data = load_keymap(vmid)
        self.default_bindings = dict(data.get("bindings") or {})
        self.profiles = dict(data.get("profiles") or {})
        self.active_profile: str | None = None
        self.bindings = dict(self.default_bindings)

        self.geometry = GeometryCache(self.adb)
        self.injector = None
        self._pressed: set[str] = set()
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._profile_thread: threading.Thread | None = None

    # -- logging -----------------------------------------------------------
    def log(self, message: str, force: bool = False) -> None:
        if not (force or self.verbose):
            return
        text = f"[keymap] {message}"
        if self._log_sink:
            self._log_sink(text)
        else:
            print(text, flush=True)

    # -- profiles ----------------------------------------------------------
    def _apply_profile(self, name: str | None, profile: dict | None) -> None:
        if name == self.active_profile:
            return
        self.active_profile = name
        self.bindings = merge_bindings(self.default_bindings, profile)
        self.log(f"profile: {name or 'default'} ({len(self.bindings)} bindings)", force=True)

    def _refresh_profile(self) -> None:
        if self.profile_override:
            return
        package, activity = foreground_component(self.adb)
        self._apply_profile(*resolve_profile(self.profiles, package, activity))

    def _background_loop(self) -> None:
        """Keep the (slow) geometry probe out of the injection path."""
        self.geometry.refresh()
        while not self._stop.wait(self.PROFILE_TTL):
            try:
                self._refresh_profile()
                self.geometry.refresh()
            except Exception:                                  # noqa: BLE001
                pass

    # -- injection ---------------------------------------------------------
    def _dispatch(self, name: str, binding: dict) -> None:
        action = binding.get("action")
        started = time.perf_counter()
        try:
            if action == "swipe":
                detail = self.injector.swipe(binding["from"], binding["to"],
                                             binding.get("duration_ms", 140))
            elif action == "tap":
                detail = self.injector.tap(binding["at"])
            elif action == "hold":
                detail = self.injector.hold(binding["at"], binding.get("duration_ms", 600))
            elif action == "keyevent":
                detail = self.injector.keyevent(binding["code"])
            else:
                detail = f"unknown action {action!r} (ignored)"
        except (subprocess.SubprocessError, OSError, KeyError, TypeError, ValueError) as exc:
            self.log(f"{name}: {exc}", force=True)
            return
        elapsed = (time.perf_counter() - started) * 1000.0
        self.log(f"{name} -> {detail}  [{elapsed:.0f} ms]")

    # -- main loop ---------------------------------------------------------
    def run(self) -> int:
        if not self.adb.is_online():
            print(f"[-] Instance {self.vmid} is not reachable over ADB ({self.adb.target}).")
            print("    Start it first: ./bin/lemuc start -i %d" % self.vmid)
            return 1
        self._print_bindings()

        try:
            self.injector = make_injector(self.injector_kind, self.vmid, self.geometry,
                                          self.log, source=self.source)
        except SystemExit as exc:
            print(exc)
            return 1
        self.log(f"injector: {self.injector.kind}", force=True)

        if self.profile_override:
            self._apply_profile(self.profile_override,
                                self.profiles.get(self.profile_override))
        else:
            self._refresh_profile()

        self._profile_thread = threading.Thread(target=self._background_loop,
                                                name="keymap-bg", daemon=True)
        self._profile_thread.start()

        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="keymap")
        if self._stop.is_set():
            # A stop raced the start-up and found nothing to terminate yet.
            self._shutdown(None)
            print("\n[+] Keymapper stopped.")
            return 0
        self._proc = subprocess.Popen(
            ["adb", "-s", self.adb.target, "exec-out", "getevent", "-lq"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        if self._stop.is_set():
            # Same race, one instruction later: break the read before it blocks.
            self._terminate(self._proc)
        print("[+] Keymapper running - focus the emulator window and play. Ctrl-C to stop.")
        proc = self._proc
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                if self._stop.is_set():
                    break
                m = _KEY_LINE_RE.search(line)
                if not m:
                    continue
                name, state = m.group(1), m.group(2)
                if state == "UP":
                    self._pressed.discard(name)
                    continue
                # DOWN/REPEAT: only act on the press edge (ignore autorepeat).
                if name in self._pressed:
                    continue
                self._pressed.add(name)
                binding = self.bindings.get(name)
                if binding:
                    assert self._pool is not None
                    self._pool.submit(self._dispatch, name, binding)
        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown(proc)
        print("\n[+] Keymapper stopped.")
        return 0

    @staticmethod
    def _terminate(proc: subprocess.Popen | None) -> None:
        """Kill the ``adb exec-out getevent`` reader so the read loop sees EOF."""
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
        except OSError:
            pass

    def _shutdown(self, proc: subprocess.Popen | None) -> None:
        self._stop.set()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._pool is not None:
            self._pool.shutdown(wait=False)
        if self.injector is not None:
            self.injector.close()
            self.injector = None

    def stop(self) -> None:
        """Ask a running :meth:`run` to wind down (safe from another thread).

        Also safe during bring-up: if the ``getevent`` reader does not exist yet,
        :meth:`run` notices the flag and aborts instead of blocking on a pipe
        that only produces output when somebody types.
        """
        self._stop.set()
        proc, self._proc = self._proc, None
        self._terminate(proc)

    # -- reporting ---------------------------------------------------------
    def _describe(self, binding: dict) -> str:
        action = binding.get("action", "?")
        if action == "swipe":
            return f"swipe {binding.get('from')} -> {binding.get('to')} ({binding.get('duration_ms', 140)}ms)"
        if action == "tap":
            return f"tap {binding.get('at')}"
        if action == "hold":
            return f"hold {binding.get('at')} ({binding.get('duration_ms', 600)}ms)"
        if action == "keyevent":
            return f"keyevent {binding.get('code')}"
        return str(action)

    def _print_bindings(self) -> None:
        print(f"[*] Keymap for instance {self.vmid} ({keymap_path(self.vmid)}):")
        if not self.default_bindings:
            print("    (no bindings)")
        for key in sorted(self.default_bindings):
            print(f"    {key:<12} {self._describe(self.default_bindings[key])}")
        if self.profiles:
            print("[*] Per-game profiles:")
            for name in sorted(self.profiles):
                if name.startswith("_"):
                    continue
                overrides = (self.profiles[name] or {}).get("bindings") or {}
                keys = ", ".join(sorted(overrides)) or "(none)"
                print(f"    {name:<28} overrides: {keys}")


# ---------------------------------------------------------------------------
# Front-ends
# ---------------------------------------------------------------------------
def run_keymapper(vmid: int = 0, source: str | None = None, list_only: bool = False,
                  verbose: bool = False, injector: str = "auto",
                  profile: str | None = None) -> int:
    """Entry point for ``lemuc keymap``."""
    if list_only:
        keymapper = Keymapper(vmid, injector=injector, profile=profile, verbose=verbose)
        keymapper._print_bindings()
        return 0
    return Keymapper(vmid, injector=injector, source=source, profile=profile,
                     verbose=verbose).run()


class KeymapperController:
    """Start/stop the daemon from a GUI thread without blocking the event loop."""

    def __init__(self, vmid: int, injector: str = "auto", profile: str | None = None,
                 log=None, on_state=None):
        self.vmid = vmid
        self.injector = injector
        self.profile = profile
        self._log = log or (lambda message: None)
        self._on_state = on_state or (lambda running, rc: None)
        self._keymapper: Keymapper | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def active_profile(self) -> str | None:
        """The scheme the daemon settled on, for GUI status lines."""
        return self._keymapper.active_profile if self._keymapper is not None else None

    @property
    def injector_kind(self) -> str | None:
        """``touch``/``input`` once the daemon picked one, else ``None``."""
        injector = self._keymapper.injector if self._keymapper is not None else None
        return getattr(injector, "kind", None)

    def start(self) -> bool:
        if self.running:
            return False
        self._keymapper = Keymapper(self.vmid, injector=self.injector,
                                    profile=self.profile, verbose=True, log=self._log)
        self._thread = threading.Thread(target=self._run, name="keymapper", daemon=True)
        self._thread.start()
        self._on_state(True, None)
        return True

    def _run(self) -> None:
        rc = 1
        keymapper = self._keymapper              # local: stop() may null the field
        try:
            if keymapper is not None:
                rc = keymapper.run()
        except Exception as exc:                                # noqa: BLE001
            self._log(f"[keymap] failed: {exc}")
        finally:
            self._on_state(False, rc)

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the daemon and wait (briefly) for its thread to unwind.

        The wait keeps GUI state honest: the touchscreen device is released before
        the caller can start again, so a restart never leaves two devices behind.
        """
        keymapper, self._keymapper = self._keymapper, None
        if keymapper is not None:
            keymapper.stop()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
