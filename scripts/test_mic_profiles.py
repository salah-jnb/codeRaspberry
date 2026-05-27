"""Benchmark several mic-capture commands against Vosk + Azure STT.

Goal : tester plusieurs combinaisons (device ALSA, canaux natifs, remix sox,
arecord vs sox, plughw vs pipewire) pour savoir quelle commande
d'enregistrement donne le meilleur signal pour la reconnaissance vocale.

Pour chaque profil :
  1. on attend que l'utilisateur appuie sur ENTER,
  2. on enregistre N secondes (default 5),
  3. on sauvegarde le WAV dans /tmp/koda_mic_pNN_<name>.wav,
  4. on calcule RMS/peak,
  5. on lance Vosk localement,
  6. on envoie le WAV au backend pour Azure STT.

L'utilisateur prononce TOUJOURS la meme phrase : "صباح الخير محسن كيف حالك".
A la fin, un tableau resume les transcriptions par profil pour comparaison.

Run :
    python -m scripts.test_mic_profiles
    python -m scripts.test_mic_profiles --seconds 4
    python -m scripts.test_mic_profiles --only 2,3,7         # subset
    python -m scripts.test_mic_profiles --no-vosk            # skip Vosk
    python -m scripts.test_mic_profiles --no-azure           # skip backend
"""
from __future__ import annotations

import argparse
import array
import asyncio
import json
import math
import shutil
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.backend_client import BackendClient
from app.config import load_config
from utils.logger import get_logger

logger = get_logger("test_mic_profiles")

GRN = "\033[1;32m"
DIM = "\033[2;37m"
CYN = "\033[1;36m"
YEL = "\033[1;33m"
RED = "\033[1;31m"
BLD = "\033[1m"
RST = "\033[0m"

TARGET_PHRASE = "صباح الخير محسن كيف حالك"


@dataclass
class Profile:
    name: str
    description: str
    tool: str  # "sox" or "arecord"
    cmd_template: List[str]  # placeholders: {device} {rate} {seconds} {out}
    device: str = "plughw:3,0"
    rate: int = 16000

    def build(self, out_path: Path, seconds: float) -> List[str]:
        return [
            tok.format(device=self.device, rate=self.rate,
                       seconds=f"{seconds:.3f}", out=str(out_path))
            for tok in self.cmd_template
        ]


@dataclass
class ProfileResult:
    profile: Profile
    wav_path: Optional[Path] = None
    rec_seconds: float = 0.0
    rms_avg: int = 0
    rms_peak: int = 0
    vosk_text: str = ""
    vosk_seconds: float = 0.0
    azure_text: str = ""
    azure_seconds: float = 0.0
    record_error: str = ""
    notes: List[str] = field(default_factory=list)


