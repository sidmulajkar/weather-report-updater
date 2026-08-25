# Weather Report Updater — Maharashtra Rainfall Risk Pipeline

Local, on-demand weather-risk report generator for Maharashtra with severity-gated Telegram delivery.

## What it does

1. Fetches Open-Meteo multi-model forecasts (`ecmwf_ifs`, `icon_global`, `gfs_seamless`) for 10 Maharashtra cities.
2. Fuses ensembles with automatic renormalization when a model is missing.
3. Ingests IMD authoritative inputs:
   - District warnings / colour-tier alerts
   - Nowcast freshness gate with TTL validation
   - AWS observations
   - Radar / satellite / lightning feeds
4. Computes local impact dynamics:
   - IDF burst intensity
   - 7-day antecedent saturation
   - 3×3 proximity scan
   - Mumbai tidal drainage-lock logic
   - Western Ghats orographic multiplier
5. Generates analyst-style outputs:
   - Plain-text bulletin (`output/report.md`)
   - District choropleth
   - State synoptic map
   - MMR zoom / asset brief
   - Severe / timing / field maps
6. Delivers via Telegram:
   - Normal mode: 2 media items
   - Severe mode: 6 media items
   - MP4 transcode for smaller animation payload

## Directory layout

```
weather-report-updater/
├── run_report.py
├── run_local_test.py
├── requirements.txt
├── README.md
├── .github/workflows/production_pipeline.yml
├── config/
│   ├── locations.json
│   └── tides_2026.csv
├── engine/
│   ├── fetch_openmeteo.py
│   ├── fetch_imd.py
│   ├── fetch_imd_nowcast.py
│   ├── fetch_metno.py
│   ├── observed.py
│   ├── grid.py
│   ├── maps.py
│   ├── report.py
│   ├── classify.py
│   ├── verify.py
│   └── ...
├── delivery/
│   └── telegram.py
├── output/
│   ├── report.md
│   ├── state_synoptic_brief.png
│   ├── district_choropleth.png
│   └── ...
├── cache/
│   ├── forecast_cache.json
│   └── imd_district_polygons.json
└── engine/archive.db
```

## Run locally

```bash
python -m venv venv1
source venv1/bin/activate
pip install -r requirements.txt
python run_report.py
```

Outputs are written to `output/`.

## Dry-run / test harness

```bash
python run_local_test.py
```

This runs the full pipeline with a fake Telegram sender and prints:
- Generated bulletin text
- Media files produced
- File sizes
- Severity-gated attachment plan

No network sends occur unless `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set.

## Telegram delivery

This pipeline sends analyst bulletins to Telegram with severity-gated media attachments.

### Environment variables

- `TELEGRAM_BOT_TOKEN` — bot token from @BotFather
- `TELEGRAM_CHAT_ID` — target chat/channel ID
- `DRY_RUN` — set to `true` to disable sending; prints to stdout instead

If `TELEGRAM_BOT_TOKEN` is missing, the run automatically stays in dry-run mode.

### Run live

```bash
TELEGRAM_BOT_TOKEN=<token> TELEGRAM_CHAT_ID=<chat> python run_report.py
```

### Delivery behavior

- **Normal mode:** 2 media items
  - `state_synoptic_brief.png`
  - `mmr_asset_brief.mp4`
- **Severe mode:** 6 media items
  - The 2 base items above
  - `nowcast_map.png`
  - `severe_map.png`
  - `district_choropleth.png`
  - `timing_map.png`

### Text format

- Bulletin text is plain format
- Capped at 4096 characters with section-priority folding
- IMD district warnings are authoritative and lead the message
- Model estimates are shown separately and never override IMD colours

### Animation handling

- `mmr_asset_brief.gif` is transcoded to MP4 via ffmpeg
- MP4 is preferred for Telegram because it loops natively and reduces payload size
- If ffmpeg is unavailable, GIF fallback is used automatically

### Rate limiting

- Telegram sends are paced to respect API limits
- HTTP 429 responses are honored with retry-after backoff

## CI / GitHub Actions

Workflow: `.github/workflows/production_pipeline.yml`

- Runs on push to `main`
- Installs dependencies from `requirements.txt`
- Caches pip + forecast cache + archive DB
- Executes `python run_local_test.py`
- Fails fast on non-zero exit

## Honesty / data limitations

- IMD district warnings are authoritative; model estimates are shown separately.
- Missing physics are reported as unavailable, never fabricated.
- Convective proxy may be unavailable if `lifted_index` is not served.
- Soil moisture (`soil_moisture`) is currently unavailable from the no-key grid source.
- Skill metrics require 30+ paired forecast/observed days; fewer days are reported as indicative only.
- Radar reflectivity is metadata-only; numeric dBZ ingestion is pending.

## License

UNOFFICIAL analyst product. Cross-verify with official IMD:
- https://mausam.imd.gov.in
- https://rsmcnewdelhi.imd.gov.in
