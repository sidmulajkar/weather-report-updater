# Weather Updater — Maharashtra (local, on-demand)

Generates a Maharashtra rainfall-risk report + two maps (rainfall forecast, wind
field) locally, using Open-Meteo multi-model fusion. No GitHub/cron/IP traps.
Telegram is optional (dry-run by default).

## Run (on-demand, local)
```bash
cd weather-updater
./venv1/bin/python run_report.py
```
Output: `output/report.md`, `output/rainfall_map.png`, `output/wind_map.png`.

## Send to Telegram (when ready)
```bash
TELEGRAM_BOT_TOKEN=xxxx TELEGRAM_CHAT_ID=-100xxxx ./venv1/bin/python run_report.py
```
Without the token it DRY-RUNS (prints + saves files, sends nothing).

## What it does
1. Fetches ECMWF + ICON + GFS hourly precip/prob/wind for 10 Maharashtra cities.
2. Fuses models; flags disagreement (spread) -> confidence.
3. Classifies level by IMD impact-based thresholds (64.5/115.6/204.5 mm / 24h).
4. Composes an analyst-style ranked report.
5. Renders rainfall (tricontourf) + wind (quiver) maps.

## Honesty notes
- IMD official district colour is NOT auto-parsed (web app) -> report is MODEL-ONLY / unofficial.
  Cross-check against official IMD before acting. (See engine/fetch_imd.py.)
- Free Open-Meteo = non-commercial; 10 m wind (not 850 hPa).
- Accuracy ceiling ≈ IMD district Day-1–5 (~75–80%).
- To reproduce the analyst's sample image, swap in real IMD district warnings + a
  basemap GeoJSON for true coastline.

## Files
- config/locations.json — cities + models + window
- engine/fetch_openmeteo.py — multi-model fusion
- engine/fetch_imd.py — IMD check (honest, no fabrication)
- engine/classify.py — IMD colour thresholds + confidence
- engine/report.py — Markdown composer
- engine/maps.py — rainfall + wind PNG renderers
- delivery/telegram.py — paced sender + dry-run
- run_report.py — orchestrator