def build_profiles(device: str) -> List[Profile]:
    """Each profile is one recording command we want to compare.

    Order goes from "current/default" (P1) to more experimental.
    """
    return [
        Profile(
            name="arecord_c1_downmix",
            description="arecord -c 1 (baseline — ALSA downmix all 6 canaux → mono dilue)",
            tool="arecord",
            cmd_template=[
                "arecord", "-D", "{device}",
                "-f", "S16_LE", "-r", "{rate}", "-c", "1",
                "-d", "{seconds}", "-t", "wav", "-q", "{out}",
            ],
            device=device,
        ),
        Profile(
            name="sox_6ch_remix1_dsp",
            description="sox 6ch → remix 1 (canal DSP processed : AEC+BF+NS+AGC)",
            tool="sox",
            cmd_template=[
                "sox", "-q",
                "-t", "alsa", "{device}",
                "-r", "{rate}", "-c", "6", "-b", "16", "-e", "signed-integer",
                "-t", "wav", "{out}",
                "remix", "1",
                "trim", "0", "{seconds}",
            ],
            device=device,
        ),
        Profile(
            name="sox_6ch_remix2_mic1",
            description="sox 6ch → remix 2 (mic raw #1)",
            tool="sox",
            cmd_template=[
                "sox", "-q", "-t", "alsa", "{device}",
                "-r", "{rate}", "-c", "6", "-b", "16", "-e", "signed-integer",
                "-t", "wav", "{out}",
                "remix", "2", "trim", "0", "{seconds}",
            ],
            device=device,
        ),
        Profile(
            name="sox_6ch_remix3_mic2",
            description="sox 6ch → remix 3 (mic raw #2)",
            tool="sox",
            cmd_template=[
                "sox", "-q", "-t", "alsa", "{device}",
                "-r", "{rate}", "-c", "6", "-b", "16", "-e", "signed-integer",
                "-t", "wav", "{out}",
                "remix", "3", "trim", "0", "{seconds}",
            ],
            device=device,
        ),
        Profile(
            name="sox_6ch_remix4_mic3",
            description="sox 6ch → remix 4 (mic raw #3)",
            tool="sox",
            cmd_template=[
                "sox", "-q", "-t", "alsa", "{device}",
                "-r", "{rate}", "-c", "6", "-b", "16", "-e", "signed-integer",
                "-t", "wav", "{out}",
                "remix", "4", "trim", "0", "{seconds}",
            ],
            device=device,
        ),
        Profile(
            name="sox_6ch_remix5_mic4",
            description="sox 6ch → remix 5 (mic raw #4)",
            tool="sox",
            cmd_template=[
                "sox", "-q", "-t", "alsa", "{device}",
                "-r", "{rate}", "-c", "6", "-b", "16", "-e", "signed-integer",
                "-t", "wav", "{out}",
                "remix", "5", "trim", "0", "{seconds}",
            ],
            device=device,
        ),
        Profile(
            name="sox_6ch_remix_2345_avg",
            description="sox 6ch → remix 2,3,4,5 (moyenne 4 mics raw, sans DSP)",
            tool="sox",
            cmd_template=[
                "sox", "-q", "-t", "alsa", "{device}",
                "-r", "{rate}", "-c", "6", "-b", "16", "-e", "signed-integer",
                "-t", "wav", "{out}",
                "remix", "2,3,4,5", "trim", "0", "{seconds}",
            ],
            device=device,
        ),
        Profile(
            name="arecord_pipewire",
            description="arecord -D pipewire (utilise source par defaut wpctl)",
            tool="arecord",
            cmd_template=[
                "arecord", "-D", "pipewire",
                "-f", "S16_LE", "-r", "{rate}", "-c", "1",
                "-d", "{seconds}", "-t", "wav", "-q", "{out}",
            ],
            device="pipewire",
        ),
        Profile(
            name="arecord_hw_no_plug",
            description="arecord -D hw:3,0 (sans 'plug', pas de conversion auto)",
            tool="arecord",
            cmd_template=[
                "arecord", "-D", "hw:3,0",
                "-f", "S16_LE", "-r", "{rate}", "-c", "1",
                "-d", "{seconds}", "-t", "wav", "-q", "{out}",
            ],
            device="hw:3,0",
        ),
    ]


def rms_of_wav(path: Path) -> tuple[int, int]:
    """Return (avg_rms, peak_amplitude) on a S16 mono WAV. (0,0) if read fails."""
    try:
        with wave.open(str(path), "rb") as wf:
            n = wf.getnframes()
            if n == 0:
                return 0, 0
            raw = wf.readframes(n)
            sampwidth = wf.getsampwidth()
            ch = wf.getnchannels()
    except (wave.Error, OSError):
        return 0, 0
    if sampwidth != 2:
        return 0, 0
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if not samples:
        return 0, 0
    # If multichannel, downmix to mono for RMS purpose
    if ch > 1:
        mono = array.array("h")
        for i in range(0, len(samples) - ch + 1, ch):
            mono.append(int(sum(samples[i : i + ch]) / ch))
        samples = mono
    total = 0
    peak = 0
    for s in samples:
        a = abs(s)
        if a > peak:
            peak = a
        total += s * s
    avg = int(math.sqrt(total / len(samples)))
    return avg, peak


