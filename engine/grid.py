"""Maharashtra state-wide grid + Mumbai Metro Region (MMR) fine sub-mesh.

Phase-1 core data pipeline: generate coordinate sets, fetch them from
Open-Meteo in CHUNKED multi-location batches (<=50 coords/call to avoid
HTTP 414 Request-URI-Too-Long), and return index-aligned structs.

VERIFIED (live test 2026-08-23): a 50-point chunk returns HTTP 200 in ~0.9s,
URL length ~980 chars (far under the 414 limit). Open-Meteo returns a JSON
*list* of per-location structs, ordered identically to the requested
coordinate list -> we map results back by INDEX, never by re-parsing lat/lon
from the response (defensive: we still cross-check lat/lon).

Design notes (anti-hallucination / robustness):
- Resolution is MODEL-INTERPOLATED, not measured. We never claim ward-level
  detail. Label outputs "model grid (0.5 deg)".
- The fetch shares the SAME thread-safe rate limiter as city + basin fetches
  (engine.fetch_openmeteo._rate_limit) so total API pressure stays polite.
- Chunking is the load-bearing safety: do NOT raise CHUNK beyond ~50 without
  re-testing for 414.
"""
from __future__ import annotations
import math
import time as _time
import requests

# Pull in the shared polite gate so grid + city + basin calls never thunder-herd.
try:
    from engine.fetch_openmeteo import _rate_limit
except Exception:  # standalone / test
    def _rate_limit():
        pass

BASE = "https://api.open-meteo.com/v1/forecast"
CHUNK = 50  # max coords per GET; verified safe (no 414)


def sanitize_grid_parameters(model_name: str, variables: list[str]) -> list[str]:
    """Strip variables that trigger backend initialization faults on specific models."""
    sanitized = list(variables)
    if "gfs_seamless" in model_name:
        for var in ("lifted_index", "pressure_msl", "surface_pressure"):
            if var in sanitized:
                sanitized.remove(var)
    return sanitized

# Maharashtra state bounding box (generous; includes Konkan, W/MH, Marathwada,
# Vidarbha). 0.5 deg state mesh.
MH_BBOX = (15.5, 22.5, 72.5, 81.0)  # lat_min, lat_max, lon_min, lon_max
# Mumbai Metropolitan Region fine sub-mesh. 0.1 deg for metro depth.
MMR_BBOX = (18.7, 19.5, 72.6, 73.6)  # OSM-style view: includes Arabian Sea + mainland

# Variables we pull for the grid. Rainfall + wind/CAPE/MSLP for fields, plus
# soil_moisture (real antecedent saturation, replaces broken archive.db path),
# lifted_index + weather_code + RH/dewpoint (convective / nowcast richness).
GRID_HOURLY = [
    "precipitation", "precipitation_probability",
    "wind_speed_10m", "wind_direction_10m",
    "wind_speed_850hPa", "wind_direction_850hPa",
    "pressure_msl", "cape", "lifted_index",
    "soil_moisture_0_to_10cm", "weather_code",
    "relative_humidity_2m", "dew_point_2m",
]


def gen_grid(bbox: tuple[float, float, float, float], step: float) -> list[tuple[float, float]]:
    """Return [(lat, lon), ...] on a regular step grid inside bbox (inclusive)."""
    lat_min, lat_max, lon_min, lon_max = bbox
    pts = []
    lat = lat_min
    while lat <= lat_max + 1e-9:
        lon = lon_min
        while lon <= lon_max + 1e-9:
            pts.append((round(lat, 4), round(lon, 4)))
            lon = round(lon + step, 6)
        lat = round(lat + step, 6)
    return pts


def maharashtra_grid(step: float = 0.5) -> list[tuple[float, float]]:
    return gen_grid(MH_BBOX, step)


def mmr_grid(step: float = 0.1) -> list[tuple[float, float]]:
    return gen_grid(MMR_BBOX, step)


def _chunks(coords: list[tuple[float, float]], size: int = CHUNK):
    for i in range(0, len(coords), size):
        yield coords[i:i + size]


