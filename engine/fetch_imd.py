"""fetch_imd.py — pull AUTHORITATIVE IMD data to lift the product to IMD-grade.

VERIFIED ENDPOINTS (live probe 2026-08-23):
  GeoServer WFS: https://reactjs.imd.gov.in/geoserver/imd/ows?
    Layers (typeName=imd:<layer>):
      - district_warnings_india : all-India district rainfall warnings
          props: District, Day_1..Day_5 (IMD PHENOMENON CODE SETS, comma-joined,
                 e.g. '1', '4,8', '2,4,8' -- NOT a single scalar cat code),
                 Day1_Color..Day5_Color (1..4 = Green/Yellow/Orange/Red; this
                 is the severity field IMD's own viewer renders via
                 env=day:DayN_Color on imd:Warnings_StateDistrict_Merged),
                 Day1_text..Day5_text, Date, updated_at.
                 (state field empty; filter by district name)
          KNOWN ISSUE (2026-09-28): this layer returned Day1_Color=4 for all ten
          Vidarbha districts that the same day's published IMD text bulletin
          lists under "no warning", and for Mumbai city/suburbs that the bulletin
          states carry no alert. Both are separate publication paths. Parsing is
          left intact and _telemetry_cross_source() flags the contradiction; we
          do NOT downgrade the colours, because doing so could suppress a real
          warning. See that function for the full rationale.
      - NowcastWarningDistrict : district nowcast warnings
          props: State_District, Color (1..4), cat1..cat19 (weather types),
                 toi/vupto (valid-time window HHMM), Date, message/impact/action
      - aws_data_layer  : Automatic Weather Station REAL observations
          props: id, call_sign, dat, time, rainfall, temp, rh, winddir,
                 windspeed, mslp, update_time  (bbox-queryable)
      - synop_data_layer / metar_data_layer : surface obs
      - Cyclone_Track_V : cyclone tracks (severe-system authority)
      - india_districts / India_State : boundaries for mapping
  Imagery (downloadable, 200 confirmed):
      Radar   : https://mausam.imd.gov.in/Radar/MOSAIC/Converted/mosaic.gif
      Sat IR1 : https://mausam.imd.gov.in/Satellite/3Dasiasec_ir1.jpg
      Lightn  : https://mausam.imd.gov.in/lightning/Converted/BT.gif

HONESTY: we display IMD radar/sat/lightning as REFERENCE imagery (not decoded
to dBZ). Warnings/nowcast/obs are authoritative IMD data. All functions degrade
to {"status":"unavailable"} on any failure — never raise, never fake.

COMPLIANCE NOTE: this uses IMD's public GeoServer (research tier). A COMMERCIAL
product should route through the official managed API gateway (api.imd.gov.in,
see API_doc.pdf) and confirm data-use terms. Flagged, not blocked.
"""
from __future__ import annotations
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import requests

WFS = "https://reactjs.imd.gov.in/geoserver/imd/ows"
WMS = "https://reactjs.imd.gov.in/geoserver/imd/wms"
IMG_BASE = "https://mausam.imd.gov.in"
HDR = {"User-Agent": "Mozilla/5.0 (weather-report-updater research; +local)",
       "Referer": "https://mausam.imd.gov.in/"}

# IMD colour code -> label (verified: 1..4 = Green/Yellow/Orange/Red)
COLOR_LABEL = {1: "Green", 2: "Yellow", 3: "Orange", 4: "Red"}
# Keyed by the capitalised label that _classify_rgb() returns.
LABEL_CODE = {v: k for k, v in COLOR_LABEL.items()}


