from __future__ import annotations

import asyncio
import shutil
import time
import urllib.request
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Optional

from utils.logger import get_logger
from utils.subprocess_registry import track_subprocess, untrack_subprocess

logger = get_logger(__name__)


class CameraAdapter:
    """Capture one JPEG frame from the Raspberry Pi camera.

    The preferred path is MJPEG because it matches the working
    ``face_capture_web.py`` script: it uses ``rpicam-vid`` / ``libcamera-vid``
    and extracts a complete JPEG frame from the stream. This avoids the common
    Pi camera crash caused by opening ``rpicam-still`` while another preview or
    MJPEG stream already owns the camera.

    If ``mjpeg_url`` is set, the adapter first reads a frame from that existing
    stream, for example ``http://127.0.0.1:5000/video_feed``. If that fails, it
    tries a short local MJPEG process, then falls back to still capture.
    """

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        capture_timeout_ms: int = 500,
        process_timeout_s: float = 6.0,
        prefer_mjpeg: bool = True,
        mjpeg_url: Optional[str] = None,
        mjpeg_timeout_s: float = 4.0,
        stream_width: int = 640,
        stream_height: int = 480,
        stream_fps: int = 10,
    ) -> None:
        self._width = width
        self._height = height
        self._capture_timeout_ms = capture_timeout_ms
        self._process_timeout_s = process_timeout_s
        self._prefer_mjpeg = prefer_mjpeg
        self._mjpeg_url = mjpeg_url
        self._mjpeg_timeout_s = mjpeg_timeout_s
        self._stream_width = stream_width
        self._stream_height = stream_height
        self._stream_fps = max(1, stream_fps)
        self._lock = asyncio.Lock()

    def _build_still_candidates(self, out_path: Path) -> List[List[str]]:
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

    def _build_mjpeg_candidates(self) -> List[List[str]]:
        duration_ms = max(500, int(self._mjpeg_timeout_s * 1000))
        return [
            [
                "rpicam-vid",
                "-t", str(duration_ms),
                "--codec", "mjpeg",
                "--width", str(self._stream_width),
                "--height", str(self._stream_height),
                "--framerate", str(self._stream_fps),
                "--inline",
                "-o", "-",
            ],
            [
                "libcamera-vid",
                "-t", str(duration_ms),
                "--codec", "mjpeg",
                "--width", str(self._stream_width),
                "--height", str(self._stream_height),
                "--framerate", str(self._stream_fps),
                "--inline",
                "-o", "-",
            ],
        ]

    async def capture_jpeg(self) -> Optional[bytes]:
        """Capture one JPEG. Returns bytes, or None on graceful failure."""
        async with self._lock:
            if self._mjpeg_url:
                data = await self._capture_from_mjpeg_url()
                if data:
                    return data

            if self._prefer_mjpeg:
                data = await self._capture_from_mjpeg_process()
                if data:
                    return data

            return await self._capture_from_still_process()

    async def _capture_from_still_process(self) -> Optional[bytes]:
        tmp = NamedTemporaryFile(suffix=".jpg", prefix="koda_face_", delete=False)
        tmp.close()
        out_path = Path(tmp.name)
        try:
            last_error = None
            for cmd in self._build_still_candidates(out_path):
                if shutil.which(cmd[0]) is None:
                    continue
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    track_subprocess(proc, label=f"{cmd[0]} face still")
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
                    finally:
                        untrack_subprocess(proc)
                    if proc.returncode != 0:
                        last_error = (stderr or b"").decode("utf-8", "replace").strip()[:300]
                        continue
                    if out_path.exists() and out_path.stat().st_size > 0:
                        data = out_path.read_bytes()
                        logger.debug("Captured %d bytes via %s", len(data), cmd[0])
                        return data
                    last_error = f"{cmd[0]} returned 0 but no output file"
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    last_error = f"{cmd[0]}: {exc}"
            logger.warning("Camera capture failed: %s", last_error or "no camera command found")
            return None
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _capture_from_mjpeg_process(self) -> Optional[bytes]:
        last_error = None
        for cmd in self._build_mjpeg_candidates():
            if shutil.which(cmd[0]) is None:
                continue
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                track_subprocess(proc, label=f"{cmd[0]} face mjpeg")
                data = None
                try:
                    assert proc.stdout is not None
                    buf = b""
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + self._mjpeg_timeout_s
                    while loop.time() < deadline:
                        remaining = max(0.1, deadline - loop.time())
                        chunk = await asyncio.wait_for(proc.stdout.read(8192), timeout=remaining)
                        if not chunk:
                            break
                        buf += chunk
                        if len(buf) > 512 * 1024:
                            buf = buf[-256 * 1024 :]
                        data = self._extract_first_jpeg(buf)
                        if data:
                            break
                except asyncio.TimeoutError:
                    last_error = f"{cmd[0]} timeout after {self._mjpeg_timeout_s:.1f}s"
                finally:
                    if proc.returncode is None:
                        proc.terminate()
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()
                    untrack_subprocess(proc)

                if data:
                    logger.debug("Captured %d bytes via %s MJPEG", len(data), cmd[0])
                    return data
                if proc.returncode != 0:
                    stderr = b""
                    if proc.stderr is not None:
                        try:
                            stderr = await proc.stderr.read()
                        except Exception:
                            stderr = b""
                    last_error = (stderr or b"").decode("utf-8", "replace").strip()[:300]
                else:
                    last_error = f"{cmd[0]} produced no complete JPEG frame"
            except FileNotFoundError:
                continue
            except Exception as exc:
                last_error = f"{cmd[0]}: {exc}"
        if last_error:
            logger.warning("MJPEG camera capture failed: %s", last_error)
        return None

    async def _capture_from_mjpeg_url(self) -> Optional[bytes]:
        try:
            return await asyncio.to_thread(self._read_mjpeg_url_once)
        except Exception as exc:
            logger.warning("MJPEG URL capture failed from %s: %s", self._mjpeg_url, exc)
            return None

    def _read_mjpeg_url_once(self) -> Optional[bytes]:
        if not self._mjpeg_url:
            return None
        deadline = time.monotonic() + self._mjpeg_timeout_s
        buf = b""
        with urllib.request.urlopen(self._mjpeg_url, timeout=self._mjpeg_timeout_s) as response:
            while time.monotonic() < deadline:
                chunk = response.read(8192)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 512 * 1024:
                    buf = buf[-256 * 1024 :]
                data = self._extract_first_jpeg(buf)
                if data:
                    logger.debug("Captured %d bytes from MJPEG URL %s", len(data), self._mjpeg_url)
                    return data
        return None

    @staticmethod
    def _extract_first_jpeg(payload: bytes) -> Optional[bytes]:
        soi = payload.find(b"\xff\xd8")
        if soi < 0:
            return None
        eoi = payload.find(b"\xff\xd9", soi + 2)
        if eoi < 0:
            return None
        return payload[soi:eoi + 2]
