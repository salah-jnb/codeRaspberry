"""Download and extract a Vosk model into ``models/vosk/<lang>/``.

Used at runtime by :mod:`services.wake_word.vosk_engine` when ``VOSK_AUTO_DOWNLOAD``
is enabled and the requested model is missing locally. Can also be invoked
manually from the project root::

    python -m scripts.download_vosk_model ar
    python -m scripts.download_vosk_model fr --force

The downloads come straight from https://alphacephei.com/vosk/models and are
verified by checking the extracted directory contains the expected sub-folders.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("download_vosk_model")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS_DIR = PROJECT_ROOT / "models" / "vosk"

# Mandatory subdirs present in every valid Vosk model archive.
_REQUIRED_DIRS = ("am", "conf", "graph", "ivector")


@dataclass(frozen=True)
class ModelSpec:
    code: str
    url: str
    archive_root: str
    approx_size_mb: int
    notes: str = ""

    @property
    def required_disk_mb(self) -> int:
        # Peak during install = zip (~approx_size_mb) + extracted tree (~2x zip)
        # + a little headroom. Better to over-estimate and fail upfront than
        # hit OSError 28 mid-extraction.
        return int(self.approx_size_mb * 3 + 100)


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "ar": ModelSpec(
        code="ar",
        url="https://alphacephei.com/vosk/models/vosk-model-ar-mgb2-0.4.zip",
        archive_root="vosk-model-ar-mgb2-0.4",
        approx_size_mb=318,
        notes="Modern Standard Arabic (MGB-2 corpus). Best for Arabic names like محسن.",
    ),
    "fr": ModelSpec(
        code="fr",
        url="https://alphacephei.com/vosk/models/vosk-model-small-fr-0.22.zip",
        archive_root="vosk-model-small-fr-0.22",
        approx_size_mb=41,
        notes="French small. Light enough for keyword spotting on Pi 4.",
    ),
    "en": ModelSpec(
        code="en",
        url="https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        archive_root="vosk-model-small-en-us-0.15",
        approx_size_mb=40,
        notes="English (US) small. Light enough for keyword spotting on Pi 4.",
    ),
    "es": ModelSpec(
        code="es",
        url="https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip",
        archive_root="vosk-model-small-es-0.42",
        approx_size_mb=39,
        notes="Spanish small.",
    ),
    "de": ModelSpec(
        code="de",
        url="https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip",
        archive_root="vosk-model-small-de-0.15",
        approx_size_mb=45,
        notes="German small.",
    ),
    "it": ModelSpec(
        code="it",
        url="https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip",
        archive_root="vosk-model-small-it-0.22",
        approx_size_mb=48,
        notes="Italian small.",
    ),
    "tr": ModelSpec(
        code="tr",
        url="https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip",
        archive_root="vosk-model-small-tr-0.3",
        approx_size_mb=35,
        notes="Turkish small.",
    ),
    "pt": ModelSpec(
        code="pt",
        url="https://alphacephei.com/vosk/models/vosk-model-small-pt-0.3.zip",
        archive_root="vosk-model-small-pt-0.3",
        approx_size_mb=31,
        notes="Portuguese small.",
    ),
    "ru": ModelSpec(
        code="ru",
        url="https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
        archive_root="vosk-model-small-ru-0.22",
        approx_size_mb=45,
        notes="Russian small.",
    ),
    "cn": ModelSpec(
        code="cn",
        url="https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip",
        archive_root="vosk-model-small-cn-0.22",
        approx_size_mb=42,
        notes="Mandarin Chinese small.",
    ),
}


def _looks_valid(model_dir: Path) -> bool:
    return all((model_dir / sub).is_dir() for sub in _REQUIRED_DIRS)


_BLOCK_SIZE = 64 * 1024
_PROGRESS_INTERVAL_BYTES = 4 * 1024 * 1024  # log every 4 MB
_MAX_DOWNLOAD_ATTEMPTS = 6
_INITIAL_BACKOFF_S = 2.0
_MAX_BACKOFF_S = 30.0
_TIMEOUT_S = 60.0
_TRANSIENT_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionError,
    TimeoutError,
    urllib.error.URLError,
    OSError,
)


def _probe_total_size(url: str) -> int:
    """Return the remote Content-Length, or ``0`` if the server does not advertise it."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            value = resp.headers.get("Content-Length")
            return int(value) if value and value.isdigit() else 0
    except _TRANSIENT_ERRORS:
        return 0


