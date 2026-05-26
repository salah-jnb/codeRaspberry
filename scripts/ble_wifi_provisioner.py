"""BLE peripheral that lets the KodaMate phone app provision Wi-Fi on the Pi.

How it works
============
1. The Pi advertises a GATT service `4b6f6461-0000-…` with the local name
   ``KODA-<robot_id>``.
2. The phone scans, discovers the service, connects.
3. The phone writes a JSON payload to the **wifi_credentials** characteristic:
       {"ssid": "Lee7", "password": "44gguihh"}
4. The Pi forwards it to ``nmcli`` and reports progress on the
   **wifi_status** characteristic (read + notify):
       {"state": "connecting" | "connected" | "failed", "ssid": "...",
        "ip": "192.168.X.X", "error": "..."}

Run it
======
    sudo apt install -y bluez bluez-tools python3-dbus python3-gi
    pip install bluez-peripheral
    python -m scripts.ble_wifi_provisioner

Or have systemd start it at boot — see ``scripts/koda-ble.service``.

The script never blocks KODA itself: it runs as its own asyncio loop and
talks to NetworkManager via ``nmcli`` (configured for password-less sudo via
``scripts/install_nmcli_sudoers.sh``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import time
from typing import Optional

logger = logging.getLogger("koda.ble")

# ---------------------------------------------------------------------------
# UUIDs — kept short/ASCII-derived ("Koda") so they're easy to spot in logs.
# ---------------------------------------------------------------------------
SERVICE_UUID = "4b6f6461-0000-1000-8000-00805f9b34fb"
CHAR_WIFI_CREDENTIALS_UUID = "4b6f6461-0001-1000-8000-00805f9b34fb"
CHAR_WIFI_STATUS_UUID = "4b6f6461-0002-1000-8000-00805f9b34fb"

DEVICE_NAME = f"KODA-{os.environ.get('ROBOT_ID', 'koda-01')}"
LOG_LEVEL = os.environ.get("BLE_LOG_LEVEL", "INFO").upper()


# ---------------------------------------------------------------------------
# Wi-Fi side: shell out to nmcli (we keep this dependency-light & robust).
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str, str]:
    logger.debug("$ %s", " ".join(cmd))
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError as exc:
        return 127, "", f"command not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def _nmcli_prefix() -> list[str]:
    """Return a prefix that lets ``nmcli`` run without an interactive password.

    Tries (in order):
      1. plain ``nmcli`` (works if the user is in the ``netdev`` group or if
         polkit is configured)
      2. ``sudo -n nmcli`` (needs NOPASSWD entry; see install_nmcli_sudoers.sh)
    """
    rc, _, _ = _run(["nmcli", "-t", "general", "status"], timeout=4)
    if rc == 0:
        return ["nmcli"]
    return ["sudo", "-n", "nmcli"]


_NMCLI = _nmcli_prefix()


def _current_ip() -> Optional[str]:
    """Best-effort IPv4 of the active wlan interface."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def _current_ssid() -> Optional[str]:
    rc, out, _ = _run(_NMCLI + ["-t", "-f", "active,ssid", "device", "wifi"], timeout=4)
    if rc != 0:
        return None
    for line in out.splitlines():
        active, _, ssid = line.partition(":")
        if active == "yes" and ssid:
            return ssid
    return None


def apply_wifi(ssid: str, password: str, timeout: int = 25) -> dict:
    """Connect the Pi to ``ssid`` using ``password``. Blocking; runs nmcli twice
    (delete stale profile if any, then connect)."""
    ssid = (ssid or "").strip()
    password = (password or "").strip()
    if not ssid:
        return {"state": "failed", "ssid": "", "error": "empty SSID"}

    # Wipe any stale connection with the same name so nmcli doesn't reuse a
    # cached (and possibly wrong) password.
    _run(_NMCLI + ["connection", "delete", ssid], timeout=5)

    cmd = _NMCLI + ["device", "wifi", "connect", ssid, "password", password, "ifname", "wlan0"]
    rc, out, err = _run(cmd, timeout=timeout)
    if rc != 0:
        return {
            "state": "failed",
            "ssid": ssid,
            "error": (err or out or f"nmcli exit {rc}")[:240],
        }

    # nmcli returns immediately; wait a moment for IPv4 to be assigned.
    deadline = time.time() + 8.0
    ip = None
    while time.time() < deadline:
        ip = _current_ip()
        if ip and not ip.startswith("169.254."):
            break
        time.sleep(0.5)

    return {
        "state": "connected" if ip else "connecting",
        "ssid": ssid,
        "ip": ip or "",
    }


