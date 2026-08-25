"""Data sanitation & metadata validation for IMD + radar/AWS cross-checks.

Phase 1 additions:
- IMD WFS TTL gate: flag nowcast payloads older than 180 min as STALE.
- Valid-from/valid-to parser: extract operational windows from IMD text.
- Zero-rain anomaly gate: cross-check AWS tipping-bucket vs radar reflectivity.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------
_TS_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%Y-%m-%d",
]


def _parse_ist_ts(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    # Normalize common IMD variants
    s = s.replace("IST", "+05:30")
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.astimezone(IST)
        except ValueError:
            continue
    return None


def ist_now() -> datetime:
    return datetime.now(IST)


# ---------------------------------------------------------------------------
# IMD WFS TTL gate
# ---------------------------------------------------------------------------
def nowcast_ttl_status(rec: dict, ttl_minutes: int = 180) -> dict:
    """Return {fresh, status, age_min, reason} for a single nowcast record.

    Fields checked in order: update_time -> Date -> toi.
    If none parseable, record is STALE.
    """
    ts_raw = rec.get("update_time") or rec.get("Date") or rec.get("toi")
    parsed = _parse_ist_ts(ts_raw)
    if parsed is None:
        return {
            "fresh": False,
            "status": "stale",
            "age_min": None,
            "reason": f"unparseable/missing timestamp: {ts_raw!r}",
        }
    age_min = (ist_now() - parsed).total_seconds() / 60.0
    if age_min > ttl_minutes:
        return {
            "fresh": False,
            "status": "stale",
            "age_min": round(age_min, 1),
            "reason": f"age {age_min:.0f}min exceeds {ttl_minutes}min TTL",
        }
    return {
        "fresh": True,
        "status": "ok",
        "age_min": round(age_min, 1),
        "reason": "",
    }


def filter_stale_nowcast(nc_payload: dict, ttl_minutes: int = 180) -> dict:
    """Walk a nowcast payload and mark stale records. Returns augmented payload."""
    out = dict(nc_payload)
    out.setdefault("stale_flags", {})
    out["stale_summary"] = {"ok": 0, "stale": 0, "reasons": []}
    for district, rec in out.get("by_district", {}).items():
        ttl = nowcast_ttl_status(rec, ttl_minutes=ttl_minutes)
        rec["ttl"] = ttl
        out["stale_flags"][district] = ttl
        if ttl["fresh"]:
            out["stale_summary"]["ok"] += 1
        else:
            out["stale_summary"]["stale"] += 1
            out["stale_summary"]["reasons"].append(
                f"{district}: {ttl['reason']}"
            )
    return out


# ---------------------------------------------------------------------------
# Valid-from / valid-to extraction
# ---------------------------------------------------------------------------
_VALID_WINDOW_RE = re.compile(
    r"valid\s+from\s+(\d{4})\s+ist\s+to\s+(\d{4})\s+ist",
    re.IGNORECASE,
)


def extract_valid_window(text: str) -> dict:
    """Extract HHMM validity window from IMD text payloads.

    Returns {valid_from, valid_to, raw_match} or all-None if absent.
    """
    if not text:
        return {"valid_from": None, "valid_to": None, "raw_match": None}
    m = _VALID_WINDOW_RE.search(text)
    if not m:
        return {"valid_from": None, "valid_to": None, "raw_match": None}
    return {
        "valid_from": m.group(1),
        "valid_to": m.group(2),
        "raw_match": m.group(0),
    }


# ---------------------------------------------------------------------------
# Zero-rain anomaly cross-check (AWS tipping-bucket vs radar reflectivity)
# ---------------------------------------------------------------------------
# Thresholds per operational spec
_AWS_HIGH_RATE_MMH = 15.0        # >15 mm/hr flagged
_RADAR_ECHO_DBZ = 10.0           # <10 dBZ treated as zero-ish
_RADIUS_KM = 25.0                 # neighbour search radius


def _haversine_km(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def zero_rain_anomaly_check(
    aws_points: list[dict],
    radar_cells: list[dict] | None = None,
    aws_threshold_mmh: float = _AWS_HIGH_RATE_MMH,
    radar_dbz_floor: float = _RADAR_ECHO_DBZ,
    radius_km: float = _RADIUS_KM,
) -> dict:
    """Cross-check AWS rainfall rates against nearby radar reflectivity.

    aws_points: [{name, lat, lon, rain_mmh, ...}]
    radar_cells: [{lat, lon, dbz, ...}] or None when dBZ ingestion is unavailable.

    Returns {status, anomalies:[], note}.
    When radar_cells is empty/None, returns DATA_UNAVAILABLE with no fabricated flags.
    """
    if not aws_points:
        return {"status": "ok", "anomalies": [], "note": "no AWS points supplied"}
    if not radar_cells:
        return {
            "status": "DATA_UNAVAILABLE",
            "anomalies": [],
            "note": "radar dBZ cells not yet ingested; zero-rain check inactive",
        }

    anomalies = []
    for pt in aws_points:
        rain_mmh = pt.get("rain_mmh")
        if rain_mmh is None or rain_mmh <= aws_threshold_mmh:
            continue
        neighbours = [
            c for c in radar_cells
            if _haversine_km(pt["lat"], pt["lon"], c["lat"], c["lon"]) <= radius_km
        ]
        max_dbz = max((c.get("dbz", 0) or 0) for c in neighbours) if neighbours else None
        if max_dbz is None or max_dbz < radar_dbz_floor:
            anomalies.append({
                "name": pt.get("name"),
                "lat": pt["lat"],
                "lon": pt["lon"],
                "rain_mmh": rain_mmh,
                "max_dbz": max_dbz,
                "reason": (
                    "AWS high rate but no radar echo nearby"
                    if max_dbz is None
                    else f"max_dbz={max_dbz:.1f} < {radar_dbz_floor} dBZ"
                ),
            })

    return {
        "status": "ok" if not anomalies else "ANOMALIES_DETECTED",
        "anomalies": anomalies,
        "note": f"checked {len(aws_points)} AWS points against {len(radar_cells)} radar cells",
    }


# ---------------------------------------------------------------------------
# Helpers for text payloads / narrative
# ---------------------------------------------------------------------------
def nowcast_validity_text(rec: dict) -> str:
    """Build a compact validity-string for narrative/map rendering."""
    vw = extract_valid_window(rec.get("message") or "")
    if vw["valid_from"] and vw["valid_to"]:
        return f"Valid {vw['valid_from']}–{vw['valid_to']} IST"
    # fallback to raw toi/vupto fields if present
    toi = rec.get("toi")
    vupto = rec.get("vupto")
    if toi and vupto:
        return f"Valid {toi}–{vupto} IST"
    return ""
