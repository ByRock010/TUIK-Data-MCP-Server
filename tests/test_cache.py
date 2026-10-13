from tuik_mcp.cache import MemoryTTLCache


def test_cache_expiry_eviction_and_isolation(monkeypatch):
    clock = [0]
    monkeypatch.setattr("tuik_mcp.cache.time.monotonic", lambda: clock[0])
    cache = MemoryTTLCache(max_entries=2)
    cache.set("a", {"x": []}, 10)
    cache.get("a")["x"].append(1)
    assert cache.get("a") == {"x": []}
    cache.set("b", 2, 10)
    cache.set("c", 3, 10)
    assert cache.get("a") is None
    clock[0] = 11
    assert cache.get("b") is None
