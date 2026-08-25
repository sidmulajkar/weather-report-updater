"""Daily forecast cache (no API key required).

WHY: Open-Meteo's anonymous free tier has a *daily* request ceiling. From a
single shared IP (local dev, or GitHub Actions' Azure egress pool) repeated
runs blow past it -> every call returns 429 "Daily API request limit exceeded"
and the whole pipeline stalls for 15+ minutes retrying.

FIX: fetch the grid + per-city forecasts ONCE per day, persist them to a local
JSON file with a TTL, and reuse for every subsequent run inside that window.
On CI we pair this with `actions/cache` so the Azure shared IP only makes the
fetch ONCE per day (the cache file is restored between workflow runs). This
cuts daily call volume from ~42/run to ~42/day and survives IP bans.

Honesty: every cached payload records its `fetched_at_ist` so the report can
label data age ("Grid: fetched 08:14 IST, age 3.2h"). We never present stale
data as live.

TTL default = 6h (per ops decision).
"""
from __future__ import annotations
import os
import json
import time as _time
from typing import Optional

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "forecast_cache.json")
TTL_SECONDS = 6 * 3600  # 6h, per ops decision

# IST offset (UTC+5:30) without external tz dependency.
_IST_OFFSET_S = 5 * 3600 + 30 * 60


def _now_ist_epoch() -> float:
    return _time.time() + _IST_OFFSET_S


def _iso_ist(epoch: float) -> str:
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(epoch - _IST_OFFSET_S).strftime(
        "%Y-%m-%d %H:%M IST")


def load() -> Optional[dict]:
    """Return the cache dict if present AND fresh, else None."""
    try:
        if not os.path.exists(CACHE_FILE):
            return None
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return None
    fetched = data.get("fetched_at_epoch")
    if fetched is None or (_now_ist_epoch() - fetched) > TTL_SECONDS:
        return None
    return data


def save(grid: dict, cities: dict, model: str, conv_model: str,
         days: int) -> dict:
    """Persist grid + cities payloads. Returns the cache dict."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    epoch = _now_ist_epoch()
    data = {
        "fetched_at_epoch": epoch,
        "fetched_at_ist": _iso_ist(epoch),
        "ttl_seconds": TTL_SECONDS,
        "model": model,
        "conv_model": conv_model,
        "forecast_days": days,
        "grid": grid,
        "cities": cities,
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)
    return data


def update_cities(cities: dict) -> dict:
    """Merge per-city model payloads into the existing cache (preserving grid).
    Called after a successful live city fetch so subsequent runs reuse them."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    data = load() or {}
    epoch = data.get("fetched_at_epoch", _now_ist_epoch())
    data["fetched_at_epoch"] = epoch
    data["fetched_at_ist"] = data.get("fetched_at_ist", _iso_ist(epoch))
    data["ttl_seconds"] = TTL_SECONDS
    data["cities"] = cities
    # keep any existing grid payload
    if "grid" not in data:
        data["grid"] = None
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)
    return data


def age_hours(fetched_at_epoch: float) -> float:
    return (_now_ist_epoch() - fetched_at_epoch) / 3600.0


def is_daily_ban(body_text: str) -> bool:
    """True if the API returned Open-Meteo's hard daily-limit block."""
    return "Daily API request limit exceeded" in (body_text or "")
