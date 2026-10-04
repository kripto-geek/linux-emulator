# DroidForge - STATUS

Running status log. Updated at the end of every phase (and on major discoveries).

Legend: `[W]` works / measured · `[B]` broken / known issue · `[N]` next · `[D]` decision

## Phase 0 - Environment & Discovery

### Findings (measured on this host, 2026-10-05)

| Item | Result | Notes |
|---|---|---|
| OS | Omarchy 4.0.4 (Arch-based) | `ID=omarchy`, `ID_LIKE=arch` |
| Kernel | 7.2.5-3-omarchy | very recent; KVM modules loaded (`kvm_amd`, `kvm`) |
| CPU | AMD Ryzen 9 5900HS, 16 cores | `svm` (AMD-V) enabled, AVX/AVX2 present |
| `/dev/kvm` | present, mode `0666` | current user can read/write without group |
| `kvm` group | empty, user `k` not a member | not required here (ACL grants access) but harmless to join |
| GPU (discrete) | NVIDIA GA107M (RTX 3050 Mobile), driver 610.57.04 | proprietary; active GL provider |
| GPU (iGPU) | AMD Cezanne (Vega Mobile) | Mesa 26.2.2; present for dual-GPU laptops |
| Active GL renderer | NVIDIA GeForce RTX 3050 Laptop GPU | OpenGL 4.6.0 |
| Session | **Wayland** (Hyprland + quickshell) | `WAYLAND_DISPLAY=wayland-1`, `DISPLAY=:0` (X11 legacy also up) |
| Audio | PipeWire 1.6.8 (user service active) | `wpctl` reachable |
| QEMU | **not installed** (Arch repo has `qemu-full 11.1.1-1`) | see "install list" below |
| virglrenderer | **not installed** (Arch repo has `1.3.0-2`) | |
| adb | **not installed** (Arch repo has `android-tools 37.0.0-5`) | |
| Qt 6 | `qt6-base 6.11.2-3` + `qt6-wayland` already present | QML/UI work can start once cmake/ninja land |
| Vulkan | Mesa + radeon ICDs present; `nvidia_icd.json` also present | both paths available for Venus experiment |

### Decisions

- **[D] Guest image:** **Bliss OS 16.9.7 x86_64 (FOSS, Generic)** — Android 11 based, current latest stable in the official x86_64 channel.
  - URL: `https://sourceforge.net/projects/blissos-x86/files/Official/BlissOS16/FOSS/Generic/Bliss-v16.9.7-x86_64-OFFICIAL-foss-20241011.iso`
  - SHA-256: `735cb962ec6bd92b62eb82a812831a38d79a0dfdf12b7973d2d0f7ab001ba68e`
  - FOSS build (no gapps, no proprietary blobs) — matches our "don't bundle proprietary" constraint.
  - BlissOS17 folder on SourceForge is currently empty (no files yet), so 16.9.7 is the newest verified artifact.
  - BlissOS 16 = "Android 13" branding per the BlissOS org; the x86_64 Official ISOs in the `BlissOS16` folder are the FOSS/gapps pair we use.
  - Confirms `libndk_translation` for ARM app translation — verify via `getprop ro.product.cpu.abilist` after first boot.
- **[D] Hypervisor:** QEMU + KVM, controlled over QMP on a unix socket. No libvirt.
- **[D] Graphics (first):** `virtio-gpu-gl-pci` (virgl). QEMU 11.1.1 exposes this device; virglrenderer 1.3.0 uses the host's active GL provider (here: NVIDIA).
- **[D] Guest audio:** QEMU `-audio` with `driver=pipewire` (fall back to `pulse`).
- **[D] Networking:** QEMU user-mode (slirp) with host-forwarded ADB port per instance.
- **[D] Storage:** one read-only qcow2 base + per-instance qcow2 overlay.
- **[D] Mouse capture (Phase 3):** Wayland pointer-constraints + relative-pointer **and** X11 grab. Both paths to be implemented since the host is Wayland but users may also be on X11.

### Install list (verified against live Arch repos, 2026-10-05)

Run `sudo bash tools/install-deps.sh`. Package set:

```
qemu-full            virglrenderer            android-tools
vulkan-icd-loader    vulkan-mesa              vulkan-radeon   vulkan-tools
qt6-base             qt6-quick                qt6-quickcontrols2
qt6-quick3d          qt6-declarative          qt6-5compat     qt6-wayland
qt6-x11extras        qt6-sql-sqlite           qt6-tools
cmake                ninja                    base-devel
nlohmann-json        fmt
gtest                gmock
wayland              wayland-protocols        libxkbcommon    xkeyboard-config
libfuse              fuse3                   virtiofsd
e2fsprogs            dosfstools              util-linux      parted
pipewire             libpipewire
mesa-utils           pciutils
```

Post-install (as your normal user):
```
sudo usermod -aG kvm $USER    # optional here (ACL already grants /dev/kvm), still good hygiene
# re-login (or run via: sg kvm -c '...')
tools/doctor
```

### Doctor baseline (run on 2026-10-05, before installs)

```
11 passed, 5 warnings, 4 failures
Failures: QEMU not installed, virglrenderer not installed, adb not installed.
Warnings: cmake, ninja, virtiofsd not installed; user not in kvm group (harmless).
```

### Files in this phase

- `tools/install-deps.sh` — idempotent `pacman -S --needed` installer (requires root).
- `tools/doctor` — read-only environment checker with actionable fixes.
- `docs/STATUS.md` — this file.
- `README.md` — project overview + honest anti-cheat disclaimer.

### Next (Phase 1)

- [N] Get user approval to run `sudo bash tools/install-deps.sh`.
- [N] Add user to `kvm` group (optional, hygiene).
- [N] Download Bliss OS 16.9.7 FOSS ISO; verify SHA-256.
- [N] Convert ISO → read-only qcow2 base image (`base.qcow2`).
- [N] Write `core/qemu_cmdline.{h,cpp}` (pure command builder, unit-tested).
- [N] Write `tools/boot.sh` that assembles the overlay + runs QEMU with KVM, virtio-gpu-gl-pci, QMP socket, pipewire audio, slirp + ADB host-forward.
- [N] Verify: `adb devices` lists the instance; guest GL renderer is NVIDIA/Mesa (not `GLES: ... SwiftShader`); home screen < 60 s.

## Phases 2-4

_Not started yet. Will be appended as work proceeds._
