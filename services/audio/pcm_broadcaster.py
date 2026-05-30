"""Fan out a single PCM stream to N concurrent consumers.

Why this exists
===============
For the hybrid wake-word architecture, both Vosk (local recognizer) and the
Azure WS forwarder need to consume the SAME audio bytes at the SAME time.
Spawning two ``arecord``/``sox`` processes against the ReSpeaker causes the
USB firmware to error out, so we serialize on one capture and broadcast the
chunks to each consumer's own asyncio.Queue.

Each consumer gets a bounded queue (drop-oldest on overflow) so a slow
consumer doesn't stall the others.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, List

from utils.logger import get_logger

logger = get_logger(__name__)


class PcmBroadcaster:
    """Wraps ``RespeakerAdapter.stream_pcm`` and tees its output to N queues."""

    def __init__(self, source_stream: AsyncIterator[bytes], queue_maxsize: int = 32) -> None:
        self._source = source_stream
        self._queue_maxsize = queue_maxsize
        self._consumers: List[asyncio.Queue[bytes | None]] = []
        self._lock = asyncio.Lock()
        self._dispatcher_task: asyncio.Task | None = None
        self._closed = False

    async def add_consumer(self) -> asyncio.Queue[bytes | None]:
        """Register a new consumer. Returns an asyncio.Queue that will receive
        PCM chunks. A ``None`` value signals end-of-stream."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._consumers.append(queue)
        return queue

    async def remove_consumer(self, queue: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._consumers.remove(queue)
            except ValueError:
                pass

    async def start(self) -> None:
        if self._dispatcher_task is not None:
            return
        self._dispatcher_task = asyncio.create_task(self._dispatch(), name="pcm_broadcaster")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except (asyncio.CancelledError, BaseException):
                pass
        async with self._lock:
            for q in self._consumers:
                with self._suppress():
                    q.put_nowait(None)
            self._consumers.clear()

    @staticmethod
    def _suppress():
        import contextlib
        return contextlib.suppress(BaseException)

    async def _dispatch(self) -> None:
        try:
            async for chunk in self._source:
                if self._closed:
                    return
                if not chunk:
                    continue
                async with self._lock:
                    consumers = list(self._consumers)
                for q in consumers:
                    if q.full():
                        # Drop oldest to keep latency low for fast consumers.
                        try:
                            _ = q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        logger.debug("Broadcaster: dropped 1 chunk for slow consumer")
                    q.put_nowait(chunk)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("PcmBroadcaster dispatch crashed")


async def queue_iterator(queue: asyncio.Queue[bytes | None]) -> AsyncIterator[bytes]:
    """Consume a broadcaster queue as an async iterator. Stops on a ``None`` sentinel."""
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item


@asynccontextmanager
async def consume(broadcaster: PcmBroadcaster) -> AsyncIterator[AsyncIterator[bytes]]:
    """Convenience context manager that auto-registers/unregisters a consumer."""
    q = await broadcaster.add_consumer()
    try:
        yield queue_iterator(q)
    finally:
        await broadcaster.remove_consumer(q)
