"""Fault-injection profile: IMD city-forecast page fully offline.

Driver:
    SIMULATE_IMD_PAGE_OFFLINE=1 python3 tests/simulate_imd_offline.py

Verifies the ONE-GLANCE DISPATCHes how the system responds when the IMD
city-forecast (thermal) feed cannot be read for any station. Fail-closed:
  verified stations -> UNKNOWN (tmax=None); unmonitored districts -> NOT MONITORED.
Never green, never a fabricated warning.

Only the LIGHT weight the dispatch needs is driven:
  * engine.thermal is read under an offline patch (real engine call).
  * per-city matrix rows (model peaks / convective / source) are carried from
    the verified live report.md (the thermal outage does not touch Open-Meteo).
Deliverable: the verbatim one-glance dispatch body the system would send
on Telegram, plus fail-closed assertions on that printed text.
"""

import os
import sys
import json
from unittest import mock

REPO = "/home/sid/Documents/sid/codefiles/extra-projects/weather-report-updater/githubcode/weather-report-updater"
sys.path.insert(0, REPO)
from engine import thermal, radar, dispatch, fetch_imd  # noqa: E402
import requests  # noqa: E402

os.makedirs(REPO, exist_ok=True)


def _offline_thermal_all(locations, target_date="16-Oct"):
    """engine.thermal output with the IMD city-forecast fetch forced offline."""
    with mock.patch.object(
            thermal, "_fetch",
            return_value=(None, "SSL: UNEXPECTED_EOF_WHILE_READING")):
        return thermal.thermal_by_location(locations, target_date)


def _live_results():
    """Per-city matrix rows carried verbatim from the verified live report.md.

    Verified (16 Oct 2026): 24h raw->corr + model peak bursts + source.
    """
    with open(REPO + "/config/locations.json") as f:
        locations = json.load(f)["locations"]

    # name -> (status, model peak mm/h, peak hour) -- verified from live report
    peaks = {
        "Ratnagiri": ("ADVISORY", 0.6, 0.0),
        "Mumbai": ("ADVISORY", 0.1, 0.0),
        "Thane": ("ADVISORY", 2.5, 15.0),
        "Navi Mumbai": ("ADVISORY", 0.0, 0.0),
        "Kolhapur": ("ADVISORY", 1.9, 2.0),
        "Pune": ("NOMINAL", 0.0, 0.0),
        "Nashik": ("NOMINAL", 0.0, 0.0),
        "Nagpur": ("NOMINAL", 0.1, 0.0),
        "Aurangabad": ("NOMINAL", 0.0, 0.0),
        "Solapur": ("NOMINAL", 0.0, 0.0),
    }
    rows = []
    for L in locations:
        name = L["name"]
        st, pk, pa = peaks.get(name, ("NOMINAL", 0.0, 0.0))
        rows.append({
            "name": name,
            "imd": {
                "status": st,
                "col": "Yellow" if st == "ADVISORY" else "Green",
                "tmax_c": None, "tmin_c": None,
            },
            "peak_mm": pk,
            "peak_at_ist": ("%02g:00 IST" % pa) if pk > 0 else None,
            "convect": {"flag": None},
            "source": "open-meteo",
        })
    return rows


def build_doc():
    """City-forecast page offline only (thermal outage); nowcast + radar live.
    Dispatches the fail-closed offline labels (not the blackout block).
    """
    with open(REPO + "/config/locations.json") as f:
        CFG = json.load(f)
    locations = CFG["locations"]

    # 1) Thermal city-forecast page offline (verified stations -> UNKNOWN,
    #    unmonitored districts -> NOT MONITORED).
    with mock.patch.object(
            thermal, "_fetch",
            return_value=(None, "SSL: UNEXPECTED_EOF_WHILE_READING")):
        thermal_all = thermal.thermal_by_location(locations, "16-Oct")
    results = _live_results()

    # 2) Nowcast WMS live (separate feed, unaffected by city-forecast outage).
    nc = fetch_imd.district_nowcast([L["district"].upper() for L in locations])
    nowcast_status = nc.get("status", "unavailable")

    # 3) Radar page live (separate feed).
    rd = radar.check_feed()
    radar_status = rd.get("note", "Mumbai radar page reachable (checked)")

    return {
        "thermal_all": thermal_all,
        "results": results,
        "sev": {"has_signal": False, "watch": "NONE", "bands": "CLEAR"},
        "radar_status": radar_status,
        "nowcast_status": nowcast_status,
        "cfg": CFG,
        "today": "16-Oct-2026",
        "run_label": "16 Oct 2026 20:44 IST",
    }