# ---------------------------------------------------------------------------
# BLE side: bluez-peripheral wrapper.
# ---------------------------------------------------------------------------
def _import_bluez():
    """Lazy import so unit tests on Windows don't choke on dbus_next."""
    from bluez_peripheral.gatt.service import Service
    from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags
    from bluez_peripheral.util import get_message_bus, Adapter
    from bluez_peripheral.advert import Advertisement
    from bluez_peripheral.agent import NoIoAgent
    return Service, characteristic, CharacteristicFlags, get_message_bus, Adapter, Advertisement, NoIoAgent


async def main() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    )

    Service, characteristic, CharFlags, get_message_bus, Adapter, Advertisement, NoIoAgent = _import_bluez()

    class WifiProvisioning(Service):
        def __init__(self) -> None:
            super().__init__(SERVICE_UUID, primary=True)
            self._last_status: dict = {"state": "idle", "ssid": _current_ssid() or "", "ip": _current_ip() or ""}

        def _status_bytes(self) -> bytes:
            return json.dumps(self._last_status, ensure_ascii=False).encode("utf-8")

        @characteristic(CHAR_WIFI_STATUS_UUID, CharFlags.READ | CharFlags.NOTIFY)
        def wifi_status(self, options):  # type: ignore[no-untyped-def]
            return self._status_bytes()

        @characteristic(CHAR_WIFI_CREDENTIALS_UUID, CharFlags.WRITE | CharFlags.WRITE_WITHOUT_RESPONSE)
        def wifi_credentials(self, options):  # type: ignore[no-untyped-def]
            return b""  # write-only

        @wifi_credentials.setter  # type: ignore[no-untyped-def]
        def wifi_credentials(self, value: bytes, options):  # type: ignore[no-untyped-def]
            try:
                raw = bytes(value).decode("utf-8", errors="replace").strip()
                logger.info("BLE received credentials payload (%d bytes)", len(raw))
                payload = json.loads(raw)
                ssid = str(payload.get("ssid", "")).strip()
                password = str(payload.get("password", "")).strip()
            except Exception as exc:
                logger.exception("Failed to parse credentials payload")
                self._last_status = {"state": "failed", "ssid": "", "error": f"bad payload: {exc}"}
                self._notify_status()
                return

            self._last_status = {"state": "connecting", "ssid": ssid, "ip": ""}
            self._notify_status()

            # Run the blocking nmcli in a thread so we don't stall the DBus loop.
            loop = asyncio.get_event_loop()
            loop.create_task(self._connect_and_report(ssid, password))

        async def _connect_and_report(self, ssid: str, password: str) -> None:
            try:
                result = await asyncio.to_thread(apply_wifi, ssid, password)
            except Exception as exc:
                logger.exception("apply_wifi crashed")
                result = {"state": "failed", "ssid": ssid, "error": str(exc)[:240]}
            logger.info("Wi-Fi apply result: %s", result)
            self._last_status = result
            self._notify_status()

        def _notify_status(self) -> None:
            # bluez-peripheral re-reads the property when notifications are
            # enabled; assigning the same value is what triggers the change.
            try:
                self.wifi_status.changed(self._status_bytes())  # type: ignore[attr-defined]
            except Exception:
                # Older versions of the library use a different signal API;
                # the next read by the client will still get fresh data.
                logger.debug("notify hook missing — client must re-read status", exc_info=True)

    bus = await get_message_bus()
    service = WifiProvisioning()
    await service.register(bus)

    adapter = await Adapter.get_first(bus)
    await adapter.set_powered(True)
    await NoIoAgent().register(bus)

    advert = Advertisement(DEVICE_NAME, [SERVICE_UUID], appearance=0, timeout=0)
    await advert.register(bus, adapter)

    logger.info("BLE provisioning ready — advertising as %r, service=%s", DEVICE_NAME, SERVICE_UUID)
    logger.info("WLAN current SSID=%r ip=%r", _current_ssid(), _current_ip())

    # Sleep forever; the DBus bus is driven by the imported library.
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
