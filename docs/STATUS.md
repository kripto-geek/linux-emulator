# DroidForge - STATUS

Running status log. Updated at the end of every phase (and on major discoveries).

Legend: `[W]` works / measured · `[B]` broken / known issue · `[N]` next · `[D]` decision

## Phase 0 - Environment & Discovery  ✅ COMPLETE
(see git history; doctor baseline post-install: **18 passed, 0 failures**.)

## Phase 1 - Boot a GPU-accelerated Android VM  🟡 PARTIAL (critical GPU finding)

### Done & measured
- [W] **`core/` C++20 library**: `QemuCommandBuilder` + `QmpClient` (with a
  self-contained JSON parser). **35 gtest cases pass.**
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

### ⚠ Critical finding: virgl does NOT work on this host
- [B] **`-display sdl,gl=on` + `virtio-gpu-gl-pci` fails to initialize virgl:**
  ```
  Unable to create OpenGL context >= 3.0
  failed to initialize vrend renderer
  qemu-system-x86_64: virgl could not be initialized: 22
  ```
  Root cause: this is an **NVIDIA-dominant laptop** (RTX 3050 Mobile as the
  active GL provider, driver 610.57). `virglrenderer` is a **Mesa** library and
  cannot create a GL context on the proprietary NVIDIA driver. The AMD Cezanne
  iGPU exposes **no usable Mesa render node** (`DRI_PRIME`/explicit card
  selection both still report NVIDIA; the iGPU has no Mesa GL path on this
  system). QEMU falls back to software rendering and the VM still boots.
- **[D] Decision:** for this host the Phase 1 acceptance ("GPU acceleration
  actually active, not SwiftShader") **cannot be met with virgl**. Options, in
  order of preference:
  1. **Documented fallback to `virtio-gpu-pci` (software)** — works today,
     usable for bring-up and non-gaming use; not the performance target.
  2. **Venus (Vulkan, `-device virtio-vulkan-pci`)** — needs a Vulkan ICD the
     host exposes to QEMU; `nvidia_icd.json` is present, so this is the most
     promising path to *hardware* acceleration on this machine. → Phase 2
     experiment.
  3. **Force a Mesa-owned DRI node** (e.g. kernel `video=efifb`/`nomodeset`
     for the iGPU, or a headless offload setup) so virglrenderer gets a
     working context. Fragile on a dual-GPU laptop; last resort.

### Acceptance status
- [W] Boots reliably (stable >90 s, no crash).
- [W] `adb connect` reachable (hostfwd port open, guest on 10.0.2.15).
- [N] **60 FPS / GPU-accelerated rendering: NOT met on this host** (virgl
  blocked by NVIDIA driver). Re-measure after the Venus experiment.
- [N] Confirm `ro.product.cpu.abilist` includes `arm64-v8a` (needs a booted,
  post-install guest — see next step).

### Next
- [N] Drive a full install: boot the ISO, run the Android-x86 installer to the
      overlay (one-time, semi-manual — documented), then boot the installed
      system and confirm home screen + `ro.product.cpu.abilist` + GL renderer.
- [N] **Venus experiment** (`virtio-vulkan-pci` + NVIDIA Vulkan ICD) to get
      real hardware acceleration; document per-GPU results in `docs/gpu.md`.
- [N] Measure input latency (Phase 2) once a stable display path is chosen.

## Phase 2 - Touch-injection research spike  ⏸ NOT STARTED
## Phase 3 - The gaming shell  ⏸ NOT STARTED
## Phase 4 - Packaging & polish  ⏸ NOT STARTED