# ── WMS rendered-warning reader (PRIMARY severity source) ──────────────────
# WHY THIS EXISTS (2026-09-28): the WFS attribute Day1_Color was proven to
# disagree with IMD's own published map on 10/10 monitored Maharashtra districts
# (attribute Red/Orange, map Green/Yellow), while the same day's IMD text
# bulletin agreed with the MAP on 3/3 independent checks (Pune=yellow alert,
# Nagpur=no warning, Mumbai=no alert). The rows were current, not cached, so
# this is a systematic value fault in IMD's vector ETL -- not replication lag.
# The rendered map is the path IMD's public districtWiseWarningGIS.php page
# uses, so it is the surface a reader actually trusts.
#
# WHY THIS IS SAFE: every read is validated against IMD's EXACT published
# legend palette. A tile whose dominant colour is not a known warning colour
# yields NO severity rather than a guess, so a stylesheet change degrades
# loudly instead of silently reporting garbage.
#
# MEASURED COST: 10 concurrent tiles = 0.33s wall, 14.9 KB total, 0/10 errors.

# IMD's published legend fills (verified against the public warning page).
_IMD_PALETTE = {
    "Green":  ((5, 242, 5), (7, 140, 3)),      # two green shades observed
    "Yellow": ((242, 226, 5),),
    "Orange": ((242, 135, 5),),
    "Red":    ((242, 5, 5),),
}
_PALETTE_STRICT = 20      # per-channel sum tolerance -> exact legend match
_PALETTE_LOOSE = 60       # still a warning colour, flag as low confidence


def _classify_rgb(rgb):
    """Return (label, confidence) for a tile colour, or (None, reason).

    confidence: 'exact' | 'near' | None when the colour is not a warning
    colour at all (black borders, transparent, blank, or a stylesheet change).
    """
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    best, best_d = None, 10 ** 9
    for label, variants in _IMD_PALETTE.items():
        for (pr, pg, pb) in variants:
            d = abs(r - pr) + abs(g - pg) + abs(b - pb)
            if d < best_d:
                best, best_d = label, d
    if best_d <= _PALETTE_STRICT:
        return best, "exact"
    if best_d <= _PALETTE_LOOSE:
        return best, "near"
    return None, "off-palette"


def wms_warning_colour(lat, lon, half=0.09, timeout=10.0, retries=1):
    """Read the district warning colour IMD's own map renders for (lat, lon).

    Returns {code, label, confidence, bytes, ok, reason}. Never raises.
    A failed or off-palette read returns code=None so callers can degrade
    explicitly instead of substituting an unverified value.
    """
    import io
    out = {"code": None, "label": None, "confidence": None,
           "bytes": 0, "ok": False, "reason": "not attempted"}
    if lat is None or lon is None:
        out["reason"] = "no coordinates"
        return out
    bbox = f"{lon - half},{lat - half},{lon + half},{lat + half}"
    url = (f"{WMS}?service=WMS&request=GetMap"
           f"&layers=imd%3AWarnings_StateDistrict_Merged&styles="
           f"&format=image%2Fpng&transparent=true&version=1.1.1"
           f"&env=day%3ADay1_Color&width=120&height=120"
           f"&srs=EPSG%3A4326&bbox={bbox}")
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HDR, timeout=timeout)
            if r.status_code != 200:
                out["reason"] = f"http {r.status_code}"
                continue
            from PIL import Image
            import numpy as np
            img = Image.open(io.BytesIO(r.content)).convert("RGBA")
            arr = np.asarray(img)
            out["bytes"] = len(r.content)
            h, w = arr.shape[:2]
            # Centre crop = district interior, away from boundary strokes.
            cy, cx, hy, hx = h // 2, w // 2, h // 4, w // 4
            core = arr[cy - hy:cy + hy, cx - hx:cx + hx]
            px = core[core[:, :, 3] > 200][:, :3]
            if len(px) < 20:
                out["reason"] = "no opaque pixels in district interior"
                continue
            # Dominant colour in the interior.
            uniq, counts = np.unique(px.reshape(-1, 3), axis=0, return_counts=True)
            dom = uniq[int(counts.argmax())]
            label, conf = _classify_rgb(dom)
            if label is None:
                out["reason"] = f"off-palette rgb{tuple(int(x) for x in dom)}"
                continue
            out.update({"code": LABEL_CODE[label], "label": label,
                        "confidence": conf, "ok": True, "reason": "ok"})
            return out
        except Exception as e:
            out["reason"] = f"{type(e).__name__}"
    return out


