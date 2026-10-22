# Lemu - MEmu Play Reconstructed for Linux (Omarchy / Arch Linux)

**Lemu** is a native Linux reconstruction of the **MEmu Play** Android gaming
emulator, reverse-engineered from the Windows installer
(`MEmu-Setup-9.5.7.1-hafb3411bb.exe`) and rebuilt to run on modern Linux with
**hardware KVM acceleration** and **no Windows host drivers**.

It runs a 64-bit Android 9.0 guest under plain `qemu-system-x86_64` with
MEmu's **Intel Houdini** ARM translation grafted in, so ARM-only games
(`arm64-v8a` / `armeabi-v7a`) install and play.

---

## 🔬 Reverse Engineering Summary

By analyzing and extracting the MEmu 9.5.7.1 installer package:

1. **Hypervisor engine** - a proprietary rebranded fork of **Oracle VM
   VirtualBox 6.1.40** (`memuhyperv-6.1.40`), renaming binaries
   (`VBoxManage` → `MEmuManage`, `VBoxSVC` → `MEmuSVC`, `VBoxHeadless` →
   `MEmuHeadless`, `VBoxDrv.sys` → `MEmuDrv.sys`).
2. **Guest system** - Android 9.0 (Pie, 64-bit, API 28). The original guest
   image boots, but its display and GL path are mediated by MEmu's proprietary
   Windows host pipes (`MEmuPipe*` PCI devices, HGCM, and a serial-backed
   OpenGL translator) that QEMU cannot provide. On Linux the stock guest only
   reaches a root shell + `adbd`.
3. **ARM gaming translation** - ships **Intel Houdini**:
   `/system/lib64/libhoudini.so` + `/system/lib/libhoudini.so` (the 64-bit and
   32-bit bridges) driven by `/system/lib64/libnb.so` (the native-bridge shim
   loaded into `zygote`/`zygote64`). Reported version:
   `9.0.7a_z.36020 RELEASE`.
4. **Graphics** - the Windows guest connects over virtual pipes
   (`/dev/qemu_pipe`, `/dev/ttyS17`, `/dev/ttyS21`) to an AOSP-derived OpenGL
   ES translator (`libOpenglRender`). Lemu replaces this with **stock
   Android-x86 software rendering**, so no proprietary GL bridge is needed.
5. **Installer disk topology** - `MEmu96-*-disk1.vmdk` (boot/system) and
   `MEmu96-*-disk2.vmdk` (userdata).

---

## 🚀 Reconstructed Architecture on Linux

Rather than requiring the proprietary Windows kernel driver (`MEmuDrv.sys`),
Lemu uses Linux-native hardware virtualization:

- **Hypervisor** - Linux **KVM** (`/dev/kvm`) via `qemu-system-x86_64`.
- **Guest** - a **64-bit Android-x86 9.0-r2** system, the one MEmu's 64-bit
  Houdini actually requires (it needs a genuine x86-64 kernel). Booted with
  QEMU's external `-kernel` / `-initrd` rather than a disk bootloader.
- **Instant multi-instance cloning** - each instance's disk is a copy-on-write
  QCOW2 overlay of the master image, so clones are created in under a second
  and share the master's storage.
- **Root & insecure ADB** - `adbd` is reachable at `127.0.0.1:21503 + 10 * vmid`
  with passwordless root.
- **Display backends** - SDL2, GTK, or headless.

### Guest flavours

| `guest` | Image | Status |
|---|---|---|
| `x86_64` (default) | `androidx86_64_master.qcow2` | **Fully working.** Android-x86 9.0-r2 + grafted Houdini. |
| `x86` | `androidx86_master.qcow2` | Reference. 32-bit Android-x86; Houdini needs a 64-bit kernel, so ARM apps do not run. |
| `memu` | `system_master.qcow2` + `data_master.qcow2` | Reference / RE only. Reaches a root shell + adbd; no usable display. |

---

## 🏗️ Building the Android-x86 master image

`images/androidx86_64_master.qcow2` is the provisioned master: a whole boot
disk containing the Android-x86 install, the grafted Houdini bridges, the
patched vendor check, and the guest provisioning script. Every instance is a
COW overlay of it. The recipe below is how the master was produced.

### 1. Boot artifacts

The guest boots from an external kernel + initramfs, so these are kept in the
repo under `boot/androidx86_64/`:

