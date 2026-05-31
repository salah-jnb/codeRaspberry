"""Process-wide registry of long-running subprocesses so the shutdown hook
can kill anything we leaked (sox, arecord, rpicam-still, yt-dlp, …).

Why this exists
---------------
Without explicit tracking, an exception inside an async-iterator that yields
PCM (e.g. ``RespeakerAdapter.stream_pcm``) can skip the ``finally`` cleanup if
the consumer cancels it during an `await` not awaited inside the iterator
itself. The subprocess stays alive and keeps the USB endpoint busy until the
whole Python process exits.

By registering every long-running ``asyncio.subprocess.Process`` we create,
the ``_shutdown`` path can iterate the live set and kill everything left.

Usage
-----
    proc = await asyncio.create_subprocess_exec(...)
    track_subprocess(proc, label="sox capture")
    try:
        # use proc
    finally:
        untrack_subprocess(proc)
        # plus your own terminate/wait

    # At shutdown:
    await kill_tracked_subprocesses()
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)

# (proc, label) so the cleanup log tells you who leaked.
_REGISTRY: Set[Tuple[asyncio.subprocess.Process, str]] = set()


def track_subprocess(proc: asyncio.subprocess.Process, label: str = "?") -> None:
    """Register a subprocess so the global shutdown can kill it if it leaks."""
    if proc is None:
        return
    _REGISTRY.add((proc, label))


def untrack_subprocess(proc: Optional[asyncio.subprocess.Process]) -> None:
    """Remove a subprocess from the registry (called by the owner on clean exit)."""
    if proc is None:
        return
    for entry in list(_REGISTRY):
        if entry[0] is proc:
            _REGISTRY.discard(entry)
            return


async def kill_tracked_subprocesses(*, grace_s: float = 1.5) -> None:
    """Politely terminate, then SIGKILL anything still alive. Idempotent."""
    leftovers = [(p, label) for p, label in _REGISTRY if p.returncode is None]
    if not leftovers:
        return
    logger.warning(
        "Shutdown: %d subprocess(es) still alive — terminating: %s",
        len(leftovers), [lbl for _, lbl in leftovers],
    )
    for proc, _ in leftovers:
        try:
            proc.terminate()
        except (ProcessLookupError, Exception):
            pass
    # Wait grace_s for SIGTERM to take effect.
    deadline = asyncio.get_event_loop().time() + grace_s
    for proc, label in leftovers:
        remaining = max(0.0, deadline - asyncio.get_event_loop().time())
        try:
            await asyncio.wait_for(proc.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
                logger.warning("Shutdown: SIGKILL'd %s (PID %s)", label, proc.pid)
            except (ProcessLookupError, Exception):
                pass
    _REGISTRY.clear()


def pkill_orphans(patterns: Optional[list[str]] = None) -> int:
    """Belt-and-suspenders: run `pkill` against well-known capture binaries
    that we might have spawned. Returns the count killed."""
    if patterns is None:
        patterns = ["sox", "arecord", "rpicam-still", "rpicam-vid",
                    "libcamera-still", "yt-dlp", "ffmpeg"]
    killed = 0
    for pat in patterns:
        # -P $$ would only kill our direct children; we use a name match
        # restricted to processes owned by the current user.
        rc = os.system(f"pkill -u {os.getuid()} -f {pat} >/dev/null 2>&1")
        if rc == 0:
            killed += 1
    return killed