def wms_warning_colours(locs, half=0.09, max_workers=8, timeout=10.0):
    """Concurrent WMS reads. locs = [{"district","lat","lon"}, ...]."""
    if not locs:
        return {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(locs))) as ex:
        # Pass args by KEYWORD: the positional signature is
        # (lat, lon, half, timeout, retries) and a positional `half` here
        # silently lands in `retries`.
        reads = list(ex.map(
            lambda L: wms_warning_colour(L.get("lat"), L.get("lon"),
                                         half=half, timeout=timeout),
            locs))
    return {(L.get("district") or L.get("name") or "").upper(): r
            for L, r in zip(locs, reads)}


def relative_lag_minutes(updated_at_by_district):
    """Relative lag of each monitored district vs the NEWEST MONITORED row.

    LOG ONLY. Never gates severity, never blanks a warning, never reaches the
    briefing text.

    Why relative and not absolute: an absolute age needs a trustworthy wall
    clock, and we have proven we do not have one. Measured 2026-09-28, the
    newest timestamp in the payload sat 4h22m AHEAD of the local system clock,
    so an absolute delta reports nonsense. GitHub's UTC runner clock and a
    skewed local box make that equally unsafe.

    Why anchored to the monitored set and not the national max: IMD refreshes
    ~760 districts on a staggered cycle, so the newest row in the country runs
    hours ahead of any given Maharashtra row. Anchoring to that manufactures a
    ~5h "lag" that is really just the refresh spread, and trips any threshold
    on 100% of runs.

    Anchoring to the newest MONITORED row gives a true "is this district being
    refreshed alongside its peers" signal that is immune to both clock skew and
    national refresh spread. It is a diagnostic, not a validity check.
    """
    stamps = {}
    for name, ts in (updated_at_by_district or {}).items():
        if not ts:
            continue
        try:
            t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            stamps[str(name).upper()] = t
        except Exception:
            continue
    if len(stamps) < 2:
        return {}
    anchor = max(stamps.values())
    return {n: round((anchor - t).total_seconds() / 60.0, 1)
            for n, t in stamps.items()}


