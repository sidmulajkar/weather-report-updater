"""Grounded analysis layer over Open-Meteo model arrays.

Everything here is DERIVED from data we actually fetch (48h hourly across the
0.5deg state grid + 0.1deg MMR mesh + 10 asset cities). No invented physics.

Future hooks (NOT executed, no data source wired yet):
  # HOOK: SST orographic trigger  -> requires NOAA OISST ingestion stream
  # HOOK: INSAT-3D CTT parser     -> requires INSAT CTT NetCDF/JSON feed
  # HOOK: elevation-weighted interp-> requires published lapse-rate downscaling
                                         (linear interp used; labeled as such)
"""

import math
import numpy as np

# Published convective criteria (operational meteorology, not invented):
#   CAPE >= 2500 J/kg  AND  lifted_index <= -3 C  => elevated convection.
CAPE_THRESHOLD = 2500.0      # J/kg
LI_THRESHOLD = -3.0          # deg C
# State-wide precip below this (mm summed over grid) => STATIONARY/DIFFUSE.
TRACK_MIN_RAIN = 0.1
# Soil volumetric moisture at/above this fraction of field capacity (~0.35) is
# treated as "saturated runoff risk". 0.35 is typical loam field capacity.
SOIL_FIELD_CAPACITY = 0.35

# Non-overlapping regional assignment for rollups. A point belongs to the FIRST
# matching box (priority order) so each grid node lands in exactly one region.
REGIONS = [
    ("Konkan Coastal",      (15.5, 18.8), (72.5, 73.6)),
    ("Mumbai Metro (MMR)",  (18.8, 19.4), (72.7, 73.2)),
    ("Western Maharashtra", (17.0, 22.5), (73.2, 76.5)),
    ("Marathwada",          (17.5, 21.0), (76.0, 78.5)),
    ("Vidarbha",            (19.5, 22.0), (78.0, 81.0)),
]


# ---------------------------------------------------------------------------
# Storm-track (advection) vector — mass-weighted centroid + great-circle bearing
# ---------------------------------------------------------------------------
def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def centroid_at(grid_results, hour_index, var="precipitation"):
    """Mass-weighted centroid (Lat, Lon) of `var` over the grid at hour_index.
    Returns (lat, lon, total) or None if total < TRACK_MIN_RAIN (diffuse)."""
    lat_w = 0.0
    lon_w = 0.0
    total = 0.0
    for r in grid_results:
        h = r.get("hourly")
        if not h or var not in h:
            continue
        arr = h[var]
        if not arr or hour_index >= len(arr):
            continue
        v = arr[hour_index]
        if v is None or v <= 0:
            continue
        lat, lon = r["lat"], r["lon"]
        lat_w += v * lat
        lon_w += v * lon
        total += v
    if total < TRACK_MIN_RAIN:
        return None
    return (lat_w / total, lon_w / total, total)