def build_blackout_doc():
    """Full upstream telemetry outage: IMD city-forecast + nowcast WMS +
    radar page all unreachable. Dispatches the fail-closed blackout block.
    """
    with open(REPO + "/config/locations.json") as f:
        CFG = json.load(f)
    locations = CFG["locations"]

    # 1) Thermal city-forecast page offline (verified stations -> UNKNOWN,
    #    unmonitored districts -> NOT MONITORED). Matches SIMULATE_IMD_PAGE_OFFLINE.
    with mock.patch.object(
            thermal, "_fetch",
            return_value=(None, "SSL: UNEXPECTED_EOF_WHILE_READING")):
        thermal_all = thermal.thermal_by_location(locations, "16-Oct")
    results = _live_results()

    # 2) Nowcast WMS offline (separate feed).
    with mock.patch.object(
            fetch_imd, "district_nowcast",
            return_value={"status": "unavailable", "reason": "wfs fetch failed"}):
        nc = fetch_imd.district_nowcast([L["district"].upper() for L in locations])
    nowcast_status = nc.get("status", "unavailable")

    # 3) IMD urban radar page offline (separate feed).
    with mock.patch.object(
            radar, "check_feed",
            return_value={"checked": True, "ok": False,
                          "note": "IMD urban radar page UNREACHABLE"}):
        rd = radar.check_feed()
    radar_status = "IMD urban radar page UNREACHABLE"

    doc = {
        "thermal_all": thermal_all,
        "results": results,
        "sev": {"has_signal": False, "watch": "NONE", "bands": "CLEAR"},
        "radar_status": radar_status,
        "nowcast_status": nowcast_status,
        "cfg": CFG,
        "today": "16-Oct-2026",
        "run_label": "16 Oct 2026 20:58 IST",
        "blackout": True,   # fail-closed render of a total upstream telemetry outage
    }
    return doc


def main():
    # Profile selection: SIMULATE_GLOBAL_BLACKOUT -> complete upstream telemetry
    # outage (city-forecast + nowcast WMS + radar); else city-forecast offline
    # only (SIMULATE_IMD_PAGE_OFFLINE).
    if os.environ.get("SIMULATE_GLOBAL_BLACKOUT"):
        doc = build_blackout_doc()
        print("### SIMULATE_GLOBAL_BLACKOUT: IMD city-forecast + nowcast WMS "
              "+ radar page all unreachable")
    else:
        doc = build_doc()
        print("### SIMULATE_IMD_PAGE_OFFLINE: IMD city-forecast page offline")

    print("### thermal_all under OFFLINE IMD (per district)")
    for n in sorted(doc["thermal_all"]):
        t = doc["thermal_all"][n] or {}
        print("  %-16s | %-16s | tmax=%s" % (
            n, t.get("status"), t.get("tmax_c")))

    text = dispatch.build_one_glance_dispatch(doc)
    print("\n" + "=" * 60)
    print("### ONE-GLANCE DISPATCH UNDER %s" % (
        "GLOBAL BLACKOUT" if os.environ.get("SIMULATE_GLOBAL_BLACKOUT")
        else "IMD PAGE OFFLINE"))
    print("=" * 60)
    print(text)
    print("### end of dispatch (%d chars)" % len(text))

    # Fail-closed assertions on the printed text.
    notes = []
    if "CRITICAL THERMAL ALERT" in text:
        notes.append("FAIL: fabricated CRITICAL THERMAL ALERT for offline station")
    if len(text) > 4096:
        notes.append("FAIL: dispatch %d chars > 4096 limit" % len(text))

    if os.environ.get("SIMULATE_GLOBAL_BLACKOUT"):
        # Blackout profile must show the fail-closed infrastructure block.
        for n in ("CRITICAL INFRASTRUCTURE DATA BLACKOUT",
                  "POTENTIAL THERMAL ALERT", "TEMPERATURE DATA STREAM UNAVAILABLE",
                  "OFFLINE / FETCH FAILED", "IMD urban radar page is unreachable",
                  "CRITICAL TELEMETRY DEFICIT — ALL DISTRICT FEEDS OFFLINE",
                  "Stale/Cached Panels", "FEEDS OFFLINE"):
            if n.lower() not in text.lower():
                notes.append("blackout missing: '%s'" % n)
        # A real town/failure label: the advisory-map overlay list (Thane,
        # Kolhapur) — evidential, sourced from the projected matrix. The
        # precipitation-vector line derives the model peaks from the matrix.
        if "model peak" not in text.lower():
            notes.append("model peak vectors not rendered in blackout")
    else:
        # Offline profile (city-forecast only) — no fabricated alert; fail-closed
        # labels present; model peaks retained; telemetry gap preserved.
        for n in ("NOT MONITORED", "Read-unavailable/unconfirmed", "Telemetry Deficit",
                  "IMD nowcast UNAVAILABLE", "15:00 IST", "02:00 IST"):
            if n not in text:
                notes.append("expected '%s' not found in dispatch" % n)
        if "Telemetry Deficit" in text and "CRITICAL INFRASTRUCTURE DATA BLACKOUT" in text:
            notes.append("FAIL: inherited blackout block under city-forecast-only outage")

    print("\n### FAIL-CLOSED ASSERTIONS")
    for note in notes:
        print("  ", note)
    if not notes:
        print("  ", ("PASS: global blackout layout; fail-closed, no fabricated alert,"
                    if os.environ.get("SIMULATE_GLOBAL_BLACKOUT")
                    else "PASS: no fabricated alert; fail-closed labels present; "
                       "within 4096 limit."))
    return 1 if notes else 0


if __name__ == "__main__":
    sys.exit(main())