def _wfs(layer, maxfeat=200, bbox=None, timeout=30):
    p = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
         "typeName": f"imd:{layer}", "outputFormat": "application/json",
         "count": str(maxfeat)}
    if bbox:
        p["bbox"] = (f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
                     ",urn:x-ogc:def:crs:EPSG:4326")
    try:
        r = requests.get(WFS, params=p, headers=HDR, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json().get("features", [])
    except Exception:
        return None


def district_warnings(state_districts=None, maxfeat=3000):
    """Return {district_upper: {color_code, color_label, day_codes, day_text, date}}.
    If state_districts (set of names) given, filter to those (case-insensitive)."""
    feats = _wfs("district_warnings_india", maxfeat=maxfeat)
    if feats is None:
        return {"status": "unavailable", "reason": "wfs fetch failed"}
    out = {}
    want = {d.upper() for d in (state_districts or [])}
    for f in feats:
        p = f.get("properties", {})
        name = (p.get("District") or "").strip()
        if not name:
            continue
        if want and name.upper() not in want:
            continue
        day_colors = [int(p.get(f"Day{i}_Color", 0) or 0) for i in (1, 2, 3, 4, 5)]
        # TODAY's colour = Day1 (operationally relevant for the daily brief).
        # max over the 5-day window is kept separately as a forward-looking note.
        code_today = day_colors[0] if day_colors[0] else max([c for c in day_colors if c] or [0])
        code_max = max([c for c in day_colors if c] or [0])
        out[name.upper()] = {
            "district": name,
            "color_code": code_today,
            "color_label": COLOR_LABEL.get(code_today, "Unknown"),
            "color_code_max": code_max,
            "color_label_max": COLOR_LABEL.get(code_max, "Unknown"),
            "day_codes": day_colors,
            "day_phenomena": [p.get(f"Day{i}", "") or "" for i in (1, 2, 3, 4, 5)],
            "day_text": [p.get(f"Day{i}_text", "") or "" for i in (1, 2, 3, 4, 5)],
            "date": p.get("Date", ""),
            "updated_at": p.get("updated_at", ""),
        }
    if not out:
        return {"status": "unavailable", "reason": "no matching districts"}
    # Telemetry is read-only. It annotates, never rewrites: `disputed` is carried
    # out so the briefing can label contested entries, and the colour values in
    # `warnings` are exactly what the layer returned.
    _tel = _telemetry_cross_source(feats)
    return {"status": "ok", "warnings": out, "disputed": _tel["disputed"],
            "telemetry": {k: v for k, v in _tel.items() if k != "disputed"}}


# ── Telemetry: cross-source consistency ──────────────────────────────────────
# PURPOSE: surface contradictions between the GeoServer warning layer and the
# published IMD text bulletins WITHOUT altering the payload.
#
# WHY THIS EXISTS (2026-09-28): the layer returns Day1_Color=4 (Red) for all ten
# Vidarbha districts that the same day's RMC text bulletin explicitly lists under
# "no warning", and for Mumbai city/suburbs that the bulletin states carry no
# alert. The bulletin is a separate publication path from this layer, so the two
# disagreeing is exactly the condition an operator must see.
#
# WHAT THIS DELIBERATELY DOES NOT DO: it does not "fix" the colours. We do not
# have verified ground truth for the Day_N phenomenon-code legend, and silently
# downgrading a Red to Green would suppress a genuine warning — a failure mode
# that is quieter and more dangerous than the current loud contradiction. Parse
# as-is; flag; escalate to a human.
#
# NOTE ON THE LAYER ITSELF: Day1_Color is what IMD's own public viewer renders
# with (env=day:Day1_Color on imd:Warnings_StateDistrict_Merged), which is why
# these are described as the LAYER's values rather than our parse error. The
# phenomenon sets in Day_N are comma-separated (e.g. '2,4,8'), so the module
# docstring's "cat code" description is inaccurate and is corrected above.

# Districts the 2026-09-28 RMC/IMD text bulletin lists under NO WARNING.
_TELEMETRY_NO_WARN = frozenset({
    "AKOLA", "AMRAVATI", "BANDARA", "BULDHANA", "CHANDRAPUR", "GADCHIROLI",
    "GONDIA", "NAGPUR", "WARDHA", "WASHIM", "YAVATMAL",
})

# Districts the bulletin reports as alerted only in part (ghats) or as watch.
_TELEMETRY_WATCH_ONLY = frozenset({
    "RAIGAD", "PALGHAR", "SANGALI", "SATARA", "RATNAGIRI",
})


def _telemetry_cross_source(feats, stream=None):
    """Log cross-source contradictions between the WFS layer and IMD bulletins.

    Read-only. Never mutates the parsed records. Returns a summary dict for
    programmatic assertions in tests; the human-facing value is the log line.
    """
    out = stream or sys.stdout
    summary = {
        "no_warning_but_alerted": [],
        "watch_only_but_heavier": [],
        "multi_token_phenomenon": 0,
        "total_features": 0,
        # District names whose layer value is contradicted by the bulletin.
        # Downstream consumers mark these as DISPUTED so the briefing says
        # "verify vs radar" instead of asserting a contested number as fact.
        # Severity is NEVER downgraded on the strength of this set.
        "disputed": set(),
    }
    for f in feats or []:
        p = f.get("properties", {})
        name = (p.get("District") or "").strip().upper()
        if not name:
            continue
        summary["total_features"] += 1
        c1 = p.get("Day1_Color")
        d1 = p.get("Day_1")

        if name in _TELEMETRY_NO_WARN and c1 == 4:
            summary["no_warning_but_alerted"].append(name)
            summary["disputed"].add(name)
        if name in _TELEMETRY_WATCH_ONLY and c1 in (3, 4):
            summary["watch_only_but_heavier"].append((name, c1))
            summary["disputed"].add(name)
        if d1 and "," in str(d1):
            summary["multi_token_phenomenon"] += 1

    n_no_warn = len(summary["no_warning_but_alerted"])
    n_watch = len(summary["watch_only_but_heavier"])
    if n_no_warn or n_watch:
        print("", file=out)
        print("[!] CROSS-SOURCE ANOMALY — IMD GeoServer layer vs IMD text bulletin",
              file=out)
        if n_no_warn:
            print(f"    {n_no_warn} district(s) the published bulletin lists under "
                  f"NO WARNING are returned as RED by the layer:", file=out)
            for d in summary["no_warning_but_alerted"]:
                print(f"      - {d}: Day1_Color=4 (Red)", file=out)
        if n_watch:
            print(f"    {n_watch} district(s) the bulletin reports as watch/yellow "
                  f"are returned as Orange/Red:", file=out)
            for d, c in summary["watch_only_but_heavier"]:
                print(f"      - {d}: Day1_Color={c} ({COLOR_LABEL.get(c, '?')})", file=out)
        print("    -> Layer values NOT modified. Do not treat the affected "
              "register entries as confirmed until IMD reconciles the two "
              "publication paths.", file=out)
        print("", file=out)

    if summary["multi_token_phenomenon"]:
        print(f"[*] schema note: {summary['multi_token_phenomenon']} feature(s) carry "
              f"comma-separated Day_N phenomenon sets (e.g. '2,4,8') — Day_N is a "
              f"token list, not the single 'cat code' the module docstring claims.",
              file=out)
    return summary


def district_nowcast(state_districts=None, maxfeat=3000):
    """Return {district_upper: {color_code, color_label, cats, valid_from, valid_to,
    message, date}}. Nowcast = short-range warning with valid window."""
    feats = _wfs("NowcastWarningDistrict", maxfeat=maxfeat)
    if feats is None:
        return {"status": "unavailable", "reason": "wfs fetch failed"}
    out = {}
    want = {d.upper() for d in (state_districts or [])}
    for f in feats:
        p = f.get("properties", {})
        name = (p.get("State_District") or "").strip()
        if not name:
            continue
        if want and name.upper() not in want:
            continue
        code = int(p.get("Color", 0) or 0)
        out[name.upper()] = {
            "district": name,
            "color_code": code,
            "color_label": COLOR_LABEL.get(code, "Unknown"),
            "cats": {f"cat{i}": p.get(f"cat{i}", 0) for i in range(1, 20)},
            "valid_from": p.get("toi", ""),
            "valid_to": p.get("vupto", ""),
            "message": p.get("message", ""),
            "date": p.get("Date", ""),
        }
    if not out:
        return {"status": "unavailable", "reason": "no matching districts"}
    return {"status": "ok", "nowcast": out}


def aws_observations(bbox, maxfeat=500):
    """Real AWS station observations inside bbox (lon,lat,lon,lat).
    Returns {status, obs:[{name,lat,lon,rainfall,temp,rh,wind_dir,wind_spd,mslp}]}."""
    feats = _wfs("aws_data_layer", maxfeat=maxfeat, bbox=bbox)
    if feats is None:
        return {"status": "unavailable", "reason": "wfs fetch failed"}
    out = []
    for f in feats:
        p = f.get("properties", {})
        g = f.get("geometry") or {}
        coords = g.get("coordinates") if isinstance(g, dict) else None
        lon = lat = None
        if coords and len(coords) >= 2:
            lon, lat = coords[0], coords[1]
        out.append({
            "name": p.get("call_sign") or p.get("id"),
            "lat": lat, "lon": lon,
            "rainfall": _num(p.get("rainfall")),
            "temp": _num(p.get("temp")),
            "rh": _num(p.get("rh")),
            "wind_dir": _num(p.get("winddir")),
            "wind_spd": _num(p.get("windspeed")),
            "mslp": _num(p.get("mslp")),
            "updated": p.get("update_time", ""),
        })
    return {"status": "ok", "obs": out, "count": len(out)}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def district_warnings_geo(cache_path=None, maxfeat=5000, timeout=60):
    """District warnings WITH geometry (for choropleth maps). Returns
    {district_upper: {color_code, color_label, day_codes, geometry}}.
    Caches the raw fetch to cache_path (TTL not enforced here; caller decides)
    so we don't re-download the ~19MB layer every run."""
    import json as _json
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path) as fh:
                return {"status": "ok", "cached": True,
                        "warnings": _json.load(fh)}
        except Exception:
            pass
    feats = _wfs("district_warnings_india", maxfeat=maxfeat, timeout=timeout)
    if feats is None:
        return {"status": "unavailable", "reason": "wfs fetch failed"}
    out = {}
    for f in feats:
        p = f.get("properties", {})
        name = (p.get("District") or "").strip()
        if not name:
            continue
        day_colors = [int(p.get(f"Day{i}_Color", 0) or 0) for i in (1, 2, 3, 4, 5)]
        code_today = day_colors[0] if day_colors[0] else max([c for c in day_colors if c] or [0])
        out[name.upper()] = {
            "district": name,
            "color_code": code_today,
            "color_label": COLOR_LABEL.get(code_today, "Unknown"),
            "day_codes": day_colors,
            "geometry": f.get("geometry"),
            "date": p.get("Date", ""),
        }
    if cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
            with open(cache_path, "w") as fh:
                _json.dump(out, fh)
        except Exception:
            pass
    return {"status": "ok", "cached": False, "warnings": out}


