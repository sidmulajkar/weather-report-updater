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
from engine import observed, fetch_imd_nowcast, fetch_imd, bias_correct, narrative
from engine import intensity, antecedent, buffer, tides, terrain, radar
import engine.sanity as sanity
import engine.convective as convective
from delivery import telegram
import engine.verify as verify
import engine.classify as classify
import engine.maps as maps
import engine.report as report
import engine.severe_systems as severe
import engine.forecast_cache as fcache
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
    print(f"[*] nowcast stale summary: {nc_all.get('stale_summary', {})}")

    # ── IMD AUTHORITATIVE DATA (lift to IMD-grade; research-tier GeoServer) ──
    # District rainfall warnings + nowcast (authoritative severity) + real AWS
    # observations (ground truth) + radar/sat/lightning imagery (nowcasting ref).
    # All degrade gracefully to "unavailable" — never fake, never raise.
    MH_DISTRICTS = {l.get("district", l["name"]).upper() for l in locs}
    MH_BBOX = (72.0, 15.0, 81.0, 23.0)  # Maharashtra + surrounds
    try:
        imd_warn = fetch_imd.district_warnings(MH_DISTRICTS)
        imd_now = fetch_imd.district_nowcast(MH_DISTRICTS)
        imd_obs = fetch_imd.aws_observations(MH_BBOX)
        imd_img = fetch_imd.fetch_imagery(OUT)
        # Extract ALL frames of the radar GIF for an animated 2nd composite
        # (left = static MMR zoom, right = cycling radar frames → animated GIF).
        imd_radar_frames = []
        if imd_img.get("radar") and os.path.exists(imd_img["radar"]):
            try:
                from PIL import Image
                gif = Image.open(imd_img["radar"])
                n = 0
                frame_dir = os.path.join(OUT, "radar_frames")
                os.makedirs(frame_dir, exist_ok=True)
                while True:
                    gif.seek(n)
                    frame_path = os.path.join(frame_dir, f"frame_{n:03d}.png")
                    gif.convert("RGB").save(frame_path)
                    imd_radar_frames.append(frame_path)
                    n += 1
                    try:
                        gif.seek(n)
                    except EOFError:
                        break
                print(f"[*] radar frames: {n} extracted")
            except Exception as e:
                print(f"[!] radar frame extract failed: {e}")
                imd_radar_frames = []
        imd_geo = fetch_imd.district_warnings_geo(
            cache_path=os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "cache", "imd_district_polygons.json"
            ))
        print(f"[*] IMD warnings: {imd_warn['status']} | nowcast: {imd_now['status']} "
              f"| AWS obs: {imd_obs.get('status')} ({imd_obs.get('count','?')} stns) "
              f"| imagery: {[k for k,v in imd_img.items() if v]} "
              f"| district polygons: {imd_geo.get('status')}")
    except Exception as e:
        imd_warn = {"status": "unavailable", "reason": str(e)}
        imd_now = {"status": "unavailable"}
        imd_obs = {"status": "unavailable"}
        imd_img = {}
        imd_geo = {"status": "unavailable"}
        imd_radar_frames = []
        print(f"[!] IMD authoritative fetch failed (non-fatal): {e}")


    results = []
    archive_rows = []  # for verify.py
    map_points = []

    # ── Per-city processing (extracted so it can run CONCURRENTLY) ──
    # DAILY CACHE (no-key): reuse TTL-fresh per-city model payloads so we
    # don't re-hit Open-Meteo's anonymous daily cap on every run.
    _city_cache_all = (fcache.load() or {}).get("cities", {})
    _fresh_city_models = {}  # name -> pf.models, collected when live-fetched
    def _fetch_city(loc):
        name = loc["name"]
        cached_m = _city_cache_all.get(name)
        if cached_m:  # rebuild a PointForecast from cached model payloads
            pf = om.PointForecast(name=loc["name"], lat=loc["lat"], lon=loc["lon"],
                                  state=loc.get("state", ""), ftype=loc.get("type", ""))
            pf.models = cached_m
            print(f"  - {name}: CACHED city payload (age "
                  f"{fcache.age_hours((fcache.load() or {}).get('fetched_at_epoch', 0)):.1f}h)")
            return pf
        pf = om.fetch_location(loc, models, forecast_days=cfg.get("forecast_days", 2))
        _fresh_city_models[name] = pf.models
        return pf

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
        pf = _fetch_city(loc)
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
            fstats = om.fusion_stats(p24, weights=om.ENSEMBLE_WEIGHTS)
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
            w850d = om.fusion_stats(om.model_850hpa_wind(pf, window), weights=om.ENSEMBLE_WEIGHTS)["mean"]
            d850 = []
            for h in pf.models.values():
                dd = [x for x in h.get("wind_direction_850hPa", [])[:window] if x is not None]
                if dd: d850.append(sum(dd) / len(dd))
            w850dir = round(sum(d850) / len(d850), 1) if d850 else None
            cape = om.fusion_stats(om.model_cape(pf, window), weights=om.ENSEMBLE_WEIGHTS)["mean"]
            gust = om.fusion_stats(om.model_wind_gust(pf, window), weights=om.ENSEMBLE_WEIGHTS)["mean"]
            hourly_precip = [x for x in first.get("precipitation", [])[:window] if x is not None]

        # --- Convective proxy gate: radiosonde fallback when CAPE is missing ---
        convective_flag = None
        if not cape or cape < 2500:
            try:
                snd = convective.fetch_sounding()
                proxy = convective.convective_proxy_index(
                    rh850=snd.get("rh850"),
                    rh700=snd.get("rh700"),
                    tpw_kgm2=snd.get("tpw_kgm2"),
                    conv_score=snd.get("conv_score"),
                )
                convective_flag = proxy
            except Exception as e:
                convective_flag = {"flag": None, "status": "DATA_UNAVAILABLE", "reason": f"radiosonde gate failed: {e}"}

        # --- U3 bias correction on the accumulation ---
        bc = bias_correct.correct(loc.get("district", name).upper(), acc)
        corrected = bc["corrected_mm"]

        # --- U2 IMD nowcast for this district ---
        district_u = loc.get("district", name).upper()
        nc = fetch_imd_nowcast.nowcast_for_district(nc_all, loc.get("district", name))
        # Prefer AUTHORITATIVE IMD district rainfall WARNING colour (district_warnings_india)
        # over the nowcast text colour. Both are IMD; the warning layer is the
        # official colour-coded severity. Graceful if unavailable.
        imd_colour = None
        imd_code = None
        if imd_warn.get("status") == "ok":
            wd = imd_warn.get("warnings", {}).get(district_u)
            if wd and wd.get("color_label"):
                imd_colour = wd["color_label"]
                imd_code = wd.get("color_code")
        if not imd_colour:
            imd_colour = nc.get("color_label") if nc.get("found") else None
            imd_code = nc.get("color") if nc.get("found") else None
        imd_status = nc.get("status", "unavailable")
        imd_payload = {"colour": imd_colour, "color_code": imd_code,
                       "status": "ok" if imd_colour else imd_status,
                       "cats": (nc.get("cats") if nc.get("found") else None),
                       "message": nc.get("message", ""), "color": nc.get("color")}

        # --- Step 2: Intensity-Duration (IDF) burst analysis from hourly series ---
        inten = intensity.analyze(hourly_precip, window) if hourly_precip else {}
        peak_idx = inten.get("peak_hour_index")
        peak_dt = (dt.datetime.now(INDIA) + dt.timedelta(hours=peak_idx or 0)) if peak_idx else None

        # --- Step 3: Antecedent 7-day saturation (soil runoff) from archive ---
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
        terr = terrain.adjust(district_u, corrected, wdir, w10, w850d)

        # --- Step 6: Tidal coincidence (drainage lock) vs peak rain hour ---
        COASTAL = {"MUMBAI", "MUMBAI SUBURBAN", "THANE", "NAVI MUMBAI", "RAIGAD",
                   "RATNAGIRI", "PALGHAR", "KONKAN", "MUMBAI CITY", "MUMBAI SUBURBAN"}
        if district_u in COASTAL:
            tidal = tides.tidal_factor(tide_tbl, peak_dt) if peak_dt else {"overlap": False}
            # lock WINDOW (onset/clear timestamps) for operational planning
            if peak_dt:
                lw = tides.lock_window(tide_tbl, peak_dt)
                tidal["lock_window"] = lw
        else:
            tidal = {"overlap": False, "note": "inland — no coastal drainage lock"}

        # --- Radar: live IMD feed status; numeric dBZ pending ingestion ---
        radar_res = radar.detect(loc["lat"], loc["lon"], [])

        # NOTE: classify is DEFERRED to the driver (phase 3), after the
        # proximity grid is computed from already-fetched cities. This avoids
        # a fragile re-classify that dropped the tidal escalation.
        obs_d = observed.observed_for_district(obs_all, loc.get("district", name))

        # --- AWS zero-rain anomaly cross-check ---
        # If an AWS station reports >15 mm/hr but surrounding radar cells show
        # near-zero reflectivity, flag it as anomalous and exclude from any
        # future interpolation. Radar dBZ ingestion is not yet wired, so this
        # gate stays DATA_UNAVAILABLE until radar cells are supplied.
        aws_anomaly = sanity.zero_rain_anomaly_check(
            aws_points=[
                {
                    "name": loc["name"],
                    "lat": loc["lat"],
                    "lon": loc["lon"],
                    "rain_mmh": (obs_d.get("actual_mm") or 0.0) / 24.0,
                }
            ],
            radar_cells=None,
        )
        inputs = {
            "name": name, "type": loc.get("type"), "district": district_u,
            "raw_mm": round(acc, 2), "corrected_mm": corrected,
            "prob_max": round(prob, 1), "wind_kmh": w10, "wind_dir": wdir,
            "wind850_kmh": w850d, "wind850_dir": w850dir, "cape": cape, "gust": gust,
            "imd": imd_payload, "observed": obs_d, "source": src,
            "bias_applied": bc["applied"],
            "intensity": inten, "antecedent_7d_mm": round(ant_7d, 1),
            "terrain": terr, "tidal": tidal, "radar": radar_res, "aws_anomaly": aws_anomaly,
            "convective": convective_flag,
            "spread": fstats.get("spread", 0) if pf.models else 0,
            "peak_rain_dt": peak_dt.isoformat() if peak_dt else None,
            # proximity filled in phase 2
            "proximity": {"flagged": False, "count": 0, "threats": [],
                          "vector": None, "note": "pending grid pass"},
        }
        archive_row = {"district": verify.alias_district(loc.get("district", name)).upper(),
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
    # Persist freshly-fetched city payloads to the daily cache (so the next
    # run within TTL reuses them and we don't re-hit the anonymous daily cap).
    if _fresh_city_models:
        fcache.update_cities(_fresh_city_models)
        print(f"[*] cached {len(_fresh_city_models)} city payloads for "
              f"{fcache.TTL_SECONDS//3600}h")

    # ── Phase 2: proximity — LIVE 3x3 neighbour scan (full original coverage) ──
    # We restore the original 8-point ring around EVERY asset (not just the
    # other configured cities) so a burst sitting in a GAP between assets is
    # still detected — no analytical downgrade. To keep it fast we fetch only
    # precipitation per neighbour (om.precip_only) concurrently, and the shared
    # thread-safe rate limiter spaces the calls so Open-Meteo never throttles.
    from concurrent.futures import ThreadPoolExecutor as _TPE

    _nb_coords = {}
    for l in locs:
        _nb_coords[l["name"]] = buffer.neighbour_coords(l["lat"], l["lon"])

    def _nb_probe(coord):
        nlat, nlon = coord
        arr = om.precip_only(nlat, nlon, "ecmwf_ifs", cfg.get("forecast_days", 2))
        if not arr:
            return None
        arr = [x for x in arr[:24] if x is not None]
        if not arr:
            return None
        return {"name": f"({nlat},{nlon})",
                "max1h_mm": intensity.max_rolling(arr, 1),
                "sum24h_mm": round(sum(arr), 1)}

    _all_nb = {l["name"]: [] for l in locs}
    with _TPE(max_workers=min(sum(len(c) for c in _nb_coords.values()), 16)) as px:
        futs = {}
        for nm, coords in _nb_coords.items():
            for coord in coords:
                futs[px.submit(_nb_probe, coord)] = nm
        for fut in futs:
            nm = futs[fut]
            try:
                res = fut.result()
            except Exception:
                res = None
            if res:
                _all_nb[nm].append(res)

    # ── Phase 3: single classify per city with proximity + all layers ──
    # Classify ONCE here (after proximity is known) so tidal/IDF/IMD/terrain
    # all stay intact — no fragile re-classify that drops escalations.
    for l, r in zip(locs, results):
        prox = buffer.evaluate_proximity(_all_nb[l["name"]])
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
    obs_by_dist = {verify.alias_district(d).upper(): r.get("actual_mm") for d, r in
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
                               out_path=os.path.join(OUT, "rainfall_map.png"),
                               issued_ist=today, sources="Open-Meteo multi-model fusion")
    p_wind = maps.wind_map(map_points, model_label="multi-model fusion",
                           out_path=os.path.join(OUT, "wind_map.png"),
                           issued_ist=today, sources="Open-Meteo multi-model fusion")
    p_w850 = maps.wind850_map(map_points, model_label="multi-model fusion",
                              out_path=os.path.join(OUT, "wind850_map.png"),
                              issued_ist=today, sources="Open-Meteo multi-model fusion")
    p_nc = maps.nowcast_map(map_points, model_label="IMD official",
                            out_path=os.path.join(OUT, "nowcast_map.png"),
                            issued_ist=today, sources="IMD official")
    p_sev = maps.severe_map(mslp_grid, sev["alerts"],
                            out_path=os.path.join(OUT, "severe_map.png"),
                            issued_ist=today, sources="Open-Meteo multi-model fusion")
    print(f"[*] maps -> {p_rain}, {p_wind}, {p_w850}, {p_nc}, {p_sev}")

    # ---- Phase 1+2: state-wide grid field + MMR zoom (model-interpolated) ----
    # ---- Phase 1+2: state-wide grid field + MMR zoom (model-interpolated) ----
    # Fetched in chunked batches (<=50 pts/call) sharing the rate limiter, with
    # 429/503 backoff inside fetch_grid. Each map + the analysis step is wrapped
    # independently so a single flaky fetch (e.g. MMR 503) degrades locally
    # instead of killing the whole grid/insight block.
    analysis = None
    grid_maps = []
    try:
        from engine import grid as grid_mod
        from engine import analysis as analysis_mod
        from engine import fieldmap as fieldmap_mod
        import numpy as np
        MH_BBOX = grid_mod.MH_BBOX
        MMR_BBOX = grid_mod.MMR_BBOX
        GRID_MODEL = "ecmwf_ifs"      # precip / field / storm-track source
        CONV_MODEL = "gfs_seamless"   # only GFS carries lifted_index for convective flag
        state_coords = grid_mod.maharashtra_grid(0.5)
        mmr_coords = grid_mod.mmr_grid(0.1)
        print(f"[*] grid: MH {len(state_coords)} + MMR {len(mmr_coords)} nodes "
              f"(chunked fetch, model={GRID_MODEL})")
        # analysis dict is always defined so summarize() never hits a NameError;
        # grid_ok flags whether the grid layer actually produced data.
        analysis = {"grid_ok": False}
        state_res = mmr_res = conv_res = None

        # ---- DAILY CACHE: reuse a TTL-fresh grid fetch (default 6h) so we
        # don't re-hit Open-Meteo's anonymous daily cap on every run. ----
        cached = fcache.load()
        cache_grid = cached.get("grid") if cached else None
        if cache_grid:
            state_res = cache_grid.get("state") or []
            mmr_res = cache_grid.get("mmr") or []
            conv_res = cache_grid.get("conv")
            print(f"[*] grid: using CACHED fetch from {cached['fetched_at_ist']} "
                  f"(age {fcache.age_hours(cached['fetched_at_epoch']):.1f}h)")
            ok_state = sum(1 for r in state_res if r.get("hourly"))
            ok_mmr = sum(1 for r in mmr_res if r.get("hourly"))
            print(f"[*] cached grid: state {ok_state}/{len(state_res)} ok | "
                  f"mmr {ok_mmr}/{len(mmr_res)} ok")
            grid_ok = ok_state >= 3
        else:
            # state grid: the backbone; if this fails there is no field/insight
            state_res = grid_mod.fetch_grid(state_coords, GRID_MODEL,
                                            cfg.get("forecast_days", 2))
            ok_state = sum(1 for r in state_res if r.get("hourly"))
            print(f"[*] grid fetched: state {ok_state}/{len(state_res)} ok")
            grid_ok = ok_state >= 3
            # MMR zoom (independent; tolerant of 503)
            try:
                mmr_res = grid_mod.fetch_grid(mmr_coords, GRID_MODEL,
                                              cfg.get("forecast_days", 2))
                ok_mmr = sum(1 for r in mmr_res if r.get("hourly"))
                print(f"[*] grid fetched: mmr {ok_mmr}/{len(mmr_res)} ok "
                      f"(convective grid on {CONV_MODEL})")
            except Exception as e:
                print(f"[!] mmr grid failed (non-fatal): {e}")
                mmr_res = []
            # convective-only GFS fetch (cheap add-on, cape+LI+RH850)
            try:
                conv_res = grid_mod.fetch_grid(
                    state_coords, CONV_MODEL, cfg.get("forecast_days", 2),
                    hourly=["cape", "lifted_index", "relative_humidity_850"])
            except Exception as e:
                print(f"[!] convective grid failed: {e}")
                conv_res = None
            # persist to daily cache (only if we actually got grid data)
            if grid_ok:
                fcache.save(grid={"state": state_res, "mmr": mmr_res,
                                  "conv": conv_res},
                            cities={}, model=GRID_MODEL, conv_model=CONV_MODEL,
                            days=cfg.get("forecast_days", 2))
                print(f"[*] grid cached for {fcache.TTL_SECONDS//3600}h "
                      f"(next runs reuse it)")

        if grid_ok:
            # city asset coordinates for the field-map overlay
            maps.rainfall_field_map._assets = [
                {"name": l["name"], "lat": l["lat"], "lon": l["lon"]} for l in locs]
            try:
                p_field = maps.rainfall_field_map(
                    state_res, MH_BBOX,
                    out_path=os.path.join(OUT, "rainfall_field.png"),
                    imd_warnings=(imd_geo.get("warnings") if imd_geo.get("status") == "ok" else None))
                grid_maps.append(p_field)
            except Exception as e:
                print(f"[!] rainfall_field map failed: {e}")
            try:
                p_sev_field = maps.severe_field_map(
                    mslp_grid, sev["alerts"],
                    out_path=os.path.join(OUT, "severe_field.png"))
                grid_maps.append(p_sev_field)
            except Exception as e:
                print(f"[!] severe_field map failed: {e}")
            # ---- grounded analysis layer (derived, no invented physics) ----
            try:
                # regional rollup uses the MERGED state + MMR-mesh grids so the
                # fine 0.1deg Mumbai mesh (42 nodes) is counted in the
                # "Mumbai Metro (MMR)" region, not just the ~1 coarse state node.
                combined = list(state_res)
                if mmr_res:
                    combined += list(mmr_res)
                track = analysis_mod.storm_track(combined, hours=(0, 6, 12))
                regions = analysis_mod.regional_rollup(combined)
                analysis["grid_ok"] = True  # grid data present; insight is real
                # convective flag from cached/live GFS convective grid
                if conv_res:
                    conv = analysis_mod.convective_flag(conv_res)
                    _, _, ant_avail = analysis_mod.antecedent_states(conv_res)
                else:
                    conv = {"triggered": False, "max_cape": None,
                            "min_li": None, "li_available": False}
                    ant_avail = False
                analysis = {
                    "track": track, "conv": conv, "regions": regions,
                    "antecedent_available": ant_avail,
                    "model_grid": GRID_MODEL, "model_conv": CONV_MODEL,
                    "grid_ok": True,
                }
                # stationary-stall detection (uses grid we already have)
                peak_int = analysis_mod.peak_intensity_grid(state_res)
                analysis["stall"] = analysis_mod.check_for_stalled_system(
                    track.get("speed_kmh", 0.0), peak_int)
                analysis["peak_intensity_mm_h"] = peak_int
                print(f"[*] analysis: track={track['status']} "
                      f"bearing={track['bearing_deg']} speed={track['speed_kmh']} | "
                      f"conv_trig={conv['triggered']} "
                      f"(li_avail={conv['li_available']}) | "
                      f"peak_int={peak_int}mm/h stall={analysis['stall'] is not None} | "
                      f"regions={list(regions.keys())}")
            except Exception as e:
                print(f"[!] analysis failed: {e}")
            # timing map: hour-of-peak per grid cell
            try:
                tcoords, thrs = analysis_mod.peak_hour_field(state_res)
                tlons, tlats, tgrid = fieldmap_mod.build_field(
                    tcoords, thrs, MH_BBOX, resolution=0.25)
                p_timing = maps.timing_map(tlats, tlons, tgrid, MH_BBOX,
                                           out_path=os.path.join(OUT, "timing_map.png"),
                                           issued_ist=today)
                grid_maps.append(p_timing)
            except Exception as e:
                print(f"[!] timing map failed: {e}")
            # mmr zoom panel (data already in mmr_res from cache or live)
            try:
                if mmr_res and sum(1 for r in mmr_res if r.get("hourly")) >= 3:
                    # MMR-region IMD district boundaries (Mumbai layout) from the
                    # cached district_warnings_india polygons.
                    mmr_polys = []
                    if imd_geo.get("status") == "ok":
                        _mb = (72.6, 18.8, 73.3, 19.5)
                        def _cent(g):
                            try:
                                rings = (g["coordinates"][0] if g.get("type") == "Polygon"
                                         else [p[0] for p in g["coordinates"]])
                                xs=[]; ys=[]
                                for r in rings:
                                    for pt in r:
                                        xs.append(float(pt[0])); ys.append(float(pt[1]))
                                if not xs: return None
                                return (sum(xs)/len(xs), sum(ys)/len(ys))
                            except Exception:
                                return None
                        for _n, _w in imd_geo.get("warnings", {}).items():
                            _g = _w.get("geometry")
                            _c = _cent(_g)
                            if not _c or not (_mb[0] <= _c[0] <= _mb[2] and _mb[1] <= _c[1] <= _mb[3]):
                                continue
                            mmr_polys.append({"name": _n.title(), "geometry": _g,
                                              "color_label": _w.get("color_label"),
                                              "color_code": _w.get("color_code")})
                        print(f"[*] mmr district polys: {[p['name'] for p in mmr_polys]}")
                    p_mmr = maps.mmr_zoom_map(
                        mmr_res, (MMR_BBOX[2], MMR_BBOX[0], MMR_BBOX[3], MMR_BBOX[1]),
                        out_path=os.path.join(OUT, "mmr_zoom.png"),
                        district_polys=mmr_polys)
                    grid_maps.append(p_mmr)
                    # convective CAPE map from the GFS grid (if available)
                    ccoords, ccape = [], []
                    if conv_res:
                        for r in conv_res:
                            h = r.get("hourly")
                            if h and h.get("cape"):
                                arr = [x for x in h["cape"][:24] if x is not None]
                                if arr:
                                    ccoords.append((r["lat"], r["lon"]))
                                    ccape.append(max(arr))
                    if len(ccoords) >= 3:
                        clons, clats, cgrid = fieldmap_mod.build_field(
                            ccoords, np.asarray(ccape), MH_BBOX, resolution=0.25)
                        p_conv = maps.convective_map(clats, clons, cgrid, MH_BBOX,
                                                    out_path=os.path.join(OUT, "convective_map.png"))
                        grid_maps.append(p_conv)
            except Exception as e:
                print(f"[!] mmr/convective map failed: {e}")
        else:
            print("[!] state grid insufficient (<3 good points); skipping field/analysis")
        print(f"[*] field maps -> {grid_maps}")
    except Exception as e:
        print(f"[!] grid setup failed (non-fatal): {e}")
        grid_maps = grid_maps or []


    # ── IMD district choropleth (authoritative warning polygons) ──
    p_district = None
    if imd_geo.get("status") == "ok" and imd_geo.get("warnings"):
        try:
            _assets = [{"name": l["name"], "lat": l["lat"], "lon": l["lon"]} for l in locs]
            # NOTE: choro_bbox is (minlon,minlat,maxlon,maxlat) — NOT grid_mod.MH_BBOX
            # which is (lat_min,lat_max,lon_min,lon_max). Use explicit lon/lat order.
            choro_bbox = (72.0, 15.0, 81.0, 23.0)
            p_district = maps.district_choropleth(
                imd_geo["warnings"], choro_bbox, assets=_assets,
                out_path=os.path.join(OUT, "district_choropleth.png"),
                issued_ist=today)
            print(f"[*] district choropleth -> {p_district}")
        except Exception as e:
            print(f"[!] district choropleth failed: {e}")

    # report
    text = report.compose(results, cfg.get("region_focus", "Maharashtra"),
                          narrative=narr, skill=skill, bias_mm=bias_mm,
                          observed_status=obs_all["status"], nowcast_status=nc_all["status"],
                          run_date=today, severe=sev, radar_status=radar_status)
    with open(os.path.join(OUT, "report.md"), "w") as f:
        f.write(text + "\n")
    print(f"[*] report saved -> {os.path.join(OUT, 'report.md')}")

    # deliver — concise one-paragraph summary as the chat text (Telegram's
    # 4096-char limit rejects the full report; full report stays in report.md).
    summary = report.summarize(results, cfg.get("region_focus", "Maharashtra"),
                                today, sev, radar_status, analysis=analysis)
    summary = _fold_long_text(summary, limit=4096)
    print(f"[*] telegram text length: {len(summary)} chars (limit 4096)")

    # ── Adaptive delivery: lean by default, escalate on severity ──────────────
    # NORMAL  (Option A): text + 2 consolidated composites only (clean feed).
    # SEVERE  (Option C fallback): + standalone nowcast + severe-system outlook,
    #          so operators get the glanceable per-district + basin alerts when
    #          the situation is genuinely severe / extreme (not just monsoon).
    # Trigger uses ONLY data we already computed — no extra API calls.
    def _severe_mode(results, analysis, sev):
        for r in results:
            rk = r.get("risk")
            lvl = getattr(rk, "level", None)
            if lvl in ("WARNING", "CRITICAL"):
                return True
            imd = r.get("imd") or {}
            if imd.get("colour") in ("Orange", "Red"):
                return True
        if analysis:
            if analysis.get("stall"):
                return True
            if (analysis.get("peak_intensity_mm_h") or 0) >= 20:
                return True
            for reg in (analysis.get("regions") or {}).values():
                cn = reg.get("clustered_node")
                if cn and (cn.get("max_mm") or 0) >= 115.5:
                    return True
        if sev and (sev.get("alerts") or sev.get("has_signal")):
            return True
        return False

    severe_mode = _severe_mode(results, analysis, sev)
    print(f"[*] delivery mode: {'SEVERE (Option C)' if severe_mode else 'NORMAL (Option A)'}")

    # Composite generation — always create the visual briefs, but delivery is lean.
    composite_paths = []
    if grid_maps or os.path.exists(p_nc):
        _all_grid_maps = list(grid_maps)
        if os.path.exists(p_nc) and p_nc not in _all_grid_maps:
            _all_grid_maps.append(p_nc)
        try:
            composite_paths = maps.compile_consolidated_visual_briefs(
                OUT, _all_grid_maps, issued_ist=today, radar_frames=imd_radar_frames)
            print(f"[*] consolidated visual briefs -> {composite_paths}")
        except Exception as e:
            print(f"[!] visual consolidation failed: {e}")

    # MP4 transcode for Telegram looping video — preferred over GIF.
    _mmr_mp4 = None
    for cp in composite_paths:
        if cp.endswith(".gif") and os.path.exists(cp):
            _mmr_mp4 = cp.replace(".gif", ".mp4")
            tr = telegram.transcode_gif_to_mp4(cp, _mmr_mp4)
            if tr.get("ok"):
                print(f"[*] transcoded {cp} -> {_mmr_mp4}")
            else:
                print(f"[!] mp4 transcode failed: {tr.get('error')}; using GIF fallback")
                _mmr_mp4 = cp
            break

    telegram.send_message(CHAT, summary, dry_run=DRY)

    # Choose looping video source: MP4 if transcode succeeded, else GIF fallback.
    _mmr_video = _mmr_mp4 if _mmr_mp4 and os.path.exists(_mmr_mp4) else None
    _mmr_gif_candidate = os.path.join(OUT, "mmr_asset_brief.gif")
    if not _mmr_video and os.path.exists(_mmr_gif_candidate):
        _mmr_video = _mmr_gif_candidate

    _synoptic = os.path.join(OUT, "state_synoptic_brief.png")

    def _safe_send_photo(path, caption):
        if path and os.path.exists(path):
            telegram.send_photo(CHAT, path, caption=caption, dry_run=DRY)

    def _safe_send_video(path, caption):
        if path and os.path.exists(path):
            telegram.send_animation(CHAT, path, caption=caption, dry_run=DRY, supports_streaming=True)

    # Frame 0: synoptic brief
    _safe_send_photo(_synoptic, "Maharashtra synoptic risk brief")
    # Frame 1: MMR animated brief
    if _mmr_video:
        _safe_send_video(_mmr_video, "MMR/asset animated brief")

    if severe_mode:
        # Severe escalation: nowcast + severe-system outlook + district choropleth + timing map
        _safe_send_photo(p_nc, "IMD district nowcast (next 3h)")
        _safe_send_photo(p_sev, "Severe-system outlook (3-7d, AS+BoB)")
        if p_district and os.path.exists(p_district):
            _safe_send_photo(p_district,
                             "Maharashtra district rainfall warnings (IMD, authoritative)")
        p_timing = os.path.join(OUT, "timing_map.png")
        if os.path.exists(p_timing):
            _safe_send_photo(p_timing, "Peak rainfall timing map")
    print("[*] done.")


def _median(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _fold_long_text(text: str, limit: int = 4096, fold_marker: str = "\n[...]\n") -> str:
    """Hard-cap Telegram text at `limit` chars.

    If the text exceeds the limit, prefer truncating within the least critical
    tail section. We split on major section breaks and rebuild from the front,
    dropping middle/last sections first.
    """
    if len(text) <= limit:
        return text
    sections = [s.strip() for s in text.split("\n\n") if s.strip()]
    if not sections:
        return text[:limit]
    rebuilt = [sections[0]]
    total = len(sections[0])
    for sec in sections[1:]:
        if total + len(sec) + len(fold_marker) <= limit:
            rebuilt.append(sec)
            total += len(sec) + len(fold_marker)
        else:
            break
    return ("\n\n".join(rebuilt) + fold_marker + "Full report in report.md").strip()


if __name__ == "__main__":
    main()
