"""fetch_imd.py — pull AUTHORITATIVE IMD data to lift the product to IMD-grade.

VERIFIED ENDPOINTS (live probe 2026-08-23):
  GeoServer WFS: https://reactjs.imd.gov.in/geoserver/imd/ows?
    Layers (typeName=imd:<layer>):
      - district_warnings_india : all-India district rainfall warnings
          props: District, Day_1..Day_5 (cat code), Day1_Color..Day5_Color
                 (1..4 = Green/Yellow/Orange/Red), Day1_text..Day5_text,
                 Date, updated_at. (state field empty; filter by district name)
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
import requests

WFS = "https://reactjs.imd.gov.in/geoserver/imd/ows"
IMG_BASE = "https://mausam.imd.gov.in"
HDR = {"User-Agent": "Mozilla/5.0 (weather-report-updater research; +local)",
       "Referer": "https://mausam.imd.gov.in/"}

# IMD colour code -> label (verified: 1..4 = Green/Yellow/Orange/Red)
COLOR_LABEL = {1: "Green", 2: "Yellow", 3: "Orange", 4: "Red"}


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
            "day_text": [p.get(f"Day{i}_text", "") or "" for i in (1, 2, 3, 4, 5)],
            "date": p.get("Date", ""),
            "updated_at": p.get("updated_at", ""),
        }
    if not out:
        return {"status": "unavailable", "reason": "no matching districts"}
    return {"status": "ok", "warnings": out}


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