async def record_profile(profile: Profile, out_path: Path, seconds: float) -> tuple[bool, str, float]:
    """Run the recording command. Return (ok, error_msg, elapsed_s)."""
    cmd = profile.build(out_path, seconds)
    binary = cmd[0]
    if shutil.which(binary) is None:
        return False, f"{binary} not in PATH", 0.0
    out_path.unlink(missing_ok=True)

    print(f"  {DIM}$ {' '.join(cmd)}{RST}")
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # Hard timeout = seconds + 8s buffer for ALSA open/close.
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=seconds + 8.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, f"{binary} timeout after {seconds + 8.0:.1f}s", loop.time() - t0
        elapsed = loop.time() - t0
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()[:300]
            return False, f"{binary} exit {proc.returncode}: {err}", elapsed
        if not out_path.exists() or out_path.stat().st_size < 1024:
            return False, f"{binary} produced empty/tiny file", elapsed
        return True, "", elapsed
    except FileNotFoundError:
        return False, f"{binary} not found", 0.0
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", loop.time() - t0


def vosk_transcribe(model, wav_path: Path) -> str:
    """Synchronous Vosk decode of a WAV file. Returns the final text."""
    from vosk import KaldiRecognizer

    with wave.open(str(wav_path), "rb") as wf:
        rate = wf.getframerate()
        recognizer = KaldiRecognizer(model, rate)
        recognizer.SetWords(False)
        chunk_size = 4000
        while True:
            data = wf.readframes(chunk_size)
            if not data:
                break
            recognizer.AcceptWaveform(data)
    return (json.loads(recognizer.FinalResult()).get("text") or "").strip()


async def ensure_vosk_model(language: str, models_dir: str):
    """Load the Vosk model once. Returns None if Vosk is not installed/available."""
    try:
        from vosk import Model, SetLogLevel
    except ImportError:
        logger.warning("vosk not installed — pip install vosk on the Pi to enable offline STT")
        return None

    SetLogLevel(-1)
    candidate = Path(models_dir)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    target = candidate / language
    if not target.is_dir():
        logger.warning("Vosk model missing at %s — run `python -m scripts.download_vosk_model %s`",
                       target, language)
        return None
    logger.info("Loading Vosk model from %s ...", target)
    return await asyncio.to_thread(Model, str(target))


async def run_one_profile(
    idx: int,
    profile: Profile,
    seconds: float,
    vosk_model,
    backend: Optional[BackendClient],
    no_prompt: bool,
) -> ProfileResult:
    res = ProfileResult(profile=profile)

    print(f"\n{BLD}{CYN}── Profile {idx}/{profile.name} ──{RST}")
    print(f"  {profile.description}")

    if not no_prompt:
        try:
            input(f"  {YEL}▶ Appuie sur ENTER, puis dis : « {TARGET_PHRASE} »{RST}")
        except EOFError:
            no_prompt = True

    out_path = Path(tempfile.gettempdir()) / f"koda_mic_p{idx:02d}_{profile.name}.wav"
    print(f"  {YEL}▶ Enregistrement {seconds:.1f}s …{RST}")
    ok, err, dt = await record_profile(profile, out_path, seconds)
    res.rec_seconds = dt

    if not ok:
        res.record_error = err
        print(f"  {RED}✗ Recording failed: {err}{RST}")
        return res

    res.wav_path = out_path
    res.rms_avg, res.rms_peak = rms_of_wav(out_path)
    quality = (
        f"{RED}SILENT{RST}" if res.rms_peak < 150 else
        f"{YEL}very low{RST}" if res.rms_peak < 600 else
        f"{GRN}OK{RST}" if res.rms_peak < 28000 else
        f"{YEL}near clipping{RST}"
    )
    print(f"  {GRN}✓ {out_path.name} ({out_path.stat().st_size} bytes, "
          f"RMS avg={res.rms_avg} peak={res.rms_peak} [{quality}]){RST}")

    # Vosk
    if vosk_model is not None:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            res.vosk_text = await asyncio.to_thread(vosk_transcribe, vosk_model, out_path)
        except Exception as exc:
            res.notes.append(f"vosk: {exc}")
            print(f"  {RED}✗ Vosk error: {exc}{RST}")
        else:
            res.vosk_seconds = loop.time() - t0
            mark = GRN if res.vosk_text else DIM
            print(f"  {mark}🔵 Vosk  [{res.vosk_seconds:.2f}s]: {res.vosk_text or '(empty)'}{RST}")
    else:
        print(f"  {DIM}— Vosk skipped (model not loaded){RST}")

    # Azure (via backend)
    if backend is not None:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            wav_bytes = out_path.read_bytes()
            res.azure_text = await backend.speech_to_text(wav_bytes)
        except Exception as exc:
            detail = getattr(getattr(exc, "response", None), "text", "")
            res.notes.append(f"azure: {(detail or str(exc))[:200]}")
            print(f"  {RED}✗ Azure error: {(detail or str(exc))[:200]}{RST}")
        else:
            res.azure_seconds = loop.time() - t0
            mark = GRN if res.azure_text else DIM
            print(f"  {mark}🟣 Azure [{res.azure_seconds:.2f}s]: {res.azure_text or '(empty)'}{RST}")
    else:
        print(f"  {DIM}— Azure skipped (backend disabled){RST}")

    return res


