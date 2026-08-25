# Cyclone & Severe-System Early Warning (Module G) — plan + evidence

## The user's need (paraphrased)
"How do we know a CYCLONE is forming that could impact us in 3-7 days? Also LOW-PRESSURE
belts, WIND STORMS, even TORNADOES — give early warning, not just 24-48h rain."

## Evidence (verified this session)
1. IMD RSMC New Delhi — authoritative NIO tropical cyclone centre.
   - `genesis-forecast.php`: cyclogenesis probability in 4 bands
     nil(0%) / low(1-33%) / moderate(34-67%) / high(68-100%) for Arabian Sea (AS),
     Bay of Bengal (BoB), NIO; issued for next 3, 5, AND 7 days. Skill measured via Brier
     Score. [LIVE HTTP 200, text confirmed]
   - `four-stage-warning.php`: STAGE 1 "PRE CYCLONE WATCH" issued 72h in advance about a
     disturbance DEVELOPING. Stages: Watch(72h)->Alert(48h)->Warning(24h)->Post-landfall.
   - Special Tropical Weather Outlook PDFs (blocked/JS) carry the live genesis narrative.
2. Model-based genesis detection (ECMWF/NOAA TC-gen algorithm, cyclone-r1.md):
   a closed MSLP min + 850hPa vorticity max within ~2deg + 250-850 thickness max within ~2deg
   + wind<17 m/s; if sustained >=24h -> genesis. WE CAN REPLICATE THIS from Open-Meteo
   hourly MSLP + 850hPa wind (verified: 10-day, 240 steps, out to +9 days).
3. Low-pressure systems are the MORE FREQUENT Maharashtra monsoon threat (synoptic-r1.md:
   monsoon trough/LPA over BoB drives Maharashtra heavy rain via Arabian-Sea moisture).
   -> our existing 850hPa + MSLP monitoring already covers this; we add a "low-pressure
   belt" detector (broad MSLP trough, not closed).
4. Tornadoes: rare in peninsular India; no dedicated free feed. We cover the *severe wind/
   convective* precursor via CAPE (already have) + 850hPa vorticity + wind-gust. Honest:
   we flag "severe convective / high wind potential", not a tornado-specific warning.

## Design: Module G = engine/severe_systems.py
Two independent detectors, both feeding a single "severe outlook":
  A) OFFICIAL (ground truth): scrape IMD RSMC genesis-forecast probability band for AS/BoB
     + detect any "Pre-Cyclone Watch" text. HIGH confidence when present.
  B) MODEL (our own): scan Open-Meteo 10-day MSLP + 850hPa over AS+BoB grid boxes; apply
     TC-gen criteria (closed low + vorticity max + thickness + sub-storm wind) sustained
     >=24h -> estimate genesis probability + ETA (days). Also flag broad MSLP trough
     (low-pressure belt) and high-CAPE/high-gust convective cells.
Both produce structured alerts with lead time (days) and impact confidence.

Outputs:
  - list of alerts: {type: cyclone|low_pressure|severe_wind, region, probability/level,
    eta_days, source, confidence, basis}
  - a `severe_outlook.md` section + a `severe_map.png` (MSLP field over AS+BoB with markers)
Wired into run_report.py so the daily report ALSO carries the 3-7 day severe outlook.

## Honesty
- IMD genesis probability is the gold standard; we quote it verbatim + attribute RSMC.
- Model genesis detection is a simplified TC-gen heuristic -> labelled "model-indicated,
  unverified", lower confidence; cross-checked vs IMD.
- If IMD site is JS/blocked we degrade to model-only and SAY SO (never fake a watch).
- No tornado-specific product (none free); we surface severe-convective potential only.

## Build order
1. engine/severe_systems.py (both detectors + MSLP grid scan)  [TEST live]
2. engine/maps.py: severe_map (MSLP over AS+BoB + markers)    [TEST]
3. run_report.py: call + attach severe outlook to report       [TEST e2e]