def _download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` with HTTP Range resume + retry/backoff.

    ``dest`` is preserved between attempts so a connection drop on byte N
    resumes from byte N rather than restarting from zero. Once the file size
    matches the remote ``Content-Length`` the download is considered complete.
    """
    logger.info("Downloading %s", url)
    total_size = _probe_total_size(url)
    backoff = _INITIAL_BACKOFF_S

    for attempt in range(1, _MAX_DOWNLOAD_ATTEMPTS + 1):
        downloaded = dest.stat().st_size if dest.exists() else 0
        if total_size and downloaded >= total_size:
            logger.info("Already fully downloaded (%d bytes); skipping fetch.", downloaded)
            sys.stderr.write(
                f"\r  100.0%  ({downloaded // 1024 // 1024} MB / {total_size // 1024 // 1024} MB)\n"
            )
            sys.stderr.flush()
            return

        headers: dict[str, str] = {}
        open_mode = "wb"
        if downloaded > 0:
            headers["Range"] = f"bytes={downloaded}-"
            logger.info(
                "Attempt %d/%d — resuming at %d MB / %s MB",
                attempt, _MAX_DOWNLOAD_ATTEMPTS,
                downloaded // 1024 // 1024,
                (total_size // 1024 // 1024) if total_size else "?",
            )

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                # If we asked for a Range and the server replied 206 → resume.
                # If we asked for a Range but got 200 → server ignored Range,
                # so we must restart from zero in 'wb' mode.
                if downloaded > 0 and status == 206:
                    open_mode = "ab"
                else:
                    if downloaded > 0:
                        logger.info("Server did not honor Range; restarting from 0.")
                    open_mode = "wb"
                    downloaded = 0

                last_log = 0
                with dest.open(open_mode) as fp:
                    while True:
                        chunk = resp.read(_BLOCK_SIZE)
                        if not chunk:
                            break
                        fp.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and downloaded - last_log >= _PROGRESS_INTERVAL_BYTES:
                            pct = min(100.0, 100.0 * downloaded / total_size)
                            sys.stderr.write(
                                f"\r  {pct:5.1f}%  "
                                f"({downloaded // 1024 // 1024} MB / {total_size // 1024 // 1024} MB)"
                            )
                            sys.stderr.flush()
                            last_log = downloaded

            sys.stderr.write("\n")
            sys.stderr.flush()

            if total_size and downloaded < total_size:
                raise RuntimeError(
                    f"Short read: got {downloaded} / {total_size} bytes — connection likely truncated"
                )
            return

        except _TRANSIENT_ERRORS as exc:
            if attempt >= _MAX_DOWNLOAD_ATTEMPTS:
                logger.error(
                    "Download failed after %d attempts: %s (partial file kept at %s for next run)",
                    attempt, exc, dest,
                )
                raise
            sys.stderr.write("\n")
            sys.stderr.flush()
            logger.warning(
                "Network error on attempt %d/%d (%s) — sleeping %.1fs then resuming",
                attempt, _MAX_DOWNLOAD_ATTEMPTS, exc, backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_S)
        except RuntimeError as exc:
            # Short-read truncation: also retry within budget.
            if attempt >= _MAX_DOWNLOAD_ATTEMPTS:
                raise
            logger.warning(
                "Truncated download on attempt %d/%d (%s) — sleeping %.1fs then resuming",
                attempt, _MAX_DOWNLOAD_ATTEMPTS, exc, backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_S)


def _extract(archive: Path, work_dir: Path) -> Path:
    logger.info("Extracting %s", archive.name)
    with zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(work_dir)
    entries = [p for p in work_dir.iterdir() if p.is_dir()]
    if not entries:
        raise RuntimeError(f"Archive {archive.name} did not contain a model directory")
    return entries[0]


def ensure_model(
    language: str,
    models_dir: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Return the local path of the Vosk model for ``language``; download if needed.

    The downloaded archive is kept in ``<models_dir>/.partial/<code>.zip`` between
    attempts so a Ctrl-C / SIGTERM / connection drop / power-off does not erase
    progress — re-running the script resumes from the byte where the previous
    attempt died.
    """
    spec = MODEL_REGISTRY.get(language)
    if spec is None:
        supported = ", ".join(sorted(MODEL_REGISTRY))
        raise ValueError(
            f"Unsupported VOSK_LANGUAGE={language!r}. Supported: {supported}. "
            "Add a new entry in scripts/download_vosk_model.py MODEL_REGISTRY."
        )

    base_dir = models_dir or DEFAULT_MODELS_DIR
    target_dir = base_dir / spec.code
    partial_dir = base_dir / ".partial"
    archive = partial_dir / f"{spec.code}.zip"

    if not force and target_dir.is_dir() and _looks_valid(target_dir):
        logger.debug("Vosk model already present at %s", target_dir)
        return target_dir

    if force and archive.exists():
        logger.info("--force: discarding partial archive at %s", archive)
        archive.unlink()

    if target_dir.exists():
        logger.info("Removing incomplete or stale model at %s", target_dir)
        shutil.rmtree(target_dir)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    partial_dir.mkdir(parents=True, exist_ok=True)

    free_mb = shutil.disk_usage(partial_dir).free // 1024 // 1024
    if free_mb < spec.required_disk_mb:
        raise RuntimeError(
            f"Not enough disk space to install model {spec.code!r}: "
            f"{free_mb} MB free at {partial_dir}, need ≈{spec.required_disk_mb} MB "
            f"(zip {spec.approx_size_mb} MB + extraction ~2x + headroom). "
            "Free space (sudo apt clean, remove old logs/snaps) or move models_dir to a larger volume."
        )
    logger.info(
        "Installing Vosk model for %s (≈%d MB, %d MB free at %s)…",
        spec.code, spec.approx_size_mb, free_mb, partial_dir,
    )

    _download(spec.url, archive)

    extract_dir = partial_dir / f"{spec.code}_extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    try:
        extracted_root = _extract(archive, extract_dir)
        shutil.move(str(extracted_root), str(target_dir))
    finally:
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)

    if not _looks_valid(target_dir):
        raise RuntimeError(
            f"Downloaded model at {target_dir} is missing required folders "
            f"({', '.join(_REQUIRED_DIRS)})."
        )

    # Successful install — partial archive is no longer needed.
    try:
        archive.unlink(missing_ok=True)
    except OSError:
        pass

    logger.info("Vosk model installed at %s", target_dir)
    return target_dir


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download a Vosk model into the project models/vosk/<lang>/ folder.",
    )
    parser.add_argument(
        "language",
        help="Language code to download (e.g. ar, fr, en).",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=DEFAULT_MODELS_DIR,
        help="Override the destination directory (default: <repo>/models/vosk).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if a valid model is already present.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _build_arg_parser().parse_args(argv)
    try:
        path = ensure_model(args.language, models_dir=args.models_dir, force=args.force)
    except (ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
