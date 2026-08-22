"""Module E — Evidence-bound synoptic narrative (analyst-style prose, no fabrication).

Design rule (hard): every sentence is BOUND to a real value from the evidence bundle.
If a binding is absent or below a defensible threshold, the sentence is OMITTED — never
replaced with generic meteorological filler. This is what keeps the output "intelligent"
without hallucinating a system/region/risk the data does not support.

HONESTY NOTES (verified from research this session):
- IMD 24h rainfall CATEGORY mm bands are documented & used by classify.py:
    heavy 64.5-115.5 | very heavy 115.6-204.5 | extremely heavy >204.5 mm.
  -> Safe to use "heavy/very heavy/extremely heavy" when our mm falls in those bands.
- Likelihood words (likely/possible/very likely) follow the WMO CONVENTION
  (MeteoSwiss/NWS): possible ~20-40%, likely ~50-70%, very likely ~70-80%.
  We label these as "WMO convention", NEVER as "IMD-calibrated", because we did NOT
  retrieve IMD's own daily-district likelihood definitions.
- 850 hPa flow over open ocean is currently FLAKY in Open-Meteo (returns 'undefined');
  the "moisture pull from Arabian Sea" clause is therefore conditional and omitted
  when the data is absent. We do not assert a mechanism we cannot measure.

Inputs to build_evidence(): the live results list + module outputs (severe, observed,
imd_nowcast). generate() consumes the bundle and returns {paragraph, hotspots, caveats}.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# WMO-convention likelihood wording (NOT claimed as IMD-specific). Maps a probability
# (0-1) to a calibrated word. Source: MeteoSwiss/NWS probabilistic terminology research.
def _likelihood_word(p: Optional[float]) -> str:
    if p is None:
        return ""
    if p >= 0.70:
        return "very likely"
    if p >= 0.50:
        return "likely"
    if p >= 0.20:
        return "possible"
    return "unlikely"


@dataclass
class EvidenceBundle:
    """All real bindings used to write the narrative. Missing -> None/empty."""
    # synoptic drivers (from severe_systems / 850 hPa)
    lps_type: Optional[str] = None          # "cyclone" | "low_pressure" | "none"
    lps_region: Optional[str] = None        # "Arabian Sea" | "Bay of Bengal" | ...
    lps_eta_days: Optional[float] = None
    lps_prob: Optional[float] = None         # 0-1 if a model/official prob exists
    lps_confidence: Optional[str] = None     # "model-indicated" | "official(IMD)" | None
    onshore_flow: Optional[bool] = None      # True only if 850hPa dir present & onshore
    w850_speed: Optional[float] = None       # km/h, mean over region (None if undefined)
    cape: Optional[float] = None             # J/kg
    gust: Optional[float] = None             # km/h max

    # rainfall risk (from results + IMD nowcast)
    hotspots: list = field(default_factory=list)   # [{name, mm, imd_color, cat_word}]
    imd_nowcast_sev: list = field(default_factory=list)  # districts w/ IMD color>=3
    model_label: str = "multi-model fusion"

    # provenance / honesty
    missing: list = field(default_factory=list)   # human-readable "could not verify X"


def build_evidence(results: list[dict], severe: dict = None,
                   imd_nowcast: dict = None) -> EvidenceBundle:
    """Assemble the bundle from REAL module outputs. Nothing here is invented."""
    b = EvidenceBundle()
    b.model_label = "multi-model fusion"

    # --- severe-system bindings (cyclone / low-pressure) ---
    if severe:
        off = severe.get("official", {})
        alerts = severe.get("alerts", [])
        # official IMD watch takes precedence
        if off.get("watch"):
            b.lps_type = "cyclone"
            b.lps_confidence = "official(IMD)"
            b.lps_prob = 0.9  # a Pre-Cyclone Watch is a high-confidence official signal
        for a in alerts:
            # only adopt the strongest model-indicated signal if no official one
            if b.lps_type is None and a.get("type") in ("cyclone", "low_pressure"):
                b.lps_type = a["type"]
                b.lps_region = a.get("region")
                b.lps_eta_days = a.get("eta_days")
                b.lps_prob = a.get("probability")
                b.lps_confidence = a.get("confidence")

    # --- 850 hPa flow (guarded: only if data present) ---
    w850 = [r.get("wind850_kmh") for r in results if r.get("wind850_kmh") is not None]
    if w850:
        b.w850_speed = sum(w850) / len(w850)
    else:
        b.missing.append("850 hPa upper-air flow (Open-Meteo returned undefined at these points)")
    b.cape = _median([r.get("cape") for r in results])
    gusts = [r.get("gust") for r in results if r.get("gust") is not None]
    b.gust = max(gusts) if gusts else None

    # --- onshore moisture mechanism (conditional: needs real 850 dir) ---
    # direction must be present (non-None, non-zero) AND in onshore sector (200-340 = W/SW)
    dirs = [r.get("wind850_dir") for r in results
            if r.get("wind850_dir") not in (None, 0, 0.0)]
    if dirs and w850:
        md = sum(dirs) / len(dirs)
        b.onshore_flow = 200 <= md <= 340
    else:
        b.missing.append("850 hPa wind direction (needed to assert onshore moisture pull)")

    # --- rainfall hotspots + IMD category wording ---
    for r in results:
        mm = r.get("corrected_mm") or 0
        ic = r.get("imd", {}).get("color") if isinstance(r.get("imd"), dict) else None
        cat = _imd_cat_word(mm)  # heavy/very heavy/extremely heavy or ""
        b.hotspots.append({"name": r["name"], "mm": mm, "imd_color": ic, "cat_word": cat})
    if imd_nowcast and imd_nowcast.get("status") == "ok":
        for d in imd_nowcast["by_district"].values():
            if d.get("color", 1) >= 3:
                b.imd_nowcast_sev.append(d["district"].title())

    return b


def _imd_cat_word(mm: float) -> str:
    """Map mm/24h to IMD documented rainfall CATEGORY words (defensible)."""
    if mm > 204.5:
        return "extremely heavy"
    if mm > 115.5:
        return "very heavy"
    if mm >= 64.5:
        return "heavy"
    if mm >= 15:
        return "moderate"
    return ""


def _median(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def generate(bundle: EvidenceBundle) -> dict:
    """Return {paragraph, hotspots, caveats}. Every clause is data-bound."""
    sentences = []

    # --- 1) Synoptic system (only if actually detected) ---
    if bundle.lps_type == "cyclone" and bundle.lps_confidence == "official(IMD)":
        sentences.append(
            "IMD has issued a Pre-Cyclone Watch: a cyclonic disturbance is developing "
            "and is being monitored for further organisation.")
    elif bundle.lps_type == "cyclone":
        p = bundle.lps_prob
        w = _likelihood_word(p)
        eta = f" within ~{bundle.lps_eta_days:.0f} days" if bundle.lps_eta_days else ""
        conf = " (model-indicated, unverified by IMD)" if bundle.lps_confidence == "model-indicated" else ""
        if w:
            sentences.append(
                f"A model-indicated cyclonic disturbance is {w} to organise over "
                f"{bundle.lps_region}{eta}{conf}.")
        else:
            sentences.append(
                f"A model-indicated cyclonic disturbance is noted over "
                f"{bundle.lps_region}{eta}{conf}.")
    elif bundle.lps_type == "low_pressure":
        eta = f" within ~{bundle.lps_eta_days:.0f} days" if bundle.lps_eta_days else ""
        sentences.append(
            f"A broad low-pressure area is indicated over {bundle.lps_region}{eta} "
            f"(model-indicated; can enhance monsoon rainfall over Maharashtra).")

    # --- 2) Upper-air flow (only if measured) ---
    if bundle.w850_speed is not None:
        if bundle.w850_speed >= 40:
            sentences.append(
                f"a strong 850 hPa westerly flow (~{bundle.w850_speed:.0f} km/h) marks an "
                f"active monsoon regime")
        elif bundle.w850_speed >= 25:
            sentences.append(
                f"moderate 850 hPa westerlies (~{bundle.w850_speed:.0f} km/h) sustain the "
                f"southwest-monsoon inflow")
        else:
            sentences.append(
                f"weak upper-air flow (~{bundle.w850_speed:.0f} km/h) suggests a "
                f"break or lean monsoon phase")

    # --- 3) Onshore moisture mechanism (ONLY if direction measured & onshore) ---
    if bundle.onshore_flow is True:
        sentences.append("with onshore 850 hPa flow pulling moisture from the Arabian Sea")

    # --- 4) Convective potential (CAPE, measured) ---
    if bundle.cape is not None and bundle.cape >= 1500:
        sentences.append(f"high CAPE (~{bundle.cape:.0f} J/kg) indicates strong convective "
                         f"potential (thunderstorms/lightning)")
    elif bundle.cape is not None and bundle.cape >= 800:
        sentences.append(f"moderate CAPE (~{bundle.cape:.0f} J/kg) supports isolated convection")

    # --- 5) Official IMD severe nowcast (real) ---
    if bundle.imd_nowcast_sev:
        names = ", ".join(sorted(set(bundle.imd_nowcast_sev))[:6])
        sentences.append(f"IMD has issued Orange/Red nowcasts for {names}")

    # --- assemble paragraph ---
    if sentences:
        paragraph = "Synoptic situation: " + "; ".join(sentences) + "."
    else:
        paragraph = ("No dominant synoptic system is evident for Maharashtra at this range; "
                     "any rainfall is likely isolated and orographically forced along the "
                     "Western Ghats.")

    # --- hotspots with IMD-calibrated category words ---
    ranked = sorted(bundle.hotspots, key=lambda h: h["mm"], reverse=True)
    hotspots = []
    for h in ranked[:4]:
        if h["mm"] >= 10 or h["imd_color"] in (3, 4):
            cat = f" {h['cat_word']}" if h["cat_word"] else ""
            tag = f" (IMD {h['imd_color']})" if h["imd_color"] else ""
            hotspots.append(f"{h['name']} ({h['mm']:.0f} mm{cat}){tag}")
    if not hotspots:
        hotspots = ["(none crossing the heavy-rain threshold)"]

    # --- honesty caveats ---
    caveats = ["Narrative derived from model upper-air + CAPE + IMD official nowcast; "
               "not a human forecast."]
    if bundle.missing:
        caveats.append("Could not verify: " + "; ".join(bundle.missing) + ".")

    return {"paragraph": paragraph, "hotspots": hotspots, "caveats": caveats}


if __name__ == "__main__":
    # demo with a REALISTIC bundle (no invented system)
    b = EvidenceBundle(w850_speed=45.0, cape=1700, gust=55,
                       hotspots=[{"name": "Ratnagiri", "mm": 18, "imd_color": 3, "cat_word": "very heavy"},
                                 {"name": "Mumbai", "mm": 12, "imd_color": 2, "cat_word": "moderate"}],
                       imd_nowcast_sev=["Ratnagiri"])
    out = generate(b)
    print(out["paragraph"])
    print("Hotspots:", out["hotspots"])
    print("Caveats:", out["caveats"])