```
boot/androidx86_64/kernel      # Android-x86 9.0-r2 x86_64 kernel
boot/androidx86_64/initrd.img  # matching initramfs
```

They are taken from the Android-x86 9.0-r2 64-bit ISO. QEMU hands them to the
guest directly; the guest's own copies also live inside the image at
`/android-9.0-r2/`.

The kernel command line (`lemu/config.py: X86_64_CMDLINE`) is:

```
root=/dev/ram0 vmalloc=192M SRC=/android-9.0-r2 console=ttyS0 SETUPWIZARD=0
```

- `root=/dev/ram0` boots the initramfs.
- `SRC=/android-9.0-r2` selects the install directory on the data partition
  that holds `system.img`.
- `SETUPWIZARD=0` skips the first-boot wizard.
- `console=ttyS0` mirrors the boot log to a file on the host.

### 2. Disk layout

The master is a raw disk image with a **1 MiB MBR head** followed by an
**8 GiB ext4** partition (built with `sfdisk` + `mke2fs`). The partition is
formatted as `/data` and holds:

```
/android-9.0-r2/kernel
/android-9.0-r2/initrd.img
/android-9.0-r2/system.img     # the Android /system as an ext4 image (loop-mounted at boot)
/android-9.0-r2/data/          # the /data mountpoint
```

Files are injected into `system.img` and the partition with `debugfs`, which
avoids needing a privileged loop mount on the host.

### 3. Grafting Houdini

The Houdini pieces are extracted from the MEmu guest system disk
(`MEmu96-*-disk1.vmdk`) and injected into the Android-x86 `system.img`:

```
/system/lib64/libnb.so          # native-bridge shim for zygote64
/system/lib/libnb.so            # native-bridge shim for zygote
/system/lib64/libhoudini.so     # 64-bit bridge   (from MEmu)
/system/lib/libhoudini.so       # 32-bit bridge   (from MEmu)
/system/lib64/arm64/*           # houdini64 runtime (arm64 translation)
/system/lib/arm/*               # houdini32 runtime (arm translation)
```

`zygote`/`zygote64` pick the bridge up because the ramdisk adds
`-XX:NativeBridge=libnb.so` to their command lines and `init.sh` sets
`persist.sys.nativebridge 1`.

### 4. Patching the Houdini vendor check

Houdini refuses to initialise unless it believes it is running on an Intel
CPU. Instead of lying to the whole guest about the CPU vendor (which breaks
32-bit compat - see *CPU model* below), the check is neutralised in both
bridges by flipping a conditional branch:

| Library | File offset | Before | After | Meaning |
|---|---|---|---|---|
| `/system/lib64/libhoudini.so` | `0x32f2d0` | `0x75` | `0xeb` | `jne` → `jmp` |
| `/system/lib/libhoudini.so`   | `0x2a6a9d` | `0x75 0x17` | `0xeb 0x17` | `jne` → `jmp` (short jump preserved) |

Both libraries are `ET_DYN` with their ELF `LOAD` segment starting at
virtual address `0`, so **virtual address == file offset** and the patch can be
applied with a plain file-offset write (e.g. `printf '\xeb' | dd of=... bs=1
seek=... conv=notrunc`).

Patched artifacts (MD5):

```
8b4afac572f6156904da759246fcd0c7  libhoudini64.so  (system/lib64)
0e731ee6b4f8e1326575d00c4e875e39  libhoudini.so    (system/lib)
```

On first boot `logcat` shows the result:

```
houdini : found /system/lib/libhoudini.so
houdini : houdini enabled
libnb   : Found /system/lib64/libhoudini.so version 3
houdini : Initialize library(version: 9.0.7a_z.36020 RELEASE)... successfully.
```

### 5. Guest provisioning

`/system/etc/init.sh` is executed by `/init.android_x86_64.rc` on `post-fs`
and again with `bootcomplete`. Two Lemu functions were appended (before the
trailing `case` block, so they are defined before they run):

- `lemu_provision` (runs at `post-fs`) sets `persist.sys.nativebridge 1` - the
  property that makes Houdini enable ARM translation.
- `lemu_network` (runs at `bootcomplete`) brings up the QEMU user-mode NIC
  (`wifi_eth`), assigns `10.0.2.15/24`, installs the default route, adds an
  `ip rule` so unprivileged apps inherit it, and pins the display on
  (`screen_off_timeout`, `svc power stayon true`).

