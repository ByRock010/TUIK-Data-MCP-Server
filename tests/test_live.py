import importlib.util
from pathlib import Path

import pytest


@pytest.mark.live
async def test_live_mcp_workflow():
    script = Path(__file__).parents[1] / "scripts" / "live_smoke.py"
    spec = importlib.util.spec_from_file_location("live_smoke", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = await module.run()
    assert len(report["tools"]) == 5
