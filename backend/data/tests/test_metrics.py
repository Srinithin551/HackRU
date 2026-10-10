"""The 5 stress-score asserts from data.md, plus edge cases."""
from datetime import datetime, timezone

import pytest

from backend.contracts import Baseline, VitalsReading
from backend.data.metrics import stress_level, stress_score

B = Baseline(heart_rate=72.0, breathing_rate=14.0, focus=0.8, n_samples=30)
TS = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)


def reading(hr=72.0, br=14.0, focus=0.8, confidence=0.95):
    return VitalsReading(ts=TS, heart_rate=hr, breathing_rate=br, focus=focus,
                         expression="neutral", confidence=confidence)


def test_calm_reading_is_about_zero():
    assert stress_score(reading(), B) == pytest.approx(0, abs=1e-9)
    assert stress_score(reading(hr=73.0, br=14.3, focus=0.78), B) < 10      # normal noise stays calm


def test_hr_plus_25pct_is_about_50():
    assert stress_score(reading(hr=72.0 * 1.25), B) == pytest.approx(50)


def test_all_maxed_is_100():
    assert stress_score(reading(hr=72.0 * 1.25, br=14.0 * 1.4, focus=0.3), B) == pytest.approx(100)
    assert stress_score(reading(hr=200, br=60, focus=0.0), B) == pytest.approx(100)     # clamped


def test_missing_focus_renormalizes():
    # HR and BR maxed, focus missing: weights 0.5 + 0.3 renormalize to 1, so still 100
    assert stress_score(reading(hr=90.0, br=19.6, focus=None), B) == pytest.approx(100)
    # HR maxed only, focus missing: 0.5 / (0.5 + 0.3) = 62.5
    assert stress_score(reading(hr=90.0, focus=None), B) == pytest.approx(62.5)
    # HR only (no BR, no focus): HR is the whole score
    assert stress_score(reading(hr=90.0, br=None, focus=None), B) == pytest.approx(100)


def test_low_confidence_is_none():
    assert stress_score(reading(confidence=0.49), B) is None
    assert stress_score(reading(confidence=0.5), B) is not None


def test_no_baseline_or_no_inputs_is_none():
    assert stress_score(reading(), None) is None
    assert stress_score(reading(hr=None, br=None, focus=None), B) is None


def test_below_baseline_never_negative():
    assert stress_score(reading(hr=60, br=10, focus=1.0), B) == 0


def test_mock_spike_peak():
    # mock_vitals.py 's' at its peak: HR +25%, BR +30%, focus 0.5
    assert stress_score(reading(hr=90.0, br=18.2, focus=0.5), B) == pytest.approx(84.5)


def test_stress_level_thresholds():
    assert [stress_level(x) for x in (None, 0, 39.9, 40, 70, 70.1)] == \
        [None, "calm", "calm", "rising", "rising", "high"]
