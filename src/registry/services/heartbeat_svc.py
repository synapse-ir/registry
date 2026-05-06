"""
Heartbeat cache service — §8 C3.

Background thread polls each registered model's heartbeat_endpoint every
30 s (staggered across the window). All routing queries read from this
cache; live HTTP requests on the hot path are forbidden.

Fallback table (§8 C3):
  fresh         (<30 s)  → available
  stale         (30–90 s)→ degraded  (include, log warning)
  very stale    (>90 s)  → unavailable
  never polled           → unavailable (baseline capacity 0.8 from perf_profile)
  failures ≥3            → unavailable immediately
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_POLL_INTERVAL = int(os.getenv("SYNAPSE_HEARTBEAT_POLL_INTERVAL_SECONDS", "30"))
_STALE_THRESHOLD = int(os.getenv("SYNAPSE_HEARTBEAT_STALE_THRESHOLD_SECONDS", "30"))
_DROP_THRESHOLD = int(os.getenv("SYNAPSE_HEARTBEAT_DROP_THRESHOLD_SECONDS", "90"))
_POLL_TIMEOUT = float(os.getenv("SYNAPSE_HEARTBEAT_TIMEOUT_SECONDS", "5"))
_MAX_FAILURES = 3


@dataclass
class HeartbeatMeta:
    fetched_at: float       # unix timestamp of last successful poll
    is_stale: bool
    consecutive_failures: int
    last_error: str | None = None


@dataclass
class CachedHeartbeat:
    response: dict[str, Any]
    meta: HeartbeatMeta


@dataclass
class _ModelEntry:
    model_id: str
    heartbeat_endpoint: str
    next_poll_at: float     # unix timestamp when next poll is due


class HeartbeatService:
    """
    In-process heartbeat cache. One instance per registry process.
    MUST NOT be shared across processes (§8 C3 storage requirement).
    """

    def __init__(self) -> None:
        self._cache: dict[str, CachedHeartbeat] = {}
        self._models: dict[str, _ModelEntry] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling thread."""
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="heartbeat-poller",
            daemon=True,
        )
        self._thread.start()
        logger.info("HeartbeatService started (interval=%ds)", _POLL_INTERVAL)

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("HeartbeatService stopped")

    # ------------------------------------------------------------------
    # Model registration
    # ------------------------------------------------------------------

    def register_model(self, model_id: str, heartbeat_endpoint: str) -> None:
        """Register a model for polling. Staggered across the poll window."""
        with self._lock:
            if model_id not in self._models:
                delay = random.uniform(0, _POLL_INTERVAL)
                self._models[model_id] = _ModelEntry(
                    model_id=model_id,
                    heartbeat_endpoint=heartbeat_endpoint,
                    next_poll_at=time.time() + delay,
                )
            else:
                # Endpoint may have changed on model update
                self._models[model_id].heartbeat_endpoint = heartbeat_endpoint

    def unregister_model(self, model_id: str) -> None:
        """Remove a deprecated model from polling and clear its cache entry."""
        with self._lock:
            self._models.pop(model_id, None)
            self._cache.pop(model_id, None)

    # ------------------------------------------------------------------
    # Cache reads (hot path — no I/O)
    # ------------------------------------------------------------------

    def get_cached(self, model_id: str) -> CachedHeartbeat | None:
        """Return the cached heartbeat entry, or None if never polled."""
        with self._lock:
            return self._cache.get(model_id)

    def routing_status(self, model_id: str) -> str:
        """
        Return routing decision per §8 C3 fallback table.

        Returns one of:
          'available'   — fresh (<30 s)
          'degraded'    — stale (30–90 s); include with log warning
          'unavailable' — very stale (>90 s) or ≥3 consecutive failures
          'never_polled'— no poll result yet
        """
        with self._lock:
            entry = self._cache.get(model_id)

        if entry is None:
            return "never_polled"

        if entry.meta.consecutive_failures >= _MAX_FAILURES:
            return "unavailable"

        age = time.time() - entry.meta.fetched_at
        if age > _DROP_THRESHOLD:
            return "unavailable"
        if age > _STALE_THRESHOLD:
            return "degraded"
        return "available"

    def get_capacity_pct(self, model_id: str) -> float:
        """
        Return capacity_pct from the cached heartbeat response.
        Falls back to 0.8 baseline for never-polled models (§8 C3).
        """
        with self._lock:
            entry = self._cache.get(model_id)
        if entry is None:
            return 0.8
        return float(entry.response.get("capacity_pct", 0.8))

    # ------------------------------------------------------------------
    # Background polling
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Main loop: every second, poll any model whose timer has elapsed."""
        while not self._stop.is_set():
            now = time.time()
            due: list[_ModelEntry] = []
            with self._lock:
                for entry in list(self._models.values()):
                    if now >= entry.next_poll_at:
                        due.append(entry)

            for entry in due:
                self._poll_one(entry)

            self._stop.wait(timeout=1.0)

    def _poll_one(self, entry: _ModelEntry) -> None:
        """Poll a single model endpoint and update the cache."""
        model_id = entry.model_id
        try:
            resp = httpx.get(entry.heartbeat_endpoint, timeout=_POLL_TIMEOUT)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

            now = time.time()
            with self._lock:
                self._cache[model_id] = CachedHeartbeat(
                    response=data,
                    meta=HeartbeatMeta(
                        fetched_at=now,
                        is_stale=False,
                        consecutive_failures=0,
                    ),
                )
                if model_id in self._models:
                    self._models[model_id].next_poll_at = now + _POLL_INTERVAL

            logger.debug("Heartbeat OK: %s", model_id)

        except Exception as exc:
            now = time.time()
            with self._lock:
                existing = self._cache.get(model_id)
                prev_failures = existing.meta.consecutive_failures if existing else 0
                failures = prev_failures + 1
                self._cache[model_id] = CachedHeartbeat(
                    response=existing.response if existing else {},
                    meta=HeartbeatMeta(
                        fetched_at=existing.meta.fetched_at if existing else 0.0,
                        is_stale=True,
                        consecutive_failures=failures,
                        last_error=str(exc),
                    ),
                )
                if model_id in self._models:
                    self._models[model_id].next_poll_at = now + _POLL_INTERVAL

            logger.warning(
                "Heartbeat FAIL: %s — %s (consecutive_failures=%d)",
                model_id, exc, failures,
            )


# Module-level singleton — one per registry process
heartbeat_service = HeartbeatService()
