"""imd_terms.py — IMD's official terminology, as a single source of truth.

These are IMD's published rainfall WARNING colour bands and rainfall
AMOUNT descriptors (per IMD's district rainfall warning schema). Used so the
brief reads in IMD's register, not a chatbot's.

Colour code (from district_warnings_india DayX_Color): 1=Green, 2=Yellow,
3=Orange, 4=Red. Yellow/Green are "advisory"; Orange/Red are "warning".
"""
from __future__ import annotations

# IMD 24h rainfall WARNING thresholds (mm) — verified against classify.py bands.
THRESH = {"green_max": 64.5, "yellow_max": 115.5, "orange_max": 204.5}

COLOR_LABEL = {1: "GREEN", 2: "YELLOW", 3: "ORANGE", 4: "RED"}
COLOR_LABEL_LOWER = {1: "Green", 2: "Yellow", 3: "Orange", 4: "Red"}


def color_label(code):
    try:
        return COLOR_LABEL.get(int(code), "UNKNOWN")
    except (TypeError, ValueError):
        return "UNKNOWN"


def amount_band(mm):
    """IMD rainfall amount descriptor for a 24h accumulation (mm)."""
    try:
        mm = float(mm)
    except (TypeError, ValueError):
        return "n/a"
    if mm < 0:
        return "n/a"
    if mm < 64.5:
        return "Light to moderate rainfall"
    if mm < 115.5:
        return "Heavy rainfall (64.5-115.5 mm)"
    if mm < 204.5:
        return "Very heavy rainfall (115.5-204.5 mm)"
    return "Extremely heavy rainfall (>204.5 mm)"


def color_amount_band(code):
    """IMD amount descriptor keyed to the official WARNING COLOUR (not our mm).
    Red -> extremely heavy, Orange -> very heavy, etc. This is IMD's own
    band for that colour, shown SEPARATELY from our model point-estimate so
    the two provenance streams are never conflated."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ""
    return {1: "Light to moderate rainfall (<64.5 mm) likely",
            2: "Moderate rainfall (64.5-115.5 mm) likely",
            3: "Heavy to very heavy rainfall (115.5-204.5 mm) likely",
            4: "Extremely heavy rainfall (>204.5 mm) likely"}.get(code, "")


def band_range(mm):
    """Return the IMD 24h band range string for a 24h accumulation (mm)."""
    try:
        mm = float(mm)
    except (TypeError, ValueError):
        return ""
    if mm < 64.5:
        return "<64.5 mm"
    if mm < 115.5:
        return "64.5-115.5 mm"
    if mm < 204.5:
        return "115.5-204.5 mm"
    return ">204.5 mm"


# IMD standard hazard/weather-type codes (subset; from NowcastWarningDistrict
# cat1..cat19 + subdiv Day_X codes). We surface the human label only when the
# code is present in the IMD data — never assert a hazard we didn't receive.
HAZARD_CODES = {
    1: "Rainfall",
    2: "Thunderstorm",
    3: "Hailstorm",
    4: "Heavy Rainfall",
    5: "Wind squall (50+ km/h)",
    6: "Duststorm",
    7: "Heat wave",
    8: "Cold wave",
    9: "Fog",
    10: "Heavy snowfall",
    11: "Landslide",
    12: "Flash flood",
    13: "Waterlogging",
    14: "Cyclonic wind",
    15: "High wave / swell",
    16: "Tidal flooding",
    17: "Lightning",
    18: "Drought",
    19: "Rain (general)",
}


def hazard_labels(cat_dict):
    """cat_dict: {cat1:code, cat2:code,...} -> list of human labels present."""
    labels = []
    if not cat_dict:
        return labels
    for v in cat_dict.values():
        try:
            code = int(v)
        except (TypeError, ValueError):
            continue
        if code and code in HAZARD_CODES:
            labels.append(HAZARD_CODES[code])
    # de-dup preserve order
    seen, out = set(), []
    for l in labels:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out
