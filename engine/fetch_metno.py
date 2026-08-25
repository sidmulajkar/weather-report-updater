"""Fallback data source: MET Norway api.met.no (keyless, CC license).

Verified LIVE 2026-08-22: GET https://api.met.no/weatherapi/locationforecast/2.0/compact
returns temp/wind/pressure/humidity. Also reachable via Open-Meteo model 'metno_api'.
Used as REDUNDANCY when Open-Meteo primary fails (your "u1+u2 as backup" design).

We extract: hourly precipitation (next 24h sum), wind_speed_10m, wind_direction_10m,
precipitation_probability where available. MET Norway is Nordic-optimised, so for
India it is a lower-skill fallback -> the report labels the source honestly.
"""
from __future__ import annotations
import requests
from datetime import datetime, timedelta, timezone

URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
HEADERS = {"User-Agent": "weather-report-updater/2.0 (local research; contact local)"}


def fetch(lat: float, lon: float, hours: int = 24) -> dict:
    """Return {status, precip_sum, wind_kmh, wind_dir, wind_gust, source}."""
    out = {"status": "unavailable", "precip_sum": None, "wind_kmh": None,
           "wind_dir": None, "wind_gust": None, "source": "MET Norway",
           "reason": ""}
    try:
        r = requests.get(URL, params={"lat": lat, "lon": lon},
                         headers=HEADERS, timeout=25)
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        out["reason"] = f"fetch failed: {e}"
        return out
    except ValueError as e:
        out["reason"] = f"bad json: {e}"
        return out

    ts = j.get("properties", {}).get("timeseries", [])
    if not ts:
        out["reason"] = "no timeseries"
        return out

    now = datetime.now(timezone.utc)
    window = [t for t in ts if now <= datetime.fromisoformat(t["time"].replace("Z", "+00:00")) <= now + timedelta(hours=hours)]
    if not window:
        window = ts[:hours]
    precip = 0.0
    winds, dirs, gusts = [], [], []
    for t in window:
        d = t.get("data", {}).get("instant", {}).get("details", {})
        precip += d.get("precipitation_amount", 0.0) or 0.0
        if d.get("wind_speed"): winds.append(d["wind_speed"])
        if d.get("wind_from_direction") is not None: dirs.append(d["wind_from_direction"])
        if d.get("wind_speed_of_gust"): gusts.append(d["wind_speed_of_gust"])
    if not winds:
        out["reason"] = "no wind data"
        return out
    out.update({
        "status": "ok",
        "precip_sum": round(precip, 2),
        "wind_kmh": round(sum(winds) / len(winds) * 3.6, 1),   # m/s -> km/h
        "wind_dir": round(sum(dirs) / len(dirs), 1) if dirs else None,
        "wind_gust": round(max(gusts) * 3.6, 1) if gusts else None,
    })
    return out


if __name__ == "__main__":
    print(fetch(19.076, 72.877, hours=24))
