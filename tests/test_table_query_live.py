import importlib.util
from pathlib import Path

import pytest


@pytest.mark.live
async def test_live_snapshot_bound_queries():
    script = Path(__file__).parents[1] / "scripts/live_query_smoke.py"
    spec = importlib.util.spec_from_file_location("live_query_smoke", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = await module.run()
    for name in ("csv", "excel"):
        assert len(report[name]["pages"]) == 2
        assert report[name]["pages"][0]["snapshot_id"] == report[name]["pages"][1]["snapshot_id"]
