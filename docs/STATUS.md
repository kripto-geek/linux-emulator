# DroidForge - STATUS

Running status log. Updated at the end of every phase (and on major discoveries).

Legend: `[W]` works / measured · `[B]` broken / known issue · `[N]` next · `[D]` decision

## Phase 0 - Environment & Discovery  ✅ COMPLETE

### Findings (measured on this host, 2026-10-05)

| Item | Result |
|---|---|
| OS | Omarchy 4.0.4 (Arch-based), kernel `7.2.5-3-omarchy` |
| CPU | AMD Ryzen 9 5900HS, 16 cores — `svm` (AMD-V) on, AVX/AVX2 |
| `/dev/kvm` | present, `0666`, user can read/write |
| GPU | **NVIDIA RTX 3050 Mobile** (active GL, driver 610.57) + AMD Cezanne iGPU |
| Session | **Wayland** (Hyprland) |
| Audio | PipeWire 1.6.8 active |

### Decisions
- **[D] Guest image:** Bliss OS 16.9.7 x86_64 FOSS (Generic). SHA-256 `735cb9...ba68e`. FOSS = no gapps/houdini, ships `libndk_translation`.
- **[D] Hypervisor:** QEMU + KVM, QMP over unix socket. No libvirt.
- **[D] Graphics:** `virtio-gpu-gl-pci` (virgl) first. NVIDIA host driver is active; virglrenderer uses the DRI3/GL context.
- **[D] Audio:** QEMU `-audio driver=pipewire`.
- **[D] Network:** slirp + host-forwarded ADB port per instance.
- **[D] Storage:** read-only qcow2 base + per-instance qcow2 overlay.

### Files
- `tools/doctor`, `tools/install-deps.sh`, `docs/STATUS.md`, `README.md`, `THIRD_PARTY.md`, `COPYING`.
- doctor baseline post-install: **18 passed, 3 warnings (non-blocking), 0 failures**.

## Phase 1 - Boot a GPU-accelerated Android VM  🔄 IN PROGRESS

### Done
- [W] **`core/` C++20 library** (UI-independent, no Qt dep):
  - `QemuCommandBuilder` — emits full QEMU argv: q35, KVM, virtio-gpu-gl-pci (virgl), PipeWire audio + HDA, slirp + hostfwd ADB, QMP socket, SMBIOS instance-id serial, linked-clone overlay drive, `-S` start-paused.
  - `QmpClient` — line-oriented QMP over unix socket (handshake, `execute`, `cont`/`stop`/`quit`, `query-status`, HMP escape) + self-contained JSON parser/dumper.
- [W] **35 gtest unit tests** all passing (22 builder + 13 JSON).
- [W] CMake build: `core` always, `tests` always, `app` when Qt6 present.
- [W] `app/` minimal Qt6 Widgets bootstrap (proves Qt links against core).
- [W] `tools/image-prep.sh` — download + SHA-256 verify + create read-only base qcow2.
- [W] `tools/boot.sh` — creates overlay (linked clone) and launches QEMU. Mirrors `QemuCommandBuilder` (cross-checked by tests).
- [N] Bliss OS ISO download in progress (~670 MB / 1.75 GB, ~1.7 MB/s).

### Next (to hit Phase 1 acceptance)
- [N] Finish ISO download; verify SHA-256 via `tools/image-prep.sh`.
- [N] Boot with `tools/boot.sh demo --first-boot`; confirm guest reaches GRUB/Android.
- [N] Confirm GPU is hardware-accelerated in-guest (GL renderer string, not SwiftShader).
- [N] `adb connect 127.0.0.1:<port>` lists the instance.
- [N] Install a test APK; measure boot time (< 60 s target).
- [N] Document per-GPU results (NVIDIA vs iGPU) in `docs/`.

### Known risks
- [B] **virgl on NVIDIA host** — virglrenderer is a Mesa library; it should work via the NVIDIA DRI3 context, but this is untested. Fallback: force the AMD iGPU, or drop to `-device virtio-gpu-pci` (software).
- [B] **First-boot install** — the base qcow2 is created *empty*; the actual Android install happens by booting the ISO and running the installer. This is a one-time semi-manual step (documented). A fully automated install (kickstart-style) is a Phase 3 nicety.
- [N] `libndk_translation` verification — must confirm `ro.product.cpu.abilist` includes `arm64-v8a`/`armeabi-v7a` after boot.

## Phase 2 - Touch-injection research spike  ⏸ NOT STARTED
## Phase 3 - The gaming shell  ⏸ NOT STARTED
## Phase 4 - Packaging & polish  ⏸ NOT STARTED
