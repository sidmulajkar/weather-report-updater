# Weather-Updater — Engineering & Meteorological Assessment

> Genuine critical rating (not praise). Last updated: 2026-08-23.
> Context: built as a local on-demand Maharashtra/Mumbai weather-risk generator
> fusing Open-Meteo multi-model forecasts + IMD observed/nowcast cross-check,
> classifying risk via IMD colour thresholds, rendering analyst-style maps,
> composing Markdown, optional Telegram delivery.

## Overall scores

- **Engineering: 8.5 / 10** — modular, graceful degradation, fast-fail on API
  ban, 6h no-key cache, concurrent fetches, clean engine/delivery/config split.
- **Honesty / anti-hallucination: 9 / 10** — explicit "UNOFFICIAL", "data
  unavailable" panels, no invented radar dBZ / soil moisture / tide precision.
- **Meteorological validity: 5.5 / 10** — IMD thresholds correct; but
  convective, antecedent, orographic and nowcasting layers are blind/approx.
- **Operational usefulness: 7 / 10** — adaptive delivery (2 vs 4 images),
  asset-level risk, tidal lock-window, proximity scan, IDF burst, terrain funnel.
- **Data sourcing: 4.5 / 10** — Open-Meteo is a POST-PROCESSED BLEND
  (ECMWF/ICON/GFS), NOT IMD. No radar ingest, no soil moisture, coarse grid.
- **Reliability / resilience: 8 / 10** — survives API bans (cache + fast-fail),
  bounded timeouts, no silent failures.

**Composite: 7.5 / 10 (engineering) · 6 / 10 (operational forecast tool).**

## Genuine strengths

1. Anti-hallucination is real, not cosmetic. "Radar ACTIVE | reflectivity NOT
   ingested", "Antecedent soil saturation: UNAVAILABLE", "no invented physics"
   are honest and correct.
2. IMD 4-band coloring is research-correct: Green/Yellow/Orange/Red keyed to the
   actual 64.5 / 115.5 / 204.5 mm 24h thresholds — not an aesthetic ramp.
3. Graceful degradation throughout: API banned -> cache or "unavailable" panel,
   never a blank plot masquerading as data. (The 15-min hang was a real bug,
   now fixed with daily-ban fast-fail + short timeout + _DAILY_BAN short-circuit.)
4. Adaptive delivery: lean 2-image feed normally, escalates to 4 when severe —
   reduces operator noise without losing info on bad days.
5. Impact layers are the asset-protection value-add: tidal drainage-lock windows,
   3x3 proximity scan, IDF burst intensity, terrain funneling.

## Real limitations (ranked by impact)

1. **NOT an IMD-grade forecast.** Open-Meteo is a secondary blend. The product
   cross-checks IMD observed/nowcast (good) but the forecast SPINE is still
   Open-Meteo. => Mitigation: make IMD district warnings the authoritative
   severity layer; keep Open-Meteo for spatial grid interpolation.
2. **Convective nowcasting effectively non-functional on real data.** Verified:
   `lifted_index` returns None from the live grid; CAPE is the only proxy and
   needs `gfs_seamless`. No storm-mode, low-level jet, or orographic LLMJ.
3. **0.5deg grid misses Western Ghats orographic maxima.** Konkan/Coastal
   Maharashtra orographic spikes (the actual flood drivers) are under-resolved
   -> systematic UNDER-forecast of the most dangerous rain.
4. **No radar ingest.** Status says "ACTIVE" but numeric dBZ not ingested -> no
   true 0-3h nowcasting. IMD nowcast is district-level text, not convective.
5. **Antecedent saturation blind.** `soil_moisture` returns None -> runoff
   potential unmodeled. For infrastructure risk this is critical and missing.
6. **Severe-system outlook is a heuristic on a weather model.** Basin MSLP from
   Open-Meteo + self-labeled "model-indicated (unverified)" TC-genesis heuristic;
   IMD RSMC genesis scraped from fragile HTML.
