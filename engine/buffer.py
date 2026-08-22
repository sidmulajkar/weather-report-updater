"""Spatial Buffer Zoning — 3x3 grid "Proximity Threat Vector".

Weather does not respect administrative district lines. A target facility (e.g.
a warehouse in Thane) can be hit by a convective system hovering 2 km outside
its exact coordinate box. We sample the 8 neighbouring grid cells around the
target coordinate (±~0.25° ≈ ±25 km at this latitude) and, if any neighbour shows
an extreme short-duration burst or high 24 h accumulation, we raise a
"Proximity Threat Vector" flag on the target even if its own cell looks clear.

Conservative by design: a neighbour spike escalates caution, it never *lowers*
a target's own risk.
"""
from __future__ import annotations
from typing import Optional

STEP = 0.25  # degrees ~= 25 km at 19°N; 8-neighbour ring

# neighbour is "extreme" if its peak 1 h burst or 24 h sum crosses these
NEIGHBOUR_BURST_MM_H = 50.0     # extreme cloudburst-class neighbour
NEIGHBOUR_SUM24_MM = 120.0      # heavy 24 h neighbour


def neighbour_coords(lat: float, lon: float, step: float = STEP):
    out = []
    for dlat in (-step, 0, step):
        for dlon in (-step, 0, step):
            if dlat == 0 and dlon == 0:
                continue
            out.append((round(lat + dlat, 3), round(lon + dlon, 3)))
    return out


def evaluate_proximity(neighbours: list[dict]) -> dict:
    """neighbours: list of {max1h_mm, sum24h_mm, name}.

    Returns proximity dict with flag + worst neighbour summary.
    """
    flagged = []
    for n in neighbours:
        m1 = n.get("max1h_mm") or 0.0
        s24 = n.get("sum24h_mm") or 0.0
        if m1 >= NEIGHBOUR_BURST_MM_H or s24 >= NEIGHBOUR_SUM24_MM:
            flagged.append({"name": n.get("name", "?"),
                            "max1h_mm": round(m1, 1),
                            "sum24h_mm": round(s24, 1)})
    return {
        "flagged": bool(flagged),
        "count": len(flagged),
        "threats": flagged,
        "vector": "PROXIMITY THREAT" if flagged else None,
    }


def scan_from_grid(lat: float, lon: float, city_points: list[dict]) -> dict:
    """Proximity WITHOUT extra network calls.

    `city_points`: list of already-fetched assets, each
        {"lat", "lon", "max1h_mm", "sum24h_mm", "name"}.
    For the target's 3x3 neighbour ring we look up which already-fetched
    assets fall on/near those ring coordinates and evaluate them. This reuses
    data we already paid Open-Meteo for, instead of firing 8 new requests
    per city (which caused minute-long CI runs under rate-limiting).

    If a ring cell has no nearby fetched asset, it is treated as a data gap
    (honest: we do not fabricate a neighbour).
    """
    neighbours = []
    for nlat, nlon in neighbour_coords(lat, lon):
        # find the closest fetched asset within ~0.30 deg (~30 km)
        best = None
        best_d = 1e9
        for c in city_points:
            d = abs(c["lat"] - nlat) + abs(c["lon"] - nlon)
            if d < best_d:
                best_d, best = d, c
        if best and best_d <= 0.30 and best.get("max1h_mm") is not None:
            neighbours.append({
                "name": best.get("name", "?"),
                "max1h_mm": best["max1h_mm"],
                "sum24h_mm": best.get("sum24h_mm", 0.0),
            })
    return evaluate_proximity(neighbours)


def scan(lat: float, lon: float, fetch_fn) -> dict:
    """Legacy live 3x3 proximity scan (kept for tests/local use).

    `fetch_fn(coord) -> {"max1h_mm": float, "sum24h_mm": float, "name": str}`
    is injected by the caller. For each of the 8 neighbouring grid cells we
    fetch its burst/sum and then run evaluate_proximity(). Prefer
    `scan_from_grid` in production to avoid redundant API calls.
    """
    neighbours = []
    for nlat, nlon in neighbour_coords(lat, lon):
        try:
            r = fetch_fn((nlat, nlon))
            if r:
                neighbours.append(r)
        except Exception:
            continue
    return evaluate_proximity(neighbours)


if __name__ == "__main__":
    nb = [{"name": "N1", "max1h_mm": 55, "sum24h_mm": 10},
          {"name": "N2", "max1h_mm": 5, "sum24h_mm": 30}]
    print("neighbours:", nb)
    print("proximity:", evaluate_proximity(nb))
