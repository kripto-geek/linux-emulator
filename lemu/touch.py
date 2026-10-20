"""
A *genuine* multitouch touchscreen for Lemu guests.

QEMU's ``usb-tablet`` is reported to the guest as an absolute **mouse**, so
touch-only Android games ignore its drags.  The guest kernel, however, has
uinput built in (``CONFIG_INPUT_UINPUT=y``) and ``/dev/uinput`` present, so we
can materialise a real evdev touchscreen from inside the guest:

* ``INPUT_PROP_DIRECT`` + ``BTN_TOUCH`` + ``ABS_MT_*`` makes Android's
  InputReader classify the device as ``DEVICE_CLASS_TOUCHSCREEN|TOUCH_MT`` with
  ``Sources: 0x00001002`` (``SOURCE_TOUCHSCREEN``) and a
  ``Touch Input Mapper (mode - direct)`` - verified on Android-x86 9.0-r2 and
  confirmed to need **no** IDC file (an earlier ``touch.deviceType`` override on
  the USB tablet was not honoured by this platform).

``tools/lemu_touch.c`` is the tiny helper that owns the device.  It is compiled
``-static`` on the host so it can run against the guest's bionic userspace (a
static ELF needs no dynamic loader and talks to the kernel directly), pushed to
``/data/local/tmp`` and driven over a one-command-per-line protocol on stdin.
Because the helper writes straight into evdev, a whole gesture costs one
``write(2)`` per event - a tap round-trips in ~1 ms instead of the ~80-90 ms an
``adb shell input`` invocation needs.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import threading
from pathlib import Path

from lemu.adb import LemuADB

BASE_DIR = Path(__file__).resolve().parent.parent
HELPER_SOURCE = BASE_DIR / "tools" / "lemu_touch.c"
HELPER_BINARY = BASE_DIR / "bin" / "lemu-touch"

#: Where the helper lives inside the guest.
GUEST_PATH = "/data/local/tmp/lemu-touch"
#: Name Android will show for the virtual touchscreen.
DEVICE_NAME = "Lemu Touchscreen"


class TouchUnavailable(RuntimeError):
    """The uinput touchscreen cannot be provided (no compiler, no root, ...)."""


def build_helper(force: bool = False) -> Path:
    """Compile ``tools/lemu_touch.c`` into ``bin/lemu-touch`` (cached)."""
    if (not force and HELPER_BINARY.exists() and HELPER_SOURCE.exists()
            and HELPER_BINARY.stat().st_mtime >= HELPER_SOURCE.stat().st_mtime):
        return HELPER_BINARY
    if not HELPER_SOURCE.exists():
        raise TouchUnavailable(f"helper source missing: {HELPER_SOURCE}")
    cc = shutil.which("gcc") or shutil.which("cc")
    if not cc:
        raise TouchUnavailable("no C compiler (gcc/cc) on PATH")
    HELPER_BINARY.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [cc, "-static", "-O2", "-o", str(HELPER_BINARY), str(HELPER_SOURCE)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip().splitlines()
        raise TouchUnavailable(
            "failed to build the touchscreen helper: " +
            (detail[-1] if detail else "unknown error"))
    return HELPER_BINARY


class TouchDevice:
    """Owns the guest-side virtual touchscreen and speaks its line protocol."""

    def __init__(self, vmid: int = 0, name: str = DEVICE_NAME, log=None):
        self.vmid = vmid
        self.adb = LemuADB(vmid)
        self.name = name
        self._log = log or (lambda msg: None)
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._alive = False

    # -- lifecycle ---------------------------------------------------------
    def start(self, timeout: float = 20.0) -> "TouchDevice":
        binary = build_helper()
        self.adb.connect()
        if not self.adb.is_online():
            raise TouchUnavailable(f"instance {self.vmid} is not reachable over ADB")

        res = self.adb.run_adb("push", str(binary), GUEST_PATH, timeout=60)
        if res.returncode != 0 or "error" in (res.stderr or "").lower():
            raise TouchUnavailable(
                f"could not push the helper: {(res.stderr or res.stdout or '').strip()[:200]}")
        self.adb.run_adb("shell", "chmod", "755", GUEST_PATH, timeout=15)

        self._proc = subprocess.Popen(
            ["adb", "-s", self.adb.target, "shell", "su", "0", GUEST_PATH, self.name],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self._reader = threading.Thread(target=self._drain, name="lemu-touch",
                                        daemon=True)
        self._reader.start()

        line = self._readline(timeout)
        if line != "READY":
            err = ""
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() or "").strip()[:300] if self._proc.stderr else ""
            self.close()
            raise TouchUnavailable(
                "the guest refused to create the touchscreen "
                f"(got {line!r}){(': ' + err) if err else ''} - is the guest rooted?")
        self._alive = True
        self._log(f"{self.name!r} up in guest {self.vmid}")
        return self

    def _drain(self) -> None:
        """Keep stdout flowing so the helper never blocks on a full pipe."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                self._lines.put(line.strip())
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put("")

    def _readline(self, timeout: float) -> str | None:
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def alive(self) -> bool:
        return self._alive and self._proc is not None and self._proc.poll() is None

    def close(self) -> None:
        self._alive = False
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin and proc.poll() is None:
                proc.stdin.write("quit\n")
                proc.stdin.flush()
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except (OSError, ValueError):
                pass

    def __enter__(self) -> "TouchDevice":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- protocol ----------------------------------------------------------
    def send(self, command: str) -> None:
        """Queue one gesture.  Serialised: a finger is a single resource."""
        with self._lock:
            if not self.alive:
                raise TouchUnavailable("touchscreen helper is not running")
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(command + "\n")
            self._proc.stdin.flush()

    def tap(self, x: float, y: float, duration_ms: int = 40) -> None:
        self.send(f"tap {x:.6f} {y:.6f} {int(duration_ms)}")

    def swipe(self, x1: float, y1: float, x2: float, y2: float,
              duration_ms: int = 140) -> None:
        self.send(f"swipe {x1:.6f} {y1:.6f} {x2:.6f} {y2:.6f} {int(duration_ms)}")

    def down(self, x: float, y: float) -> None:
        self.send(f"down {x:.6f} {y:.6f}")

    def move(self, x: float, y: float) -> None:
        self.send(f"move {x:.6f} {y:.6f}")

    def up(self) -> None:
        self.send("up")
