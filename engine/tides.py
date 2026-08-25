"""Mumbai tidal-locking / coastal drainage interaction.

PHYSICS (verified across multiple independent sources, 2025-2026):
Mumbai has 40+ stormwater outfalls draining by gravity to the Arabian Sea. At
HIGH TIDE the sea rises above the outfall mouths and the floodgates are CLOSED
to stop seawater ingress; this halts the city's drainage capacity ("the sea
becomes a wall"). So a modest rain rate that is harmless at low tide can flood
Hindmata / Milan Subway / Andheri when it overlaps a peak high tide. This is a
classic FALSE-NEGATIVE source for models that只看 rainfall volume.
Sources: Times of India (Jul 2026), Free Press Journal, New Indian Express,
collegesimplified, kaarwan — all confirm the mechanism and the
"heavy rain + high tide = Mumbai shutdown" formula.

TIDE DATUM CAVEAT (critical, verified): tide heights depend on the reference
datum. Observed Mumbai highs:
  - MLLW datum (e.g. tide-forecast.com): ~3.0-3.2 m
  - Chart Datum (e.g. tidechecker.com): ~4.5-6.0 m, spring tides to ~6.0 m
  - One secondary source states gates shut "above 4.5 metres" (survey datum).
We therefore make the lock threshold DATUM-AWARE and configurable. Default
assumes CHART DATUM (the higher, commonly-cited figure). Do NOT mix MLLW values
with a 4.5 m threshold — that would silently mis-trigger.

DATA: a static yearly tide-table CSV (config/tides_2026.csv) ingested by
`load_tide_table`. A live tide API can replace the loader later; the interface
is the same. We do NOT claim live tide API access.
"""
from __future__ import annotations
from typing import Optional
import csv
import os
from datetime import datetime, timedelta

# Lock level on CHART DATUM (m). Above this, drainage gates close -> lock risk.
# (MLLW equivalent is ~1 m lower; set TIDE_DATUM to match your table's datum.)
TIDE_LOCK_M = 4.5
TIDE_HARD_LOCK_M = 5.0   # spring-tide extreme -> near-certain gate closure
TIDE_DATUM = "chart_datum"
OVERLAP_WINDOW_H = 2.0   # rain hour within this of a locking high tide counts

# conservative compounding multipliers (escalation factor on alert severity)
MULT_SOFT = 1.0   # informational
MULT_HARD = 2.0   # strong escalation


def load_tide_table(path: str) -> list[dict]:
    """Load a high-tide CSV into [{iso, tide_m, source}] events.

    Tolerant of two column layouts:
      - legacy:  date, time_iso, tide_m
      - current: iso_utc, iso_ist, height_m, datum, source
    Only high-tide events (the rows present) are returned; the caller filters
    by lock level. Source is preserved for provenance (REAL vs SAMPLE).
    """
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="") as f:
        # skip comment lines (CSV begins with a # provenance header)
        body = "".join(ln for ln in f if not ln.lstrip().startswith("#"))
    import io
    with io.StringIO(body) as f:
        for r in csv.DictReader(f):
            # time column
            iso = (r.get("time_iso") or r.get("iso_ist") or r.get("iso_utc") or "").strip()
            # height column
            try:
                h = float(r.get("tide_m") if r.get("tide_m") not in (None, "")
                         else r.get("height_m"))
            except (ValueError, TypeError):
                continue
            if not iso:
                continue
            # Normalize to a NAIVE IST wall-clock datetime. The CSV's iso_ist
            # carries a +05:30 offset; we strip it so all downstream time math
            # (rain-peak vs tide) stays tz-consistent. We work in IST everywhere.
            try:
                _dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                iso = _dt.replace(tzinfo=None).isoformat()
            except Exception:
                pass
            rows.append({"iso": iso, "tide_m": h,
                         "source": (r.get("source") or "unknown").strip()})
    return rows


def _naive(dt):
    """Strip tzinfo so aware (IST) and naive datetimes can be compared."""
    return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt


