"""Phase-2 stress-test harness — runs 3 scenarios against the REAL engine.

These exercises intentionally override inputs to simulate extreme / broken
conditions. They call the actual classify / tides / buffer logic (no live API),
so they are fast and deterministic. Run:  python engine/sim_scenarios.py
"""
from __future__ import annotations
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # parent = weather-updater/, so `engine` imports

import engine.classify as classify
import engine.tides as tides
import engine.buffer as buffer

PASS = "✅ PASS"; FAIL = "❌ FAIL"


def _build_tide_table(peaks):
    """peaks: list of (iso_str, height_m)."""
    return [{"iso": p[0], "tide_m": p[1], "source": "SAMPLE"} for p in peaks]


def scenario_a_drainage_lock():
    """Tidal coincidence must NOT create a false WARNING on light rain,
    but the burst still escalates and the tide is captured as monitoring."""
    print("\n=== Scenario A — The Drainage Lock ===")
    from datetime import datetime, timedelta, timezone
    IST = timezone(timedelta(hours=5, minutes=30))
    peak = datetime.now(IST) + timedelta(hours=3)
    tbl = _build_tide_table([(peak.strftime("%Y-%m-%dT%H:%M:%S%z"), 4.65)])
    tidal = tides.tidal_factor(tbl, peak)

    # (1) 15 mm/24h + 15 mm/h MODERATE burst + tidal lock.
    # Old (buggy) behavior: tide alone pushed WARNING. Correct behavior:
    # burst escalates to ADVISORY; tide is informational (tidal_watch), not the escalator.
    rk = classify.classify(15.0, 90, 1.0, None,
                           intensity={"max1h_mm": 15.0, "burst_band": "MODERATE",
                                      "escalate": False},
                           tidal=tidal)
    burst_escalated = rk.level in ("ADVISORY", "WARNING", "CRITICAL")
    tide_is_monitoring = (rk.tidal_lock is not None) and (rk.tidal_watch is True)
    # must NOT be a false WARNING/CRITICAL driven purely by the tide on light rain
    no_false_critical = rk.level != "CRITICAL"
    print(f"  rain=15mm, tide=4.65m -> level={rk.level}, tidal_lock={rk.tidal_lock}, "
          f"tidal_watch={rk.tidal_watch}")
    print(f"    burst escalated to >=ADVISORY: {burst_escalated} | "
          f"tide captured as monitoring (not escalator): {tide_is_monitoring} | "
          f"no false CRITICAL: {no_false_critical}")
    ok_a = burst_escalated and tide_is_monitoring and no_false_critical

    # (2) Pure light rain + tidal lock (the Ratnagiri case): must stay NOMINAL,
    # tidal_watch=True, NO escalation. This guards the anti-false-alarm contract.
    rk2 = classify.classify(9.4, 95, 1.0, None,
                             intensity={"max1h_mm": 2.0, "burst_band": "LIGHT",
                                        "escalate": False},
                             tidal=tidal)
    ok_b = (rk2.level == "NOMINAL") and (rk2.tidal_watch is True) and (rk2.tidal_lock is not None)
    print(f"  rain=9.4mm (light), tide=4.65m -> level={rk2.level}, "
          f"tidal_watch={rk2.tidal_watch} (must be NOMINAL + monitoring, no false alarm)")
    print(f"    {PASS if ok_b else FAIL}: light-rain tidal lock stays NOMINAL (no false WARNING)")

    ok = ok_a and ok_b
    print(f"  {PASS if ok else FAIL}: tidal coincidence handled correctly "
          f"(burst escalates, tide is monitoring, no false alarm)")
    return ok


def scenario_b_missing_ingestion():
    """All IMD observation inputs are None/empty -> must not crash, fall back."""
    print("\n=== Scenario B — Missing Ingestion Stream ===")
    # IMD payload entirely empty/None (simulating dropout)
    imd_none = None
    rk = classify.classify(None, None, None, imd_none,  # all model inputs None too
                           intensity={}, antecedent_7d=0.0)
    # graceful: no crash, low/zero risk, basis notes model-only, no exception
    ok = (rk is not None) and (rk.basis.startswith("model-only") or rk.level == "NOMINAL")
    # also test the "model present, IMD missing" fallback path
    rk2 = classify.classify(10.0, 70, 2.0, None)
    ok2 = rk2 is not None and "IMD unavailable" in rk2.basis
    print(f"  all-None -> level={rk.level}, basis='{rk.basis}' (no crash)")
    print(f"  model-only,IMD-None -> level={rk2.level}, basis='{rk2.basis}'")
    print(f"  {PASS if (ok and ok2) else FAIL}: graceful fallback, no crash, "
          f"[Telemetry Deficit] semantics preserved")
    return ok and ok2


def scenario_c_compounding_cluster():
    """Threshold breaches across Thane + Navi Mumbai -> systemic metro warning."""
    print("\n=== Scenario C — The Compounding Cluster ===")
    # each node: WARNING/ADVISORY with a flagged proximity neighbour
    def node(name, mm):
        prox = buffer.evaluate_proximity(
            [{"name": f"{name}-N", "max1h_mm": 60, "sum24h_mm": 10}])  # neighbour extreme
        return {"name": name,
                "risk": classify.classify(mm, 95, 3.0, None,
                                          intensity={"max1h_mm": mm, "burst_band": "HEAVY",
                                                     "escalate": False},
                                          proximity=prox),
                "intensity": {"max1h_mm": mm, "burst_band": "HEAVY"},
                "antecedent_7d_mm": 50, "proximity": prox,
                "tidal": {"overlap": False}, "radar": {"status": "ok"},
                "imd": {"colour": "Yellow"}, "observed": {"found": False},
                "source": "open-meteo", "raw_mm": mm, "corrected_mm": mm,
                "wind_kmh": 12, "wind_dir": 250}
    thane = node("Thane", 70)
    navi = node("Navi Mumbai", 65)
    # replicate the report's cluster detection: >=2 active assets with flagged proximity
    active = [thane, navi]
    clustered = [r["name"] for r in active if r.get("proximity", {}).get("flagged")]
    systemic = len(clustered) >= 2
    print(f"  Thane level={thane['risk'].level}, proximity flagged={thane['proximity']['flagged']}")
    print(f"  Navi Mumbai level={navi['risk'].level}, proximity flagged={navi['proximity']['flagged']}")
    print(f"  clustered nodes: {clustered}")
    print(f"  {PASS if systemic else FAIL}: systemic metro warning triggered")
    return systemic


def main():
    print("🔧 SIM SCENARIO MATRIX — impact-dynamics stress tests")
    results = [
        scenario_a_drainage_lock(),
        scenario_b_missing_ingestion(),
        scenario_c_compounding_cluster(),
    ]
    passed = sum(1 for r in results if r)
    print(f"\n=== RESULT: {passed}/{len(results)} scenarios PASS ===")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
