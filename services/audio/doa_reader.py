from __future__ import annotations

import math
import struct
import threading
import time
from typing import List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# Seeed ReSpeaker 4-Mic Array v2.0 — USB vendor/product
_VENDOR_ID = 0x2886
_PRODUCT_ID = 0x0018

# XMOS parameter registry — only the ones we need.
# Format: (param_id, offset, kind)
# kind: "int" → raw int read, "float" → mantissa*2^exponent (we don't use it here).
# Reference: https://github.com/respeaker/usb_4_mic_array/blob/master/tuning.py
_PARAM_DOAANGLE = (21, 0, "int")
_PARAM_VOICEACTIVITY = (19, 32, "int")

_CTRL_TIMEOUT_MS = 200


class _RespeakerTuning:
    """Minimal in-process driver for the ReSpeaker XMOS tuning interface.

    Wraps the USB control transfer used by Seeed's tuning.py:
      - Vendor request, IN direction
      - bRequest: 0
      - wValue:   (0x80 [read] | offset) [| 0x40 if int param]
      - wIndex:   parameter id
      - 8 bytes returned (2× int32 little-endian)
    """

    def __init__(self, dev) -> None:
        self._dev = dev
        # ctrl_transfer is not thread-safe per device; serialize reads.
        self._lock = threading.Lock()

    def _read(self, param: tuple) -> int:
        import usb.util
        param_id, offset, kind = param
        cmd = 0x80 | offset
        if kind == "int":
            cmd |= 0x40
        with self._lock:
            buf = self._dev.ctrl_transfer(
                usb.util.CTRL_IN
                | usb.util.CTRL_TYPE_VENDOR
                | usb.util.CTRL_RECIPIENT_DEVICE,
                0, cmd, param_id, 8, _CTRL_TIMEOUT_MS,
            )
        # 8 bytes = two little-endian int32 (value, fraction-exponent).
        value, _ = struct.unpack("<ii", bytes(buf))
        return value

    @property
    def direction(self) -> int:
        return self._read(_PARAM_DOAANGLE)

    @property
    def is_voice(self) -> int:
        return self._read(_PARAM_VOICEACTIVITY)


class DOAReader:
    """Reads the Direction of Arrival angle (0-359°) from the ReSpeaker.

    The reader degrades gracefully if `pyusb` is missing or the ReSpeaker
    isn't on the bus: `start()` returns False, `read_angle()` returns None,
    and the conversation loop keeps working without rotation.
    """

    def __init__(self) -> None:
        self._tuning: Optional[_RespeakerTuning] = None
        self._available = False

    def start(self) -> bool:
        try:
            import usb.core  # noqa: F401
        except ImportError:
            logger.warning(
                "DOAReader unavailable: pyusb not installed (sudo apt install python3-usb or pip install pyusb)"
            )
            return False
        try:
            import usb.core
            dev = usb.core.find(idVendor=_VENDOR_ID, idProduct=_PRODUCT_ID)
        except Exception as exc:
            logger.warning("DOAReader: USB enumeration failed: %s", exc)
            return False
        if dev is None:
            logger.warning(
                "DOAReader: ReSpeaker (2886:0018) not found on USB — DOA disabled"
            )
            return False
        try:
            # Sanity read — if this fails we keep the reader disabled.
            tuning = _RespeakerTuning(dev)
            _ = tuning.direction
        except Exception as exc:
            logger.warning(
                "DOAReader: control transfer rejected (%s). "
                "Likely missing udev rule — see codeRaspberry/scripts/install_respeaker_udev.sh",
                exc,
            )
            return False
        self._tuning = tuning
        self._available = True
        logger.info("DOAReader ready (ReSpeaker XMOS tuning interface)")
        return True

    @property
    def available(self) -> bool:
        return self._available

    def read_angle(self) -> Optional[int]:
        """Return the current DOA angle [0, 359], or None if unavailable / read failed."""
        if not self._available or self._tuning is None:
            return None
        try:
            return int(self._tuning.direction) % 360
        except Exception:
            logger.exception("DOA read failed")
            return None

    def voice_active(self) -> bool:
        if not self._available or self._tuning is None:
            return False
        try:
            return bool(self._tuning.is_voice)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Stable, voice-gated DOA — the right way to read this hardware.
    # ------------------------------------------------------------------
    # A single `read_angle()` is the *instantaneous* direction of the loudest
    # sound — easily pulled off by Pi fan noise, reflections, or a stale value
    # captured after the wake word already ended. Instead we poll for a short
    # window WHILE the person is speaking, keep only voice-active samples, and
    # return a circular mean with outlier rejection. This is blocking (uses
    # time.sleep); call it via asyncio.to_thread.
    def read_angle_stable(
        self,
        window_s: float = 2.0,
        poll_interval_s: float = 0.08,
        min_samples: int = 5,
        require_voice: bool = True,
        outlier_deg: float = 40.0,
    ) -> Optional[int]:
        """Return a robust DOA angle [0, 359] sampled over ``window_s``.

        Strategy:
          1. Poll DOA every ``poll_interval_s`` for ``window_s`` seconds.
          2. Prefer samples taken while the XMOS voice-activity bit is set
             (rejects fan / steady Pi noise). Fall back to all samples only if
             too few voiced ones were captured.
          3. Circular-mean the samples, drop those more than ``outlier_deg``
             from the mean, then re-average the inliers.
        Returns None if fewer than ``min_samples`` usable readings — caller
        should then skip rotation rather than turn toward noise.
        """
        if not self._available or self._tuning is None:
            return None

        all_samples: List[int] = []
        voiced_samples: List[int] = []
        deadline = time.monotonic() + max(0.2, window_s)
        while time.monotonic() < deadline:
            try:
                angle = int(self._tuning.direction) % 360
            except Exception:
                angle = None
            if angle is not None:
                all_samples.append(angle)
                if require_voice:
                    try:
                        if bool(self._tuning.is_voice):
                            voiced_samples.append(angle)
                    except Exception:
                        pass
            time.sleep(poll_interval_s)

        chosen = voiced_samples if len(voiced_samples) >= min_samples else all_samples
        if len(chosen) < min_samples:
            logger.debug(
                "DOA stable: only %d/%d samples (voiced=%d) — not enough",
                len(chosen), len(all_samples), len(voiced_samples),
            )
            return None

        angle = self._circular_mean_filtered(chosen, outlier_deg)
        logger.debug(
            "DOA stable: %d samples (voiced=%d) → %s°",
            len(chosen), len(voiced_samples), angle,
        )
        return angle

    @staticmethod
    def _circular_mean(angles: List[int]) -> Optional[int]:
        if not angles:
            return None
        s = sum(math.sin(math.radians(a)) for a in angles)
        c = sum(math.cos(math.radians(a)) for a in angles)
        if abs(s) < 1e-9 and abs(c) < 1e-9:
            return None  # antipodal samples cancel out — no meaningful mean
        return int(round(math.degrees(math.atan2(s, c)))) % 360

    @classmethod
    def _circular_mean_filtered(cls, angles: List[int], outlier_deg: float) -> Optional[int]:
        mean = cls._circular_mean(angles)
        if mean is None:
            return None

        def circ_diff(a: int, b: int) -> float:
            return abs((a - b + 180) % 360 - 180)

        inliers = [a for a in angles if circ_diff(a, mean) <= outlier_deg]
        if len(inliers) >= max(3, len(angles) // 2):
            refined = cls._circular_mean(inliers)
            if refined is not None:
                return refined
        return mean