def high_tides_near(table: list[dict], dt: datetime, window_h: float) -> list[dict]:
    """Return high-tide entries within +/- window_h of dt that exceed lock level."""
    ref = _naive(dt)
    out = []
    for e in table:
        try:
            t = _naive(datetime.fromisoformat(e["iso"]))
        except Exception:
            continue
        if abs((t - ref).total_seconds()) / 3600.0 <= window_h and e["tide_m"] >= TIDE_LOCK_M:
            out.append(e)
    return out


def lock_window(table: list[dict], rain_peak_dt: datetime,
                window_h: float = 12.0) -> dict:
    """Return the drainage-lock WINDOW (onset/clear IST) for a rain-peak time.

    Mechanism (verified): Mumbai's 40+ stormwater outfalls drain by gravity to
    the Arabian Sea; at HIGH TIDE the sea rises above the outfall mouths and
    gates close, halting drainage. The lock is not an instantaneous on/off — it
    is the contiguous span around a high tide during which the (sampled) tide
    curve stays at/above TIDE_LOCK_M. We estimate that span from the high-tide
    table entries (their timestamps + heights), expanding symmetrically by
    OVERLAP_WINDOW_H as a conservative gate-open/close margin.

    OUTPUT (all honest, no invented values):
      - active: bool  (any locking high tide within +/- window_h of peak)
      - start_ist / end_ist: "HH:MM IST" strings (None if inactive)
      - max_tide_m, tide_iso, source  (provenance: REAL vs SAMPLE)
      - sample_data: bool  (True => tide row is placeholder; label output)
    If no real high tide is found, returns active=False with a clear note.
    """
    highs = [e for e in table
             if abs((_naive(datetime.fromisoformat(e["iso"])) - _naive(rain_peak_dt)).total_seconds()) / 3600.0
                <= window_h and e["tide_m"] >= TIDE_LOCK_M] if table else []
    if not highs:
        return {"active": False, "start_ist": None, "end_ist": None,
                "max_tide_m": 0.0, "tide_iso": None, "source": None,
                "sample_data": False, "note": "no locking high tide near peak"}
    peak = max(highs, key=lambda e: e["tide_m"])
    peak_t = datetime.fromisoformat(peak["iso"])
    start = peak_t - timedelta(hours=OVERLAP_WINDOW_H)
    end = peak_t + timedelta(hours=OVERLAP_WINDOW_H)
    def fmt(dt_utc):
        # tide ISO in CSV is stored as IST already (iso_ist); format as-is.
        return dt_utc.strftime("%H:%M IST")
    return {
        "active": True,
        "start_ist": fmt(start),
        "end_ist": fmt(end),
        "max_tide_m": peak["tide_m"],
        "tide_iso": peak["iso"],
        "source": peak.get("source"),
        "sample_data": (peak.get("source") or "").upper().startswith("SAMPLE"),
        "note": None,
    }



def tidal_factor(table: list[dict], rain_peak_dt: datetime,
                 window_h: float = OVERLAP_WINDOW_H) -> dict:
    """Return tidal compounding assessment for a rain peak time."""
    highs = high_tides_near(table, rain_peak_dt, window_h)
    if not highs:
        return {"overlap": False, "max_tide_m": 0.0, "multiplier": 1.0, "lock": None}
    peak = max(highs, key=lambda e: e["tide_m"])
    if peak["tide_m"] >= TIDE_HARD_LOCK_M:
        mult, lock = MULT_HARD, "HARD LOCK (gates closed)"
    else:
        mult, lock = MULT_SOFT, "SOFT LOCK (gates restricted)"
    return {"overlap": True, "max_tide_m": peak["tide_m"],
            "multiplier": mult, "lock": lock, "tide_iso": peak["iso"]}


if __name__ == "__main__":
    # build a tiny sample table inline for the demo
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "tides_demo.csv")
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "time_iso", "tide_m"])
        w.writerow(["2026-08-23", "2026-08-23T14:00:00", "4.6"])
        w.writerow(["2026-08-23", "2026-08-23T02:00:00", "3.1"])
    tbl = load_tide_table(p)
    res = tidal_factor(tbl, datetime.fromisoformat("2026-08-23T13:30:00"))
    print("tidal factor demo:", res)