def print_summary(results: List[ProfileResult]) -> None:
    print(f"\n{BLD}{CYN}══════ SUMMARY ══════{RST}")
    print(f"  Target phrase : {BLD}{TARGET_PHRASE}{RST}\n")
    for i, r in enumerate(results, 1):
        head = f"{BLD}P{i:02d} {r.profile.name}{RST}"
        if r.record_error:
            print(f"{head}  {RED}✗ {r.record_error}{RST}")
            continue
        print(f"{head}  RMS peak={r.rms_peak}  wav={r.wav_path.name if r.wav_path else '?'}")
        print(f"   🔵 Vosk  : {r.vosk_text or DIM + '(empty)' + RST}")
        print(f"   🟣 Azure : {r.azure_text or DIM + '(empty)' + RST}")
        if r.notes:
            for n in r.notes:
                print(f"   {DIM}note: {n}{RST}")
    print()
    print(f"{DIM}WAV files kept in {tempfile.gettempdir()} for replay :")
    print(f"  aplay /tmp/koda_mic_p*.wav     # ou: paplay /tmp/koda_mic_p01_*.wav{RST}")


async def main(args: argparse.Namespace) -> int:
    cfg = load_config()
    profiles = build_profiles(cfg.respeaker.alsa_device)

    if args.only:
        wanted = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
        profiles = [p for i, p in enumerate(profiles, 1) if i in wanted]
        if not profiles:
            print(f"{RED}--only={args.only} did not match any profile (1..{len(build_profiles(cfg.respeaker.alsa_device))}){RST}")
            return 2

    print(f"{BLD}{CYN}=== KODA mic-profile benchmark ==={RST}")
    print(f"  Backend       : {cfg.backend.base_url}")
    print(f"  Default device: {cfg.respeaker.alsa_device}")
    print(f"  Sample rate   : {cfg.respeaker.sample_rate}")
    print(f"  Seconds/test  : {args.seconds}")
    print(f"  Profils       : {len(profiles)}")
    print(f"  Phrase cible  : {BLD}{TARGET_PHRASE}{RST}")

    # Load Vosk once
    vosk_model = None
    if not args.no_vosk:
        vosk_model = await ensure_vosk_model(cfg.vosk.language, cfg.vosk.models_dir)

    backend: Optional[BackendClient] = None
    if not args.no_azure:
        backend = BackendClient(cfg.backend.base_url, cfg.backend.timeout_seconds)
        await backend.start()
        if not await backend.health():
            print(f"{RED}✗ Backend health failed → désactivation Azure (--no-azure implicite){RST}")
            await backend.close()
            backend = None

    results: List[ProfileResult] = []
    try:
        for i, p in enumerate(profiles, 1):
            try:
                res = await run_one_profile(i, p, args.seconds, vosk_model, backend, args.no_prompt)
            except KeyboardInterrupt:
                print(f"\n{YEL}↩ Interrompu par l'utilisateur{RST}")
                break
            results.append(res)
    finally:
        if backend is not None:
            await backend.close()

    print_summary(results)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=5.0,
                        help="Durée de chaque enregistrement (default 5)")
    parser.add_argument("--only", type=str, default="",
                        help="Liste d'index 1-based séparés par virgule, ex: 2,3,7")
    parser.add_argument("--no-vosk", action="store_true",
                        help="Désactive le décodage Vosk local")
    parser.add_argument("--no-azure", action="store_true",
                        help="N'envoie pas au backend pour Azure STT")
    parser.add_argument("--no-prompt", action="store_true",
                        help="Pas d'attente ENTER entre profils (utile pour replay non-interactif)")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args)))
    except KeyboardInterrupt:
        raise SystemExit(130)