The source of truth for this file (the provisioned guest
`/system/etc/init.sh`) is kept in the repo at **`guest/init.sh`**; search for
`lemu_provision` / `lemu_network`.

### 6. Snapshotting the master

The master is produced by booting a scratch overlay of the freshly-installed
guest, applying the graft/patches/provisioning, powering the guest off
cleanly, and converting the overlay into the master:

```bash
qemu-img convert -p -O qcow2 scratch.qcow2 images/androidx86_64_master.qcow2
```

`images/androidx86_64_master.prefix.qcow2` is a backup from *before* the
Houdini vendor check was patched. It can be deleted to reclaim ~2.5 GB.

---

## 💻 CLI Usage (`lemuc`)

`lemuc` is a drop-in replacement for Windows `memuc.exe`:

### List instances
```bash
./bin/lemuc list
```

### Start / stop an instance
```bash
./bin/lemuc start -i 0              # graphical window (GTK)
./bin/lemuc start -i 0 --headless   # no window
./bin/lemuc stop  -i 0
./bin/lemuc stopall
```

### Create & clone
```bash
./bin/lemuc create -n "MyPhone" --cpus 4 --memory 4096
./bin/lemuc clone  -i 0 -r "Gaming_Alt"
./bin/lemuc remove -i 1
```

### Applications & ADB
```bash
# Install an APK (ARM-only games work via Houdini)
./bin/lemuc installapp -i 0 /path/to/game.apk

# Launch / force-stop an app
./bin/lemuc startapp -i 0 com.kiloo.subwaysurf
./bin/lemuc stopapp  -i 0 com.kiloo.subwaysurf

# Raw ADB
./bin/lemuc adb -i 0 shell getprop ro.build.version.release

# Interactive root shell
./bin/lemuc console -i 0

# Map host keys to touch gestures for touch-only games (see "Keyboard mapping")
./bin/lemuc keymap -i 0
```

### Verified end to end

```bash
./bin/lemuc start -i 0 --headless
adb connect 127.0.0.1:21503
adb -s 127.0.0.1:21503 shell getprop sys.boot_completed        # -> 1
adb -s 127.0.0.1:21503 shell dumpsys package com.kiloo.subwaysurf | grep primaryCpuAbi  # -> arm64-v8a
adb -s 127.0.0.1:21503 shell monkey -p com.kiloo.subwaysurf -c android.intent.category.LAUNCHER 1
```

A fresh instance boots to the launcher with no manual steps, `wifi_eth` is up,
Houdini initialises, and the installed ARM game runs.

---

## 🖥️ Graphical manager (`lemu-gui`)

```bash
./bin/lemu-gui      # or: ./bin/lemu
```

Or search for **"Lemu"** in the Hyprland / Omarchy launcher (Rofi / Wofi).

System dependencies (not pip packages): **tk** and **python-pillow** provide the
Tk runtime and the screenshot support for the visual keymap editor:

```bash
sudo pacman -S tk python-pillow
```

The Keymapper window has a **Visual Editor…** button that opens a GameLoop-style
editor directly on a live screencap of the guest: pick tap / swipe / hold /
key-event, press the host key, and click (or drag) on the frame to place the
binding — no hand-typed coordinates.  Markers are draggable, re-bindable and
removable, and every change is written straight into the selected scheme.

---

## 🎮 Keyboard mapping (touch-only games)

Android games are driven by **touch gestures**, but QEMU forwards host key
presses into the guest as *keyboard* events, which a touch-only game ignores.
`lemuc keymap` bridges the two: it watches the guest's input devices for the keys
you press on the host and replays each bound key as a synthetic touch gesture.

```bash
./bin/lemuc keymap -i 0                       # start (Ctrl-C to stop)
./bin/lemuc keymap -i 0 --list                # show the current bindings
./bin/lemuc keymap -i 0 --verbose             # log every injected gesture
./bin/lemuc keymap -i 0 -j touch              # force the native touchscreen
./bin/lemuc keymap -i 0 -j input              # force adb shell input
./bin/lemuc keymap -i 0 -p com.kiloo.subwaysurf   # pin one game profile
```

Start it while the instance is running, then focus the emulator window and play.

