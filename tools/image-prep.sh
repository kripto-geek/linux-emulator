#!/usr/bin/env bash
# DroidForge - image preparation.
#
# Downloads (or verifies) the Bliss OS x86_64 ISO, verifies its SHA-256,
# and converts it into the read-only qcow2 base image that all instances
# link-clone from.
#
# Usage:
#   tools/image-prep.sh            # full: download (if missing) + verify + build base
#   tools/image-prep.sh --verify   # only check the ISO checksum
#   tools/image-prep.sh --no-dl    # skip download, assume ISO present
#
# All artifacts live under images/ by default. Override with env:
#   DROIDFORGE_IMG_DIR=/path   (default: <repo>/images)
set -euo pipefail

# ---------------------------------------------------------------------------
# Pinned image (verified 2026-10-05). Re-pinning is a one-line change each.
# ---------------------------------------------------------------------------
ISO_NAME="Bliss-v16.9.7-x86_64-OFFICIAL-foss-20241011.iso"
ISO_SHA256="735cb962ec6bd92b62eb82a812831a38d79a0dfdf12b7973d2d0f7ab001ba68e"
ISO_URL="https://sourceforge.net/projects/blissos-x86/files/Official/BlissOS16/FOSS/Generic/${ISO_NAME}/download"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
IMG_DIR="${DROIDFORGE_IMG_DIR:-$REPO_DIR/images}"
BASE_QCOW2="$IMG_DIR/base.qcow2"
ISO_PATH="$IMG_DIR/$ISO_NAME"

mkdir -p "$IMG_DIR"

log()  { printf '\033[32m[img-prep]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[img-prep]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[img-prep]\033[0m %s\n' "$*" >&2; exit 1; }

MODE="full"
for a in "$@"; do
    case "$a" in
        --verify) MODE="verify" ;;
        --no-dl)  MODE="no-dl" ;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown arg: $a (see --help)" ;;
    esac
done

command -v curl      >/dev/null 2>&1 || die "curl not found"
command -v qemu-img  >/dev/null 2>&1 || die "qemu-img not found (sudo pacman -S qemu-full)"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum not found"

verify_iso() {
    [[ -f "$ISO_PATH" ]] || die "ISO not present at $ISO_PATH"
    local actual
    actual="$(sha256sum "$ISO_PATH" | awk '{print $1}')"
    if [[ "$actual" == "$ISO_SHA256" ]]; then
        log "ISO checksum OK: $actual"
    else
        die "ISO checksum MISMATCH (got $actual, want $ISO_SHA256). Re-download."
    fi
}

download_iso() {
    log "Downloading $ISO_NAME (~1.8 GB) ..."
    curl -L -C - --retry 20 --retry-delay 3 --retry-all-errors --max-time 14400 \
        -o "$ISO_PATH" "$ISO_URL"
    log "Download complete: $(du -h "$ISO_PATH" | cut -f1)"
}

if [[ "$MODE" != "verify" ]]; then
    if [[ ! -f "$ISO_PATH" ]]; then
        download_iso
    else
        log "ISO already present: $ISO_PATH ($(du -h "$ISO_PATH" | cut -f1))"
    fi
fi
verify_iso

# ---------------------------------------------------------------------------
# Build the read-only base qcow2 from the ISO.
#
# Android-x86 / Bliss ships a pre-formatted data.img inside the ISO. The
# install flow copies it to the guest's disk. For a *pre-installed* base we
# boot the ISO once and let the installer run, then snapshot the resulting
# overlay as the new base. That is a one-time, semi-manual step documented in
# docs/STATUS.md; this script only produces the qcow2 container that the
# installer will write into.
#
# The base is created empty (sparse) at 16 GiB - large enough for Android
# plus user apps. Overlays link-clone from it.
# ---------------------------------------------------------------------------
if [[ "$MODE" != "verify" ]]; then
    if [[ -f "$BASE_QCOW2" ]]; then
        log "Base image already exists: $BASE_QCOW2 (reusing)"
    else
        log "Creating read-only base qcow2 (16 GiB, sparse) ..."
        qemu-img create -f qcow2 "$BASE_QCOW2" 16G
        chmod a-w "$BASE_QCOW2"    # read-only base: instances only write overlays
        log "Base image ready: $BASE_QCOW2"
    fi
fi

log "Done."
echo "  ISO : $ISO_PATH"
echo "  Base: $BASE_QCOW2"
