"""Fused Open-Meteo fetcher.

Open-Meteo returns ONE model per request (the `models=a,b` multi-value is NOT
supported and returns 'Data corrupted'). So fusion = query each model separately
and combine. This module:
  - fetches hourly precipitation / probability / wind for one point, per model
  - returns a dict of model -> hourly arrays
  - detects model disagreement (spread) so the classifier can improvise on confidence

Free, keyless. ~10k req/day. Non-commercial on free tier (see research notes).
"""
from __future__ import annotations
import requests
from dataclasses import dataclass, field
from typing import Optional

BASE = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = ("precipitation,precipitation_probability,wind_speed_10m,wind_direction_10m,"
               "weather_code,wind_speed_850hPa,wind_direction_850hPa,cape,wind_gusts_10m")

# Polite rate limiter: Open-Meteo is free/keyless and throttles burst traffic.
# A small floor between calls prevents 429s that otherwise cause long CI backoff.
import time as _time
_LAST_CALL = 0.0
_MIN_GAP_S = 0.25  # <=4 calls/sec; well within Open-Meteo's generous free limit


def _rate_limit():
    global _LAST_CALL
    wait = _MIN_GAP_S - (_time.monotonic() - _LAST_CALL)
    if wait > 0:
        _time.sleep(wait)
    _LAST_CALL = _time.monotonic()



@dataclass
class PointForecast:
    name: str
    lat: float
    lon: float
    state: str
    ftype: str
    # model -> {time:[], precipitation:[], precipitation_probability:[], wind_speed_10m:[],
    #           wind_direction_10m:[], weather_code:[]}
    models: dict = field(default_factory=dict)
    error: Optional[str] = None


def fetch_point(lat: float, lon: float, model: str, forecast_days: int = 2,
                timezone: str = "Asia/Kolkata", timeout: int = 30) -> Optional[dict]:
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": HOURLY_VARS,
        "models": model,
        "forecast_days": forecast_days,
        "timezone": timezone,
    }
    _rate_limit()  # polite floor to avoid 429 backoff under CI load
    try:
        r = requests.get(BASE, params=params, timeout=timeout)
    except requests.RequestException as e:
        return {"_error": str(e)}
    if r.status_code != 200:
        return {"_error": f"HTTP {r.status_code}: {r.text[:160]}"}
    j = r.json()
    if "hourly" not in j:
        return {"_error": str(j.get("reason", j))}
    return j["hourly"]


def fetch_location(loc: dict, models: list[str], forecast_days: int = 2) -> PointForecast:
    pf = PointForecast(name=loc["name"], lat=loc["lat"], lon=loc["lon"],
                       state=loc.get("state", ""), ftype=loc.get("type", ""))
    # CRITICAL PERF FIX: fetch the N models CONCURRENTLY (network I/O bound),
    # not in a serial for-loop. This preserves per-model spread (used for
    # confidence) while cutting wall-time ~Nx. On CI runners a serial 3-model
    # loop was the dominant cause of 9-minute runs.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    def _one(m):
        return m, fetch_point(loc["lat"], loc["lon"], m, forecast_days)
    with ThreadPoolExecutor(max_workers=min(len(models), 4)) as ex:
        futs = {ex.submit(_one, m): m for m in models}
        for fut in as_completed(futs):
            m = futs[fut]
            try:
                _m, h = fut.result()   # _one returns (model, hourly_dict)
            except Exception as e:
                _m, h = m, {"_error": str(e)}
            if h and "_error" not in h:
                pf.models[_m] = h
            else:
                pf.error = (pf.error or "") + f" [{_m}:{h.get('_error') if h else 'no-data'}]"
    if not pf.models:
        pf.error = pf.error or "all models failed"
    return pf


def accumulate_next_n_hours(hourly: dict, var: str, n: int, start_idx: int = 0) -> float:
    """Sum `var` over the next n hourly slots from start_idx (default now)."""
    arr = hourly.get(var, [])
    if not arr:
        return 0.0
    seg = arr[start_idx:start_idx + n]
    return round(sum(seg), 2)


def model_24h_precip(pf: PointForecast, n: int = 24) -> dict:
    """Return model -> 24h accumulated precipitation (mm)."""
    out = {}
    for m, h in pf.models.items():
        out[m] = accumulate_next_n_hours(h, "precipitation", n)
    return out


def fusion_stats(values: dict) -> dict:
    """Given model->value, compute mean, min, max, spread, agreement flag."""
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "spread": 0.0, "n": 0}
    vals = list(values.values())
    mean = sum(vals) / len(vals)
    return {
        "mean": round(mean, 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "spread": round(max(vals) - min(vals), 2),
        "n": len(vals),
    }


def mean_over_window(hourly: dict, var: str, n: int, start_idx: int = 0) -> float:
    """Mean of `var` over next n hourly slots (for CAPE/wind which are rates)."""
    arr = hourly.get(var, [])
    if not arr:
        return 0.0
    seg = [x for x in arr[start_idx:start_idx + n] if x is not None]
    return round(sum(seg) / len(seg), 1) if seg else 0.0


def model_850hpa_wind(pf: PointForecast, n: int = 24) -> dict:
    """model -> mean 850 hPa wind speed (km/h) over window."""
    out = {}
    for m, h in pf.models.items():
        out[m] = mean_over_window(h, "wind_speed_850hPa", n)
    return out


def model_cape(pf: PointForecast, n: int = 24) -> dict:
    """model -> mean CAPE (J/kg) over window."""
    out = {}
    for m, h in pf.models.items():
        out[m] = mean_over_window(h, "cape", n)
    return out


def model_wind_gust(pf: PointForecast, n: int = 24) -> dict:
    """model -> max 10m wind gust (km/h) over window."""
    out = {}
    for m, h in pf.models.items():
        arr = [x for x in h.get("wind_gusts_10m", [])[:n] if x is not None]
        out[m] = round(max(arr), 1) if arr else 0.0
    return out


if __name__ == "__main__":
    import json, sys
    loc = {"name": "Surat", "lat": 21.17, "lon": 72.83, "state": "Gujarat", "type": "coastal"}
    pf = fetch_location(loc, ["ecmwf_ifs", "icon_global", "gfs_seamless"], forecast_days=2)
    print("models returned:", list(pf.models.keys()))
    p24 = model_24h_precip(pf)
    print("24h precip by model:", p24)
    print("fusion:", fusion_stats(p24))
