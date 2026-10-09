"""Small TTL cache over a named Apify key-value store (shared across runs). Failures are non-fatal."""

from __future__ import annotations

import re
import time
from typing import Any

STORE_NAME = "usace-waterway-source-cache"


class KeyValueCache:
    def __init__(self, store: Any) -> None:
        self.store = store

    @staticmethod
    def _key(key: str) -> str:
        return re.sub(r"[^a-zA-Z0-9!\-_.'()]", "_", key)[:250]

    async def get(self, key: str, ttl_seconds: float) -> tuple[Any, float] | None:
        """Return `(value, fetchedAt)` when present and younger than the TTL."""
        try:
            entry = await self.store.get_value(self._key(key))
        except Exception:
            return None
        if not isinstance(entry, dict) or "value" not in entry:
            return None
        fetched = float(entry.get("fetchedAt", 0))
        if time.time() - fetched > ttl_seconds:
            return None
        return entry["value"], fetched

    async def put(self, key: str, value: Any) -> None:
        try:
            await self.store.set_value(self._key(key), {"fetchedAt": time.time(), "value": value})
        except Exception:
            return


class NullCache(KeyValueCache):
    def __init__(self) -> None:
        super().__init__(None)

    async def get(self, key: str, ttl_seconds: float) -> tuple[Any, float] | None:
        return None

    async def put(self, key: str, value: Any) -> None:
        return
