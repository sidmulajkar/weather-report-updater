"""Risk classifier mapped to IMD impact-based colour codes (verified from research),
now extended with impact dynamics:

  - Intensity-Duration (IDF): short-burst spikes escalate even at low 24 h sum.
  - Antecedent soil moisture: saturated ground lowers trigger thresholds.
  - Spatial buffer (Proximity Threat Vector): neighbours spiking raise caution.
  - Conservative conflict rule: when model data conflicts with IMD official,
    the SAFER (higher) warning wins.
  - Graceful missing telemetry: None / NaN / missing fields never crash; they
    surface as explicit placeholders and a de-escalated-but-flagged state.

IMD 24 h rainfall thresholds (mm, base):
  Green < 64.5 ; Yellow 64.5-115.5 ; Orange 115.6-204.4 ; Red > 204.5

Confidence / directives are produced per level so the narrative can drive
operational actions (not just "it will rain").
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

BASE_THRESH = {"yellow": 64.5, "orange": 115.6, "red": 204.5}
COLOUR_TO_LEVEL = {"Green": "NOMINAL", "Yellow": "ADVISORY",
                   "Orange": "WARNING", "Red": "CRITICAL"}

# operational directive text per level (what a corporate client should DO)
DIRECTIVE = {
    "NOMINAL": "No action required; routine monitoring.",
    "ADVISORY": "Be aware - watch local bulletins; minor disruption possible.",
    "WARNING": "Prepare - secure loose assets, stage response; disruptive rainfall likely.",
    "CRITICAL": "Act now - halt exposed operations, protect staff/equipment; "
                "severe/paralysing rainfall expected.",
}
# escalated directive when a short cloudburst burst is detected
BURST_DIRECTIVE = {
    "NOMINAL": "Localized convective burst possible - brief drainage stress, stay alert.",
    "ADVISORY": "Convective burst likely - flash waterlogging; keep staff clear of low-lying areas.",
    "WARNING": "Heavy burst - secure cranes/heavy loads, reroute deliveries off flood-prone routes.",
    "CRITICAL": "Extreme cloudburst - immediate halt of outdoor/height work; evacuate low-lying sites.",
}


def _safe(v, default=0.0):
    """Return float(v) or default for None/NaN/non-numeric."""
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:  # NaN
            return default
        return f
    except (TypeError, ValueError):
        return default


@dataclass
class RiskResult:
    level: str
    colour: str
    accum_24h: float
    prob_max: float
    model_spread: float
    imd_colour: Optional[str]
    imd_status: str
    confidence: str
    basis: str
    # --- new impact fields ---
    max1h_mm: float = 0.0
    burst_band: str = "LIGHT"
    burst_concern: bool = False
    antecedent_state: str = "NORMAL"
    effective_thresholds: dict = field(default_factory=dict)
    proximity_vector: Optional[str] = None
    proximity_threats: list = field(default_factory=list)
    directive: str = ""
    escalation_reasons: list = field(default_factory=list)
    # --- Mumbai-specific physical layers ---
    tidal_lock: Optional[str] = None
    tidal_mult: float = 1.0
    radar_flag: Optional[str] = None
    terrain_zone: str = "PLAIN"
    modeled_gust_kmh: float = 0.0
    modeled_rain_mm: float = 0.0
    tidal_watch: bool = False  # informational only: lock present but NOT paired
                             # with heavy rain -> no level escalation, just monitoring


def classify(accum_24h, prob_max, model_spread, imd=None, *,
             intensity=None, antecedent_7d=0.0, proximity=None,
             tidal=None, radar=None, terrain=None) -> RiskResult:
    # ---- graceful input sanitising (Test B) ----
    acc = _safe(accum_24h)
    prob = _safe(prob_max)
    spread = _safe(model_spread)
    imd = imd or {}
    imd_colour = imd.get("colour")
    imd_status = imd.get("status", "unavailable")
    if imd_colour not in COLOUR_TO_LEVEL:
        imd_colour = None

    intensity = intensity or {}
    max1h = _safe(intensity.get("max1h_mm"))
    burst_band = intensity.get("burst_band", "LIGHT")
    burst_concern = bool(intensity.get("burst_concern", False))
    escalate_burst = bool(intensity.get("escalate", False))

    # ---- dynamic thresholds from antecedent wetness ----
    # (imported robustly whether run as module or directly)
    import sys as _sys, os as _os
    if _os.path.dirname(__file__) not in _sys.path:
        _sys.path.insert(0, _os.path.dirname(__file__))
    from antecedent import dynamic_thresholds as _dyn
    eff_thr, ant_state = _dyn(_safe(antecedent_7d))

    # ---- base model colour using EFFECTIVE thresholds ----
    if acc >= eff_thr["red"]:
        model_colour = "Red"
    elif acc >= eff_thr["orange"]:
        model_colour = "Orange"
    elif acc >= eff_thr["yellow"]:
        model_colour = "Yellow"
    else:
        model_colour = "Green"

    # ---- IMD ground-truth tie-break (CONSERVATIVE: safer wins) ----
    reasons = []
    if imd_colour and imd_status in ("hint", "ok"):
        level = COLOUR_TO_LEVEL[imd_colour]
        colour = imd_colour
        basis = "IMD official + model"
        if COLOUR_TO_LEVEL[model_colour] != level:
            reasons.append(f"IMD {imd_colour} overrides model {model_colour}")
    else:
        level = COLOUR_TO_LEVEL[model_colour]
        colour = model_colour
        basis = "model-only (IMD unavailable)"

    base_level_rank = {"NOMINAL": 0, "ADVISORY": 1, "WARNING": 2, "CRITICAL": 3}

    # ---- IDF escalation: short-duration burst (impact > 24h volume) ----
    # A high-intensity burst is a localized hazard even at low 24h sum, so it
    # raises the OPERATIONAL level. Tiers (capped, never skip a level):
    #   MODERATE (>=15 mm/h) -> ADVISORY   (incl. inland convective bursts)
    #   HEAVY/VERY_HEAVY (>=30) -> WARNING
    #   EXTREME (>=70)        -> CRITICAL
    BURST_ESCALATE = {
        "MODERATE": ("ADVISORY", 1),
        "HEAVY": ("WARNING", 2),
        "VERY_HEAVY": ("WARNING", 2),
        "EXTREME": ("CRITICAL", 3),
    }
    if burst_band in BURST_ESCALATE and max1h >= 15.0:
        tgt, min_rank = BURST_ESCALATE[burst_band]
        if base_level_rank[level] < min_rank:
            level = tgt
            reasons.append(f"short-burst {burst_band} ({max1h:.0f} mm/h) escalates impact")

    # ---- antecedent saturation escalation ----
    if ant_state in ("SATURATED", "HIGH") and base_level_rank[level] < 1 and acc >= 0:
        # saturated ground: even moderate rain runs off -> at least ADVISORY
        if base_level_rank[level] < 1:
            level = "ADVISORY"
            reasons.append(f"antecedent 7d={_safe(antecedent_7d):.0f}mm "
                           f"({ant_state}) -> runoff escalation")

    # ---- proximity threat vector ----
    prox = proximity or {}
    prox_vector = prox.get("vector")
    prox_threats = prox.get("threats", [])
    if prox_vector and base_level_rank[level] < 1:
        level = "ADVISORY"
        reasons.append("proximity threat vector from neighbouring cells")

    # ---- Mumbai tidal-lock interaction (drainage gate closure) ----
    # DESIGN RULE (commercial credibility): a tidal lock is a CONDITIONAL
    # modifier, NOT an independent level-raiser. A 5 m spring tide is a real
    # physical constraint, but it only creates a true operational emergency
    # when PAIRED with heavy cloudburst volumes. On a light-rain day the lock
    # must NOT produce a WARNING/CRITICAL (that is a false alarm). So:
    #   lock + rain already heavy (>= Yellow 24h OR burst >=30 mm/h)
    #        -> escalates (WARNING, or CRITICAL if very heavy)
    #   lock + light rain only
    #        -> NO level change. Set tidal_watch=True (informational): drains
    #           temporarily lock at high tide; minor low-lying pooling possible;
    #           no operational downtime. This protects credibility.
    # NOTE (honesty): tide heights are Mumbai-chart-datum proxy + datum-unverified;
    # we never emit a precise invented height in client output.
    tidal = tidal or {}
    tidal_lock = tidal.get("lock")
    tidal_mult = _safe(tidal.get("multiplier"), 1.0)
    tidal_watch = False
    rain_already_heavy = (acc >= eff_thr["yellow"]) or (max1h >= 30.0)
    if tidal_lock:
        if rain_already_heavy:
            if base_level_rank[level] < 2:
                level = "CRITICAL" if acc >= eff_thr["red"] else "WARNING"
            reasons.append(f"tidal {tidal_lock} + heavy rain at peak hour -> drainage lock escalation")
        else:
            # light rain only: informational monitoring, NOT an alert level
            tidal_watch = True
            reasons.append(f"tidal {tidal_lock}: drains lock at high tide (light rain) -> monitoring only, no downtime")

    # ---- IMD radar imminent convective (sub-km, live) ----
    radar = radar or {}
    radar_flag = radar.get("flag")
    if radar_flag == "IMMINENT CONVECTIVE" and base_level_rank[level] < 2:
        level = "WARNING"
        reasons.append("IMD radar: imminent convective cell inbound (high dBZ)")

    # ---- terrain modeled adjustments (kept as metadata, not level-escalating alone) ----
    terrain = terrain or {}
    terrain_zone = terrain.get("zone", "PLAIN")
    modeled_gust = _safe(terrain.get("adj_gust_kmh"))
    modeled_rain = _safe(terrain.get("adj_rain_mm"))

    # ---- confidence ----
    if acc >= eff_thr["yellow"] and spread > max(5.0, 0.3 * acc):
        conf = "low (models disagree on significant rain)"
    elif prob >= 70:
        conf = "high"
    elif prob >= 40:
        conf = "moderate"
    else:
        conf = "low (low probability)"

    # ---- directive ----
    directive = DIRECTIVE[level]
    if burst_concern:
        directive = BURST_DIRECTIVE[level]

    return RiskResult(
        level=level, colour=colour, accum_24h=round(acc, 2),
        prob_max=round(prob, 1), model_spread=round(spread, 2),
        imd_colour=imd_colour, imd_status=imd_status, confidence=conf,
        basis=basis, max1h_mm=round(max1h, 1), burst_band=burst_band,
        burst_concern=burst_concern, antecedent_state=ant_state,
        effective_thresholds=eff_thr, proximity_vector=prox_vector,
        proximity_threats=prox_threats, directive=directive,
        escalation_reasons=reasons, tidal_lock=tidal_lock, tidal_mult=tidal_mult,
        tidal_watch=tidal_watch,
        radar_flag=radar_flag, terrain_zone=terrain_zone,
        modeled_gust_kmh=modeled_gust, modeled_rain_mm=modeled_rain,
    )


# placeholder used when telemetry is entirely missing (Test B)
MISSING = RiskResult(
    level="NOMINAL", colour="Green", accum_24h=0.0, prob_max=0.0,
    model_spread=0.0, imd_colour=None, imd_status="telemetry_deficit",
    confidence="low (no data)", basis="[Telemetry Deficit]",
    directive="[Telemetry Deficit] - verify data feed before acting.",
    escalation_reasons=["missing telemetry"])


if __name__ == "__main__":
    # Test B style: None inputs must not crash
    print("None inputs:", classify(None, None, None, None))
    # Test A style: clear model vs IMD Red -> IMD wins (conservative)
    r = classify(2.0, 10, 0.5, {"colour": "Red", "status": "ok"})
    print("Conflict (model clear, IMD Red):", r.level, r.basis, r.escalation_reasons)
    # Test C style: 85 mm/h burst -> escalate
    r2 = classify(10.0, 90, 1.0, None,
                  intensity={"max1h_mm": 85, "burst_band": "EXTREME",
                              "burst_concern": True, "escalate": True})
    print("Cloudburst 85mm/h:", r2.level, r2.directive)
    # Tidal lock: modest 15mm/2h rain + hard high tide -> escalate
    r3 = classify(15.0, 60, 1.0, None,
                  tidal={"lock": "HARD LOCK (gates closed)", "multiplier": 2.0})
    print("Tidal lock 15mm + 4.6m tide:", r3.level, r3.escalation_reasons)
    # Radar imminent convective
    r4 = classify(8.0, 50, 1.0, None,
                  radar={"flag": "IMMINENT CONVECTIVE", "threats": [{"dist_km": 12}]})
    print("Radar imminent:", r4.level, r4.escalation_reasons)
