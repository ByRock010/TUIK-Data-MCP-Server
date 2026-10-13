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
