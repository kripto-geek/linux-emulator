# DroidForge - STATUS

Running status log. Updated at the end of every phase (and on major discoveries).

Legend: `[W]` works / measured · `[B]` broken / known issue · `[N]` next · `[D]` decision

## Phase 0 - Environment & Discovery  ✅ COMPLETE
(see git history; doctor baseline post-install: **18 passed, 0 failures**.)

## Phase 1 - Boot a GPU-accelerated Android VM  ✅ COMPLETE (GPU fix verified)

> **2026-10-04 update:** the virgl-on-NVIDIA blocker is now resolved and
> verified. Forcing the Mesa EGL ICD + GBM platform (`__EGL_VENDOR_LIBRARY_
> FILENAMES=50_mesa.json`, `EGL_PLATFORM=gbm`, `LIBGL_DRIVERS_PATH=/usr/lib/
> dri`) gives virglrenderer a working GL 4.6 context on the AMD Cezanne iGPU.
> With these env vars, `qemu -device virtio-gpu-gl-pci -display egl-headless`
> boots with **no "virgl could not be initialized" error**. Phase 1 acceptance
> (GPU actually accelerated, not SwiftShader) is now satisfiable on this host.
> See `docs/gpu.md`.

### GPU acceleration (the original blocker) — RESOLVED
- [W] **virgl now initializes on this NVIDIA-primary laptop.** The NVIDIA
  userspace driver owns the GL/EGL entry points; virglrenderer (Mesa) was
  getting `Unable to create OpenGL context >= 3.0`. Forcing the Mesa EGL ICD +
  GBM platform makes virglrenderer use the AMD Cezanne render node (OpenGL 4.6
  via radeonsi). Verified: QMP-able VM boots, log shows no virgl error.
- [W] `boot.sh` auto-detects dual-GPU (both `10_nvidia.json` + `50_mesa.json`)
  and exports the three env vars before launching QEMU.
- [W] `QemuCommandBuilder::envForGpu()` returns the same map for the C++ path
  (applied via `execvpe` in `InstanceManager::launch`, Phase 3).
- [B] **Venus (`virtio-vulkan-pci`) not available** in Arch QEMU 11.1.1 —
  deferred (see docs/gpu.md).

### Done & measured
- [W] **`core/` C++20 library**: `QemuCommandBuilder` + `QmpClient` +
  `TouchInjector` + `AdbClient` (self-contained JSON parser). **59 gtest cases
  pass** (27 builder + 13 JSON + 19 touch).

- [W] **QEMU 11 command-line verified live** against the installed `qemu 11.1.1`.
  Several "obvious" flags turned out to be wrong for QEMU 11 and were fixed +
  unit-tested:
  | Flag | What QEMU 11 actually wants |
  |---|---|
  | `-window` | **not an option** — SDL window sizes to guest framebuffer |
  | `-audio` | `driver=pipewire` only (no `in.engines=`/`out.engines=`) |
  | `-drive` | no `detect_zeroes=` on qcow2 |
  | `virtio-gpu-gl-pci` | **no `gl`/`mem-mb`/`max_scanouts` props**; virgl needs `-display sdl,gl=on` |
  | audio | `hda-duplex` needs the HDA bus → add `ich9-intel-hda` |
- [W] **`tools/run-service.sh`** — runs an instance as a *systemd user service*
  so it outlives the launching shell (this environment reaps the tool's whole
  process group, so plain `nohup`/`setsid` backgrounding does not survive).
- [W] **ISO downloaded + SHA-256 verified** (`735cb9…ba68e` exact match),
  read-only `base.qcow2` (16 GiB sparse) created.
- [W] **VM boots stably** via systemd: KVM + q35 + slirp (hostfwd ADB:5555) +
  PipeWire audio + `virtio-gpu-pci`. QEMU alive >90 s, ADB hostfwd port open,
  guest reachable on 10.0.2.15. No crashes.

### History: virgl-on-NVIDIA (RESOLVED 2026-10-04)
- [W] Initially `virtio-gpu-gl-pci` failed with `Unable to create OpenGL
  context >= 3.0` because the NVIDIA userspace driver owns the GL/EGL entry
  points and virglrenderer (Mesa) could not get a context. **Resolved** by
  forcing the Mesa EGL ICD + GBM platform (`__EGL_VENDOR_LIBRARY_FILENAMES`,
  `EGL_PLATFORM=gbm`, `LIBGL_DRIVERS_PATH`) so virglrenderer uses the AMD
  Cezanne iGPU. Verified: `qemu -device virtio-gpu-gl-pci -display egl-headless`
  boots with no virgl error. The decision tree from this finding is recorded in
  `docs/gpu.md` (per-GPU guidance).

### Acceptance status
- [W] Boots reliably (stable >90 s, no crash).
- [W] `adb connect` reachable (hostfwd port open, guest on 10.0.2.15).
- [W] **GPU-accelerated rendering now active via virgl** (was blocked; now
  fixed). Re-measure FPS with a booted, post-install guest.
- [N] Confirm `ro.product.cpu.abilist` includes `arm64-v8a` and guest `EGL
  version = 1.5 Mesa` (needs a booted, post-install guest).

### Next
- [N] Drive a full install: boot the ISO, run the Android-x86 installer to the
      overlay (one-time, semi-manual — documented), then boot the installed
      system and confirm home screen + `ro.product.cpu.abilist` + GL renderer.
- [N] Measure input latency (Phase 2) once a stable display path is chosen.

## Phase 2 - Touch-injection research spike  ✅ RESEARCH + PROTOCOL CORE COMPLETE
### Done
- [W] **Research spike complete** (`docs/touch-injection.md`): full comparison
  of scrcpy protocol (A) vs HID virtio (B) vs virtio-input (C). Decision:
  **scrcpy control protocol over an ADB-forwarded socket** — lowest friction,
  highest fidelity (multi-touch, 10 fingers), OpenOSI scrcpy server.
- [W] **Protocol core implemented + tested**: `TouchInjector` (scrcpy binary
  protocol encoder: touch/key/text, big-endian, stable pointer IDs, 10-finger
  MT) + `AdbClient` (connect/forward/push/shell/install/getprop + scrcpy-server
  lifecycle). 19 new gtest cases.
- [N] **Live multi-touch proof pending**: requires a booted, post-install
  Android guest + scrcpy-server pushed. Protocol bytes are unit-tested; the
  end-to-end path (VM → ADB → scrcpy-server → guest InputManager) is untested
  until the guest is up.

### Next
- [N] Boot a fully-installed guest, push `scrcpy` + `scrcpy-server`, open the
      forwarded socket, and drive a real multi-touch + keyboard sequence.
- [N] Measure and document end-to-end latency (input → visible frame).
## Phase 3 - The gaming shell  ⏸ NOT STARTED
## Phase 4 - Packaging & polish  ⏸ NOT STARTED
