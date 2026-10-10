"""Stress score (PLAN.md section 3). Owned by Data, used by everyone.

    hr_part    = clamp(hr_delta_pct / 25, 0, 1)            # +25% HR = max
    br_part    = clamp(br_delta_pct / 40, 0, 1)            # +40% breathing = max
    focus_part = clamp((baseline_focus - focus) / 0.5, 0, 1)
    stress     = 100 * (0.5*hr_part + 0.3*br_part + 0.2*focus_part)

Missing inputs drop out and the remaining weights are renormalized.
The constants may be tuned with real Presage data; the 0-100 scale and the
thresholds (< 40 calm, 40-70 rising, > 70 high) stay fixed.
"""
from backend.contracts import Baseline, VitalsReading

HR_MAX_PCT = 25.0        # HR this % above baseline = full HR contribution
BR_MAX_PCT = 40.0        # breathing this % above baseline = full BR contribution
FOCUS_MAX_DROP = 0.5     # focus this far below baseline = full focus contribution
W_HR, W_BR, W_FOCUS = 0.5, 0.3, 0.2
MIN_CONFIDENCE = 0.7     # readings below this are never stored and never scored

CALM_BELOW, HIGH_ABOVE = 40.0, 70.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def delta_pct(value: float | None, base: float | None) -> float | None:
    """% above (+) or below (-) baseline, or None if either side is missing."""
    if value is None or not base:
        return None
    return (value / base - 1.0) * 100.0


def stress_score(r: VitalsReading, b: Baseline | None) -> float | None:
    """0-100, or None if there's no baseline yet, confidence < MIN_CONFIDENCE (0.7), or no usable inputs."""
    if b is None or r.confidence is None or r.confidence < MIN_CONFIDENCE:
        return None
    parts: list[tuple[float, float]] = []
    hr_d = delta_pct(r.heart_rate, b.heart_rate)
    if hr_d is not None:
        parts.append((W_HR, _clamp01(hr_d / HR_MAX_PCT)))
    br_d = delta_pct(r.breathing_rate, b.breathing_rate)
    if br_d is not None:
        parts.append((W_BR, _clamp01(br_d / BR_MAX_PCT)))
    if r.focus is not None and b.focus is not None:
        parts.append((W_FOCUS, _clamp01((b.focus - r.focus) / FOCUS_MAX_DROP)))
    if not parts:
        return None
    weight = sum(w for w, _ in parts)
    return 100.0 * sum(w * p for w, p in parts) / weight


def stress_level(stress: float | None) -> str | None:
    """'calm' (< 40), 'rising' (40 to 70), 'high' (> 70): the thresholds everyone uses."""
    if stress is None:
        return None
    return "high" if stress > HIGH_ABOVE else "rising" if stress >= CALM_BELOW else "calm"
