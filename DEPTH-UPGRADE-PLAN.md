# How to make the Weather Updater DEEPER & more accurate (research-grounded v2 plan)

> This is the "how to improve" answer. Everything below is backed by a fresh deep-research
> pass (`.research-deep/packs/`) AND live endpoint checks. No guesses.

## Why v1 is "basic" (honest)
v1 = one free API (Open-Meteo) -> 24h accumulation -> IMD thresholds. It has NO:
- observational ground truth (can't tell if it's right),
- nowcast (next 3h) layer (your analyst product is nowcast-driven),
- bias correction (global models under-forecast Ghats/coastal 3-5x),
- skill measurement (we claim 75-80% but never verify ourselves),
- real IMD official ingestion (stub returns "unavailable").

## The 5 depth upgrades (each evidence-backed)

### U1. Observed-rainfall ground truth + verification  [HIGHEST VALUE]
- Source (confirmed LIVE): open "India Rainfall Monitor" JSON ->
  `https://sayantan-aquacarta.github.io/rainfall-pipeline/api/latest.json`
  Gives per-district `day_actual_mm`, `day_normal_mm`, `day_category` for 760 districts,
  updated daily (generated_at in response). [observations-r1.md, live HTTP 200]
- Also: IMD gridded rainfall via `imdlib` package (0.25deg daily, Nandi et al. 2024)
  [observations-r1.md] — for grid-level bias fields.
- What it enables: join each city -> its district -> compare forecast vs OBSERVED.
  This is the "detect when we're wrong" capability you asked for.

### U2. IMD district nowcast (next 3h) ingestion  [matches your analyst image]
- IMD Nowcast Services = "short-range severe weather warnings for Next 3Hour"
  at `mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php` (confirmed reachable,
  references GeoJSON). [imd-nowcast-r1.md, live HTTP 200]
- Parse the embedded GeoJSON -> get official district nowcast text/colour as GROUND TRUTH.
- This replaces the fake "unavailable" stub and makes the report OFFICIAL-cross-checked.

### U3. Bias correction for orographic under-forecast
- Evidence: global models systematically over-predict drizzle, under-predict extreme/
  orographic rain; Western Ghats & NE/Himalaya have the largest biases
  [skill-r1.md: Basha et al. 2017, Tiwari et al. 2016, Choudhary & Dimri 2019].
- Method (keep simple, evidence-based): LOCI / quantile mapping on the forecast using
  a rolling history of (forecast vs IMD-observed) pairs per district. Start with linear
  scaling (LS) per district from U1 history; upgrade to LOCI/quantile mapping later.
- Effect: coastal Maharashtra (Mumbai/Thane/Ratnagiri) forecast stops being 3-5x low.

### U4. Skill scoring (measure ourselves)
- Standard India-rainfall categorical metrics: POD, FAR, CSI, ETS [skill-r1.md,
  AMS glossary, eumetrain]. Continuous: correlation, MAE, RMSE, bias.
- Pool forecast-observation pairs across districts/dates (need a rolling archive ->
  store each run's forecast + next-day observed in a local SQLite).
- Report our own CSI/ETS in the footer instead of quoting IMD's 75-80%. This is the
  "accurate predictions / correct level" proof.

### U5. Nowcast + map fidelity (compete with the image)
- Add a nowcast layer (next 3h) to the report + a 3h rainfall map.
- Add an India state-boundary GeoJSON basemap so maps show the real coastline/outline
  (currently points float on a blank grid). Bundle a small Maharashtra boundary GeoJSON.
- Windy uses ECMWF 850hPa; we keep 10m but can pull upper-air via MOSDAC/era5 later.

## Proposed v2 build order (each verifiable live)
1. `engine/observed.py` — fetch latest.json, join city->district, return observed mm.
   TEST: print observed mm for Mumbai/Pune/Nagpur. (do now)
2. `engine/verify.py` — store forecast+observed in SQLite; compute bias & CSI/ETS.
   TEST: after 2 runs a day apart, show bias per district.
3. `engine/fetch_imd_nowcast.py` — parse districtWiseNowcastGIS GeoJSON -> official colour.
   TEST: print IMD official nowcast for Mumbai district.
4. `engine/bias_correct.py` — LOCI/linear-scaling per district from history.
   TEST: corrected forecast vs raw for Ratnagiri.
5. Update `classify.py` to use (a) bias-corrected accum and (b) IMD official colour as
   ground truth. Update `maps.py` for 3h nowcast + Maharashtra boundary basemap.
6. `report.py` footer shows OUR measured CSI/ETS + "verified against IMD observed".

## Honesty / limits carried forward
- rainfall-pipeline API is a community mirror of IMD data (cite it; attribute IMD).
- imdlib gridded needs download (~MB/day); optional, for bias fields.
- MOSDAC/radar deferred (account-gated) — noted, not claimed.
- We still can't promise street-level accuracy; we CAN now MEASURE and SHOW our error.

## Source index (this pass)
- IMD observations: https://imdweb.lwcc.in/ (imdlib), https://prasadkulkarni.in/...,
  https://sayantan-aquacarta.github.io/rainfall-pipeline/ (live JSON confirmed)
- IMD daily stats: https://mausam.imd.gov.in/imd_latest/contents/rainfall_statistics_3.php
- IMD nowcast: https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php (GeoJSON)
- Skill scores: AMS POD/FAR/CSI glossary, eumetrain CSI/ETS, Wikipedia Forecast_skill
- Bias: skill-r1.md (Basha 2017, Tiwari 2016, Choudhary & Dimri 2019; LOCI/QM methods)
