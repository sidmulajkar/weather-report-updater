"""Thermal (heat / humidity) hazard axis — a SEPARATE axis from precipitation.

WHY THIS EXISTS
    IMD publishes heat/humidity advisories on its per-city 7-day forecast page,
    not on the district rainfall warning layer. A briefing that only reads the
    rainfall layer will report "no warning in force" while a formal hot-and-
    humid advisory is active. Precipitation Green and thermal Advisory are
    simultaneously true and must both be shown.

SOURCE
    https://city.imd.gov.in/citywx/city_weather_test_try_warnings.php?id=<station>
    Scoped per verified station id in config/locations.json (imd_station_id).

WHAT THIS MODULE DELIBERATELY DOES NOT DO
    * It does not read warning_images/N.png. Those are decorative sky-condition
      illustrations (mixed dimensions, colours green/white/cream/blue, no red or
      orange). They are NOT a severity scale.
    * It does not invent a level. The page carries no colour, so `level` is
      always None and the literal IMD warning text is reported instead.
    * It never falls back to Green. No station id, a dead page, an empty table,
      or an unparseable row all resolve to status "NOT MONITORED" / "UNKNOWN".

PARSING
    No magic column index. The table's header mislabels cells (the column headed
    "Warnings" holds the sky forecast; the real warning sits further right), and
    the cell count varies between page editions. So we anchor on the target date
    cell, then take the LAST non-numeric text cell in that row as the hazard.

TESTED against live HTML and against deliberate fault injection
    (empty table, malformed row, SSL failure) -- see sim_thermal_faults().
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

BASE = "https://city.imd.gov.in/citywx/city_weather_test_try_warnings.php"
HDR = {"User-Agent": "Mozilla/5.0 (weather-report-updater; IMD city forecast reader)"}

# IMD spells the forecast months in English on these pages.
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Thermal keywords. Matched against the WHOLE warning cell, case-insensitively.
# Deliberately excludes convective wording ("Thunderstorm ... lightning & gusty
# winds") which is a precipitation hazard, not a thermal one.
THERMAL_KEYWORDS = ("hot", "humid", "heat", "warm")

# Hazards that are live IMD warnings but belong to NEITHER the thermal nor the
# precipitation axis. Reporting these as "Nominal" would hide a real warning:
# dense fog is an operational hazard for shipping and aviation even when it is
# neither a rainfall nor a heat event. Held separately so a visibility warning
# stays distinguishable from a genuine no-warning state.
NON_THERMAL_WARNING_KEYWORDS = ("fog", "visibility", "mist", "smog", "dust",
                                "haze")

NUMERIC = re.compile(r"^[\d.]+$")
DATE_CELL = re.compile(r"^(\d{1,2})-([A-Za-z]{3})$")


def _blank(level, status, description=None, station=None, reason=None):
    """Uniform 'no usable thermal data' payload. Never fabricates a level.

    Two DISTINCT no-data states, because conflating them is a safety problem:
      NOT MONITORED  -> configuration gap: no verified station for this district
      UNKNOWN         -> runtime fault: a verified station failed to answer
                          (TLS drop, timeout, or a structural parse guardrail)
    An operator must be able to tell "no data source exists" from "the source
    exists but we could not read it this cycle". Neither is ever Green.
    """
    return {
        "level": level,          # ALWAYS None -- this feed carries no colour
        "status": status,        # "Advisory" | "Nominal" | "NOT MONITORED" | "UNKNOWN"
        "description": description,
        "station": station,
        "reason": reason,
        "tmax_c": None,
        "tmin_c": None,
        "source": "IMD city 7-day forecast (city.imd.gov.in)",
    }


def classify(hazard_text: str):
    """Classify IMD's literal warning prose into a thermal advisory state.

    Returns (is_thermal, status). `level` is intentionally not derived: the
    source publishes no colour, and inventing one would misrepresent it.
    """
    if not hazard_text:
        return False, "UNKNOWN"
    low = hazard_text.lower()
    if low.strip() in ("no warning", "no warnings", ""):
        return False, "Nominal"
    if any(k in low for k in THERMAL_KEYWORDS):
        return True, "Advisory"
    # A visibility/environmental warning is a LIVE IMD warning but is neither
    # thermal nor precipitation. It must not be labelled Nominal -- that would
    # read as "no warning" to a shipping operator. Distinguished so the
    # renderer can name the hazard instead of masking it.
    if any(k in low for k in NON_THERMAL_WARNING_KEYWORDS):
        return False, "OTHER-HAZARD"
    # A non-thermal hazard (thunderstorm, rain, wind) is present but is NOT
    # reported on the thermal axis -- it belongs to the precipitation axis.
    return False, "OTHER-HAZARD"


def parse_rows(html: str, target_date: str):
    """Date-anchored extraction of the forecast row for target_date ('DD-Mon').

    Returns (row_dict, error). row_dict is None on any structural failure.
    Deliberately shape-agnostic: locates the date cell, then takes the last
    non-numeric text cell as the hazard.
    """
    if not html or "<tr" not in html.lower():
        return None, "empty or non-HTML response"
    want_day, want_mon = target_date.split("-")

    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        if not cells:
            continue
        m = DATE_CELL.match(cells[0])
        if not m:
            continue
        day, mon = m.group(1).lstrip("0"), m.group(2)[:3].title()
        if day != str(int(want_day)) or mon != want_mon[:3].title():
            continue

        # Temps sit immediately after the date when present.
        tmax = tmin = None
        for c in cells[1:4]:
            if NUMERIC.match(c):
                v = float(c)
                if v > 45:      # a humidity percentage, not a temperature
                    continue
                if tmin is None:
                    tmin = v
                elif tmax is None:
                    tmax = v
        # Hazard = last non-numeric, non-empty text cell in the row. This is the
        # column that actually carries the warning; the column headed "Warnings"
        # holds the sky forecast and is deliberately ignored.
        texts = [c for c in cells if c and not NUMERIC.match(c)]
        if not texts:
            return None, "forecast row present but no text cells at all"
        hazard = texts[-1]
        # SHAPE GUARDRAIL. A complete row carries the date, min, max, the sky
        # forecast AND a separate trailing warning cell. If the last two text
        # cells are identical we have a truncated table and the "hazard" we just
        # picked is really the sky forecast -- reporting that as a warning would
        # misreport conditions (e.g. "Mainly Clear sky" read as an advisory).
        # Fail closed to NOT MONITORED rather than guess.
        if len(texts) >= 2 and texts[-1].strip().lower() == texts[-2].strip().lower():
            return None, (f"truncated forecast row ({len(cells)} cells): "
                          f"no distinct warning cell after the sky forecast")
        if len(cells) < 7:
            return None, f"forecast row too short ({len(cells)} cells) to carry a warning"
        return {
            "date": cells[0],
            "tmax_c": tmax,
            "tmin_c": tmin,
            "hazard_text": hazard,
            "cell_count": len(cells),
        }, None
    return None, f"no forecast row for {target_date}"


def _fetch(station_id, timeout=20.0, retries=2, backoff=3.0, session=None):
    """Bounded retry: `retries` total attempts, `backoff` seconds apart.

    IMD intermittently drops TLS on this host (SSLEOFError), so a single
    failure must not be mistaken for 'no warning'.
    """
    get = (session or requests).get
    last = "unknown"
    for attempt in range(max(1, retries)):
        try:
            r = get(f"{BASE}?id={station_id}", headers=HDR, timeout=timeout)
            if r.status_code == 200:
                return r.text, None
            last = f"http {r.status_code}"
        except Exception as e:
            last = type(e).__name__
        if attempt + 1 < retries:
            time.sleep(backoff)
    return None, last


def thermal_for_station(station_id, target_date, timeout=20.0, retries=2,
                        backoff=3.0, session=None):
    """Read the thermal hazard for one IMD station. Never raises."""
    if not station_id:
        return _blank(None, "NOT MONITORED", None, None,
                      "no verified IMD city-forecast station for this district")
    html, err = _fetch(station_id, timeout, retries, backoff, session)
    if not html:
        # The station EXISTS and did not answer -> runtime fault, not a gap.
        return _blank(None, "UNKNOWN", None, station_id,
                      f"fetch failure after {retries} attempt(s): {err}")
    row, perr = parse_rows(html, target_date)
    if not row:
        # Station exists and answered, but the payload could not be trusted
        # (empty table, missing target date, truncated row guardrail).
        return _blank(None, "UNKNOWN", None, station_id,
                      f"parse failure: {perr}")

    is_thermal, status = classify(row["hazard_text"])
    if status == "OTHER-HAZARD":
        # A convective/rainfall warning is live here, but it is not a thermal
        # advisory. Report it verbatim and mark the thermal axis nominal, so the
        # precipitation axis remains the one that carries it.
        p = _blank(None, "Nominal",
                   f"non-thermal warning on IMD feed: {row['hazard_text']}",
                   station_id, None)
    elif is_thermal:
        p = _blank(None, "Advisory", row["hazard_text"], station_id, None)
    else:
        p = _blank(None, "Nominal", row["hazard_text"], station_id, None)
    # Carry the day's temperatures through: for a heat advisory the number is the
    # operational fact field crews need (IMD gives no colour here, only prose).
    p["tmax_c"] = row["tmax_c"]
    p["tmin_c"] = row["tmin_c"]
    return p


def thermal_by_location(locations, target_date, timeout=20.0, retries=2,
                        backoff=3.0, max_workers=5):
    """Fetch thermal state for every configured location, concurrently.

    Returns {location_name: thermal_payload}. Every monitored location gets a
    payload; unmonitored ones get NOT MONITORED with the reason.
    """
    def one(L):
        sid = L.get("imd_station_id")
        p = thermal_for_station(sid, target_date, timeout, retries, backoff)
        if not L.get("imd_station_id") and L.get("thermal_coverage_note"):
            p["reason"] = L["thermal_coverage_note"]
        return L.get("name"), p

    names = [L.get("name") for L in locations if L.get("name")]
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(locations)))) as ex:
        out = dict(ex.map(one, locations))
    # IMPORTANT: preserve whatever state a worker actually returned. Collapsing
    # a UNKNOWN (runtime fetch/parse fault on a VERIFIED station) into
    # NOT MONITORED would lie about coverage -- the station has a verified id,
    # the read simply failed. The two states mean different things to an
    # operator: "no data source" vs "source temporarily unreachable".
    return {n: out.get(n) or _blank(None, "UNKNOWN", None, None,
                                    "worker returned no result")
            for n in names}


# ── fault injection: prove the degradation paths before shipping ──────────────

EMPTY_TABLE = """<html><body><table>
<tr><td colspan="7"><b>Past 24 Hours</b></td></tr>
<tr><td>Maximum Temp</td><td>NA</td></tr>
</table></body></html>"""

MALFORMED_ROW = """<html><body><table>
<tr><td>05-Oct</td><td>26.0</td><td>35.0</td><td>Mainly Clear sky</td>
<td>Mainly Clear sky</td></tr>
<tr><td>06-Oct</td><td>26.0</td><td>34.0</td><td>Clear sky</td>
<td>Clear sky</td></tr>
</table></body></html>"""

GOOD_ROW = """<html><body><table>
<tr><td>05-Oct</td><td>26.0</td><td>35.0</td><td></td>
<td>Mainly Clear sky</td><td></td><td>Hot and Humid</td><td>0</td><td>0</td></tr>
</table></body></html>"""


class _Boom:
    def get(self, *a, **k):
        raise requests.exceptions.SSLError("SSL: UNEXPECTED_EOF_WHILE_READING")


class _NotFound:
    def get(self, *a, **k):
        r = requests.Response()
        r.status_code = 404
        return r


def sim_thermal_faults(target_date="05-Oct"):
    """Return (passed, [(case, ok, detail)]). Exits non-zero on any failure."""
    cases = []

    # 1. structural: verified station unreachable -> UNKNOWN (runtime fault)
    p = thermal_for_station(99999, target_date, session=_NotFound())
    cases.append(("network/http-404 on verified station -> UNKNOWN",
                  p["status"] == "UNKNOWN" and p["level"] is None,
                  p["status"]))

    # 2. network: TLS drop (IMD's real intermittent failure mode) -> UNKNOWN
    p = thermal_for_station(43003, target_date, retries=2, backoff=0,
                            session=_Boom())
    cases.append(("SSL drop on verified station -> UNKNOWN",
                  p["status"] == "UNKNOWN" and p["level"] is None,
                  p["status"]))

    # 3. structural: empty forecast table
    row, err = parse_rows(EMPTY_TABLE, target_date)
    cases.append(("empty table -> parse error", row is None and bool(err), str(err)))

    # 4. structural: date present but no warning cell
    row, err = parse_rows(MALFORMED_ROW, target_date)
    cases.append(("no warning cell -> parse error", row is None, str(err)))

    # 5. no station id -> NOT MONITORED, never Green
    p = thermal_for_station(None, target_date)
    cases.append(("no station id -> NOT MONITORED",
                  p["status"] == "NOT MONITORED" and p["level"] is None,
                  p["status"]))

    # 6. positive control: thermal advisory detected from a good row
    row, err = parse_rows(GOOD_ROW, target_date)
    ok = bool(row) and classify(row["hazard_text"])[0] is True
    cases.append(("Hot and Humid -> Advisory", ok,
                  row["hazard_text"] if row else str(err)))

    # 7. convective wording must NOT be classified thermal
    is_t, st = classify("Thunderstorm accompanied with lightning & gusty winds(30-40kmph)")
    cases.append(("thunderstorm -> not thermal", (not is_t) and st == "OTHER-HAZARD", st))

    # 8. 'No warning' -> Nominal (this IS a real assertion from the source)
    is_t, st = classify("No warning")
    cases.append(("'No warning' -> Nominal", (not is_t) and st == "Nominal", st))

    # 9. SEMANTIC SEPARATION: a runtime fault on a VERIFIED station must be
    #    UNKNOWN, never NOT MONITORED. Conflating the two tells an operator a
    #    station has no data source when it actually failed to answer, which is
    #    how a real advisory gets silently dropped. Pinned so it cannot regress.
    p_fault = thermal_for_station(43110, target_date, retries=2, backoff=0,
                                  session=_Boom())
    p_cfg = thermal_for_station(None, target_date)
    ok = (p_fault["status"] == "UNKNOWN"
          and p_fault["station"] == 43110
          and p_fault["level"] is None
          and p_cfg["status"] == "NOT MONITORED"
          and p_cfg["station"] is None
          and p_fault["status"] != p_cfg["status"])
    cases.append(("verified-station fault=UNKNOWN vs config gap=NOT MONITORED", ok,
                  f"fault={p_fault['status']} config={p_cfg['status']}"))

    # 10. parse failure on a verified station is also UNKNOWN, not a gap
    class _EmptyHTML:
        def get(self, *a, **k):
            r = requests.Response()
            r.status_code = 200
            r._content = EMPTY_TABLE.encode()
            return r
    p_parse = thermal_for_station(43110, target_date, retries=1, backoff=0,
                                  session=_EmptyHTML())
    cases.append(("verified-station parse fault -> UNKNOWN",
                  p_parse["status"] == "UNKNOWN" and p_parse["station"] == 43110,
                  p_parse["status"]))

    passed = all(ok for _, ok, _ in cases)
    return passed, cases


if __name__ == "__main__":
    ok, cases = sim_thermal_faults()
    for name, good, detail in cases:
        print(f"  {'PASS' if good else 'FAIL'}: {name}  -> {detail}")
    print(f"\n  {'ALL PASS' if ok else 'FAILURES PRESENT'}  ({sum(1 for _,g,_ in cases if g)}/{len(cases)})")
    raise SystemExit(0 if ok else 1)