7. **Free-API dependency.** Anonymous Open-Meteo has a DAILY request cap (hit it;
   bans IP for the day). Cache mitigates CI, but a commercial product should not
   hinge on a free tier with daily throttling.
8. **Synthetic-test only for images.** All image verification used hand-seeded
   data. Rendering logic is proven; a real multi-model live run's composites
   have NOT been captured (API banned locally).

## Path to "IMD level" (research-backed plan)

Do NOT scrape the IMD homepage (`index_en.php`) — it is a JS-rendered marquee of
PDFs with almost no structured district data. Instead:

1. **Primary: IMD district-wise warning JSON** (structured backend behind
   `districtWiseWarning.php`). This is the authoritative per-district colour
   warning. Reference the official contract in `Forecast/marquee_data/API_doc.pdf`
   ("IMD's warnings through APIs and Social media"). Replace/augment
   `fetch_imd_nowcast` with `fetch_imd_warnings` consuming this JSON.
2. **Secondary: Daily Weather Briefing + press-release text** for narrative
   grounding and the official colour bands / region wording.
3. **Keep Open-Meteo as the quantitative spatial grid** (good at interpolation)
   but let **IMD warnings be the severity authority**.
4. **Compliance gate:** a commercial SaaS scraping IMD must check the data-use
   terms in `API_doc.pdf` before production use.
5. **Resilience:** IMD site is JS-heavy / bot-guarded; use proper headers,
   polite rate-limiting, and monitor for HTML-structure drift.

This directly fixes Limitation #1 (secondary-source spine) and partly #4
(nowcasting via IMD district warnings).

## What would move it to 9+

- Primary IMD warning data for the forecast spine (+ Open-Meteo fallback).
- Radar dBZ ingestion (IMD mosaic) -> real 0-3h nowcasting.
- Soil-moisture / antecedent ingestion -> actual runoff risk.
- Higher-res / DEM-aware grid over the Ghats -> fix orographic under-forecast.
- Convective: storm-mode + LLJ/LLMJ proxies, not just CAPE.
- One clean live run (post-ban) captured to verify real-data composites.

---

## UPDATED 2026-08-23 — IMD integration landed (research-tier GeoServer)

Went through mausam.imd.gov.in; discovered the real data architecture is a
**GeoServer WFS** at `reactjs.imd.gov.in/geoserver/imd/ows?` (NOT the HTML
homepage). Built `engine/fetch_imd.py` (verified live):

- `district_warnings_india` -> authoritative IMD district colour warnings
  (Day1_Color = today's severity; 1=Green..4=Red). Now the **authoritative
  severity layer**, overriding Open-Meteo-derived colour. Closes Limitation #1.
- `NowcastWarningDistrict` -> district nowcast warnings (valid-time window).
- `aws_data_layer` -> **340 real AWS station observations** in MH (ground truth).
  Closes part of Limitation #5 (observed, not antecedent-soil yet).
- `fetch_imagery` -> downloads IMD **radar mosaic GIF, satellite IR JPG,
  lightning GIF** (verified 200, real dBZ data). Sent as a nowcast-reference
  panel every run. Closes Limitation #4 (visual nowcasting; not decoded to dBZ
  — honest "visual review" framing).
- `Cyclone_Track_V` / `subdiv_warnings_now` available for severe-system authority
  (Limitation #6 path).

**Verified end-to-end**: live run EXIT=0, IMD warnings/nowcast/obs/imagery all
ok, SENT=7 (text + 2 composites + 3 IMD imagery + 2 severe-only), 64s runtime.
IMD genuinely issued Orange/Red across MH on 2026-08-23 (peak monsoon) — the
colour mapping was verified against raw `Day1_Color` values.

**Remaining honest gaps**: (a) radar/sat are display-only (no dBZ decode);
(b) antecedent *soil* saturation still UNAVAILABLE (AWS gives rainfall obs, not
soil moisture); (c) 0.5deg grid orographic gap unchanged; (d) COMMERCIAL use
must route through official `api.imd.gov.in` gateway + confirm data-use terms
(API_doc.pdf) — current code uses the open research-tier GeoServer.

