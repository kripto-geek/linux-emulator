#!/usr/bin/env bash
# Detached QEMU launcher for bring-up. Spawns boot.sh fully detached so the
# calling shell (or an agent tool with a 2-minute command ceiling) can return
# immediately while the VM keeps running.
#
# Usage:  tools/launch-detached.sh <instance_id> [boot.sh options...]
set -uo pipefail

INSTANCE="$1"; shift
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="/tmp/droidforge-$INSTANCE.boot.log"

# Double-fork + setsid so the VM survives the caller's exit.
setsid bash -c "
    exec bash '$REPO/tools/boot.sh' '$INSTANCE' '$*' \
        > '$LOG' 2>&1 < /dev/null
" _ "$@"

echo "launched $INSTANCE (log: $LOG)"