In the GUI, select the instance and press **⌨ Keymapper**: the dialog edits the
default scheme and each per-game profile, and starts/stops the daemon with the
injector of your choice. The per-instance overlay window (`Start` → control
overlay) carries the same Start/Stop control next to its other actions.

### Injectors

| `--injector` | Mechanism | Per gesture | Needs |
|---|---|---|---|
| `touch` (default via `auto`) | virtual touchscreen created **inside the guest** with `uinput` | **~1 ms** | root in the guest (`su 0`, auto-granted) |
| `input` | `adb shell input tap/swipe` | ~80-90 ms | nothing |

`auto` tries `touch` and silently falls back to `input`. `keyevent` bindings
always go through `input keyevent`, because the virtual device is a touchscreen,
not a keyboard.

The `touch` injector is the fast path. `tools/lemu_touch.c` is compiled statically
on the host into `bin/lemu-touch`, pushed to `/data/local/tmp`, and started as
root; it opens `/dev/uinput`, publishes a device with `INPUT_PROP_DIRECT`,
`BTN_TOUCH` and `ABS_MT_*`, then executes one gesture per line on stdin. Android
therefore sees a **real touchscreen** - `Classes: 0x00000014`
(TOUCHSCREEN|TOUCH_MT), `Sources: 0x00001002`, `Touch Input Mapper (mode - direct)`
- with **no `.idc` file at all**, which is exactly what the USB tablet could not
be made into (see below). Gestures are dispatched in ~1 ms; the measured
tap round-trip is 31 ms for a 30 ms hold and 140.7 ms for a 140 ms swipe, i.e.
the gesture duration plus about a millisecond.

### Default bindings

Written to `instances/<id>/keymap.json` on first use:

| Key | Gesture |
|---|---|
| `A` / `←` | swipe left |
| `D` / `→` | swipe right |
| `W` / `↑` | swipe up (jump) |
| `S` / `↓` | swipe down (roll) |
| `Space` / `Enter` | tap the middle of the screen |

These suit Subway Surfers and most endless runners. Bound keys still reach the
guest as normal key presses as well, which is harmless for touch-only games.

### Per-game profiles

`keymap.json` can carry a `profiles` block. A profile's bindings are **merged over
the default scheme** while its game is focused, so you only describe the
differences:

```json
{
  "bindings": {
    "KEY_D": {"action": "swipe", "from": [0.22, 0.55], "to": [0.82, 0.55], "duration_ms": 140}
  },
  "profiles": {
    "com.kiloo.subwaysurf": {
      "bindings": {
        "KEY_ESC": {"action": "keyevent", "code": 4},
        "KEY_P":   {"action": "keyevent", "code": 4},
        "KEY_F":   null
      }
    }
  }
}
```

* Profile keys may be a package (`com.kiloo.subwaysurf`), a package plus activity
  (`com.kiloo.subwaysurf/.MainActivity`) or a glob (`com.kiloo.*`).
* A `null` binding **un-binds** a key that the default scheme defines.
* The foreground window (`mCurrentFocus`) is polled once a second, so profiles
  switch by themselves as you move between the launcher and games.
  `--profile NAME` pins one and disables the polling.

### How it works

1. `getevent -lq` is streamed from the guest over `adb exec-out`; every
   `EV_KEY ... DOWN` edge is matched against the active bindings (key autorepeat
   is ignored, and the stream is read from *inside* the guest because host
   `/dev/input` is not readable by a normal user).
2. A match becomes a gesture on the virtual touchscreen (`touch`) or an
   `adb shell input` call (`input`). Both are **genuine touchscreen events**, so
   Unity/Unreal games respond exactly as they would to a real finger.
3. Both the foreground poll and the `dumpsys display` geometry probe run on a
   1-second background tick, which keeps them off the injection path - a gesture
   costs no measurable overhead.

### Coordinates

Bindings are **normalised**: `[0.0, 0.0]` is the top-left of the logical display
and `[1.0, 1.0]` its bottom-right. Because the guest letterboxes each app, the
mapper polls the *logical* display size from `dumpsys display` (the
`mOverrideDisplayInfo ... real` field) so the coordinates stay valid as the
resolution changes - the launcher runs at `1024x768` while Subway Surfers is
presented through a `590x768` compatibility display.

The two injectors need different spaces, and mixing them up puts touches in the
wrong place:

