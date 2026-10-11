"""scripts/mock_vitals.py produces the behavior data.md specifies."""
import importlib.util
import json
import random
import statistics
from datetime import datetime, timezone

import pytest

from conftest import ROOT

_spec = importlib.util.spec_from_file_location("mock_vitals", ROOT / "scripts" / "mock_vitals.py")
mv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mv)

NOW = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)


def run(events: dict[int, str], n: int, seed: int = 0) -> list[dict]:
    sim = mv.VitalsSimulator(random.Random(seed))
    out = []
    for t in range(n):
        if t in events:
            sim.trigger(events[t], t)
        out.append(sim.reading(t, NOW))
    return out


def test_baseline_ranges():
    rs = run({}, 600)
    hr = [r["heart_rate"] for r in rs]
    br = [r["breathing_rate"] for r in rs]
    assert all(70 <= v <= 74 for v in hr) and abs(statistics.mean(hr) - 72) < 0.3
    assert all(13 <= v <= 15 for v in br) and abs(statistics.mean(br) - 14) < 0.15
    assert all("focus" not in r for r in rs)            # there is no focus vital
    assert {r["expression"] for r in rs} == {"neutral"}
    assert {r["confidence"] for r in rs} == {0.95}
    assert all("session_id" not in r for r in rs)       # backend uses the active session


def test_spike_ramps_6s_then_decays_20s():
    sim = mv.VitalsSimulator()
    sim.trigger("s", 10)
    assert sim.spike_level(10) == 0
    assert sim.spike_level(13) == pytest.approx(0.5)
    assert sim.spike_level(16) == 1.0                  # peak 6 s after the key press
    assert sim.spike_level(26) == pytest.approx(0.5)
    assert sim.spike_level(36) == 0                    # back to baseline 20 s after the peak

    peak = run({10: "s"}, 17)[16]
    assert peak["heart_rate"] == pytest.approx(72 * 1.25, abs=2)
    assert peak["breathing_rate"] == pytest.approx(14 * 1.30, abs=1)
    assert peak["expression"] == "frown"


def test_face_lost_is_5s_of_nulls_with_confidence_0():
    rs = run({10: "f"}, 30)
    lost = [i for i, r in enumerate(rs) if r["confidence"] == 0]
    assert lost == [10, 11, 12, 13, 14]
    for i in lost:
        assert rs[i]["heart_rate"] is rs[i]["breathing_rate"] is rs[i]["expression"] is None


def test_parse_events():
    assert mv.parse_events("20:s,100:f") == {20: ["s"], 100: ["f"]}
    assert mv.parse_events("5:s, 5:f") == {5: ["s", "f"]}
    with pytest.raises(Exception):
        mv.parse_events("20:x")


def test_realtime_stall_skips_instead_of_bursting(capsys, monkeypatch):
    """A 10 s machine stall must drop readings, never emit several in one second."""
    clock = {"now": 0.0}

    def monotonic():
        return clock["now"]

    def sleep(s):
        clock["now"] += s
        if 7.0 <= clock["now"] < 7.06:        # the machine freezes for 10 s around t=7
            clock["now"] += 10.0

    monkeypatch.setattr(mv.time, "monotonic", monotonic)
    monkeypatch.setattr(mv.time, "sleep", sleep)
    assert mv.main(["--stdout", "--duration", "30", "--seed", "1", "--events", "10:f"]) == 0
    captured = capsys.readouterr()
    lines = [json.loads(l) for l in captured.out.splitlines()]
    assert 19 <= len(lines) <= 21                       # ~10 seconds skipped, nothing bursted
    assert "skipping to" in captured.err
    assert sum(r["confidence"] == 0 for r in lines) >= 1   # the 10:f event inside the stall still fired


def test_stdout_fast_mode(capsys):
    assert mv.main(["--stdout", "--fast", "--duration", "40", "--seed", "1", "--events", "5:s,30:f"]) == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    assert len(lines) == 40
    ts = [datetime.fromisoformat(r["ts"]) for r in lines]
    assert all((b - a).total_seconds() == 1 for a, b in zip(ts, ts[1:]))
    assert lines[11]["expression"] == "frown" and lines[30]["confidence"] == 0
    # every line validates against the PLAN.md contract
    from backend.contracts import VitalsBatch
    VitalsBatch(readings=lines)
