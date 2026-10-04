#!/usr/bin/env bash
# DroidForge - dependency installer for Arch Linux
#
# Installs everything needed to build and run DroidForge.
# Run as: sudo bash tools/install-deps.sh
#
# All package names were verified against the live Arch repos on this host
# (qemu-full 11.1.1-1, virglrenderer 1.3.0-2, android-tools 37.0.0-5,
# Qt 6.11.2, Mesa 26.2.2, PipeWire 1.6.8). Re-verify with `pacman -Si`
# before shipping - names/versions drift.
set -euo pipefail

# --- Refuse to run unprivileged ------------------------------------------------
if [[ ${EUID} -ne 0 ]]; then
    echo "ERROR: this script must be run as root (sudo bash $0)" >&2
    exit 1
fi

PKGS=(
    # Hypervisor + block tooling
    qemu-full            # QEMU w/ KVM, virtio-gpu, QMP, GUI (SDL/GTK)
    virglrenderer        # host-side virgl GL renderer for virtio-gpu-gl-pci

    # Guest control channel
    android-tools        # adb + fastboot

    # Vulkan (guest GPU + Venus experiment)
    # Note: on current Arch, the Mesa Vulkan ICD ships inside the `mesa`
    # package (already a dep of mesa-utils/virglrenderer). There is no
    # standalone `vulkan-mesa` package; only the implicit/layers variants.
    vulkan-icd-loader
    vulkan-radeon            # AMD/Intel Vulkan ICD (Venus experiment)
    vulkan-tools             # vulkaninfo / vulkan-compiler

    # Qt 6 UI
    # On this Arch build, QtQuick / QtQuick.Controls / the X11-extra and
    # sql-sqlite components are merged into qt6-base + qt6-declarative,
    # so there are no separate qt6-quick/qt6-quickcontrols2/qt6-x11extras/
    # qt6-sql-sqlite packages.
    qt6-base                 # core + widgets + X11 + sqlite driver
    qt6-declarative          # QML runtime + QtQuick + QtQuick.Controls
    qt6-quick3d              # optional 3D content
    qt6-multimedia           # QoL: recording / audio capture
    qt6-imageformats
    qt6-svg
    qt6-5compat
    qt6-wayland              # wayland platform plugin
    qt6-tools                # lrelease / qtdeploy / qmlformat

    # Build system
    cmake
    ninja
    base-devel

    # JSON + formatting for the core library
    nlohmann-json
    fmt

    # Testing
    gtest                    # gtest package bundles gmock on Arch

    # Wayland / input / keymap
    wayland
    wayland-protocols
    libxkbcommon
    xkeyboard-config

    # Shared folder (virtiofs, Phase 3 QoL)
    # Note: the libfuse runtime is provided by `fuse3` on current Arch
    # (there is no separate `libfuse` package).
    fuse3
    virtiofsd

    # Block image helpers
    e2fsprogs
    dosfstools
    util-linux
    parted

    # Audio
    pipewire
    libpipewire

    # Misc tooling used by doctor / docs
    mesa-utils           # glxinfo
    pciutils             # lspci
)

echo "==> DroidForge dependencies for Arch Linux"
echo "==> $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

echo "==> Packages to install:"
printf '    %s\n' "${PKGS[@]}"
echo

# -s : assume yes, --needed : skip installed, -y : don't prompt
pacman -S --needed --noconfirm "${PKGS[@]}"

echo
echo "==> Done. Post-install steps (run as your normal user, NOT root):"
echo "    1. Add yourself to the 'kvm' group:"
echo "         sudo usermod -aG kvm \$USER"
echo "    2. Re-login (or 'sg kvm -c \"command\"') for the group to take effect."
echo "    3. Verify with:  tools/doctor"
