"""Antecedent soil-moisture & runoff tracking (7-day cumulative rainfall).

Concept: early-monsoon rain hits dry, absorbent ground; by late August the soil
across Maharashtra is saturated, so new rain becomes immediate runoff/flooding.
We track rolling 7-day cumulative *observed* rainfall per district and, when it
crosses a saturation threshold, *lower the alert trigger limits* (dynamic
thresholds) so a modest forecast escalates further than it would in dry soil.

This is a simplified, defensible proxy (no real soil-moisture model). The
7-day cumulative observed rainfall is a standard antecedent-runoff proxy used in
operational flood guidance (e.g. USDA NRCS antecedent precipitation index idea,
simplified to a fixed 7-day window here).
"""
from __future__ import annotations
from typing import Optional

# 7-day cumulative observed rainfall (mm) thresholds for Maharashtra monsoon.
# Crossing SATURATED lowers triggers; crossing HIGH keeps them lowered.
SATURATION_MM = 150.0   # ~21 mm/day avg over a week -> ground largely wet
HIGH_MM = 250.0         # deeply saturated, runoff-dominated

# How much to lower each IMD 24 h colour threshold when saturated (mm).
# Applied as: effective_threshold = base - drop.
SAT_DROP = {"yellow": 20.0, "orange": 30.0, "red": 30.0}


def seven_day_total(archive_rows: list[dict]) -> float:
    """Sum observed_mm over the last 7 dated rows for a district.

    `archive_rows` = list of {"date": iso, "observed_mm": float|None}.
    """
    if not archive_rows:
        return 0.0
    valid = [r for r in archive_rows if r.get("observed_mm") is not None]
    recent = sorted(valid, key=lambda r: r["date"])[-7:]
    return round(sum(r["observed_mm"] for r in recent), 1)


def saturation_state(total_7d: float) -> str:
    if total_7d >= HIGH_MM:
        return "HIGH"
    if total_7d >= SATURATION_MM:
        return "SATURATED"
    return "NORMAL"


def dynamic_thresholds(total_7d: float) -> dict:
    """Return effective IMD 24 h thresholds given antecedent wetness.

    When saturated, thresholds drop so alerts escalate sooner.
    """
    drop = SAT_DROP if saturation_state(total_7d) in ("SATURATED", "HIGH") else {}
    base = {"yellow": 64.5, "orange": 115.6, "red": 204.5}
    return {k: round(v - drop.get(k, 0.0), 1) for k, v in base.items()}, \
           saturation_state(total_7d)


if __name__ == "__main__":
    dry = [{"date": f"2026-08-1{i}", "observed_mm": 2.0} for i in range(7)]
    wet = [{"date": f"2026-08-1{i}", "observed_mm": 40.0} for i in range(7)]
    print("dry 7d:", seven_day_total(dry), dynamic_thresholds(seven_day_total(dry)))
    print("wet 7d:", seven_day_total(wet), dynamic_thresholds(seven_day_total(wet)))
