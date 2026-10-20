"""
Configuration management for Lemu (MEmu Linux Reconstruction).
"""
import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
IMAGES_DIR = BASE_DIR / "images"
INSTANCES_DIR = BASE_DIR / "instances"

# ---------------------------------------------------------------------------
# Guest flavours
# ---------------------------------------------------------------------------
# "x86_64" Android-x86 9.0-r2 x86_64 (default). Boots under plain QEMU/KVM with
#          the guest's own software GL, so SurfaceFlinger, zygote and the
#          launcher come up. Its /system carries MEmu's Houdini native bridge
#          (libnb -> libhoudini), which gives the guest real arm64-v8a /
#          armeabi-v7a support. The per-instance COW overlay is the whole boot
#          disk. Booted with QEMU's external -kernel/-initrd (see BOOT below).
# "x86"    Android-x86 9.0-r2 x86 (32-bit), kept for reference.
#          Note: MEmu's Houdini needs a genuine x86-64 kernel, so 64-bit ARM
#          apps do not run on this guest.
# "memu"   The original MEmu Play VirtualBox guest, kept for reference / RE
#          work. On Linux it only reaches a root shell + adbd: its display and
#          GL path run over MEmu's proprietary Windows host pipes (MEmuPipe*
#          PCI devices, HGCM and a serial-backed OpenGL translator) which QEMU
#          cannot provide.
GUEST_X86_64 = "x86_64"
GUEST_X86 = "x86"
GUEST_MEMU = "memu"
DEFAULT_GUEST = GUEST_X86_64

X86_64_MASTER_IMAGE = IMAGES_DIR / "androidx86_64_master.qcow2"
X86_MASTER_IMAGE = IMAGES_DIR / "androidx86_master.qcow2"
MEMU_SYSTEM_IMAGE = IMAGES_DIR / "system_master.qcow2"
MEMU_DATA_MASTER = IMAGES_DIR / "data_master.qcow2"

# ---------------------------------------------------------------------------
# Boot artifacts
# ---------------------------------------------------------------------------
# Android-x86 is booted with an external kernel + initramfs rather than through
# a bootloader on the disk. The guest's own copy lives in the image at
# /android-9.0-r2/{kernel,initrd.img}; these are host-side copies QEMU hands to
# the guest directly.
X86_64_BOOT_DIR = BASE_DIR / "boot" / "androidx86_64"
X86_64_KERNEL = X86_64_BOOT_DIR / "kernel"
X86_64_INITRD = X86_64_BOOT_DIR / "initrd.img"
# root=/dev/ram0 boots the initramfs; SRC selects the install directory on the
# data partition holding system.img; SETUPWIZARD=0 skips the first-boot wizard.
X86_64_CMDLINE = (
    "root=/dev/ram0 vmalloc=192M SRC=/android-9.0-r2 "
    "console=ttyS0 SETUPWIZARD=0"
)

# CPU model. The guest must see the *genuine* host vendor (AMD here): masking it
# as GenuineIntel makes the kernel set up a sysenter-based 32-bit compat vDSO,
# which cannot execute on AMD in long mode, so every 32-bit process (and thus
# mediaserver) traps. Instead of lying about the vendor we patch Houdini's
# vendor check inside /system/lib64/libhoudini.so (and the 32-bit twin).
X86_64_CPU = "host,-pmu"

DEFAULT_INSTANCE_CONFIG = {
    "name": "MEmu",
    "guest": DEFAULT_GUEST,
    "cpus": 4,
    "memory_mb": 4096,
    "adb_port": 21503,
    "display": "gtk",  # "gtk", "sdl", or "none"
    "resolution_width": 1280,
    "resolution_height": 720,
    "dpi": 240,
    "vga": "std",  # "std", "virtio", "vmware", "qxl"
    "audio": True,
    "audio_backend": "pa",  # "pa", "pipewire", "alsa", or "none"
    # Extra 8250 UARTs exposed as /dev/ttyS1..N. The MEmu guest's init.rc chmods
    # /dev/ttyS10..ttyS15 and chowns /dev/ttyS4, so those nodes must exist or init
    # logs "errno=9 /dev/ttyS<n>: no such file or directory" on every boot (the
    # "memu" guest therefore needs 24; see below). Android-x86 does not use them.
    # (Rounded up to the nearest 4, one pci-serial-4x card per group.)
    "serial_ports": 0,
}


def guest_master_image(cfg: dict) -> Path:
    """Master image backing an instance's boot/data disk for the configured guest."""
    guest = cfg.get("guest", DEFAULT_GUEST)
    if guest == GUEST_MEMU:
        return MEMU_DATA_MASTER
    if guest == GUEST_X86_64:
        return X86_64_MASTER_IMAGE
    return X86_MASTER_IMAGE

def get_instance_dir(vmid: int) -> Path:
    inst_dir = INSTANCES_DIR / str(vmid)
    inst_dir.mkdir(parents=True, exist_ok=True)
    return inst_dir

def load_instance_config(vmid: int) -> dict:
    inst_dir = get_instance_dir(vmid)
    conf_path = inst_dir / "config.json"
    if conf_path.exists():
        try:
            with open(conf_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg = dict(DEFAULT_INSTANCE_CONFIG)
                cfg.update(data)
                # The original MEmu guest's init.rc chmods /dev/ttyS10..15 and
                # chowns /dev/ttyS4, so it needs the full serial farm; the
                # Android-x86 guests do not (see DEFAULT_INSTANCE_CONFIG).
                if "serial_ports" not in data and cfg.get("guest") == GUEST_MEMU:
                    cfg["serial_ports"] = 24
                return cfg
        except Exception:
            pass
    cfg = dict(DEFAULT_INSTANCE_CONFIG)
    cfg["name"] = f"MEmu_{vmid}" if vmid > 0 else "MEmu"
    cfg["adb_port"] = 21503 + (vmid * 10)
    save_instance_config(vmid, cfg)
    return cfg

def save_instance_config(vmid: int, config: dict):
    inst_dir = get_instance_dir(vmid)
    conf_path = inst_dir / "config.json"
    with open(conf_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
