"""U3 — Bias correction (fix Ghats/coastal under-forecast).

Research (skill-r1.md): global models under-predict extreme/orographic rain, esp.
Western Ghats & coastal Maharashtra (Basha 2017, Tiwari 2016). LOCI (Local
Intensity Scaling) is a simple, evidence-backed method that corrects the frequency
of rain days and is effective for time-series indices (Fang 2015).

LOCI per district, derived from archived (forecast, observed) pairs:
  - fit a linear scaling: observed = slope * forecast + intercept  (OLS on pairs)
    (this is "linear scaling" / LS; we call it "LOCI-lite" since we lack daily
    0/1 rain-day flags here, but the multiplicative slope is the dominant term).
  - apply to a new raw forecast: corrected = slope * raw + intercept.
If < MIN_PAIRS history, return raw unchanged and flag "uncorrected".

Honesty: correction quality scales with calibration history; we always report
n_pairs and whether correction was applied.
"""
from __future__ import annotations
import statistics
try:
    from engine.verify import _conn
except ImportError:  # allow running as a standalone script
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from verify import _conn

MIN_PAIRS = 4


def _pairs(district: str):
    conn = _conn()
    rows = conn.execute(
        "SELECT forecast_mm, observed_mm FROM archive "
        "WHERE district=? AND observed_mm IS NOT NULL", (district.upper(),)).fetchall()
    conn.close()
    return [(f, o) for f, o in rows if f is not None and o is not None]


def fit(district: str) -> dict:
    """Return {slope, intercept, n_pairs, method} from OLS over paired history."""
    pts = _pairs(district)
    n = len(pts)
    if n < MIN_PAIRS:
        return {"slope": 1.0, "intercept": 0.0, "n_pairs": n, "method": "uncorrected"}
    fx = [p[0] for p in pts]; oy = [p[1] for p in pts]
    mx = statistics.mean(fx); my = statistics.mean(oy)
    sxx = sum((x - mx) ** 2 for x in fx)
    sxy = sum((x - mx) * (y - my) for x, y in zip(fx, oy))
    slope = sxy / sxx if sxx else 1.0
    intercept = my - slope * mx
    return {"slope": round(slope, 3), "intercept": round(intercept, 2),
            "n_pairs": n, "method": "LOCI-linear"}


def correct(district: str, raw_mm: float) -> dict:
    """Return {corrected_mm, raw_mm, applied, slope, intercept, n_pairs, method}."""
    f = fit(district)
    if f["method"] == "uncorrected":
        return {"corrected_mm": raw_mm, "raw_mm": raw_mm, "applied": False,
                **f}
    corr = max(0.0, f["slope"] * raw_mm + f["intercept"])
    return {"corrected_mm": round(corr, 2), "raw_mm": round(raw_mm, 2),
            "applied": True, **f}


if __name__ == "__main__":
    for d in ["MUMBAI SUBURBAN", "PUNE", "NAGPUR", "RATNAGIRI", "RAIGAD", "KOLHAPUR"]:
        c = correct(d, 10.0)  # correct a hypothetical 10mm raw forecast
        print(f"  {d:16s} raw=10.0 -> corrected={c['corrected_mm']} applied={c['applied']} "
              f"n={c['n_pairs']} method={c['method']}")
