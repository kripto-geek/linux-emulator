# THIRD_PARTY

Third-party components used by DroidForge and their licenses. No telemetry.
No proprietary blobs are bundled. Where a component is a *runtime* dependency
(installed via pacman) rather than a vendored file, that is noted.

## Runtime dependencies (installed via `pacman`, not vendored)

| Component | Role | License |
|---|---|---|
| QEMU (`qemu-full`) | Hypervisor, virtio-gpu, QMP | GPL-2.0-or-later (with patent grant) |
| virglrenderer | Host-side GL renderer for `virtio-gpu-gl-pci` | MIT |
| android-tools (`adb`) | Control channel (APK install, shell, screenshots, `getprop`) | Apache-2.0 (AOSP) + BSD-3 portions |
| Mesa | Guest-visible / host GL on AMD; used indirectly by virglrenderer | Various per-driver (MIT/GPL) |
| PipeWire / libpipewire | Host audio | MIT |
| Qt 6 (qt6-base, qt6-quick, ...) | UI framework | LGPL-3.0-or-later (C++ core & GUI); some modules GPL-3.0 |
| Wayland / wayland-protocols | Pointer constraints, relative-pointer for mouse capture | MIT |
| libxkbcommon | Keyboard scancode → keysym translation | MIT |
| nlohmann-json | JSON config/profile parsing | MIT |
| fmt | Logging / formatting | MIT |
| GoogleTest / GoogleMock | Unit tests for `core/` | BSD-3-Clause |
| virtiofsd (libfuse, fuse3) | Shared folder (Phase 3) | BSD-3-Clause (libfuse), LGPL-3.0 (fuse3) |

## Guest image

| Component | Role | License | Notes |
|---|---|---|---|
| Bliss OS 16.9.7 x86_64 (FOSS, Generic) | Guest Android x86_64 | AOSP license (Apache-2.0 + BSD + various) + BlissOS additions | Downloaded by the user (not redistributed by DroidForge). Ships `libndk_translation` for ARM app translation. No gapps, no `libhoudini`. |
| `libndk_translation` (prebuilt, inside Bliss) | Translates ARM64 / ARM32 guest apps to x86_64 | Apache-2.0 (Google, shipped in the guest) | Not bundled or redistributed by DroidForge. Verified via `ro.product.cpu.abilist`. |

## Explicitly **not** used (per project constraints)

- `libhoudini` / Intel libhoudini / WSA ARM translation — **not bundled, not redistributed**.
- Nox / MEmu / Gameloop / BlueStacks — **not copied, not decompiled, not patched**.
- No anti-cheat evasion, no Play Integrity spoofing, no hiding of root/emulation status.

## Tools (scripts)

| Component | License |
|---|---|
| `tools/doctor`, `tools/install-deps.sh`, `tools/boot.sh`, `tools/image-prep.sh` | GPL-3.0-or-later (project license) |

## Vendor notes

- **NVIDIA** (when the host uses it): the *host* driver is proprietary, but DroidForge
  only talks to it through the public GL/DRI3 and Vulkan entry points that
  virglrenderer / Vulkan ICDs already use. No NVIDIA code is bundled.
- **AMD / Mesa** (iGPU path): Mesa is open-source. `vulkan-radeon` is used only
  for the Phase-2 Venus experiment.
