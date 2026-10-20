"""
Runtime dependency checker for Lemu.

Verifies that the external binaries Lemu requires are available before
attempting to launch a VM, giving a clear error message instead of a
cryptic FileNotFoundError.
"""
from __future__ import annotations

import shutil
from typing import Optional


REQUIRED_BINARIES = ("qemu-system-x86_64", "qemu-img", "adb")


def check_binary(name: str) -> Optional[str]:
    """Return the resolved path of ``name`` if available, else None."""
    return shutil.which(name)


def check_all() -> list[str]:
    """Check every required binary.

    Returns a list of human-readable error messages (empty when all OK).
    """
    errors: list[str] = []
    for binary in REQUIRED_BINARIES:
        if check_binary(binary) is None:
            errors.append(
                f"required binary not found: {binary}\n"
                f"  Install it (e.g. on Arch: sudo pacman -S {binary})"
            )
    return errors


def require_all() -> None:
    """Raise RuntimeError if any required binary is missing."""
    errors = check_all()
    if errors:
        raise RuntimeError(
            "Lemu dependencies missing:\n  - " + "\n  - ".join(errors)
        )
