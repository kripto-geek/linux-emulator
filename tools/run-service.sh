# DroidForge - run one Android instance as a systemd *user* service.
#
# This is the robust way to keep a QEMU VM alive independent of the shell
# that launched it (which is also how the Phase 4 autostart unit works).
#
# Usage:
#   tools/run-service.sh <instance_id> [boot.sh options...]
#
# This writes ~/.config/systemd/user/droidforge-<id>.service, reloads the
# user manager, and starts the unit. The VM keeps running as long as your
# user session is alive (or with linger enabled, always).
#
# Stop with:   systemctl --user stop droidforge-<id>
# Logs:        journalctl --user -u droidforge-<id>
set -euo pipefail

INSTANCE="${1:-}"; [[ -n "$INSTANCE" ]] || { echo "usage: $0 <instance_id> [boot.sh options...]" >&2; exit 2; }
shift
EXTRA_ARGS=("$@")

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOT_SH="$REPO/tools/boot.sh"
UNIT="droidforge-$INSTANCE"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/$UNIT.service"

mkdir -p "$UNIT_DIR"

# boot.sh backgrounds QEMU and waits; under systemd Type=simple the unit
# tracks boot.sh itself. When the unit stops, boot.sh's trap kills QEMU.
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    EXTRA_LINE="${EXTRA_ARGS[*]}"
else
    EXTRA_LINE=""
fi
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=DroidForge instance: $INSTANCE
After=network.target

[Service]
Type=simple
ExecStart=$BOOT_SH $INSTANCE $EXTRA_LINE
Restart=on-failure
Environment=TERM=dumb

[Install]
WantedBy=default.target
EOF

echo "wrote $UNIT_FILE:"
cat "$UNIT_FILE"
echo "---"
systemctl --user daemon-reload
systemctl --user start "$UNIT"
sleep 2
systemctl --user status "$UNIT" --no-pager | head -15
echo
echo "VM is now running under systemd user manager."
echo "  status:  systemctl --user status $UNIT"
echo "  logs:    journalctl --user -u $UNIT -f"
echo "  stop:    systemctl --user stop $UNIT"
