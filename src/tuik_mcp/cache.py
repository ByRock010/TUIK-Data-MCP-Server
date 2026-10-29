import time
from collections import OrderedDict
from copy import deepcopy
from typing import Any, Protocol


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: float) -> None: ...


class MemoryTTLCache:
    """Bounded process-local cache; copy values to avoid cross-request mutation."""

    def __init__(self, max_entries: int = 2048):
        self.max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        item = self._items.get(key)
        if item is None:
            return None
        if item[0] <= time.monotonic():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return deepcopy(item[1])

    def set(self, key: str, value: Any, ttl: float) -> None:
        expired = [key for key, item in self._items.items() if item[0] <= time.monotonic()]
        for old in expired:
            del self._items[old]
        self._items[key] = (time.monotonic() + ttl, deepcopy(value))
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)


class BytesTTLCache:
    """LRU cache with an exact payload-byte budget; values are immutable bytes."""

    def __init__(self, max_bytes: int, max_entries: int = 256):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._items: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self.size_bytes = 0

    def _remove(self, key: str) -> None:
        _, value = self._items.pop(key)
        self.size_bytes -= len(value)

    def get(self, key: str) -> bytes | None:
        item = self._items.get(key)
        if item is None:
            return None
        if item[0] <= time.monotonic():
            self._remove(key)
            return None
        self._items.move_to_end(key)
        return item[1]

    def remaining_ttl(self, key: str) -> float:
        """Remaining retention, without extending it (even if the same bytes return later)."""
        if self.get(key) is None:
            return 0
        return max(0, self._items[key][0] - time.monotonic())

    def set(self, key: str, value: bytes, ttl: float = 3600) -> None:
        for old in list(self._items):
            if self._items[old][0] <= time.monotonic():
                self._remove(old)
        if key in self._items:
            self._remove(key)
        if len(value) > self.max_bytes:
            return
        self._items[key] = (time.monotonic() + ttl, value)
        self.size_bytes += len(value)
        while self.size_bytes > self.max_bytes or len(self._items) > self.max_entries:
            self._remove(next(iter(self._items)))