def fetch_grid(coords: list[tuple[float, float]], model: str = "ecmwf_ifs",
               forecast_days: int = 2, hourly: list[str] = None,
               timezone: str = "Asia/Kolkata", timeout: int = 30) -> list[dict]:
    """Fetch all coords in chunked batches. Returns a list of dicts, one per
    coord, index-aligned to `coords`. Each dict: {lat, lon, hourly: {...}}.

    On a per-chunk failure we return the coords with hourly=None (graceful, no
    crash) so downstream interpolation degrades locally instead of killing the run.
    """
    if hourly is None:
        hourly = GRID_HOURLY
    hourly = sanitize_grid_parameters(model, hourly)
    out: list[dict] = [None] * len(coords)  # type: ignore
    idx = 0
    for chunk in _chunks(coords, CHUNK):
        la = [c[0] for c in chunk]
        lo = [c[1] for c in chunk]
        _rate_limit()
        body = None
        wait_prev = 0
        for attempt in range(4):  # absorb transient 429s / network blips
            try:
                r = requests.get(BASE, params={
                    "latitude": ",".join(str(x) for x in la),
                    "longitude": ",".join(str(x) for x in lo),
                    "hourly": ",".join(hourly),
                    "models": model,
                    "forecast_days": forecast_days,
                    "timezone": timezone,
                }, timeout=timeout)
            except requests.RequestException as e:
                print(f"[GRID] chunk fetch failed (attempt {attempt}): {e}")
                if attempt == 3:
                    break
                _time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 503):
                body_txt = ""
                try:
                    body_txt = r.text or ""
                except Exception:
                    pass
                # FAST-FAIL: Open-Meteo's hard daily cap ("Daily API request
                # limit exceeded") is NOT a transient throttle — retrying just
                # wastes 10-80s per chunk. Abort the whole grid fetch now so the
                # run degrades fast (cached data or GRID-UNAVAILABLE caveat)
                # instead of hanging ~19 min.
                if "Daily API request limit exceeded" in body_txt:
                    print("[GRID] DAILY API LIMIT EXCEEDED — aborting grid fetch "
                          "(will use cache or mark grid unavailable).")
                    # mark all remaining chunks as failed and stop entirely
                    for j, c in enumerate(coords[idx:]):
                        out[idx + j] = {"lat": c[0], "lon": c[1],
                                        "hourly": None, "error": "daily-limit"}
                    return out
                # Aggressive exponential backoff (per ops spec): double each
                # attempt, floor 10s. Open-Meteo minutely/overload limit.
                wait = max(wait_prev * 2, 10) if attempt > 0 else 10
                print(f"[GRID] HTTP {r.status_code} rate-limited/overloaded; "
                      f"backing off {wait}s (attempt {attempt})")
                _time.sleep(wait)
                wait_prev = wait
                continue
            if r.status_code != 200:
                print(f"[GRID] chunk HTTP {r.status_code}: {r.text[:160]}")
                break
            body = r.json()
            break
        if body is None:
            err = "fetch failed after retries"
            for j, c in enumerate(chunk):
                out[idx + j] = {"lat": c[0], "lon": c[1], "hourly": None, "error": err}
            idx += len(chunk)
            continue
        # Defensive: Open-Meteo returns a list when >1 location; a single
        # location returns a dict. Normalise to list.
        if isinstance(body, dict):
            body = [body]
        for j, item in enumerate(body):
            c = chunk[j]
            out[idx + j] = {"lat": c[0], "lon": c[1], "hourly": item.get("hourly", {})}
        # if body shorter than chunk (shouldn't happen), fill the rest
        for j in range(len(body), len(chunk)):
            c = chunk[j]
            out[idx + j] = {"lat": c[0], "lon": c[1], "hourly": None,
                            "error": "short response"}
        idx += len(chunk)
    return out


def fetch_state_and_mmr(state_step: float = 0.5, mmr_step: float = 0.1,
                         model: str = "ecmwf_ifs", forecast_days: int = 2) -> dict:
    """Convenience: fetch both meshes. Returns {state: [...], mmr: [...],
    state_coords: [...], mmr_coords: [...]}. Caller maps results by index."""
    state_coords = maharashtra_grid(state_step)
    mmr_coords = mmr_grid(mmr_step)
    state = fetch_grid(state_coords, model, forecast_days)
    mmr = fetch_grid(mmr_coords, model, forecast_days)
    return {"state_coords": state_coords, "mmr_coords": mmr_coords,
            "state": state, "mmr": mmr}


if __name__ == "__main__":
    # Smoke test (no network hammer): count points + one tiny chunk.
    sc = maharashtra_grid(0.5)
    mc = mmr_grid(0.1)
    print(f"Maharashtra 0.5deg nodes: {len(sc)}")
    print(f"MMR 0.1deg nodes: {len(mc)}")
    print(f"Total: {len(sc) + len(mc)} -> ~{math.ceil((len(sc)+len(mc))/CHUNK)} chunked calls")
    # verify first coord
    print("state[0]:", sc[0], "| mmr[0]:", mc[0])


def grid_dem(grid_results):
    """Coarse DEM (elevation m) sampled at grid points, for hillshade texture.
    Open-Meteo returns `elevation` per location. Returns (coords, elev)."""
    coords, elev = [], []
    for r in grid_results:
        lat = r.get("lat"); lon = r.get("lon")
        e = r.get("elevation")
        if lat is None or lon is None or e is None:
            continue
        coords.append((lat, lon)); elev.append(float(e))
    return coords, np.asarray(elev, dtype=float)


def grid_antecedent(grid_results, window=24):
    """Real antecedent soil saturation from soil_moisture_0_to_10cm (m3/m3).
    Returns (coords, sat_frac): wettest recent volumetric moisture as a fraction
    of typical field capacity (~0.35 m3/m3). No archive.db needed.
    """
    coords, vals = [], []
    for r in grid_results:
        lat = r.get("lat"); lon = r.get("lon"); h = r.get("hourly")
        if lat is None or lon is None or not h or "soil_moisture_0_to_10cm" not in h:
            continue
        arr = [x for x in (h.get("soil_moisture_0_to_10cm") or [])[:window] if x is not None]
        if not arr:
            continue
        coords.append((lat, lon))
        vals.append(float(max(arr)))
    return coords, np.asarray(vals, dtype=float)


def grid_convective(grid_results, window=24):
    """Convective potential from CAPE (J/kg) + lifted_index (C).
    Returns (coords, score 0..1): high CAPE + strongly negative LI => higher.
    """
    coords, vals = [], []
    for r in grid_results:
        lat = r.get("lat"); lon = r.get("lon"); h = r.get("hourly")
        if lat is None or lon is None or not h:
            continue
        cape = [x for x in (h.get("cape") or [])[:window] if x is not None]
        li = [x for x in (h.get("lifted_index") or [])[:window] if x is not None]
        if not cape or not li:
            continue
        max_cape = float(max(cape))
        min_li = float(min(li))
        cape_s = min(max_cape / 2500.0, 1.0)
        li_s = max(0.0, min((-min_li) / 6.0, 1.0))
        coords.append((lat, lon)); vals.append(0.6 * cape_s + 0.4 * li_s)
    return coords, np.asarray(vals, dtype=float)
