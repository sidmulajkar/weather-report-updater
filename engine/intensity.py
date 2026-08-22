"""Intensity-Duration-Frequency (IDF) logic — shift from volume to impact.

Problem this solves: 50 mm over 24 h is a minor infiltration event; the same
50 mm in a 45-minute convective burst paralyzes drainage. A 24 h aggregate sum
hides the burst. We therefore compute the *maximum rolling short-duration
spikes* from the hourly precipitation series and escalate the risk level when
those bursts are extreme, independent of the 24 h total.

IMD-style short-duration intensity reference bands (mm/h, indicative; IMD issues
Nowcast warnings on sub-3h convective bursts — bands below are commonly used
operational cut points for the monsoon context, documented as approximate):
  burst < 15 mm/h      : light / no flash-flood concern
  15-30 mm/h           : moderate convective burst
  30-50 mm/h           : heavy burst (localized waterlogging likely)
  50-70 mm/h           : very heavy burst (drainage stress)
  > 70 mm/h            : extreme cloudburst (paralysis risk)

These are NOT the official IMD 24 h colour thresholds (those live in classify.py)
— they are the short-duration impact layer that modulates the final level.
"""
from __future__ import annotations
from typing import Optional, Sequence

# short-duration burst severity thresholds (mm per hour-equivalent)
BURST = [
    (70.0, "EXTREME"),   # cloudburst-class
    (50.0, "VERY_HEAVY"),
    (30.0, "HEAVY"),
    (15.0, "MODERATE"),
    (0.0,  "LIGHT"),
]


def max_rolling(precip: Sequence[Optional[float]], window: int) -> float:
    """Max sum of any consecutive `window` hourly values. None treated as 0."""
    if not precip:
        return 0.0
    vals = [float(v or 0.0) for v in precip]
    best = 0.0
    for i in range(0, len(vals) - window + 1):
        s = sum(vals[i:i + window])
        if s > best:
            best = s
    # if series shorter than window, just sum what we have
    if window > len(vals):
        best = max(best, sum(vals))
    return round(best, 2)


def burst_band(mm_per_hour: float) -> str:
    for thr, label in BURST:
        if mm_per_hour >= thr:
            return label
    return "LIGHT"


def analyze(precip_hourly: Sequence[Optional[float]], window_h: int = 24) -> dict:
    """Return IDF impact dict for a city's hourly precipitation series.

    Uses the first `window_h` hours (the forecast lead we care about).
    """
    series = list(precip_hourly[:window_h]) if precip_hourly else []
    max1h = max_rolling(series, 1)          # peak hourly intensity
    # index of the peak 1h slot (for tidal-overlap timing); None if no rain
    peak_idx = None
    if series:
        try:
            peak_idx = max(range(len(series)), key=lambda i: (series[i] or 0.0))
        except ValueError:
            peak_idx = None
    max3h = max_rolling(series, 3) / 3.0    # 3 h average peak (mm/h equiv)
    sum24 = round(sum(float(v or 0.0) for v in series), 2)
    band1 = burst_band(max1h)
    # impact flag: a heavy/extreme short burst raises concern even at low 24 h sum
    burst_concern = band1 in ("HEAVY", "VERY_HEAVY", "EXTREME")
    return {
        "max1h_mm": max1h,
        "max3h_mm_per_h": round(max3h, 2),
        "sum24h_mm": sum24,
        "burst_band": band1,
        "burst_concern": burst_concern,
        "escalate": band1 in ("VERY_HEAVY", "EXTREME"),
        "peak_hour_index": peak_idx,
    }


if __name__ == "__main__":
    # 50 mm spread evenly -> low burst; same 50 mm in 1 h -> extreme
    even = [2.08] * 24
    burst = [0] * 23 + [50.0]
    print("even 50mm/24h :", analyze(even))
    print("burst 50mm/1h :", analyze(burst))