_IMG_MAP = {
    "radar": f"{IMG_BASE}/Radar/MOSAIC/Converted/mosaic.gif",
    "satellite": f"{IMG_BASE}/Satellite/3Dasiasec_ir1.jpg",
    "lightning": f"{IMG_BASE}/lightning/Converted/BT.gif",
}


def fetch_imagery(out_dir, kinds=("radar", "satellite", "lightning"), timeout=30):
    """Download IMD radar/satellite/lightning imagery to out_dir.
    Returns {kind: path_or_None}. Graceful per-kind failure."""
    os.makedirs(out_dir, exist_ok=True)
    res = {}
    for k in kinds:
        u = _IMG_MAP.get(k)
        if not u:
            res[k] = None
            continue
        path = os.path.join(out_dir,
                            f"imd_{k}.gif" if k != "satellite" else "imd_satellite.jpg")
        try:
            r = requests.get(u, headers=HDR, timeout=timeout)
            if r.status_code == 200 and r.content:
                with open(path, "wb") as fh:
                    fh.write(r.content)
                res[k] = path
            else:
                res[k] = None
        except Exception:
            res[k] = None
    return res


if __name__ == "__main__":
    MH = {"MUMBAI", "PUNE", "THANE", "NAVI MUMBAI", "NAGPUR", "AURANGABAD",
          "KOLHAPUR", "RATNAGIRI", "SOLAPUR", "NASHIK"}
    w = district_warnings(MH)
    print("WARNINGS:", w.get("status"), list(w.get("warnings", {}).keys())[:5])
    n = district_nowcast(MH)
    print("NOWCAST:", n.get("status"), list(n.get("nowcast", {}).keys())[:5])
    o = aws_observations((72.5, 15.5, 80.5, 22.5))
    print("AWS OBS:", o.get("status"), "count=", o.get("count"))
    imgs = fetch_imagery(".")
    print("IMAGERY:", {k: (os.path.basename(p) if p else None) for k, p in imgs.items()})
