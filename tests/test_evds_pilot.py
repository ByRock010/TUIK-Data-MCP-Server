"""Run the documented two-provider evidence replay as a user would."""

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

PILOT = Path(__file__).resolve().parents[1] / "docs" / "verification" / "issue-109"


def replay(path):
    return subprocess.run(
        [sys.executable, str(PILOT / "compose.py"), str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_replay_keeps_both_providers_and_exact_audit_links():
    fixture = PILOT / "2026-q1.json"
    first = replay(fixture)
    assert first.returncode == 0, first.stderr
    assert replay(fixture).stdout == first.stdout
    evidence = json.loads(first.stdout)
    assert evidence["schema_version"] == "tuik.evds-pilot-evidence/1"
    source = json.loads(fixture.read_text())
    assert evidence["sources"] == {key: source[key] for key in ("cpi", "usd_try")}
    assert [r["period"] for r in evidence["months"]] == ["2026-01", "2026-02", "2026-03"]
    assert [r["cpi"]["index"] for r in evidence["months"]] == ["100.0000", "102.9638", "104.9598"]
    assert [r["usd_try"]["index"] for r in evidence["months"]] == [
        "100.0000",
        "101.0581",
        "102.4335",
    ]
    for provider in ("cpi", "usd_try"):
        observations = {item["period"]: item for item in source[provider]["observations"]}
        for row in evidence["months"]:
            assert row[provider]["observation_id"] == observations[row["period"]]["id"]
            assert row[provider]["base_id"] == observations["2026-01"]["id"]


@pytest.mark.parametrize(
    ("provider", "key", "bad_value"),
    [
        ("cpi", "frequency", "quarterly"),
        ("cpi", "index_base", "2003=100"),
        ("cpi", "seasonal_adjustment", "adjusted"),
        ("usd_try", "currency", "USD/TRY"),
        ("usd_try", "aggregation", "daily_average"),
    ],
)
def test_mismatched_series_metadata_is_refused(tmp_path, provider, key, bad_value):
    payload = json.loads((PILOT / "2026-q1.json").read_text())
    payload[provider][key] = bad_value
    fixture = tmp_path / "mismatch.json"
    fixture.write_text(json.dumps(payload))
    result = replay(fixture)
    assert result.returncode == 2 and result.stdout == ""
    assert "Pilot evidence refused" in result.stderr
    assert bad_value not in result.stderr


@pytest.mark.parametrize("problem", ["duplicate", "missing", "url", "notation", "oversize"])
def test_incomplete_or_untrusted_inputs_are_refused(tmp_path, problem):
    payload = json.loads((PILOT / "2026-q1.json").read_text())
    observations = payload["usd_try"]["observations"]
    if problem == "duplicate":
        revised = deepcopy(observations[-1])
        revised["sha256"] = "1" * 64
        observations.append(revised)
    elif problem == "missing":
        observations.pop()
    elif problem == "url":
        observations[0]["source_url"] = "https://example.invalid/data"
    elif problem == "notation":
        observations[0]["value_text"] = "43,1"
    fixture = tmp_path / "refused.json"
    fixture.write_text(json.dumps(payload) if problem != "oversize" else " " * 32769)
    result = replay(fixture)
    assert result.returncode == 2 and result.stdout == ""
    assert "Pilot evidence refused" in result.stderr
