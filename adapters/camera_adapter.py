from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


class CameraAdapter:
    """Capture a single JPEG frame from the Raspberry Pi CSI camera.

    Tries `rpicam-still` (Pi OS Bookworm / Bullseye), `libcamera-still`
    (older Bullseye) and `raspistill` (Buster legacy) in order — uses whichever
    is available on the system. Each capture is bounded by a short timeout so
    KODA never blocks for more than ~3 seconds on a missing camera.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        capture_timeout_ms: int = 500,
        process_timeout_s: float = 6.0,
    ) -> None:
        self._width = width
        self._height = height
        self._capture_timeout_ms = capture_timeout_ms
        self._process_timeout_s = process_timeout_s
        # One capture at a time; the CSI stack errors out if two processes
        # open the camera concurrently.
        self._lock = asyncio.Lock()

    def _build_candidates(self, out_path: Path) -> List[List[str]]:
        out = str(out_path)
        return [
            [
                "rpicam-still",
                "-o", out,
                "--width", str(self._width),
                "--height", str(self._height),
                "-t", str(self._capture_timeout_ms),
                "--nopreview",
            ],
            [
                "libcamera-still",
                "-o", out,
                "--width", str(self._width),
                "--height", str(self._height),
                "-t", str(self._capture_timeout_ms),
                "--nopreview",
            ],
            [
                "raspistill",
                "-o", out,
                "-w", str(self._width),
                "-h", str(self._height),
                "-t", str(self._capture_timeout_ms),
                "-n",
            ],
        ]

    async def capture_jpeg(self) -> Optional[bytes]:
        """Capture one JPEG. Returns the file bytes or None on failure."""
        async with self._lock:
            tmp = NamedTemporaryFile(suffix=".jpg", prefix="koda_face_", delete=False)
            tmp.close()
            out_path = Path(tmp.name)
            try:
                last_error = None
                for cmd in self._build_candidates(out_path):
                    if shutil.which(cmd[0]) is None:
                        continue
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            *cmd,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        try:
                            _, stderr = await asyncio.wait_for(
                                proc.communicate(),
                                timeout=self._process_timeout_s,
                            )
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()
                            last_error = f"{cmd[0]} timeout after {self._process_timeout_s}s"
                            continue
                        if proc.returncode != 0:
                            last_error = (stderr or b"").decode("utf-8", "replace").strip()[:300]
                            continue
                        if out_path.exists() and out_path.stat().st_size > 0:
                            data = out_path.read_bytes()
                            logger.debug("📷 Captured %d bytes via %s", len(data), cmd[0])
                            return data
                        last_error = f"{cmd[0]} returned 0 but no output file"
                    except FileNotFoundError:
                        continue
                    except Exception as exc:
                        last_error = f"{cmd[0]}: {exc}"
                logger.warning("Camera capture failed: %s", last_error or "no rpicam/libcamera/raspistill found")
                return None
            finally:
                try:
                    out_path.unlink(missing_ok=True)
                except OSError:
                    pass