* `input` works in the **logical** space (`590x768` in game), matching `screencap`
  one-to-one.
* the `uinput` touchscreen is scaled by Android onto the **physical panel**
  (`1024x768`, `mBaseDisplayInfo`) and the letterbox viewport is subtracted, so a
  logical point `(nx, ny)` maps to the panel fraction

  ```
  fx = (viewport_left + nx * (frame_w - 1)) / panel_w
  fy = (viewport_top  + ny * (frame_h - 1)) / panel_h
  ```

  With the game letterboxed, the touchscreen's reported range is even shifted
  (`X min=-217 max=806`, viewport `[0,0,590,768]` over `[217,0,807,768]`).
  `DisplayGeometry.panel_fraction` implements this; `--verbose` prints both the
  logical and the panel coordinates for every gesture.


Available actions:

```json
{
  "bindings": {
    "KEY_A":     {"action": "swipe", "from": [0.78, 0.55], "to": [0.18, 0.55], "duration_ms": 140},
    "KEY_SPACE": {"action": "tap",    "at": [0.50, 0.50]},
    "KEY_F":     {"action": "hold",   "at": [0.50, 0.60], "duration_ms": 600},
    "KEY_ESC":   {"action": "keyevent", "code": 4}
  }
}
```

| Action | Meaning |
|---|---|
| `swipe` | drag `from` → `to` over `duration_ms` |
| `tap` | a quick touch at `at` |
| `hold` | press and hold at `at` for `duration_ms` |
| `keyevent` | send an Android key code (`4` = Back, `3` = Home, ...) |

Key names are the Linux names `getevent` reports (`KEY_A`, `KEY_LEFT`, ...). To
find one, run `./bin/lemuc adb -i 0 shell getevent -lq` and press the key.

### Why a guest touchscreen instead of QMP or `usb-tablet`

Lemu exposes the pointer as QEMU `-device usb-tablet`, which Android classifies
as an *absolute mouse* (`Sources: 0x2002`), not a touchscreen. A `.idc` override
(`/system/usr/idc/QEMU_QEMU_USB_Tablet.idc` with
`touch.deviceType = touchScreen`) survives a reboot but is **not honoured** by
Android-x86 9.0-r2, so its drags stay mouse events and games ignore them. QMP
`input-send-event` cannot help either: it drives the same device, so it can only
produce mouse input.

