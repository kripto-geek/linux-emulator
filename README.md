# DroidForge

An open-source, hardware-accelerated Android gaming emulator for **Linux** (first
supported host: **Arch Linux**). Built for gamers who want a Nox / MEmu /
Gameloop-style experience on their own desktop — without proprietary blobs,
without libvirt, and without pretending we can hide from anti-cheat.

> **Working name.** "DroidForge" is a placeholder and easy to rename. The CMake
> project and `PKGBUILD` use a single `PROJECT_NAME` variable so the rename is
> a one-line change.

## What this is (and is not)

- **Is:** a desktop app that creates multiple Android `x86_64` VMs on QEMU +
  KVM, installs APKs, maps keyboard/mouse/gamepad to touch, and plays with low
  latency. Each instance is a linked clone (qcow2 overlay) of a shared
  read-only base image.
- **Is not:** a Play Store re-skin. It is not Nox, MEmu, or Gameloop. We do not
  copy, decompile, or patch any of those. We do not bundle `libhoudini` or any
  other proprietary ARM-translation blob — guest ARM translation uses the
  open-source `libndk_translation` shipped inside the guest image.

### Honest disclaimer (anti-cheat / competitive play)

Some competitive titles (e.g. **PUBG Mobile, Call of Duty Mobile, Valorant**,
and others with aggressive anti-cheat) **may detect, block, or penalize
unofficial emulators**. Some ban for the use of any non-official runtime. This
project does **not** implement, and does **not** intend to implement, any
technique to evade, spoof, or defeat anti-cheat, emulator-detection, or Play
Integrity. Root and emulation status are not hidden from apps. **You play at
your own risk.**

## Architecture (target)

| Layer | Choice | Why |
|---|---|---|
| Hypervisor | QEMU + KVM | no libvirt dependency; QMP over a unix socket is enough for our control channel |
| Guest image | Bliss OS 16 (x86_64, FOSS) — Android 11/13 class | open-source, ships `libndk_translation`, verified download + checksum |
| Graphics | `virtio-gpu-gl-pci` (virgl) first; Venus (Vulkan) experiment later; gfxstream is a stretch | virgl gives us a real host-GL context on the guest with QEMU 11.1 |
| Storage | one read-only qcow2 base + per-instance qcow2 overlay | linked clones; snapshots via QMP |
| Networking | QEMU user-mode (slirp) + host-forwarded ADB port per instance | no host firewall changes; ADB is our control channel |
| Audio | QEMU `-audio driver=pipewire` (fall back to `pulse`) | matches the Arch default audio stack |
| Control | `adb` (Arch `android-tools`) | APK install, shell, screenshots, `getprop` |
| Core | C++20, Qt 6, CMake | UI-independent library (`core/`) with unit tests; Qt UI in `app/` |

## Repo layout

```
core/        UI-independent library: instance manager, QEMU cmd builder,
             QMP client, keymap engine, ADB wrapper.  Unit-tested.
app/         Qt 6 UI: instance manager window, per-instance window,
             keymap overlay editor, one-click wizard, QoL (FPS/CPU/RAM overlay,
             screenshot, recording, shared folder).
tools/       doctor, install-deps.sh, boot.sh, image-prep.sh, helpers.
docs/        STATUS.md, touch-injection.md, performance tuning, GPU notes.
packaging/   arch/PKGBUILD, .desktop, icon, optional systemd user unit.
tests/       core-library unit tests (gtest/gmock).
```

## Quick start (Phase 0 complete)

```sh
# 1. install dependencies (verified against live Arch repos)
sudo bash tools/install-deps.sh

# 2. (optional, hygiene) join the kvm group
sudo usermod -aG kvm $USER     # re-login, or use: sg kvm -c '...'

# 3. run the environment checker
tools/doctor
```

## Status

See [docs/STATUS.md](docs/STATUS.md) for the current, measured state — what
works, what's broken, and what's next.

## Licensing

Project license: **GPL-3.0-or-later** (to match QEMU, virglrenderer, and the
Bliss OS source). All third-party components and their licenses are listed in
[THIRD_PARTY.md](THIRD_PARTY.md). No telemetry. No proprietary blobs bundled.
