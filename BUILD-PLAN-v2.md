# Weather Updater v2 — Build-Ready Plan (research-grounded, all 5 modules + backup)

Status: 2026-08-22. Every endpoint below was PROBED LIVE and confirmed working unless
marked [VERIFY-IN-BUILD]. All research in `.research-deep/packs/`.

## Verified live endpoints (this session)
- Open-Meteo forecast: `past_days` OK (history backfill), `cape`+`wind_gusts_10m` OK,
  `wind_speed_850hPa`+`wind_direction_850hPa` OK (850hPa layer for analyst-style map).
- Observed rainfall: `https://sayantan-aquacarta.github.io/rainfall-pipeline/api/latest.json`
  -> per-district `day_actual_mm`, `day_normal_mm`, `day_category` (760 districts, daily).
- IMD nowcast: `https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php`
  (reachable, references GeoJSON) -> official next-3h nowcast.
- IMD daily stats: `https://mausam.imd.gov.in/imd_latest/contents/rainfall_statistics_3.php`.
- MET Norway `api.met.no` locationforecast: keyless, CC license [+ via Open-Meteo `metno_api`].
- Maharashtra district GeoJSON: datameet/india-maps-data (download + bundle locally).

## Architecture (resilience-first, per "u1+u2 as backup")
A central `fetch_weather()` tries PRIMARY (Open-Meteo fusion) and on ANY failure
falls back to MET Norway, then to a cached last-good snapshot. The report never
hard-fails: it degrades and labels the data source. U1 (observed) and U2 (IMD nowcast)
are the two independent GROUND-TRUTH backstops used to (a) verify and (b) survive
if the model layer fails.

```
config/locations.json        -> cities, models, windows, DISTRICT names (for join)
engine/fetch_openmeteo.py    -> PRIMARY fusion (ecmwf/icon/gfs) + 850hPa + cape + gusts
engine/fetch_metno.py        -> FALLBACK keyless source  [NEW]
engine/fetch_imd_nowcast.py  -> U2 official nowcast GeoJSON parse  [NEW]
engine/observed.py           -> U1 observed district rainfall join  [NEW]
engine/verify.py             -> SQLite archive + bias + CSI/ETS skill  [NEW]
engine/bias_correct.py       -> LOCI per-district bias correction  [NEW]
engine/classify.py           -> IMD thresholds + confidence + nowcast + bias
engine/narrative.py          -> synoptic narrative generator  [NEW]
engine/maps.py               -> rainfall + 850hPa wind + 3h nowcast + boundary basemap  [UPGRADE]
engine/report.py             -> analyst-style report (tables + narrative + skill)
delivery/telegram.py         -> paced sender + dry-run (already built)
run_report.py                -> orchestrator with PRIMARY->FALLBACK->CACHE degrade
geo/maharashtra_districts.geojson  -> bundled boundary (downloaded)
```

## Build order (each module tested LIVE before next)

### Module A — observed.py (U1)  [verify + detect error]
- Fetch `latest.json`; build dict district->observed mm (Maharashtra subset).
- Join each city -> its district (config holds district name) -> return observed mm + category.
- TEST: print observed mm for Mumbai/Pune/Nagpur; confirm non-null.

### Module B — fetch_imd_nowcast.py (U2)  [official ground truth]
- GET districtWiseNowcastGIS.php; extract embedded GeoJSON (search for `.geojson`/json
  blob / API call). Parse district features -> nowcast text + colour.
- TEST: print IMD nowcast for Mumbai/Nagpur districts (or honest "unavailable" if parse fails).

### Module C — verify.py (U4 core)  [measure ourselves]
- SQLite `archive.db`: store each run (date, city, district, forecast_mm, observed_mm, model).
- On each run, join TODAY's forecast to YESTERDAY's observed (via observed.py) -> compute
  per-district bias + rolling CSI/ETS/POD/FAR (threshold = Yellow 64.5mm).
- TEST: after 2 runs, show bias + skill table.

### Module D — bias_correct.py (U3)  [fix Ghats under-forecast]
- LOCI per district from archived (forecast, observed) pairs; apply to new forecast.
- Fallback: if <N history points, no correction (raw) + flag "uncorrected".
- TEST: show raw vs corrected for Ratnagiri (coastal).

### Module E — narrative.py (the missing analyst prose)  [matches image]
- Rules from research: detect offshore trough / low-pressure area / cyclonic circulation /
  Arabian-Sea moisture from 850hPa wind + CAPE + observed anomalies; emit 2-3 sentence
  synoptic paragraph + "primary hotspots" list. Honest: if no strong signal, say "no active
  synoptic system".
- TEST: print narrative for current data.

### Module F — maps.py upgrade + fetch_metno fallback + orchestrator
- Add 850hPa wind map (replicates your image's upper-air layer), 3h nowcast map,
  Maharashtra boundary basemap (choropleth outline), observed-overlay option.
- fetch_metno.py as keyless fallback; orchestrator degrades PRIMARY->MET->CACHE and
  labels source on every artifact.
- TEST: full run produces report.md + 4 maps; dry-run Telegram.

## Honesty guardrails carried forward
- rainfall-pipeline = community mirror of IMD data -> attribute IMD.
- Bias/skill only as good as archived history -> label "n days of calibration".
- MET Norway is Nordic-optimized; fallback labelled "MET Norway (lower India skill)".
- No radar/satellite pixel layer (account-gated) -> noted, not faked.
- Report labelled unofficial until U2 parsing is solid.

## Source index
- observations-r1.md, imd-nowcast-r1.md, mosdac-r1.md, skill-r1.md, gaps-r1.md, synoptic-r1.md
- Live: rainfall-pipeline JSON, IMD nowcast/stats, Open-Meteo params, MET Norway, GeoJSON
