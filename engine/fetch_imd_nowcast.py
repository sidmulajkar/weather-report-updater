"""U2 — IMD official district nowcast (GROUND TRUTH, verified LIVE 2026-08-22).

Real endpoint discovered from districtWiseNowcastGIS.php's JS:
  GET https://reactjs.imd.gov.in/geoserver/imd/wfs
      ?service=WFS&version=1.1.0&request=GetFeature
      &typename=imd:NowcastWarningDistrict&srsname=EPSG:4326&outputFormat=application/json
Returns 764 district features. Each has:
  State_District, State, District, Color (1=none/green,2=yellow,3=orange,4=red),
  cat1..cat19 (warning category flags), message, impact, action,
  toi/vupto (validity times IST), update_time, Date.

This is the OFFICIAL IMD nowcast (next 3h, updated ~3x/day). It is the authoritative
ground truth we cross-check our model forecast against. We attribute IMD.

Design: never raises on network failure; returns status flags so the report can
gracefully say "IMD nowcast unavailable" instead of faking a colour.
"""
from __future__ import annotations
import requests
from engine import sanity

WFS_URL = "https://reactjs.imd.gov.in/geoserver/imd/wfs"
LAYER = "imd:NowcastWarningDistrict"
HEADERS = {"User-Agent": "Mozilla/5.0 (weather-report-updater research; +local)"}

# IMD colour code -> label (per IMD impact-based warnings)
COLOR_LABEL = {1: "Green", 2: "Yellow", 3: "Orange", 4: "Red"}
COLOR_LEVEL = {1: "NOMINAL", 2: "ADVISORY", 3: "WARNING", 4: "CRITICAL"}

# category code -> human text (subset of the page's `category` map; we only emit
# the ones we see set, so this is non-exhaustive but honest about what's flagged)
CAT_TEXT = {
    2: "Light rain < 5 mm/hr",
    4: "Light Thunderstorms, wind < 40 kmph",
    6: "Low lightning probability (<30%)",
    7: "Moderate rain 5-15 mm/hr",
    9: "Moderate Thunderstorms, wind 41-61 kmph",
    11: "Heavy rain 15-25 mm/hr (sev1)",
    13: "Heavy Thunderstorms, wind 62-74 kmph",
    15: "Very heavy rain 25-45 mm/hr",
    17: "Severe Thunderstorms, wind 75-95 kmph",
    19: "Extremely heavy rain > 45 mm/hr",
}


def fetch_nowcast(state: str = "MAHARASHTRA", timeout: int = 30, ttl_minutes: int = 180) -> dict:
    """Return {status, date, by_district:{DISTRICT: record}, stale_summary} for the given state.

    District keys are upper-cased for matching. On failure status='unavailable'.
    Applies TTL gate: records older than `ttl_minutes` are marked stale.
    """
    out = {"status": "unavailable", "date": None, "by_district": {}, "reason": "",
           "stale_summary": {"ok": 0, "stale": 0, "reasons": []}}
    try:
        r = requests.get(WFS_URL, params={
            "service": "WFS", "version": "1.1.0", "request": "GetFeature",
            "typename": LAYER, "srsname": "EPSG:4326", "outputFormat": "application/json"
        }, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        out["reason"] = f"fetch failed: {e}"
        return out
    except ValueError as e:
        out["reason"] = f"bad json: {e}"
        return out

    byd = {}
    for f in j.get("features", []):
        p = f.get("properties", {})
        if (p.get("State") or "").strip().upper() != state.upper():
            continue
        d = (p.get("District") or p.get("State_District") or "").strip().upper()
        cats = [c for c in p.keys() if c.startswith("cat") and p[c] not in (0, "", None)]
        rec = {
            "district": d,
            "color": p.get("Color"),
            "color_label": COLOR_LABEL.get(p.get("Color"), "Unknown"),
            "level": COLOR_LEVEL.get(p.get("Color"), "UNKNOWN"),
            "message": (p.get("message") or "").strip(),
            "impact": (p.get("impact") or "").strip(),
            "action": (p.get("action") or "").strip(),
            "toi": p.get("toi"), "vupto": p.get("vupto"),
            "date": p.get("Date"), "update_time": p.get("update_time"),
            "categories": cats,
            "cat_text": [CAT_TEXT.get(int(c[3:]), c) for c in cats if c[3:].isdigit()],
            "validity_window": sanity.nowcast_validity_text({
                "message": p.get("message"),
                "toi": p.get("toi"), "vupto": p.get("vupto")
            }),
        }
        ttl = sanity.nowcast_ttl_status(rec, ttl_minutes=ttl_minutes)
        rec["ttl"] = ttl
        byd[d] = rec

    out["status"] = "ok"
    out["by_district"] = byd
    out["date"] = j.get("features", [{}])[0].get("properties", {}).get("Date") if byd else None
    out["date"] = max((r2.get("date") for r2 in byd.values() if r2.get("date")), default=None)
    # stale summary
    stale_summary = {"ok": 0, "stale": 0, "reasons": []}
    for d, r in byd.items():
        if r["ttl"]["fresh"]:
            stale_summary["ok"] += 1
        else:
            stale_summary["stale"] += 1
            stale_summary["reasons"].append(f"{d}: {r['ttl']['reason']}")
    out["stale_summary"] = stale_summary
    return out


def nowcast_for_district(nc: dict, district: str) -> dict:
    """Join config district -> IMD nowcast record."""
    if nc.get("status") != "ok":
        return {"found": False, "status": nc.get("status"), "reason": nc.get("reason", "")}
    key = district.strip().upper()
    rec = nc["by_district"].get(key)
    if rec is None:
        for d, r in nc["by_district"].items():
            if key in d:
                rec = r
                break
    if rec is None:
        return {"found": False, "status": "no_match", "reason": f"'{district}' not in IMD nowcast"}
    return {"found": True, **rec}


if __name__ == "__main__":
    nc = fetch_nowcast()
    print("nowcast status:", nc["status"], "| date:", nc["date"], "| reason:", nc.get("reason"))
    print("stale summary:", nc["stale_summary"])
    print("Maharashtra districts:", len(nc["by_district"]))
    for d in ["MUMBAI SUBURBAN", "PUNE", "NAGPUR", "RATNAGIRI", "KOLHAPUR", "RAIGAD"]:
        r = nowcast_for_district(nc, d)
        if r.get("found"):
            vw = r.get("validity_window") or "no validity window"
            print(f"  {d:16s} -> {r['color_label']:7s} | cats={r['cat_text']} | {vw} | msg={r['message'][:60]}")
        else:
            print(f"  {d:16s} -> {r['status']}: {r.get('reason')}")

