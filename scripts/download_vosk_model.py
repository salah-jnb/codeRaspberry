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
import tempfile
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


def _download(url: str, dest: Path) -> None:
    logger.info("Downloading %s", url)

    def _progress(blocks: int, block_size: int, total_size: int) -> None:
        if total_size <= 0:
            return
        downloaded = blocks * block_size
        pct = min(100.0, 100.0 * downloaded / total_size)
        if blocks % 100 == 0 or downloaded >= total_size:
            sys.stderr.write(
                f"\r  {pct:5.1f}%  ({downloaded // 1024 // 1024} MB / {total_size // 1024 // 1024} MB)"
            )
            sys.stderr.flush()

    urllib.request.urlretrieve(url, dest, reporthook=_progress)
    sys.stderr.write("\n")
    sys.stderr.flush()


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
    """Return the local path of the Vosk model for ``language``; download if needed."""
    spec = MODEL_REGISTRY.get(language)
    if spec is None:
        supported = ", ".join(sorted(MODEL_REGISTRY))
        raise ValueError(
            f"Unsupported VOSK_LANGUAGE={language!r}. Supported: {supported}. "
            "Add a new entry in scripts/download_vosk_model.py MODEL_REGISTRY."
        )

    target_dir = (models_dir or DEFAULT_MODELS_DIR) / spec.code
    if not force and target_dir.is_dir() and _looks_valid(target_dir):
        logger.debug("Vosk model already present at %s", target_dir)
        return target_dir

    if target_dir.exists():
        logger.info("Removing incomplete or stale model at %s", target_dir)
        shutil.rmtree(target_dir)

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Installing Vosk model for %s (≈%d MB)…", spec.code, spec.approx_size_mb)

    with tempfile.TemporaryDirectory(prefix=f"vosk_{spec.code}_") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "model.zip"
        _download(spec.url, archive)
        extracted_root = _extract(archive, tmp_path)
        shutil.move(str(extracted_root), str(target_dir))

    if not _looks_valid(target_dir):
        raise RuntimeError(
            f"Downloaded model at {target_dir} is missing required folders "
            f"({', '.join(_REQUIRED_DIRS)})."
        )

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
