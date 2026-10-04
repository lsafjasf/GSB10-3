"""Tiny in-memory cache with per-entry expiry."""

from .thresholds import CACHE_TTL


class Cache:
    def __init__(self, clock):
        self._clock = clock
        self._entries = {}

    def put(self, key, value, ttl=None):
        effective_ttl = CACHE_TTL if ttl is None else ttl
        self._entries[key] = (self._clock() + effective_ttl, value)

    def get(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    def __len__(self):
        return len(self._entries)
