"""
Calibration buffer — §8 C5-compatible pattern.

Signals are enqueued into an in-memory deque (capped at SYNAPSE_CAL_BUFFER_MAX)
and flushed every SYNAPSE_CAL_FLUSH_INTERVAL_SECONDS by a background asyncio
task. The flush step is where routing-weight updates would be applied once the
Calibration Layer is wired in; currently it logs and discards.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque

from registry.models.signal import CalibrationSignal

logger = logging.getLogger(__name__)

_BUFFER_MAX = int(os.getenv("SYNAPSE_CAL_BUFFER_MAX", "100"))
_FLUSH_INTERVAL = float(os.getenv("SYNAPSE_CAL_FLUSH_INTERVAL_SECONDS", "5"))
_ENABLED = os.getenv("SYNAPSE_CAL_ENABLED", "true").lower() == "true"


class CalibrationBuffer:
    """
    Async-safe in-memory buffer for CalibrationSignal objects.

    Dropping signals on overflow is intentional — routing decisions must
    never block on calibration I/O (C5 contract).
    """

    def __init__(self) -> None:
        self._buffer: deque[CalibrationSignal] = deque(maxlen=_BUFFER_MAX)
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if _ENABLED:
            self._flush_task = asyncio.create_task(
                self._flush_loop(), name="calibration-flush"
            )
            logger.info(
                "CalibrationBuffer started (max=%d, interval=%.1fs)",
                _BUFFER_MAX,
                _FLUSH_INTERVAL,
            )

    async def stop(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush()   # drain remaining signals on shutdown

    async def enqueue(self, signal: CalibrationSignal) -> None:
        """Enqueue a signal. Silently drops if buffer is full (overflow policy)."""
        if not _ENABLED:
            return
        if signal.observed_at is None:
            signal = signal.model_copy(update={"observed_at": int(time.time())})
        async with self._lock:
            self._buffer.append(signal)

    # ------------------------------------------------------------------
    # Internal flush
    # ------------------------------------------------------------------

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL)
            await self._flush()

    async def _flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            batch = list(self._buffer)
            self._buffer.clear()

        logger.info("CalibrationBuffer: flushing %d signal(s)", len(batch))
        # Future: persist to DB or forward to Calibration Layer for weight updates.


# Module-level singleton
calibration_buffer = CalibrationBuffer()
