"""The 5 stress-score asserts from data.md, plus edge cases."""
from datetime import datetime, timezone

import pytest

from backend.contracts import Baseline, VitalsReading
from backend.data.metrics import MIN_CONFIDENCE, stress_level, stress_score

B = Baseline(heart_rate=72.0, breathing_rate=14.0, n_samples=30)
TS = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)


def reading(hr=72.0, br=14.0, confidence=0.95):
    return VitalsReading(ts=TS, heart_rate=hr, breathing_rate=br, expression="neutral", confidence=confidence)


def test_calm_reading_is_about_zero():
    assert stress_score(reading(), B) == pytest.approx(0, abs=1e-9)
    assert stress_score(reading(hr=73.0, br=14.3), B) < 10      # normal noise stays calm


def test_hr_plus_25pct_is_62_5():
    # Heart rate is 0.5 of the 0.8 total weight (breathing is the other 0.3): 0.5 / 0.8 = 62.5. This is what
    # real Presage data always scored, since it never had a focus input.
    assert stress_score(reading(hr=72.0 * 1.25), B) == pytest.approx(62.5)


def test_all_maxed_is_100():
    assert stress_score(reading(hr=72.0 * 1.25, br=14.0 * 1.4), B) == pytest.approx(100)
    assert stress_score(reading(hr=200, br=60), B) == pytest.approx(100)     # clamped


def test_missing_input_renormalizes():
    # HR only (no BR): HR is the whole score
    assert stress_score(reading(hr=90.0, br=None), B) == pytest.approx(100)
    # BR only (no HR): BR is the whole score
    assert stress_score(reading(hr=None, br=14.0 * 1.2), B) == pytest.approx(50)


def test_low_confidence_is_none():
    assert stress_score(reading(confidence=MIN_CONFIDENCE - 0.01), B) is None      # below the cutoff
    assert stress_score(reading(confidence=MIN_CONFIDENCE), B) is not None


def test_no_baseline_or_no_inputs_is_none():
    assert stress_score(reading(), None) is None
    assert stress_score(reading(hr=None, br=None), B) is None


def test_below_baseline_never_negative():
    assert stress_score(reading(hr=60, br=10), B) == 0


def test_mock_spike_peak():
    # mock_vitals.py 's' at its peak: HR +25% (part 1.0), BR +30% (part 0.75): 100 * (0.5 + 0.225) / 0.8
    assert stress_score(reading(hr=90.0, br=18.2), B) == pytest.approx(90.625)


def test_no_focus_vital():
    assert "focus" not in VitalsReading.model_fields and "focus" not in Baseline.model_fields


def test_stress_level_thresholds():
    assert [stress_level(x) for x in (None, 0, 39.9, 40, 70, 70.1)] == \
        [None, "calm", "calm", "rising", "rising", "high"]
