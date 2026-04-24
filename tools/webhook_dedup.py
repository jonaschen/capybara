"""
tools/webhook_dedup.py

TTL-bounded in-memory cache for LINE webhook event IDs. LINE retries delivery
when the initial response is slow (>1s); dedup avoids double-replying.
"""

from __future__ import annotations

import time


class WebhookDeduplicator:
    def __init__(self, ttl_seconds: int = 300, cleanup_threshold: int = 100):
        self._seen: dict[str, float] = {}
        self._ttl = ttl_seconds
        self._cleanup_threshold = cleanup_threshold
        self._since_cleanup = 0

    def is_duplicate(self, event_id: str | None) -> bool:
        if not event_id:
            return False

        now = time.monotonic()
        self._since_cleanup += 1
        if self._since_cleanup >= self._cleanup_threshold:
            self._cleanup(now)
            self._since_cleanup = 0

        if event_id in self._seen:
            if now - self._seen[event_id] < self._ttl:
                return True
            del self._seen[event_id]

        self._seen[event_id] = now
        return False

    def _cleanup(self, now: float) -> None:
        expired = [k for k, ts in self._seen.items() if now - ts >= self._ttl]
        for k in expired:
            del self._seen[k]
