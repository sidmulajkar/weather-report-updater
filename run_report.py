"""Orchestrator v2 — resilient, research-grounded, all 5 modules.

Data-source resilience (your "u1+u2 as backup" requirement):
  PRIMARY  = Open-Meteo multi-model fusion (ecmwf/icon/gfs) + 850hPa + CAPE + gusts
  FALLBACK = MET Norway api.met.no (keyless) if a city's primary fetch fails
  GROUND TRUTH backstops:
     U1 = observed district rainfall (verify + detect error)
     U2 = IMD official district nowcast (official colour/severity)
  The report labels which source produced each number and never fakes a failure as all-clear.
"""
from __future__ import annotations
import os, sys, json, datetime as dt
from zoneinfo import ZoneInfo
INDIA = ZoneInfo("Asia/Kolkata")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BASE = HERE  # config/, engine/, output/ all live under weather-updater/

from engine import fetch_openmeteo as om
from engine import fetch_metno
from engine import observed, fetch_imd_nowcast, bias_correct, narrative
from engine import intensity, antecedent, buffer, tides, terrain, radar
import engine.verify as verify
import engine.classify as classify
import engine.maps as maps
import engine.report as report
import engine.severe_systems as severe
import delivery.telegram as telegram

CONFIG_PATH = os.path.join(BASE, "config", "locations.json")
OUT = os.path.join(BASE, "output")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "TEST")
# Dry-run unless: a real token+chat are set AND DRY_RUN env is not explicitly "true".
# This keeps local/test runs safe and lets CI flip to live via secrets.
_env_dry = os.environ.get("DRY_RUN", "").lower()
DRY = bool(_env_dry == "true") or not (TOKEN and CHAT and CHAT != "TEST")