The clean QEMU-side fix would be `-device virtio-multitouch-pci`, but this guest
kernel has `CONFIG_VIRTIO_INPUT=m` and **no** `CONFIG_HID_VIRTIO`, so it would
never bind a driver. Instead the touchscreen is created *inside* the guest: the
kernel has `CONFIG_INPUT_UINPUT=y`, and a `uinput` device with `INPUT_PROP_DIRECT`
is recognised by Android's `InputReader` as a real touchscreen with no `.idc` file
involved. `/dev/uinput` is `crw-rw---- system:bluetooth`, so the helper has to run
as root (`su 0`, which the guest's Superuser app grants automatically); the
`input` injector exists as a no-root fallback.

---

## ⚙️ CPU model

Lemu passes `-cpu host,-pmu` to QEMU. The guest **must** see the genuine host
CPU vendor (AMD on the reference machine). Masking it as `GenuineIntel` makes
the guest kernel build a `sysenter`-based 32-bit compat vDSO, and `sysenter`
cannot execute on AMD in long mode - every 32-bit process (and therefore
`mediaserver`) dies with `trap stack segment` at `__kernel_vsyscall+9`. That is
why Houdini's Intel expectation is satisfied by patching the library, not by
lying about the CPU.

The value can be overridden per instance with a `"cpu"` key in
`instances/<id>/config.json`.

---

## 📁 Directory structure

```
lemu/
├── bin/
│   ├── lemu        # universal entrypoint (GUI with no args, else CLI)
│   ├── lemuc       # CLI multi-instance controller
│   ├── lemu-touch  # static guest-side uinput touchscreen helper (built)
│   └── lemu-gui    # Tkinter Omarchy-themed GUI launcher
├── boot/
│   └── androidx86_64/
│       ├── kernel
│       └── initrd.img
├── guest/
│   └── init.sh                           # provisioned guest /system/etc/init.sh
├── images/
│   ├── androidx86_64_master.qcow2         # 64-bit Android-x86 + Houdini (default)
│   ├── androidx86_64_master.prefix.qcow2  # backup (pre-patch)
│   ├── androidx86_master.qcow2            # 32-bit reference
│   ├── system_master.qcow2                # original MEmu system disk
│   └── data_master.qcow2                  # original MEmu userdata
├── instances/
│   ├── 0/          # config.json, data.qcow2 (COW overlay), keymap.json, qemu.pid, console.log
│   └── ...
├── lemu/
│   ├── config.py   # guest flavours, boot artifacts, defaults
│   ├── vm.py       # QEMU/KVM process supervisor + QMP
│   ├── adb.py      # ADB wrapper
│   ├── keymap.py   # key bindings, profiles, geometry, injectors, daemon
│   ├── touch.py    # builds/pushes/drives the guest uinput touchscreen
│   ├── manager.py  # multi-instance manager
│   ├── entry.py    # universal entrypoint
│   ├── cli.py      # `lemuc` implementation
│   └── gui.py      # Tkinter desktop application
├── tools/
│   └── lemu_touch.c  # guest-side touchscreen helper (uinput, line protocol)
└── lemu.desktop    # XDG application launcher
```

---

## 🧰 Troubleshooting

- **`errno=9 /dev/ttyS<n>: no such file or directory`** - only affects the
  original `memu` guest, whose `init.rc` `chmod`s `/dev/ttyS10..15` and
  `chown`s `/dev/ttyS4`. Set `"serial_ports": 24` for that guest; the `x86_64`
  default uses `0`.
- **Black/blank screen** - the guest may simply be asleep. Send
  `adb -s 127.0.0.1:21503 shell input keyevent KEYCODE_WAKEUP`. `lemu_network`
  also pins the display on.
- **ARM game refuses to install / crashes** - confirm `persist.sys.nativebridge`
  is `1` and that `logcat` shows `houdini enabled`.
- **Everything traps with `trap stack segment`** - the CPU vendor is masked
  (see *CPU model*). Use `host,-pmu`, not `host,vendor=GenuineIntel`.
- **A key does nothing in a game** - bound keys are sent as touch gestures, so
  first check `./bin/lemuc keymap -i 0 --verbose` prints a line for the key. If
  it does, the gesture is landing outside the app's window (adjust `from`/`to`
  in `instances/<id>/keymap.json`) or the game needs a different gesture. If
  there is no line at all, the emulator window did not have focus when the key
  was pressed.
- **`touch` injector unavailable** - it needs root in the guest for
  `/dev/uinput`. `auto` falls back to `input`; to see why, check
  `./bin/lemuc keymap -i 0 -j touch --verbose`. A stale helper can be killed with
  `adb -s 127.0.0.1:21503 shell "su 0 pkill lemu-touch"`.
- **Key gestures feel laggy** - you are on the `input` fallback (~80 ms per
  gesture, an `app_process` start-up each time). With `-j touch` / `auto` on a
  rooted guest a gesture costs about a millisecond, so a lane change is bounded
  only by the gesture's own `duration_ms`.
- **The keymapper uses the wrong scheme** - profiles follow `mCurrentFocus`; if
  the game keeps focus through a dialog the profile stays applied (which is
  usually what you want). Pin one explicitly with `-p <package>`.
- **Touches land in the wrong place only with `-j touch`** - the virtual
  touchscreen is scaled onto the physical panel while bindings are logical; the
  mapper converts for you, but a game that changes its own display size mid-run
  can briefly lag the one-second geometry refresh.

### Known limitations

- Apps that require Google Play Services / Play Store are not preinstalled.
- **Guest GL is software-only on the stock Android-x86 image** — it ships only
  `libGLES_android.so` (no Mesa / dri / gralloc hardware stack), so 3D games
  render on the CPU and are frame-rate capped by your cores regardless of the
  `gpu` setting. The `virgl` mode activates automatically with a guest that has
  a Mesa client stack (e.g. Bliss OS 16). Lowering guest resolution and freeing
  host CPU headroom are the practical levers meanwhile.
- **Display device**: the default VGA device is `vmware` (vmware-svga). With
  `std`/`virtio` the host window presents the guest framebuffer with the red and
  blue channels swapped on this image (verified against guest screencaps — the
  screencap itself is always correct); vmware-svga blits with the right byte
  order. Set `vga` back to `std` if your guest comes out correct anyway.
