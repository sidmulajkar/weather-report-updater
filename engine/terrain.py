"""Microscale terrain & wind-funneling adjustment (MODELED, not measured).

Maharashtra geography: the Western Ghats create a sharp elevation gradient.
Westerly/SW winds hitting the windward Ghats force orographic lift -> rapid
rain dump on the coast/ghats, while leeward Pune stays comparatively dry. In
dense urban corridors (Mumbai, Thane, Navi Mumbai), skyscraper arrays funnel
surface wind, so localized gusts exceed regional Open-Meteo averages.

We encode this as a deterministic, transparent factor by coordinate/zone:
  - GHATS_WINDWARD (coastal Konkan, Mumbai, Ratnagiri): orographic rain boost
    + when wind is W/SW, gust amplification.
  - LEESIDE (Pune, Nashik, Aurangabad, Solapur): rain suppression, no boost.
  - URBAN (Mumbai, Thane, Navi Mumbai): additional gust amplification factor.

This is a SIMPLIFIED PHYSICAL PROXY, clearly labeled as modeled — it is NOT
observed data and must never be presented as measured. It exists to prevent
false negatives (under-calling wind/gust risk in funnels) and to flag orographic
rain enhancement.
"""
from __future__ import annotations
from typing import Optional

# zones by district keyword
GHATS_WINWARD = ["MUMBAI", "THANE", "RAIGAD", "RATNAGIRI", "PALGHAR", "NAVI MUMBAI"]
LEESIDE = ["PUNE", "NASHIK", "AURANGABAD", "SOLAPUR", "KOLHAPUR", "SATARA", "SANGLI"]
URBAN = ["MUMBAI", "THANE", "NAVI MUMBAI"]

# orographic rain multiplier applied to forecast accum when wind is onshore W/SW
OROG_RAIN_MULT = 1.3
# gust amplification factors
GUST_URBAN = 1.4
GUST_GHATS = 1.2
# wind direction sector considered "onshore forcing" (degrees, W/SW)
ONSHORE_SECTOR = (200, 340)
# speed floor for orographic activation (km/h -> ~15 kt * 1.852)
OROG_MIN_WIND_KMH = 27.8


def zone_of(district: str) -> str:
    d = (district or "").upper()
    if any(k in d for k in URBAN):
        return "URBAN_GHATS" if any(k in d for k in GHATS_WINWARD) else "URBAN"
    if any(k in d for k in GHATS_WINWARD):
        return "GHATS_WINWARD"
    if any(k in d for k in LEESIDE):
        return "LEESIDE"
    return "PLAIN"


def is_onshore(wind_dir: Optional[float]) -> bool:
    if wind_dir is None:
        return False
    lo, hi = ONSHORE_SECTOR
    return lo <= wind_dir <= hi


def adjust(district: str, forecast_mm: float, wind_dir: Optional[float],
           gust_kmh: float, wind_850_kmh: Optional[float] = None) -> dict:
    """Return modeled adjustments for a district.

    - rain_factor: multiply forecast accum by this (orographic)
    - gust_factor: multiply regional gust by this (funneling)
    """
    zone = zone_of(district)
    onshore = is_onshore(wind_dir)
    rain_factor = 1.0
    if zone in ("GHATS_WINWARD", "URBAN_GHATS") and onshore:
        speed = wind_850_kmh if wind_850_kmh is not None else gust_kmh
        if speed >= OROG_MIN_WIND_KMH:
            rain_factor = OROG_RAIN_MULT
    gust_factor = 1.0
    if zone == "URBAN":
        gust_factor = GUST_URBAN
    elif zone == "URBAN_GHATS":
        gust_factor = max(GUST_URBAN, GUST_GHATS)
    elif zone == "GHATS_WINWARD":
        gust_factor = GUST_GHATS
    return {
        "zone": zone,
        "onshore": onshore,
        "rain_factor": rain_factor,
        "gust_factor": gust_factor,
        "adj_gust_kmh": round(gust_kmh * gust_factor, 1),
        "adj_rain_mm": round(forecast_mm * rain_factor, 1),
        "modeled": True,  # explicitly flagged as not-observed
    }


if __name__ == "__main__":
    print("Mumbai onshore:", adjust("MUMBAI SUBURBAN", 12.0, 250, 30))
    print("Pune onshore:", adjust("PUNE", 2.0, 250, 20))
    print("Pune offshore:", adjust("PUNE", 2.0, 90, 20))