def load_cfg():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def main():
    cfg = load_cfg()
    today = dt.date.today().isoformat()
    locs = cfg["locations"]
    models = cfg["models"]
    window = cfg.get("accum_window_h", 24)

    # Tide table (documented sample + ingestion hook) for Mumbai drainage-lock check
    tide_csv = os.path.join(BASE, "config", "tides_2026.csv")
    tide_tbl = tides.load_tide_table(tide_csv)
    # IMD urban radar feed check (real, live; numeric dBZ not yet ingested)
    radar_status = radar.check_feed()
    print(f"[*] tide table: {len(tide_tbl)} high-tide events loaded | "
          f"radar feed: {'ACTIVE' if radar_status.get('ok') else 'UNAVAILABLE'}")

    print(f"[*] run {today} | {len(locs)} cities | models={models} | window={window}h")

    # U1 + U2 ground truth (independent of the model layer)
    obs_all = observed.fetch_observed()
    nc_all = fetch_imd_nowcast.fetch_nowcast()
    print(f"[*] observed(U1): {obs_all['status']} | IMD nowcast(U2): {nc_all['status']} "
          f"(date {nc_all.get('date')})")

    results = []
    archive_rows = []  # for verify.py
    map_points = []

    # ── Per-city processing (extracted so it can run CONCURRENTLY) ──
    # CRITICAL PERF FIX: the previous serial `for loc in locs` loop was the
    # dominant cause of 9-minute CI runs (10 cities × primary + 8 neighbour
    # fetches, all serial). We run every city in a thread pool; network I/O is
    # the bottleneck, so concurrency collapses wall-time to ~max single-city.
    def process_city(loc):
        name = loc["name"]
        # SAFE DEFAULTS: every variable the result dict references must be
        # defined on ALL branches (primary / MET-Norway fallback / both-failed).
        # Without this, a city that takes the fallback path raises
        # UnboundLocalError on the first var it didn't assign (seen on CI:
        # w850dir). Initialize once, here.
        acc = prob = w10 = wdir = w850d = cape = gust = 0.0
        w850 = w850dir = None
        hourly_precip = []
        src = "open-meteo"
        # --- PRIMARY fetch (Open-Meteo fusion, models fetched in parallel) ---
        pf = om.fetch_location(loc, models, forecast_days=cfg.get("forecast_days", 2))
        if not pf.models:
            # --- FALLBACK (MET Norway) ---
            fb = fetch_metno.fetch(loc["lat"], loc["lon"], hours=window)
            if fb["status"] == "ok":
                acc = fb["precip_sum"]; prob = 100; w10 = fb["wind_kmh"]
                wdir = fb["wind_dir"]; w850 = None; w850d = None; cape = None; gust = fb["wind_gust"]
                src = "MET Norway (fallback)"
                hourly_precip = []  # MET Norway fallback path doesn't expose hourly here
            else:
                acc = prob = w10 = wdir = w850 = w850d = cape = gust = 0.0
                src = "unavailable"
                hourly_precip = []
        else:
            p24 = om.model_24h_precip(pf, window)
            fstats = om.fusion_stats(p24)
            acc = fstats["mean"]
            probs = []
            for h in pf.models.values():
                pp = [x for x in h.get("precipitation_probability", [])[:window] if x is not None]
                if pp: probs.append(max(pp))
            prob = max(probs) if probs else 0
            first = next(iter(pf.models.values()))
            wseg = [x for x in first.get("wind_speed_10m", [])[:window] if x is not None]
            w10 = round(sum(wseg) / len(wseg) * 1.0, 1) if wseg else 0.0
            dseg = [x for x in first.get("wind_direction_10m", [])[:window] if x is not None]
            wdir = round(sum(dseg) / len(dseg), 1) if dseg else 0.0
            w850d = om.fusion_stats(om.model_850hpa_wind(pf, window))["mean"]
            d850 = []
            for h in pf.models.values():
                dd = [x for x in h.get("wind_direction_850hPa", [])[:window] if x is not None]
                if dd: d850.append(sum(dd) / len(dd))
            w850dir = round(sum(d850) / len(d850), 1) if d850 else None
            cape = om.fusion_stats(om.model_cape(pf, window))["mean"]
            gust = om.fusion_stats(om.model_wind_gust(pf, window))["mean"]
            hourly_precip = [x for x in first.get("precipitation", [])[:window] if x is not None]

        # --- U3 bias correction on the accumulation ---
        bc = bias_correct.correct(loc.get("district", name).upper(), acc)
        corrected = bc["corrected_mm"]

        # --- U2 IMD nowcast for this district ---
        nc = fetch_imd_nowcast.nowcast_for_district(nc_all, loc.get("district", name))
        imd_colour = nc.get("color_label") if nc.get("found") else None
        imd_status = nc.get("status", "unavailable")
        imd_payload = {"colour": imd_colour, "status": "ok" if imd_colour else imd_status,
                       "message": nc.get("message", ""), "color": nc.get("color")}

        # --- Step 2: Intensity-Duration (IDF) burst analysis from hourly series ---
        inten = intensity.analyze(hourly_precip, window) if hourly_precip else {}
        peak_idx = inten.get("peak_hour_index")
        peak_dt = (dt.datetime.now(INDIA) + dt.timedelta(hours=peak_idx or 0)) if peak_idx else None

        # --- Step 3: Antecedent 7-day saturation (soil runoff) from archive ---
        district_u = loc.get("district", name).upper()
        hist = verify.observed_history(district_u, 7)
        ant_7d = sum((h.get("observed_mm") or 0.0) for h in hist)

        # --- Step 4: Spatial buffer 3x3 proximity threat scan ---
        # NOTE: the live neighbour fetch was REMOVED from the per-city hot path.
        # Proximity is now computed in a SEPARATE phase (compute_proximity_all)
        # by reusing the forecasts we already fetched for other cities, so we
        # fire ZERO extra API calls. Placeholder filled after the fetch phase.
        prox = {"flagged": False, "count": 0, "threats": [],
                "vector": None, "note": "pending grid pass"}

        # --- Step 5: Terrain / urban funneling (MODELED, not measured) ---
        terr = terrain.adjust(district_u, corrected, wdir, w10)

        # --- Step 6: Tidal coincidence (drainage lock) vs peak rain hour ---
        COASTAL = {"MUMBAI", "MUMBAI SUBURBAN", "THANE", "NAVI MUMBAI", "RAIGAD",
                   "RATNAGIRI", "PALGHAR", "KONKAN", "MUMBAI CITY", "MUMBAI SUBURBAN"}
        if district_u in COASTAL:
            tidal = tides.tidal_factor(tide_tbl, peak_dt) if peak_dt else {"overlap": False}
        else:
            tidal = {"overlap": False, "note": "inland — no coastal drainage lock"}

        # --- Radar: live IMD feed status; numeric dBZ pending ingestion ---
        radar_res = radar.detect(loc["lat"], loc["lon"], [])

        # NOTE: classify is DEFERRED to the driver (phase 3), after the
        # proximity grid is computed from already-fetched cities. This avoids
        # a fragile re-classify that dropped the tidal escalation.
        obs_d = observed.observed_for_district(obs_all, loc.get("district", name))
        inputs = {
            "name": name, "type": loc.get("type"), "district": district_u,
            "raw_mm": round(acc, 2), "corrected_mm": corrected,
            "prob_max": round(prob, 1), "wind_kmh": w10, "wind_dir": wdir,
            "wind850_kmh": w850d, "wind850_dir": w850dir, "cape": cape, "gust": gust,
            "imd": imd_payload, "observed": obs_d, "source": src,
            "bias_applied": bc["applied"],
            "intensity": inten, "antecedent_7d_mm": round(ant_7d, 1),
            "terrain": terr, "tidal": tidal, "radar": radar_res,
            "spread": fstats.get("spread", 0) if pf.models else 0,
            "peak_rain_dt": peak_dt.isoformat() if peak_dt else None,
            # proximity filled in phase 2
            "proximity": {"flagged": False, "count": 0, "threats": [],
                          "vector": None, "note": "pending grid pass"},
        }
        archive_row = {"district": loc.get("district", name).upper(),
                       "forecast_mm": corrected, "observed_mm": None, "source": src}
        print(f"  - {name}: fetched raw={acc:.1f} corr={corrected:.1f}mm "
              f"prob={prob:.0f}% | IMD={imd_colour or 'n/a'} | src={src}")
        return inputs, archive_row

    from concurrent.futures import ThreadPoolExecutor
    results, archive_rows = [], []
    # Polite concurrency: the global rate limiter in fetch_openmeteo already
    # serializes the actual network calls across threads, so a small worker
    # count is enough. 4 keeps thread overhead low while still parallelizing
    # any non-network work. (10 caused a thundering herd that Open-Meteo
    # throttled on CI's shared egress IP -> 8+ min hangs.)
    with ThreadPoolExecutor(max_workers=min(len(locs), 4)) as ex:
        for res, arow in ex.map(process_city, locs):
            results.append(res)
            archive_rows.append(arow)

    # ── Phase 2: proximity (reuse already-fetched city grid, ZERO extra API) ──
    # Each asset's 3x3 neighbour ring is matched against the OTHER assets we
    # already forecast (Mumbai/Thane/Navi Mumbai sit ~25 km apart = one ring
    # step), so no new network calls are needed.
    city_points = [{
        "lat": l["lat"], "lon": l["lon"], "name": l["name"],
        "max1h_mm": r["intensity"].get("max1h_mm", 0.0) if r.get("intensity") else 0.0,
        "sum24h_mm": r.get("corrected_mm", 0.0),
    } for l, r in zip(locs, results)]

    # ── Phase 3: single classify per city with proximity + all layers ──
    # Classify ONCE here (after proximity is known) so tidal/IDF/IMD/terrain
    # all stay intact — no fragile re-classify that drops escalations.
    for l, r in zip(locs, results):
        prox = buffer.scan_from_grid(l["lat"], l["lon"], city_points)
        r["proximity"] = prox
        rk = classify.classify(
            r["corrected_mm"], r["prob_max"],
            r["spread"], r["imd"] if r["imd"].get("colour") else None,
            intensity=r["intensity"], antecedent_7d=r["antecedent_7d_mm"],
            proximity=prox, tidal=r["tidal"], radar=r["radar"], terrain=r["terrain"])
        r["risk"] = rk
        print(f"  - {l['name']}: {rk.level} | corr={r['corrected_mm']:.1f}mm "
              f"prob={r['prob_max']:.0f}% | IMD={r['imd'].get('colour') or 'n/a'} | "
              f"prox={'FLAG' if prox['flagged'] else 'clear'}")

    # verify: archive forecast, then backfill observed for TODAY (so tomorrow we pair)
    verify.record_run(today, archive_rows)
    # backfill observed for today's run from U1 (observed is for yesterday's date normally;
    # we store observed against its own date and pair forecast(today)->observed(later))
    obs_by_dist = {d.upper(): r.get("actual_mm") for d, r in
                   [(k, observed.observed_for_district(obs_all, k)) for k in
                    [l.get("district", l["name"]) for l in locs]]}
    verify.fill_observed(today, {k.upper(): v for k, v in obs_by_dist.items() if v is not None})

    # Module G — severe-system (cyclone / low-pressure / severe-wind) 3-7 day outlook
    # (computed early because the narrative binds to it)
    sev = severe.build_outlook()
    print(f"[*] severe outlook: official_watch={sev['official']['watch']} | "
          f"model alerts={len(sev['alerts'])} | signal={sev['has_signal']}")
    # MSLP grid for the severe map (reuse the basin grid fetch)
    mslp_grid = {}
    for basin, grid in severe.BASIN_GRID.items():
        gs = {}
        for (la, lo) in grid:
            s = severe._fetch_series(la, lo, 10)
            if s:
                gs[(la, lo)] = s
        mslp_grid[basin] = gs

    # narrative (Module E) — evidence-bound, no fabrication
    narr_bundle = narrative.build_evidence(results, severe=sev, imd_nowcast=nc_all)
    narr = narrative.generate(narr_bundle)

    # skill (U4) — honest about calibration size
    skill = verify.compute_skill()
    bias_mm = verify.mean_bias_mm()

    # maps (use corrected accum + IMD colour for nowcast)
    map_points = [{"name": r["name"], "lat": next(l["lat"] for l in locs if l["name"] == r["name"]),
                   "lon": next(l["lon"] for l in locs if l["name"] == r["name"]),
                   "accum_24h": r["corrected_mm"], "wind_speed": r["wind_kmh"],
                   "wind_dir": r["wind_dir"], "wind850_speed": r["wind850_kmh"] or 0,
                   "wind850_dir": r["wind850_dir"] or 0,
                   "imd_color": r["imd"].get("color") or 1,
                   "nowcast_label": (r["imd"].get("color_label") or "")} for r in results]

    os.makedirs(OUT, exist_ok=True)
    p_rain = maps.rainfall_map(map_points, model_label="multi-model fusion (bias-corrected)",
                               out_path=os.path.join(OUT, "rainfall_map.png"))
    p_wind = maps.wind_map(map_points, model_label="multi-model fusion",
                           out_path=os.path.join(OUT, "wind_map.png"))
    p_w850 = maps.wind850_map(map_points, model_label="multi-model fusion",
                              out_path=os.path.join(OUT, "wind850_map.png"))
    p_nc = maps.nowcast_map(map_points, model_label="IMD official",
                             out_path=os.path.join(OUT, "nowcast_map.png"))
    p_sev = maps.severe_map(mslp_grid, sev["alerts"],
                            out_path=os.path.join(OUT, "severe_map.png"))
    print(f"[*] maps -> {p_rain}, {p_wind}, {p_w850}, {p_nc}, {p_sev}")

    # report
    text = report.compose(results, cfg.get("region_focus", "Maharashtra"),
                          narrative=narr, skill=skill, bias_mm=bias_mm,
                          observed_status=obs_all["status"], nowcast_status=nc_all["status"],
                          run_date=today, severe=sev, radar_status=radar_status)
    with open(os.path.join(OUT, "report.md"), "w") as f:
        f.write(text + "\n")
    print(f"[*] report saved -> {os.path.join(OUT, 'report.md')}")

    # deliver
    telegram.send_message(CHAT, text, dry_run=DRY)
    telegram.send_photo(CHAT, p_rain, caption="Rainfall forecast (24h, bias-corrected)", dry_run=DRY)
    telegram.send_photo(CHAT, p_wind, caption="Wind field (10 m)", dry_run=DRY)
    telegram.send_photo(CHAT, p_w850, caption="Upper-air wind (850 hPa)", dry_run=DRY)
    telegram.send_photo(CHAT, p_nc, caption="IMD district nowcast (next 3h)", dry_run=DRY)
    telegram.send_photo(CHAT, p_sev, caption="Severe-system outlook (3-7d, AS+BoB)", dry_run=DRY)
    print("[*] done.")


def _median(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


if __name__ == "__main__":
    main()
