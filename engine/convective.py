"""Convective proxy index for severe thunderstorm / cloudburst risk.

When model RH / TPW / convergence are unavailable, fall back to parsing
public radiosonde profiles. If radiosonde fetch also fails, the product
gracefully reports DATA_UNAVAILABLE and never invents values.
"""
from __future__ import annotations
import math
import re
import urllib.request
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
_SANTACRUZ_STATION = "43003"
_WYOMING_BASE = "https://weather.uwyo.edu/cgi-bin/sounding"

# Trigger thresholds
_RH_TRIGGER = 85.0           # 850-700 hPa RH must exceed this
_TPW_TRIGGER = 55.0          # TPW kg/m²
_CONVERGENCE_TRIGGER = 0.8   # normalized convergence score 0..1

# ---------------------------------------------------------------------------
# Hygrometric helpers
# ---------------------------------------------------------------------------
def _rh_from_td(temp_c: float, dewpoint_c: float) -> float:
    """Approximate relative humidity (%) from temperature and dewpoint."""
    if temp_c is None or dewpoint_c is None:
        return None
    e = 6.112 * math.exp((17.67 * dewpoint_c) / (dewpoint_c + 243.5))
    es = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
    if es == 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * e / es))


def _convergence_score(wind_speed_850: float | None, wind_dir_850: float | None,
                       wind_speed_700: float | None, wind_dir_700: float | None) -> float | None:
    """Normalized convergence proxy from 850→700 hPa wind profile.

    Positive score = stronger low-level flow + directional veering = convergence.
    Returns 0..1, or None when inputs are missing.
    """
    if None in (wind_speed_850, wind_dir_850, wind_speed_700, wind_dir_700):
        return None
    # Speed increase aloft implies divergence; decrease implies convergence.
    speed_delta = wind_speed_700 - wind_speed_850
    # Veering with height = backing in NH = convergence.
    dir_delta = (wind_dir_700 - wind_dir_850 + 180) % 360 - 180
    dir_score = max(0.0, 1.0 - abs(dir_delta) / 90.0)
    speed_score = max(0.0, 1.0 + speed_delta / 30.0)
    return max(0.0, min(1.0, 0.6 * dir_score + 0.4 * speed_score))


# ---------------------------------------------------------------------------
# Radiosonde parsing (Wyoming upper-air text format)
# ---------------------------------------------------------------------------
def fetch_sounding(dt_ist: datetime | None = None, station: str = _SANTACRUZ_STATION) -> dict:
    """Fetch the latest Wyoming upper-air sounding for the given station.

    Returns {status, url, raw_text, parsed_levels, rh850, rh700,
             tpw_kgm2, conv_score, note}.
    """
    if dt_ist is None:
        dt_ist = datetime.now(IST)
    params = (
        f"region=seasia&TYPE=TEXT%3ALIST"
        f"&YEAR={dt_ist.year}&MONTH={dt_ist.month:02d}&DAY={dt_ist.day:02d}"
        f"&FROM=00&TO=23&STNM={station}"
    )
    url = f"{_WYOMING_BASE}?{params}"
    raw = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"status": "unavailable", "reason": f"fetch failed: {e}", "url": url}

    if not raw.strip():
        return {"status": "unavailable", "reason": "empty response", "url": url}

    levels = _parse_wyoming_levels(raw)
    rh850 = rh700 = tpw_kgm2 = conv_score = None
    for lvl in levels:
        p = lvl.get("pressure_hpa")
        if p is None:
            continue
        if abs(p - 850.0) < 10:
            rh850 = _rh_from_td(lvl.get("temp_c"), lvl.get("dewpoint_c"))
        if abs(p - 700.0) < 10:
            rh700 = _rh_from_td(lvl.get("temp_c"), lvl.get("dewpoint_c"))
            conv_score = _convergence_score(
                levels[0].get("wind_speed_kt") if levels else None,
                levels[0].get("wind_dir_deg") if levels else None,
                lvl.get("wind_speed_kt"),
                lvl.get("wind_dir_deg"),
            )

    return {
        "status": "ok",
        "url": url,
        "raw_text": raw,
        "parsed_levels": levels,
        "rh850": rh850,
        "rh700": rh700,
        "tpw_kgm2": tpw_kgm2,
        "conv_score": conv_score,
        "note": "TPW from radiosonde requires full-column moisture integration; unavailable in this implementation.",
    }


def _parse_wyoming_levels(raw: str) -> list[dict]:
    """Parse standard-level block from Wyoming TEXT:LIST output."""
    levels = []
    lines = raw.splitlines()
    in_block = False
    for line in lines:
        if not in_block:
            if "-----------------------------------------------------------------" in line or line.strip().startswith("Prs"):
                in_block = True
            continue
        if not line.strip() or line.startswith("--"):
            break
        parts = line.split()
        if len(parts) < 8:
            continue
        try:
            pressure = float(parts[0])
            height = float(parts[1])
            temp = _wyom_val(parts[2])
            dew = _wyom_val(parts[3])
            wind_dir = _wyom_val(parts[4])
            wind_speed = _wyom_val(parts[5])
            if wind_speed is not None and wind_speed < 0:
                wind_speed = abs(wind_speed)
            levels.append({
                "pressure_hpa": pressure,
                "height_m": height,
                "temp_c": temp,
                "dewpoint_c": dew,
                "wind_dir_deg": wind_dir,
                "wind_speed_kt": wind_speed,
            })
        except (ValueError, IndexError):
            continue
    return levels


def _wyom_val(s: str) -> float | None:
    s = s.strip()
    if s in ("", "-", "///", "////"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Convective proxy gate
# ---------------------------------------------------------------------------
def convective_proxy_index(
    rh850: float | None = None,
    rh700: float | None = None,
    tpw_kgm2: float | None = None,
    conv_score: float | None = None,
) -> dict:
    """Evaluate convective potential from proxy inputs.

    Returns {flag, reason, metrics}.
    When critical inputs are None, returns DATA_UNAVAILABLE.
    """
    metrics = {
        "rh850_pct": rh850,
        "rh700_pct": rh700,
        "tpw_kgm2": tpw_kgm2,
        "conv_score": conv_score,
    }

    if rh850 is None or rh700 is None or conv_score is None:
        return {
            "flag": None,
            "status": "DATA_UNAVAILABLE",
            "reason": "radiosonde proxy incomplete; RH/convergence unavailable",
            "metrics": metrics,
        }

    rh_ok = (rh850 + rh700) / 2.0 >= _RH_TRIGGER
    tpw_ok = (tpw_kgm2 is not None and tpw_kgm2 >= _TPW_TRIGGER)
    conv_ok = conv_score >= _CONVERGENCE_TRIGGER

    if rh_ok and tpw_ok and conv_ok:
        return {
            "flag": "HIGH CONVECTIVE POTENTIAL",
            "status": "CRITICAL",
            "reason": (f"RH850/700 avg {((rh850+rh700)/2):.0f}% >= {_RH_TRIGGER:.0f}%, "
                       f"TPW {tpw_kgm2:.1f} >= {_TPW_TRIGGER:.0f} kg/m², "
                       f"convergence {conv_score:.2f} >= {_CONVERGENCE_TRIGGER:.2f}"),
            "metrics": metrics,
        }

    return {
        "flag": None,
        "status": "ok",
        "reason": (f"RH avg {((rh850+rh700)/2):.0f}%, "
                   f"TPW {tpw_kgm2 if tpw_kgm2 is not None else 'n/a'}, "
                   f"conv {conv_score:.2f} — below trigger thresholds"),
        "metrics": metrics,
    }