def storm_track(grid_results, hours=(0, 6, 12), var="precipitation"):
    """Compute direction + speed of the rain band between the first and last
    sampled hours. Returns a dict:
      {status, bearing_deg, speed_kmh, from, to, total0, totalN, dir16}
    status is 'MOVING' or 'STATIONARY/DIFFUSE'."""
    c0 = centroid_at(grid_results, hours[0], var)
    cN = centroid_at(grid_results, hours[-1], var)
    if c0 is None or cN is None:
        return {"status": "STATIONARY/DIFFUSE", "bearing_deg": None,
                "speed_kmh": None, "from": None, "to": None,
                "dir16": "n/a", "total0": (c0[2] if c0 else 0.0),
                "totalN": (cN[2] if cN else 0.0)}
    lat0, lon0, _ = c0
    latN, lonN, _ = cN
    dlon = math.radians(lonN - lon0)
    la0, laN = math.radians(lat0), math.radians(latN)
    x = math.sin(dlon) * math.cos(laN)
    y = (math.cos(la0) * math.sin(laN) -
         math.sin(la0) * math.cos(laN) * math.cos(dlon))
    bearing = (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
    dt = (hours[-1] - hours[0])
    dist = _haversine_km(lat0, lon0, latN, lonN)
    speed = dist / dt if dt > 0 else 0.0
    return {
        "status": "MOVING",
        "bearing_deg": round(bearing, 1),
        "speed_kmh": round(speed, 1),
        "from": (lat0, lon0), "to": (latN, lonN),
        "dir16": _compass16(bearing),
        "total0": c0[2], "totalN": cN[2],
    }

def peak_intensity_grid(grid_results, var="precipitation", window=24):
    """Return the MAX single-hour rainfall (mm/h) anywhere in the grid over
    `window` hours. Real data (uses the per-hour arrays we already fetch).
    Used by the stationary-stall check: a slow/stationary centroid with a high
    local peak intensity is a 'rain-bomb', not a calm diffuse day."""
    mx = 0.0
    for r in grid_results:
        h = r.get("hourly")
        if not h or var not in h:
            continue
        arr = [x for x in (h[var] or [])[:window] if x is not None]
        if arr:
            mx = max(mx, max(arr))
    return round(mx, 1)


def check_for_stalled_system(centroid_speed_kmh, max_peak_intensity_mm_h):
    """Identify a severe rainfall cell anchored over the grid (storm 'stall').

    A storm can park over one city for hours: the mass-weighted centroid at
    t0 and t12 stays nearly identical -> speed ~0, which would otherwise read
    like a calm, diffuse day. If the centroid is near-stationary BUT a cell is
    dumping >=20 mm/h somewhere, that is a dangerous anchored system, not calm.

    Uses ONLY data we already compute (centroid speed from track_centroid +
    max peak-hour intensity from peak_intensity_grid). No invented inputs.
    """
    if centroid_speed_kmh < 5.0 and max_peak_intensity_mm_h >= 20.0:
        return ("DANGEROUS MONSOON CELL INVERSION: Stalled / Stationary "
                f"Convective System Anchored over asset grid corridors. "
                f"Localized intensity: {max_peak_intensity_mm_h} mm/hr.")
    return None


def _compass16(bearing):
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((bearing + 11.25) // 22.5) % 16]


# ---------------------------------------------------------------------------
# Convective flag (literal published thresholds; no weighting)
# ---------------------------------------------------------------------------
def convective_flag(grid_results, hour_index=None):
    """Return convective assessment using the PUBLISHED criterion:
    CAPE >= 2500 J/kg AND lifted_index <= -3 C. No weighting / no invented scale.

    Returns {triggered, max_cape, min_li, n_points, li_available}.
    - li_available=False means lifted_index was absent for the model used; we then
      CANNOT confirm convection and report 'data unavailable' (never claim 'none')."""
    max_cape = -1e9
    min_li = 1e9
    n = 0
    li_present = False
    for r in grid_results:
        h = r.get("hourly")
        if not h or "cape" not in h:
            continue
        cape = h["cape"]
        li = h.get("lifted_index")
        idxs = range(len(cape)) if hour_index is None else [hour_index]
        for i in idxs:
            if i < len(cape) and cape[i] is not None:
                max_cape = max(max_cape, float(cape[i])); n += 1
            if li is not None and i < len(li) and li[i] is not None:
                min_li = min(min_li, float(li[i])); li_present = True
    if not li_present:
        return {"triggered": False, "max_cape": (None if max_cape < -1e8 else round(max_cape, 0)),
                "min_li": None, "n_points": n, "li_available": False}
    triggered = (max_cape >= CAPE_THRESHOLD) and (min_li <= LI_THRESHOLD)
    return {"triggered": triggered,
            "max_cape": (None if max_cape < -1e8 else round(max_cape, 0)),
            "min_li": (None if min_li > 1e8 else round(min_li, 1)),
            "n_points": n, "li_available": True}


# ---------------------------------------------------------------------------
# Peak-hour timing per grid cell (for the timing map)
# ---------------------------------------------------------------------------
def peak_hour_field(grid_results, var="precipitation", window=24):
    """Return (coords, peak_hour_array) — hour-of-day (0..23 IST-ish, local
    model tz) of the max `var` in the first `window` hours, per grid point."""
    coords, hrs = [], []
    for r in grid_results:
        h = r.get("hourly")
        if not h or var not in h:
            continue
        arr = [x for x in (h[var] or [])[:window]]
        if not arr or all(x is None for x in arr):
            continue
        best_i, best_v = 0, -1.0
        for i, v in enumerate(arr):
            if v is not None and v > best_v:
                best_v, best_i = v, i
        coords.append((r["lat"], r["lon"]))
        hrs.append(best_i % 24)  # local hour of day
    return coords, np.asarray(hrs, dtype=float)


# ---------------------------------------------------------------------------
# Real antecedent saturation from soil_moisture (graceful None -> 0.0)
# ---------------------------------------------------------------------------
def antecedent_states(grid_results, window=24):
    """Return (coords, frac, available). frac = max recent soil_moisture / field
    capacity (clipped 0..1.5). None values fall back to 0.0 so a missing node
    never crashes the run. `available` is False if the model returned no
    soil_moisture at all (e.g. ECMWF/ICON point forecasts omit it) — callers must
    then report 'antecedent unavailable', never invent a saturation %."""
    coords, vals = [], []
    available = False
    for r in grid_results:
        h = r.get("hourly")
        if not h or "soil_moisture_0_to_10cm" not in h:
            continue
        arr = h["soil_moisture_0_to_10cm"]
        if not arr:
            continue
        available = True
        seq = [x if x is not None else 0.0 for x in arr[:window]]
        if not seq:
            continue
        wet = max(seq)
        coords.append((r["lat"], r["lon"]))
        vals.append(min(wet / SOIL_FIELD_CAPACITY, 1.5))
    return coords, np.asarray(vals, dtype=float), available


# ---------------------------------------------------------------------------
# Non-overlapping regional rollup
# ---------------------------------------------------------------------------
def assign_region(lat, lon):
    for name, (la0, la1), (lo0, lo1) in REGIONS:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return "Other Maharashtra"


def regional_rollup(grid_results, var="precipitation", window=24):
    """Aggregate grid points into non-overlapping regions. For each region
    return {n_points, max_mm, region_centroid_latlon, peak_hour_mode,
    clustered_node}.

    clustered_node (honest spatial check): if ONE node's 24h max dominates the
    regional mean (>= 2x the mean AND materially above it), we flag that the
    region's headline max is LOCALIZED, not region-wide. This prevents
    mistaking a single intense cell (e.g. over an urban hub) for uniform
    regional rain. Uses only real grid data; no invented physics.
    """
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in grid_results:
        h = r.get("hourly")
        if not h or var not in h:
            continue
        arr = [x for x in (h[var] or [])[:window] if x is not None]
        if not arr:
            continue
        reg = assign_region(r["lat"], r["lon"])
        buckets[reg].append((r["lat"], r["lon"], max(arr)))
    out = {}
    for reg, items in buckets.items():
        if not items:
            continue
        n = len(items)
        mx = max(v for _, _, v in items)
        mean = sum(v for _, _, v in items) / n
        clat = sum(la for la, _, _ in items) / n
        clon = sum(lo for _, lo, _ in items) / n
        rec = {"n_points": n, "max_mm": round(mx, 1),
               "centroid": (round(clat, 2), round(clon, 2))}
        # clustering detection — ONLY when operationally meaningful.
        # A single rainy node among dry ones (e.g. 14mm vs 2mm mean on a dry
        # day) is NOT a "localized cell over an urban hub"; it is just the grid
        # picking up one shower. We only flag when the dominant node reaches
        # IMD Yellow 24h intensity (>=64.5mm) AND dominates the regional mean
        # strongly. Below that bar we stay silent to avoid false signals.
        CLUSTER_MM = 64.5  # IMD Yellow 24h threshold (mm) — real "intense cell" bar
        if n >= 5 and mx >= CLUSTER_MM and mean > 0 and mx >= 3.0 * mean:
            dom = max(items, key=lambda t: t[2])
            rec["clustered_node"] = {
                "lat": round(dom[0], 2), "lon": round(dom[1], 2),
                "max_mm": round(dom[2], 1), "mean_mm": round(mean, 1),
            }
        out[reg] = rec
    return out
