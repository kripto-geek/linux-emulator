# DroidForge – GPU Acceleration Notes

## Host environment (test machine)

| Component | Detail |
|---|---|
| CPU | AMD Ryzen 9 5900HS (Renoir) |
| dGPU | NVIDIA GeForce RTX 3050 Mobile (GA107M), driver 610.57.04 |
| iGPU | AMD Radeon Vega / Cezanne (card2, renderD129) |
| Mesa | 26.2.2-arch1.1 |
| QEMU | 11.1.1 |
| Session | Wayland |
| virglrenderer | 1.11.0 |

---

## Approach A – virtio-gpu-gl-pci (virgl) ✅ WORKS with env fix

**Device:** `-device virtio-gpu-gl-pci`  
**Display backend:** `-display egl-headless` (headless; for windowed gaming use
`-display sdl,gl=on` or `-display gtk,gl=on`)

### The dual-GPU problem on NVIDIA-primary laptops

On a laptop where the NVIDIA proprietary driver is the active X11/Wayland
compositor GPU, the default EGL vendor is `libEGL_nvidia.so`. virglrenderer is
a Mesa library; it creates its GL context via Mesa's EGL implementation
(`libEGL_mesa.so`). The NVIDIA EGL vendor does not expose the Mesa GL profiles
that virglrenderer requires (**OpenGL ≥ 3.3 core profile via Mesa**).

Symptom:
```
Unable to create OpenGL context >= 3.0
failed to initialize vrend renderer
qemu-system-x86_64: virgl could not be initialized: 22
```

### The fix

Force the `libEGL_mesa.so` vendor ICD and point it at the AMD iGPU render node
(`/dev/dri/renderD129`):

```bash
export __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/50_mesa.json
export EGL_PLATFORM=gbm
export LIBGL_DRIVERS_PATH=/usr/lib/dri
```

With these vars, virglrenderer gets:
```
EGL vendor  : Mesa Project
EGL driver  : radeonsi
OpenGL      : 4.6 (Core Profile) Mesa 26.2.2-arch1.1 (AMD Renoir)
```

This is the GL context virglrenderer uses to translate guest OpenGL calls. The
QEMU process itself runs headlessly (or via a separate display window); the
AMD render node is only used for GPU compute/rendering by virglrenderer.

### How DroidForge launches QEMU (boot.sh + C++ builder)

`tools/boot.sh` now exports these vars before the `qemu-system-x86_64` call.
The `QemuCommandBuilder::envForGpu()` helper returns the required env-var map
for each `GpuMode`, which `InstanceManager::launch()` will apply via
`execvpe`.

### Per-GPU guidance

| Host GPU setup | Recommended gpu_mode | Notes |
|---|---|---|
| AMD only (Mesa) | `virgl` | Works out of the box; no env vars needed |
| Intel only (Mesa iris) | `virgl` | Works out of the box |
| NVIDIA proprietary + AMD/Intel iGPU | `virgl` + env fix | Set `__EGL_VENDOR_LIBRARY_FILENAMES`, `EGL_PLATFORM=gbm`; iGPU handles virgl |
| NVIDIA proprietary only (no iGPU) | `virtio` (software) | virglrenderer cannot run; software rendering is the fallback |
| NVIDIA open kernel module + Mesa nouveau | `virgl` | Experimental; nouveau must be active |

---

## Approach B – Venus (virtio-vulkan-pci) ❌ NOT AVAILABLE in QEMU 11.1.1 (Arch)

`-device virtio-vulkan-pci` is not compiled into the Arch `qemu-full` 11.1.1 build.
Venus requires a QEMU built with `--enable-virglrenderer` **and** upstream
virglrenderer ≥ 1.0 with Venus support, plus the guest must be an Android 12+
image with Venus Vulkan ICD.

Status: **deferred**. Revisit when Arch packages QEMU with Venus enabled, or
when we provide an opt-in QEMU build in `packaging/`.

---

## Approach C – gfxstream ❌ NOT IMPLEMENTED

gfxstream is Google-internal / Cuttlefish-specific. No upstream QEMU device
as of 2024. **Stretch goal only.**

---

## Guest GPU verification (once Android boots to home screen)

```bash
# Via ADB after install
adb -s 127.0.0.1:5555 shell dumpsys SurfaceFlinger | grep -i "EGL version"
adb -s 127.0.0.1:5555 shell getprop ro.hardware.egl
adb -s 127.0.0.1:5555 shell getprop ro.product.cpu.abilist
```

Expected with virgl working:
- `EGL version = 1.5 Mesa ...`
- `ro.hardware.egl = mesa` (Bliss OS / Android-x86 with Mesa)
- `ro.product.cpu.abilist` includes `x86_64,x86` (native); `arm64-v8a,armeabi-v7a` if libndk_translation is present

---

## FPS / performance expectations

| Mode | Host GPU | Expected guest FPS |
|---|---|---|
| virgl (AMD iGPU, Mesa 4.6) | AMD Renoir | 30–60 FPS UI, games vary |
| virtio (software llvmpipe) | — | 10–25 FPS |

Measurement methodology: `adb shell dumpsys gfxinfo <package> framestats`
