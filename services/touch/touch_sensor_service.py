from __future__ import annotations

import asyncio
import time
from typing import Callable, Iterable, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class TouchSensorService:
    """GPIO touch sensor wrapper.

    GPIO callbacks are executed outside the asyncio task that runs the robot
    loop. The service bridges them into the loop with ``call_soon_threadsafe``.
    It prefers gpiozero, then falls back to RPi.GPIO. On non-Pi machines it
    simply reports unavailable and KODA keeps running normally.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        pin: int = 17,
        pins: Optional[Iterable[int]] = None,
        active_high: bool = True,
        pull_up: bool = False,
        bounce_seconds: float = 0.25,
        cooldown_seconds: float = 2.0,
    ) -> None:
        self._enabled = enabled
        normalized_pins: list[int] = []
        for candidate in pins if pins is not None else (pin,):
            value = int(candidate)
            if value not in normalized_pins:
                normalized_pins.append(value)
        self._pins = tuple(normalized_pins or [pin])
        self._active_high = active_high
        self._pull_up = pull_up
        self._bounce = max(0.0, bounce_seconds)
        self._cooldown = max(self._bounce, cooldown_seconds)
        self._devices = []
        self._gpio = None
        self._last_touch_at = 0.0
        self._started = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def started(self) -> bool:
        return self._started

    def start(self, on_touch: Callable[[], None]) -> bool:
        if not self._enabled:
            logger.info("Touch sensor disabled (TOUCH_SENSOR_ENABLED=0)")
            return False

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("Touch sensor start called outside asyncio loop")
            return False

        def emit_touch(pin: int) -> None:
            now = time.monotonic()
            if self._cooldown and (now - self._last_touch_at) < self._cooldown:
                return
            self._last_touch_at = now
            logger.info("Touch detected on GPIO%d", pin)
            loop.call_soon_threadsafe(on_touch)

        if self._start_gpiozero(emit_touch):
            self._started = True
            return True
        if self._start_rpi_gpio(emit_touch):
            self._started = True
            return True

        logger.warning(
            "Touch sensor unavailable on GPIO%s. Install gpiozero or RPi.GPIO on the Pi.",
            ",".join(str(pin) for pin in self._pins),
        )
        return False

    def close(self) -> None:
        for device in self._devices:
            try:
                device.close()
            except Exception:
                logger.exception("Touch sensor gpiozero close failed")
        self._devices = []

        if self._gpio is not None:
            try:
                for pin in self._pins:
                    self._gpio.remove_event_detect(pin)
                self._gpio.cleanup(list(self._pins))
            except Exception:
                logger.exception("Touch sensor RPi.GPIO cleanup failed")
            self._gpio = None

        self._started = False

    def _start_gpiozero(self, emit_touch: Callable[[], None]) -> bool:
        try:
            from gpiozero import DigitalInputDevice  # type: ignore
        except Exception:
            return False

        devices = []
        try:
            for pin in self._pins:
                device = DigitalInputDevice(
                    pin,
                    pull_up=self._pull_up,
                    active_state=self._active_high,
                    bounce_time=self._bounce or None,
                )
                device.when_activated = lambda pin=pin: emit_touch(pin)
                devices.append(device)
            self._devices = devices
            logger.info(
                "Touch sensor ready on GPIO%s via gpiozero (active_high=%s, pull_up=%s, cooldown=%.2fs)",
                ",".join(str(pin) for pin in self._pins),
                self._active_high,
                self._pull_up,
                self._cooldown,
            )
            return True
        except Exception:
            for device in devices:
                try:
                    device.close()
                except Exception:
                    pass
            logger.exception("Touch sensor gpiozero setup failed")
            return False

    def _start_rpi_gpio(self, emit_touch: Callable[[], None]) -> bool:
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except Exception:
            return False

        try:
            GPIO.setmode(GPIO.BCM)
            if self._pull_up:
                pud = GPIO.PUD_UP
            elif self._active_high:
                pud = GPIO.PUD_DOWN
            else:
                pud = GPIO.PUD_UP
            edge = GPIO.RISING if self._active_high else GPIO.FALLING
            for pin in self._pins:
                GPIO.setup(pin, GPIO.IN, pull_up_down=pud)
                GPIO.add_event_detect(
                    pin,
                    edge,
                    callback=lambda detected_pin: emit_touch(int(detected_pin)),
                    bouncetime=max(1, int(self._bounce * 1000)),
                )
            self._gpio = GPIO
            logger.info(
                "Touch sensor ready on GPIO%s via RPi.GPIO (active_high=%s, pull_up=%s, cooldown=%.2fs)",
                ",".join(str(pin) for pin in self._pins),
                self._active_high,
                self._pull_up,
                self._cooldown,
            )
            return True
        except Exception:
            logger.exception("Touch sensor RPi.GPIO setup failed")
            return False
