"""Analyst-style report composer (Markdown) — Phase-2 executive layout.

Layout (per spec: actionable up top, validation transparent, ground-truth at bottom):
  1. Executive Alert Summary  — active alerts, immediate actions, atmospheric drivers
  2. Multi-Variable Parameter Matrix — rain rate / wind hazard / tidal-lock /
     proximity / antecedent saturation per asset
  3. Validation Context        — data gaps + bias (transparency before metrics)
  4. Per-asset detail           — basis, IMD cross-check, telemetry status
  5. Ground-Truth Verification  — previous-day observed + data-deficit notices
  6. Severe-system outlook + method/sources footer
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

INDIA = timezone(timedelta(hours=5, minutes=30))  # Asia/Kolkata, stdlib only

# IMD-register style: no emoji in the analyst bulletin (clean, official look).
# Levels render as plain text (CRITICAL/WARNING/ADVISORY/NOMINAL).
LEVEL_EMOJI = {"CRITICAL": "", "WARNING": "", "ADVISORY": "", "NOMINAL": ""}
LEVEL_RANK = {"NOMINAL": 0, "ADVISORY": 1, "WARNING": 2, "CRITICAL": 3}
LEVEL_ACTION = {
    "CRITICAL": "Immediate halt of outdoor/height work; evacuate low-lying sites; reroute logistics.",
    "WARNING": "Secure assets; limit travel; pre-position pumps; watch the next update.",
    "ADVISORY": "Watch local bulletins; minor disruption possible; brief shift leads.",
    "NOMINAL": "No significant warning; routine monitoring.",
}
# wind direction -> compass + onshore note (Mumbai: W/SW = onshore)
DIR_NAMES = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW",
             "W","WNW","NW","NNW"]
def compass(deg):
    try:
        return DIR_NAMES[int((float(deg) % 360) / 22.5)]
    except (TypeError, ValueError):
        return "?"

import engine.imd_terms as imd_terms
def onshore_note(deg):
    try:
        d = float(deg) % 360
        # onshore for Mumbai coast ~ W/SW quadrant (200-290)
        return "Onshore Flow" if 180 <= d <= 300 else "Offshore/Alongshore"
    except (TypeError, ValueError):
        return ""


def compose(results: list[dict], region: str, narrative: dict = None, skill: dict = None,
            bias_mm: dict = None, observed_status: str = "unavailable",
            nowcast_status: str = "unavailable", run_date: str = "",
            severe: dict = None, radar_status: dict = None) -> str:
    now = datetime.now(INDIA)
    run_date_str = now.strftime("%d %b %Y")
    target_date_str = (now + timedelta(hours=24)).strftime("%d %b %Y")
    date_str = f"{run_date_str}–{target_date_str}"
    ranked = sorted(results, key=lambda r: (-_lvl(r["risk"].level), -r["corrected_mm"]))

    lines = []
    # ============ 1. EXECUTIVE ALERT SUMMARY ============
    lines.append(f"# {region} Operational Rainfall Risk — next 24h ({date_str})")
    lines.append("")
    lines.append(f"**Run:** {run_date or date_str} (IST)  |  **Sources:** Open-Meteo fusion "
                 f"(ECMWF/ICON/GFS) + IMD observed & nowcast cross-check")
    lines.append("")
    lines.append("## EXECUTIVE ALERT SUMMARY")
    lines.append("")
    active = [r for r in ranked if r["risk"].level in ("CRITICAL", "WARNING", "ADVISORY")]
    if active:
        # systemic metro warning if >=2 adjacent assets at WARNING+ (proximity cluster)
        clustered = [r["name"] for r in active if r.get("proximity", {}).get("flagged")]
        # Lead with IMD's authoritative colour (not our operational override level).
        def _imd_c(r):
            return (r.get("imd") or {}).get("colour") or (r["risk"].imd_colour) or "n/a"
        lines.append(f"**Active alerts:** {len(active)} asset(s) — "
                     + ", ".join(f"{r['name']} (IMD {_imd_c(r)})"
                                 for r in active[:8]))
        top = active[0]
        lines.append(f"**Highest priority:** {top['name']} — IMD {_imd_c(top)} "
                     f"(operational: {top['risk'].level})")
        if len(clustered) >= 2:
            lines.append(f"**SYSTEMIC METRO WARNING:** compounding risk across "
                         f"{', '.join(clustered)} — treat as a connected transit/logistics "
                         f"hazard, not isolated sites.")
        # immediate actions (top 3)
        lines.append("")
        lines.append("**Immediate actions:**")
        for r in active[:3]:
            lines.append(f"  • {r['name']}: {LEVEL_ACTION[r['risk'].level]}")
        # atmospheric drivers
        drivers = []
        for r in active[:3]:
            rk = r["risk"]
            inten = r.get("intensity", {})
            if inten.get("burst_band") in ("HEAVY", "VERY_HEAVY", "EXTREME"):
                drivers.append(f"{r['name']}: {inten['burst_band']} burst "
                               f"({inten.get('max1h_mm')} mm/h)")
            if r.get("tidal", {}).get("overlap"):
                drivers.append(f"{r['name']}: tidal DRAINAGE LOCK "
                               f"({r['tidal'].get('max_tide_m')} m high tide)")
            if r.get("proximity", {}).get("flagged"):
                drivers.append(f"{r['name']}: proximity convective threat")
            if r.get("radar", {}).get("flag") and r["radar"].get("flag") != "IMMINENT CONVECTIVE":
                pass
        if drivers:
            lines.append("**Atmospheric drivers:** " + "; ".join(drivers))
    else:
        lines.append("No asset currently crosses the advisory threshold. Routine monitoring.")
    lines.append("")

    # ============ 2. MULTI-VARIABLE PARAMETER MATRIX ============
    lines.append("## MULTI-VARIABLE PARAMETER MATRIX")
    lines.append("")
    lines.append("| Asset | Level | 24h (raw→corr) | Peak burst | Wind hazard | Tidal lock | "
                 "Proximity | Antecedent 7d | IMD now |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in ranked:
        rk = r["risk"]
        inten = r.get("intensity", {})
        burst = f"{inten.get('max1h_mm',0):.0f} mm/h ({inten.get('burst_band','-')})" if inten else "-"
        w = r.get("wind_kmh", 0) or 0
        wd = r.get("wind_dir", 0) or 0
        wind_h = f"{w:.0f} km/h ({compass(wd)} – {onshore_note(wd)})"
        if r.get("terrain", {}).get("modeled_gust_kmh"):
            wind_h += f" → gust ~{r['terrain']['modeled_gust_kmh']:.0f} (modeled)"
        tidal = r.get("tidal", {})
        tidal_s = f"{tidal.get('max_tide_m')} m" if tidal.get("overlap") else "—"
        prox = r.get("proximity", {})
        prox_s = f"{prox.get('count')} neigh" if prox.get("flagged") else "clear"
        ant = r.get("antecedent_7d_mm", 0) or 0
        ant_s = f"{ant:.0f} mm ({r['risk'].antecedent_state})"
        imd = r["imd"].get("colour") or r["imd"].get("color_label") or "n/a"
        lines.append(
            f"| {r['name']} | {LEVEL_EMOJI[rk.level]} {rk.level} | "
            f"{r.get('raw_mm',0):.1f}→{r.get('corrected_mm',0):.1f} mm | {burst} | {wind_h} | "
            f"{tidal_s} | {prox_s} | {ant_s} | {imd} |")
    lines.append("")
    lines.append("_Units: rain mm (24h accumulation & mm/h burst intensity); wind km/h; "
                 "tide height m above Chart Datum. **IMD official colour** is raw government "
                 "data (Green/Yellow/Orange/Red). **Operational risk level** (NOMINAL/ADVISORY/"
                 "WARNING/CRITICAL) is OUR alert after applying local impact dynamics (tidal "
                 "lock, bursts, saturation, proximity) — it may exceed the IMD colour when "
                 "local hazards warrant (flagged as OPERATIONAL OVERRIDE below)._")
    lines.append("")

    # ============ 3. VALIDATION CONTEXT (transparency before metrics) ============
    lines.append("## VALIDATION CONTEXT")
    lines.append("")
    deficits = []
    if observed_status != "ok":
        deficits.append(f"Observed rainfall (U1): {observed_status} → [Telemetry Deficit]")
    if nowcast_status != "ok":
        deficits.append(f"IMD nowcast (U2): {nowcast_status} → [Telemetry Deficit]")
    for r in ranked:
        if r.get("source") in ("unavailable",):
            deficits.append(f"{r['name']}: model + fallback both failed → [Telemetry Deficit]")
    # radar feed status (single line; per-asset dBZ pending ingestion)
    radar_note = "numeric dBZ pending ingestion → [Metadata Only]" if any(
        r.get("radar", {}).get("status") == "no_cell_data" for r in ranked) else None
    if deficits:
        for d in deficits:
            lines.append(f"  • {d}")
    else:
        lines.append("  • All primary feeds nominal.")
    if radar_note:
        lines.append(f"  • IMD Urban Radar Feed: ACTIVE | {radar_note}")
    if not (deficits or radar_note):
        lines.append("  • All primary feeds nominal.")
    lines.append("")

    # ============ 4. PER-ASSET DETAIL ============
    lines.append("— — —")
    lines.append("")
    for r in active[:5]:
        rk = r["risk"]
        imd_c = rk.imd_colour or "unavailable"
        lines.append(f"**{r['name']}** (IMD {imd_c}, operational: {rk.level}): "
                     f"{LEVEL_ACTION[rk.level]}")
        obs = r.get("observed", {})
        obs_date = obs.get("source_date")
        obs_txt = (f"observed({obs_date})={obs.get('actual_mm')}mm"
                   if obs.get("found") else "observed=[Telemetry Deficit]")
        imd_c = rk.imd_colour or "unavailable"
        lines.append(f"   basis: {rk.basis} | IMD official: {imd_c} | {obs_txt}")
        # explicit separation of IMD data vs OPERATIONAL risk level
        imd_lvl = {"Green": "NOMINAL", "Yellow": "ADVISORY",
                    "Orange": "WARNING", "Red": "CRITICAL"}.get(imd_c, "NOMINAL")
        if LEVEL_RANK[rk.level] > LEVEL_RANK.get(imd_lvl, 0):
            lines.append(f"   OPERATIONAL OVERRIDE: IMD data = {imd_c} "
                         f"({imd_lvl}), but operational risk = {rk.level} due to "
                         f"local impact dynamics (see escalations).")
        if rk.escalation_reasons:
            lines.append(f"   escalations: {'; '.join(rk.escalation_reasons)}")
        if r["imd"].get("message"):
            lines.append(f"   IMD nowcast: {r['imd']['message']}")
    lines.append("")

    # ============ 5. GROUND-TRUTH VERIFICATION LOG ============
    lines.append("## GROUND-TRUTH VERIFICATION (previous-day observed)")
    lines.append("")
    for r in ranked:
        obs = r.get("observed", {})
        if obs.get("found"):
            lines.append(f"  • {r['name']}: observed {obs.get('actual_mm')} mm "
                         f"({obs.get('category')}) on {obs.get('source_date')} "
                         f"| normal {obs.get('normal_mm')} mm")
        else:
            lines.append(f"  • {r['name']}: observed = [Telemetry Deficit] (no match in feed)")
    if radar_status:
        st = "ACTIVE" if radar_status.get("ok") else "UNAVAILABLE"
        lines.append(f"  • IMD Urban Radar Feed: {st} | Reflectivity Core: "
                     f"Visual Verification Required (numeric dBZ pending ingestion)")
    lines.append("")

    # ============ 6. SEVERE OUTLOOK + FOOTER ============
    lines.append("## SEVERE-SYSTEM OUTLOOK (3–7 day lead)")
    if severe:
        off = severe.get("official", {})
        if off.get("watch"):
            lines.append("**IMD PRE-CYCLONE WATCH active** — cyclonic disturbance developing "
                         "(issued ~72h ahead by RSMC New Delhi).")
        if off.get("outlook_url"):
            lines.append(f"IMD Tropical Weather Outlook: {off['outlook_url']}")
        alerts = severe.get("alerts", [])
        if alerts:
            lines.append("**Model-indicated precursors** (unverified):")
            for a in alerts:
                eta = a.get("eta_days")
                lines.append(f"  • {a['type'].upper()} — {a['region']} "
                             f"(~{eta}d); prob≈{a.get('probability')} [{a.get('confidence')}]")
        else:
            lines.append("No cyclone/low-pressure precursor meeting detection criteria in the next "
                         "7 days (model scan). IMD genesis probability on RSMC site (JS widget; "
                         "not auto-parsed).")
    else:
        lines.append("Severe outlook not computed this run.")
    lines.append("")

    # skill (honest calibration)
    if skill and skill.get("n_pairs"):
        cal = "calibrated" if skill.get("calibrated") else "indicative only"
        lines.append(f"**Forecast skill** vs IMD observed (event ≥ {skill['threshold_mm']} mm/24h, "
                     f"n={skill['n_pairs']}, {cal}): POD={skill['POD']}, FAR={skill['FAR']}, "
                     f"CSI={skill['CSI']}, ETS={skill['ETS']}, BSS={skill['BSS']}.")
        if not skill.get("calibrated"):
            lines.append(f"  _Note: {skill.get('note')}._")
    else:
        lines.append("**Forecast skill:** not yet calibrated (archive accumulating automatically; "
                     "paired forecast/observed days needed).")
    if bias_mm:
        sample = {k.title(): v for k, v in list(bias_mm.items())[:5]}
        lines.append(f"Mean forecast−observed bias by district (mm): {sample}")
    lines.append("")

    lines.append("_Method & sources: multi-model fusion of Open-Meteo ECMWF/ICON/GFS 24h "
                 "accumulation (fallback MET Norway api.met.no, CC-BY 4.0) + LOCI bias correction "
                 "+ IMD observed & nowcast as ground truth. Impact layers: IDF burst intensity, "
                 "7-day antecedent saturation, 3×3 proximity scan, Mumbai tidal drainage-lock "
                 "(chart datum), modeled terrain wind multiplier. Skill = AMS/WMO POD/FAR/CSI/ETS/"
                 "BSS vs IMD observed. Accuracy ceiling ≈ IMD district Day-1–5 (~75–80%). UNOFFICIAL "
                 "analysis — verify against official IMD (mausam.imd.gov.in, rsmcnewdelhi.imd.gov.in)._")
    return "\n".join(lines)


def _lvl(level: str) -> int:
    return {"CRITICAL": 3, "WARNING": 2, "ADVISORY": 1, "NOMINAL": 0}.get(level, 0)


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html_summary(summary: str) -> str:
    """Minimal Telegram HTML post-processing: preserve structure, add safe bolding."""
    if not summary:
        return summary
    lines = []
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith("=" * 10) or stripped.startswith("-" * 10):
            lines.append(f"<pre>{_esc(stripped)}</pre>")
        elif stripped.startswith("REGION") or stripped.startswith("ISSUED") or stripped.startswith("SOURCES") or stripped.startswith("SYSTEM STATUS"):
            lines.append(f"<b>{_esc(stripped)}</b>")
        elif stripped in {"DISTRICT WARNINGS (24h):", "COASTAL ADVISORY (YELLOW):", "SYNOPTIC VALIDATION", "SYSTEMIC INSIGHT (model-derived, no invented physics)", "DIRECTIVE: Cross-verify with official IMD (mausam.imd.gov.in). Consolidated risk maps attached (state synoptic + MMR/asset). Unofficial analyst product."}:
            lines.append(f"<b>{_esc(stripped)}</b>")
        elif stripped.startswith("- ") or stripped.startswith("• "):
            lines.append(_esc(line))
        elif stripped.startswith("  ") and stripped.upper() == stripped:
            lines.append(f"<b>{_esc(stripped)}</b>")
        else:
            lines.append(_esc(line))
    return "\n".join(lines)


def summarize(results: list[dict], region: str, run_date: str, severe: dict,
              radar_status: dict = None, analysis: dict = None) -> str:
    """Smart, adaptive ONE-PARAGRAPH-ish Telegram briefing (HTML-safe, <4096 chars).

    Tier routing off the ALREADY-COMPUTED risk objects (never re-derives severity,
    never invents tide heights, never uses a 5th "WATCH" level — IMD 4-level schema):
      Tier A (WARNING/CRITICAL present): lead with immediate-action alerts + drivers.
      Tier B (all NOMINAL but a tidal_watch exists): soften to "COASTAL MONITORING",
              explain the lock qualitatively, "no downtime".
      Tier C (all NOMINAL, no tidal_watch): ultra-condensed 2-line all-clear.
    Plain text + emojis (reliable; avoids Markdown parse 400s).
    If `analysis` (Phase grid/storm-track/convective) is provided, a grounded
    SYSTEMIC INSIGHT block is appended. All numbers trace to model output or
    published thresholds; missing data is reported as unavailable, never faked.
    """
    ranked = sorted(results, key=lambda r: _lvl(r["risk"].level), reverse=True)
    top_lvl = ranked[0]["risk"].level
    real_alerts = [r for r in ranked if r["risk"].level in ("WARNING", "CRITICAL")]
    tidal_watch_assets = [r for r in ranked if r["risk"].tidal_watch]

    def confidence_tag(r):
        """Zero-cost confidence tag from the 3-model spread we ALREADY fetch
        per city. Low spread => models agree => HIGH; large spread => LOW.
        If spread is unavailable we say so honestly (never invent a level)."""
        sp = r_slug_spread(r)
        if sp is None:
            return " [CONFIDENCE: n/a]"
        return " [FORECAST CONFIDENCE: HIGH]" if sp <= 3.0 else " [FORECAST CONFIDENCE: LOW]"

    def r_slug_spread(r):
        v = r.get("spread")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # ---- header / system status (IMD bulletin register: no emoji) ----
    if top_lvl == "CRITICAL":
        status = "RED — SEVERE WEATHER WARNING"
    elif top_lvl == "WARNING":
        status = "ORANGE — WARNING"
    elif tidal_watch_assets:
        status = "YELLOW — ADVISORY (COASTAL MONITORING)"
    else:
        status = "GREEN — NO WARNING"

    lines = []
    lines.append("=" * 64)
    lines.append("   INDIA METEOROLOGICAL DEPARTMENT — DISTRICT RAINFALL WARNING")
    lines.append("   (Unofficial analyst product — cross-verify with mausam.imd.gov.in)")
    lines.append("=" * 64)
    lines.append(f"REGION         : {region.upper()}")
    lines.append(f"ISSUED (IST)   : {run_date}  |  VALIDITY : 24 H")
    lines.append(f"SOURCES        : IMD district warnings (authoritative) + "
                 f"Open-Meteo multi-model grid (model estimate)")
    lines.append(f"SYSTEM STATUS  : {status}")
    lines.append("")

    # ---- Tier A: immediate-action alerts up top (IMD warning register) ----
    if real_alerts:
        lines.append("DISTRICT WARNINGS (24h):")
        for r in real_alerts:
            rk = r["risk"]
            imd_c = (r["imd"] or {}).get("colour") or "UNKNOWN"
            band = imd_terms.amount_band(r["corrected_mm"])
            rng = imd_terms.band_range(r["corrected_mm"])
            haz = ""
            nc = r.get("imd") or {}
            if isinstance(nc, dict) and nc.get("cats"):
                labs = imd_terms.hazard_labels(nc.get("cats"))
                if labs:
                    haz = "; " + ", ".join(labs)
            drivers = []
            if rk.burst_concern:
                drivers.append(f"{r['intensity'].get('burst_band', '')} burst")
            if r.get("proximity", {}).get("flagged"):
                drivers.append("proximity threat")
            if rk.antecedent_state not in ("NORMAL",):
                drivers.append(f"{rk.antecedent_state.lower()} antecedent")
            if rk.tidal_lock and not rk.tidal_watch:
                drivers.append("tidal drainage-lock")
            d = f" [{', '.join(drivers)}]" if drivers else ""
            lines.append(
                f"  {r['name'].upper()} (IMD {imd_c}): "
                f"{imd_terms.color_amount_band(r.get('imd', {}).get('color_code') or 0)}"
                f"{haz}. Model grid estimate {r['corrected_mm']:.1f} mm/24h, "
                f"peak {r['intensity'].get('max1h_mm', 0):.0f} mm/h"
                f"{d}{confidence_tag(r)}")
        lines.append("")

    # ---- Tier B: coastal monitoring (light-rain tidal lock) ----
    elif tidal_watch_assets:
        lines.append("COASTAL ADVISORY (YELLOW):")
        for r in tidal_watch_assets:
            lw = (r.get("tidal") or {}).get("lock_window") or {}
            if lw.get("active") and lw.get("start_ist") and lw.get("end_ist"):
                sample_tag = " [SAMPLE TIDE — not observed]" if lw.get("sample_data") else ""
                win = (f" DRAINAGE-LOCK WINDOW {lw['start_ist']}–{lw['end_ist']} "
                       f"(peak tide {lw['max_tide_m']} m, source {lw.get('source')}{sample_tag}).")
            else:
                win = " (tidal overlap window: timing only; exact lock span unavailable)."
            lines.append(
                f"  {r['name'].upper()} [MONITORING]{confidence_tag(r)}: rainfall light "
                f"({r['corrected_mm']:.1f} mm/24h) but timing overlaps a tidal "
                f"drainage-lock window.{win} Outfalls temporarily restrict gravity "
                f"discharge; minor low-lying pooling possible. No facility "
                f"shutdowns required.")
        other = [r["name"] for r in ranked if not r["risk"].tidal_watch
                 and r["risk"].level == "NOMINAL"]
        if other:
            lines.append(f"  Other assets ({len(other)}): NOMINAL / safe operational windows.")
        lines.append("")

    # ---- Tier C: ultra-condensed all-clear ----
    else:
        lines.append("No warning in force for monitored assets.")
        lines.append("Logistics, shipping and outdoor operations remain unrestricted. "
                     "Routine monitoring continues.")
        lines.append("")

    # ---- synoptic validation sub-block ----
    lines.append("-" * 64)
    lines.append("SYNOPTIC VALIDATION")
    radar_ok = bool(radar_status and radar_status.get("ok"))
    if radar_ok:
        radar_line = ("- IMD Doppler Radar: ACTIVE (feed live) | reflectivity NOT "
                      "ingested — visual review at "
                      "mausam.imd.gov.in/imd_latest/contents/index_radar.php?id=Mumbai")
    else:
        radar_line = "- IMD Doppler Radar: TELEMETRY DEFICIT (feed unreachable)"
    lines.append(radar_line)
    if severe and (severe.get("has_signal") or severe.get("alerts")):
        outlook = "ALERT PRECURSOR IDENTIFIED (verify vs IMD)"
    else:
        outlook = "CLEAR — no active cyclonic precursors in scanned AS/BoB basins (model + IMD genesis scan)"
    lines.append(f"- 3-7 Day Severe Outlook: {outlook}")
    lines.append("")
    # ---- grounded analysis (storm-track / convective / regions) [Phase] ----
    if analysis and analysis.get("grid_ok"):
        tr = analysis.get("track") or {}
        cv = analysis.get("conv") or {}
        regs = analysis.get("regions") or {}
        lines.append("-" * 64)
        lines.append("SYSTEMIC INSIGHT (model-derived, no invented physics)")
        if tr.get("status") == "MOVING" and (tr.get("speed_kmh") or 0) >= 1:
            lines.append(
                f"- Rain-band track: moving {tr['dir16']} "
                f"({tr['bearing_deg']:.0f}deg) at {tr['speed_kmh']:.0f} km/h "
                f"[Model: Open-Meteo {analysis.get('model_grid','?')} 0.5deg grid, "
                f"mass-weighted centroid t0->t12h]")
        elif (tr.get("speed_kmh") or 0) < 1 and tr.get("status") == "MOVING":
            lines.append(
                f"- Rain-band track: STATIONARY / no coherent advection "
                f"(model centroid speed <1 km/h) "
                f"[Model: Open-Meteo {analysis.get('model_grid','?')} 0.5deg grid, "
                f"mass-weighted centroid t0->t12h]")
        else:
            lines.append(
                f"- Rain-band track: {tr.get('status','n/a')} "
                f"(state-wide 24h total < 0.1 mm; no coherent advection signal)")
        if cv.get("li_available"):
            if cv["triggered"]:
                lines.append(
                    f"- Convective flag: ELEVATED (CAPE {cv['max_cape']:.0f} J/kg, "
                    f"LI {cv['min_li']:.1f}C meet published severe criterion "
                    f"CAPE>=2500 & LI<=-3). Microburst/downpour risk. "
                    f"[Model: {analysis.get('model_conv','?')}]")
            else:
                lines.append(
                    f"- Convective flag: nominal (max CAPE {cv['max_cape']:.0f} J/kg, "
                    f"LI {cv['min_li']:.1f}C below severe criterion). "
                    f"Stable stratiform profile.")
        else:
            lines.append(
                f"- Convective flag: DATA UNAVAILABLE (lifted_index not served by "
                f"grid model; max CAPE seen {cv.get('max_cape')} J/kg). "
                f"No convective upgrade asserted.")
        # stationary-stall alert (grounded: slow centroid + high local peak)
        stall = analysis.get("stall")
        if stall:
            lines.append(f"- STALL ALERT: {stall}")
        if regs:
            parts = []
            for name, d in regs.items():
                s = f"{name}={d['max_mm']:.0f}mm@{d['n_points']}pts"
                cn = d.get("clustered_node")
                if cn:
                    s += (f" (LOCALIZED CLUSTER: {cn['max_mm']:.0f}mm node vs "
                          f"region-mean {cn['mean_mm']:.0f}mm)")
                parts.append(s)
            lines.append(f"- Regional 24h max (model grid): {', '.join(parts)}")
            # Honest provenance note: model grid is 0.5deg-interpolated and can
            # under-capture vs IMD's district warnings (which use denser obs).
            # Where they disagree, the IMD warning above is authoritative.
            if any(d['max_mm'] < 64.5 for d in regs.values()):
                lines.append("- NOTE: model grid maxima above are < IMD warning thresholds "
                             "for some regions — the IMD district warnings (top of this "
                             "brief) are authoritative; the grid shows spatial detail only.")
        if not analysis.get("antecedent_available"):
            lines.append("- Antecedent soil saturation: UNAVAILABLE (model soil_moisture "
                         "not served for point forecast; requires ingestion stream).")
        lines.append("")
    elif analysis is not None:
        # grid layer failed entirely (all chunks 429/timeout) -> honest caveat,
        # NOT a silent all-clear. Asset-level alerts above still stand.
        lines.append("-" * 64)
        lines.append("SYSTEMIC INSIGHT: GRID ANALYSIS UNAVAILABLE")
        lines.append("- Model grid layer could not be fetched this cycle "
                     "(API rate-limit / timeout). Storm-track, convective flag, "
                     "regional rollup and the consolidated maps are omitted. "
                     "Asset-level rainfall/tidal/advisory above remains valid. "
                     "Will retry next scheduled run.")

    lines.append("-" * 64)
    lines.append("DIRECTIVE: Cross-verify with official IMD (mausam.imd.gov.in). "
                 "Consolidated risk maps attached (state synoptic + MMR/asset). "
                 "Unofficial analyst product.")
    lines.append("=" * 64)

    return "\n".join(lines)


if __name__ == "__main__":
    from engine.classify import classify
    dummy = [{"name": "Mumbai", "risk": classify(8.0, 80, 1.2),
              "wind_kmh": 18, "wind_dir": 262, "intensity": {"max1h_mm": 12, "burst_band": "MODERATE"},
              "antecedent_7d_mm": 120, "proximity": {"flagged": False},
              "tidal": {"overlap": False}, "radar": {"status": "ok"},
              "imd": {"colour": "Green"}, "observed": {"found": True, "actual_mm": 0.2,
              "category": "LD", "source_date": "2026-08-21"}, "source": "open-meteo",
              "raw_mm": 9.4, "corrected_mm": 9.4},
             {"name": "Pune", "risk": classify(120.0, 85, 4.0),
              "wind_kmh": 12, "wind_dir": 90, "intensity": {}, "antecedent_7d_mm": 10,
              "proximity": {"flagged": False}, "tidal": {"overlap": False},
              "radar": {"status": "ok"}, "imd": {"colour": "Green"},
              "observed": {"found": False}, "source": "open-meteo", "raw_mm": 120, "corrected_mm": 120}]
    print(compose(dummy, "Maharashtra", radar_status={"ok": True}))
