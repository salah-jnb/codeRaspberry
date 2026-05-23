"""Helper interactif pour calibrer la rotation du robot KODA.

Usage (sur le Pi, depuis le répertoire codeRaspberry) :

    source .venv/bin/activate
    python -m scripts.calibrate_rotation

Le script :
  1) lit la DOA "front" (place un locuteur en face, parle quelques secondes,
     l'angle moyen devient ROTATION_FRONT_OFFSET_DEG)
  2) fait pivoter le robot d'un angle cible en plusieurs durées candidates et
     te demande à chaque essai le vrai angle atteint (mesuré au rapporteur)
  3) imprime à la fin la ligne ROTATION_LUT à coller dans .env

Avant de lancer ce script :
  - Brancher la ReSpeaker (USB) et l'Arduino (USB)
  - sudo bash scripts/install_respeaker_udev.sh  (une seule fois)
  - Mettre le robot sur une surface dure, libre de tout obstacle.
"""

from __future__ import annotations

import asyncio
import time
from typing import List, Tuple

from adapters.arduino_adapter import ArduinoAdapter, ArduinoCommand
from app.config import load_config
from services.audio.doa_reader import DOAReader


_TARGET_ANGLES = (30, 60, 90, 135, 180)


def _ask_float(prompt: str, default: float) -> float:
    raw = input(f"{prompt} [{default}] : ").strip()
    if not raw:
        return default
    return float(raw)


def _measure_front_offset(reader: DOAReader, samples: int = 20) -> int:
    print("\n--- Calibration FRONT_OFFSET ---")
    print("Place une personne ou un haut-parleur EXACTEMENT en face du robot.")
    input("Quand tu es prêt, appuie sur ENTRÉE et parle 5 secondes...")
    readings = []
    end = time.time() + 5.0
    while time.time() < end and len(readings) < samples:
        a = reader.read_angle()
        if a is not None:
            readings.append(a)
        time.sleep(0.2)
    if not readings:
        print("⚠️  Aucune lecture DOA — vérifie que la ReSpeaker est bien détectée.")
        return 0
    # Robust mean via circular average (avoids the 0/360 wrap-around bias).
    import math
    sin_sum = sum(math.sin(math.radians(a)) for a in readings)
    cos_sum = sum(math.cos(math.radians(a)) for a in readings)
    mean = int(math.degrees(math.atan2(sin_sum, cos_sum))) % 360
    print(f"FRONT_OFFSET mesuré (moyenne sur {len(readings)} lectures) = {mean}°")
    return mean


async def _pulse(arduino: ArduinoAdapter, direction: ArduinoCommand, seconds: float) -> None:
    arduino.send(direction)
    await asyncio.sleep(seconds)
    arduino.send(ArduinoCommand.STOP)


async def _calibrate_angle(
    arduino: ArduinoAdapter,
    target_deg: int,
    initial_duration_s: float,
) -> Tuple[int, float]:
    """Pulse the motors with increasing/decreasing durations until the user is satisfied."""
    print(f"\n--- Calibration {target_deg}° ---")
    print(f"On vise {target_deg}° de rotation. Place un repère / rapporteur au sol.")
    duration = initial_duration_s
    while True:
        confirm = input(f"Pulse à droite pendant {duration:.2f}s ? (ENTRÉE pour lancer, q pour passer) : ").strip().lower()
        if confirm == "q":
            return target_deg, duration
        input("Repositionne le robot puis ENTRÉE pour démarrer le pulse...")
        await _pulse(arduino, ArduinoCommand.RIGHT, duration)
        actual = _ask_float(f"Angle réel mesuré (degrés)", target_deg)
        delta = target_deg - actual
        print(f"   Cible {target_deg}°, mesuré {actual}°  → écart {delta:+.1f}°")
        if abs(delta) <= 3.0:
            print(f"✓ accepté : {target_deg}° = {duration:.3f}s")
            return target_deg, duration
        # Adjust the duration proportionally for the next try.
        if actual <= 0:
            duration *= 1.5
        else:
            duration *= (target_deg / actual)
        duration = max(duration, 0.05)


async def main() -> None:
    config = load_config()
    arduino = ArduinoAdapter(
        port=config.arduino.port,
        baudrate=config.arduino.baudrate,
        timeout=config.arduino.timeout_seconds,
        boot_delay_seconds=config.arduino.boot_delay_seconds,
        require_ack=config.arduino.require_ack,
    )
    arduino.open()

    reader = DOAReader()
    has_doa = reader.start()
    front_offset = 0
    if has_doa:
        front_offset = _measure_front_offset(reader)
    else:
        print("⚠️  DOA reader indisponible — on calibre seulement la rotation (pas le FRONT_OFFSET).")

    print(f"\nPente initiale supposée: {config.rotation.slope_deg_per_s}°/s")
    init_duration = lambda a: max(
        (a - config.rotation.offset_deg) / config.rotation.slope_deg_per_s, 0.05
    )
    samples: List[Tuple[int, float]] = []
    for angle in _TARGET_ANGLES:
        a, d = await _calibrate_angle(arduino, angle, init_duration(angle))
        samples.append((a, d))
        print(f"-> Point ajouté: {a}° → {d:.3f}s")

    arduino.send(ArduinoCommand.STOP)
    arduino.close()

    print("\n==================== RÉSULTATS ====================")
    if has_doa:
        print(f"ROTATION_FRONT_OFFSET_DEG={front_offset}")
    lut_str = ",".join(f"{int(a)}:{d:.3f}" for a, d in samples)
    print(f"ROTATION_LUT={lut_str}")
    print("===================================================")
    print("\nCopie ces deux lignes dans codeRaspberry/.env, puis relance KODA.")


if __name__ == "__main__":
    asyncio.run(main())
