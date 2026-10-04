#!/usr/bin/env bash
# DroidForge - boot an Android instance.
#
# Creates a qcow2 overlay (linked clone of the read-only base) and launches
# QEMU with KVM, virgl GPU, PipeWire audio, and ADB host-forwarding. This is
# the Phase 1 acceptance driver: it is the same command the C++ QemuCommandBuilder
# produces (cross-checked by unit tests), expressed as a shell script so it can
# be run directly during bring-up.
#
# Usage:
#   tools/boot.sh <instance_id> [options]
#
# Options:
#   --cores N        vCPU count            (default 4)
#   --ram MB         guest RAM             (default 4096)
#   --res WxH        resolution            (default 1280x720)
#   --gpu {virgl|virtio|venus}  (default virgl)
#   --adb-port P     host ADB port         (default auto: 5555+N)
#   --first-boot     attach the ISO for install (default: overlay only)
#   --no-audio       disable audio
#   --qmp            enable QMP socket (default: off for Phase 1)
#   --help
#
# Instance data lives under:  $XDG_DATA_HOME/droidforge/<id>/   (or ~/.local/share)
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
INSTANCE="${1:-}"
shift 2>/dev/null || true

CORES=4
RAM=4096
WIDTH=1280
HEIGHT=720
GPU=virgl
ADB_PORT=0            # 0 = auto-allocate
FIRST_BOOT=0
AUDIO=1
QMP=0

IMG_DIR="${DROIDFORGE_IMG_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/images}"
BASE_QCOW2="$IMG_DIR/base.qcow2"
ISO="$IMG_DIR/Bliss-v16.9.7-x86_64-OFFICIAL-foss-20241011.iso"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cores) CORES="$2"; shift 2 ;;
        --ram)   RAM="$2"; shift 2 ;;
        --res)   IFS=, read -r WIDTH HEIGHT <<< "${2//x/,}"; shift 2 ;;
        --gpu)   GPU="$2"; shift 2 ;;
        --adb-port) ADB_PORT="$2"; shift 2 ;;
        --first-boot) FIRST_BOOT=1; shift ;;
        --no-audio) AUDIO=0; shift ;;
        --qmp) QMP=1; shift ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

[[ -n "$INSTANCE" ]] || { echo "usage: boot.sh <instance_id> [options]  (see --help)" >&2; exit 2; }

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
XDG_DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_RUNTIME="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
INST_DIR="$XDG_DATA/droidforge/$INSTANCE"
OVERLAY="$INST_DIR/overlay.qcow2"
QMP_SOCK="$XDG_RUNTIME/droidforge/$INSTANCE/qmp.sock"
PID_FILE="$INST_DIR/qemu.pid"

mkdir -p "$INST_DIR"

# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------
command -v qemu-system-x86_64 >/dev/null 2>&1 || { echo "qemu not installed" >&2; exit 1; }
[[ -f "$BASE_QCOW2" ]] || { echo "base image missing: $BASE_QCOW2 (run tools/image-prep.sh)" >&2; exit 1; }

# Auto-allocate a free ADB host port starting at 5555.
if [[ "$ADB_PORT" -eq 0 ]]; then
    ADB_PORT=5555
    while ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":$ADB_PORT\$"; do
        ADB_PORT=$((ADB_PORT+1))
    done
fi
echo "[$INSTANCE] host ADB port: $ADB_PORT"

# ---------------------------------------------------------------------------
# Create the overlay (linked clone) if it doesn't already exist.
# ---------------------------------------------------------------------------
if [[ ! -f "$OVERLAY" ]]; then
    echo "[$INSTANCE] creating overlay (linked clone of base) ..."
    qemu-img create -f qcow2 -b "$BASE_QCOW2" -F qcow2 "$OVERLAY" 16G
fi

# ---------------------------------------------------------------------------
# Assemble the QEMU command. Mirrors core/QemuCommandBuilder (unit-tested).
# ---------------------------------------------------------------------------
ARGS=(
    -enable-kvm
    -machine q35,accel=kvm
    -rtc base=localtime,driftfix=slew
    -smbios "type=1,manufacturer=DroidForge,product=DroidForge,serial=$INSTANCE"
    -m "$RAM"
    -smp "cpus=$CORES,cores=$CORES,threads=1,sockets=1"
    -cpu host
    # Primary writable disk = overlay
    -drive "file=$OVERLAY,if=virtio,format=qcow2,cache=none,aio=threads"
)

# First boot: attach the ISO so the guest's GRUB offers "Install".
if [[ "$FIRST_BOOT" -eq 1 ]]; then
    [[ -f "$ISO" ]] || { echo "ISO missing: $ISO (run tools/image-prep.sh)" >&2; exit 1; }
    ARGS+=( -drive "file=$ISO,if=virtio,media=cdrom,readonly=on" -cdrom "$ISO" )
fi

# GPU
case "$GPU" in
    virgl)  ARGS+=( -device "virtio-gpu-gl-pci" ) ;;
    virtio) ARGS+=( -device "virtio-gpu-pci" ) ;;
    venus)  ARGS+=( -device "virtio-vulkan-pci,mem-mb=512" ) ;;
    *) echo "unknown gpu: $GPU" >&2; exit 2 ;;
esac

# Display backend. QEMU 11 has no -window flag; the SDL window sizes to the
# guest framebuffer. GL is enabled on the backend for virgl (virtio-gpu-gl-pci).
case "$GPU" in
    virgl) ARGS+=( -display "sdl,gl=on" ) ;;
    *)     ARGS+=( -display sdl ) ;;
esac

# Audio
if [[ "$AUDIO" -eq 1 ]]; then
    # QEMU 11 unified -audio; in/out engine+device keys are rejected, so use
    # the minimal form. The guest needs an HDA bus (ich9-intel-hda) + codec.
    ARGS+=( -audio driver=pipewire -device ich9-intel-hda -device hda-duplex )
fi

# Network: slirp + host-forward ADB
ARGS+=( -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$ADB_PORT-:5555" -device virtio-net-pci,netdev=net0 )

# QMP (optional)
if [[ "$QMP" -eq 1 ]]; then
    mkdir -p "$(dirname "$QMP_SOCK")"
    ARGS+=( -qmp "unix:$QMP_SOCK,server=on,wait=off" )
fi

# Start paused so we can confirm boot before releasing (Phase 2+ will `cont` via QMP).
# For Phase 1 we actually want it running immediately, so do NOT pass -S here.

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
echo "[$INSTANCE] launching QEMU (KVM + $GPU, ${CORES}C/${RAM}MB, ${WIDTH}x${HEIGHT}, ADB:$ADB_PORT) ..."
echo "[$INSTANCE] to connect ADB:   adb connect 127.0.0.1:$ADB_PORT"

# Run in foreground; trap INT/TERM for a clean KVM shutdown.
cleanup() {
    echo "[$INSTANCE] shutting down ..."
    # -S was NOT used, so the VM is running. QMP quit would be ideal, but for
    # Phase 1 we just kill the QEMU process (KVM resets the guest on exit).
    if [[ -f "$PID_FILE" ]]; then
        kill "$(cat "$PID_FILE")" 2>/dev/null || true
        rm -f "$PID_FILE"
    fi
}
trap cleanup INT TERM

qemu-system-x86_64 "${ARGS[@]}" &
QEMU_PID=$!
echo "$QEMU_PID" > "$PID_FILE"
echo "[$INSTANCE] QEMU pid: $QEMU_PID (qmp socket: ${QMP_SOCK:-<none>})"

wait "$QEMU_PID"
echo "[$INSTANCE] QEMU exited."
