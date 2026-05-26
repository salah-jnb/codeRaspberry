#!/usr/bin/env bash
# One-shot installer: copies the BLE service unit, enables it, starts it.
# Usage: sudo bash scripts/install_ble_service.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo." >&2
    exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)/koda-ble.service"
DEST=/etc/systemd/system/koda-ble.service

cp "$SRC" "$DEST"
chmod 0644 "$DEST"

systemctl daemon-reload
systemctl enable koda-ble.service
systemctl restart koda-ble.service

sleep 2
systemctl --no-pager --full status koda-ble.service || true
echo
echo "Tail logs with:  journalctl -u koda-ble.service -f"
