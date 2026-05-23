#!/usr/bin/env bash
# Install the udev rule that lets a non-root user open the ReSpeaker XMOS
# tuning USB endpoint (idVendor=2886 idProduct=0018).
# Without this, DOAReader.start() will see "control transfer rejected (Access denied)".
#
# Usage:  sudo bash scripts/install_respeaker_udev.sh
set -euo pipefail

RULE_FILE=/etc/udev/rules.d/60-respeaker.rules
RULE_BODY='SUBSYSTEM=="usb", ATTRS{idVendor}=="2886", ATTRS{idProduct}=="0018", MODE="0666"'

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
fi

if [[ -f "$RULE_FILE" ]] && grep -qE 'idVendor=="2886".*idProduct=="0018"' "$RULE_FILE"; then
    echo "udev rule already present at $RULE_FILE — nothing to do."
else
    echo "Writing $RULE_FILE …"
    echo "$RULE_BODY" > "$RULE_FILE"
fi

udevadm control --reload-rules
udevadm trigger

echo "Done. Unplug and re-plug the ReSpeaker (or reboot) for the rule to take effect."
