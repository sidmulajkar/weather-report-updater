"""U1 — Observed district rainfall (ground truth for verification).

Source (verified LIVE 2026-08-22): open "India Rainfall Monitor" JSON
  https://sayantan-aquacarta.github.io/rainfall-pipeline/api/latest.json
Returns per-district: day_actual_mm, day_normal_mm, day_category, ... for 760 districts,
updated daily. This is a community mirror of IMD district rainfall statistics ->
we attribute IMD and the pipeline.

This module is also a BACKSTOP: even if the model layer fails, we can still report
what *actually* rained. It never raises on network failure; returns status flags.
"""
from __future__ import annotations
import requests

LATEST_URL = "https://sayantan-aquacarta.github.io/rainfall-pipeline/api/latest.json"
HEADERS = {"User-Agent": "weather-report-updater/2.0 (local research; +github rainfall-pipeline)"}


def fetch_observed(timeout: int = 25) -> dict:
    """Return {status, generated_at, date, by_district:{DISTRICT: {...row...}}}.

    District keys are UPPER-CASED for robust matching against config.
    On failure status='unavailable' with reason; by_district={}.
    """
    out = {"status": "unavailable", "generated_at": None, "date": None,
           "by_district": {}, "reason": ""}
    try:
        r = requests.get(LATEST_URL, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        out["reason"] = f"fetch failed: {e}"
        return out
    except ValueError as e:
        out["reason"] = f"bad json: {e}"
        return out

    rows = j.get("rows", [])
    byd = {}
    for row in rows:
        d = (row.get("district") or "").strip().upper()
        st = (row.get("state") or "").strip().upper()
        byd[d] = {**row, "_state_upper": st, "_district_upper": d}
    out["status"] = "ok"
    out["generated_at"] = j.get("generated_at")
    out["date"] = j.get("date")
    out["by_district"] = byd
    return out


def observed_for_district(obs: dict, district: str, state: str = "MAHARASHTRA") -> dict:
    """Join a config district -> observed record. Robust to case/spacing.

    Returns {found, actual_mm, normal_mm, category, source_date, status}.
    """
    if obs.get("status") != "ok":
        return {"found": False, "actual_mm": None, "normal_mm": None,
                "category": None, "source_date": obs.get("date"),
                "status": obs.get("status"), "reason": obs.get("reason", "")}
    key = district.strip().upper()
    rec = obs["by_district"].get(key)
    if rec is None:
        for d, r in obs["by_district"].items():
            if r.get("_state_upper") == state and key in d:
                rec = r
                break
    if rec is None:
        return {"found": False, "actual_mm": None, "normal_mm": None,
                "category": None, "source_date": obs.get("date"),
                "status": "no_match", "reason": f"district '{district}' not in dataset"}
    return {"found": True,
            "actual_mm": rec.get("day_actual_mm"),
            "normal_mm": rec.get("day_normal_mm"),
            "category": rec.get("day_category"),
            "source_date": obs.get("date"),
            "status": "ok", "reason": ""}


if __name__ == "__main__":
    obs = fetch_observed()
    print("observed status:", obs["status"], "| date:", obs["date"], "| reason:", obs.get("reason"))
    tests = ["MUMBAI SUBURBAN", "PUNE", "NAGPUR", "RATNAGIRI", "RAIGAD"]
    for d in tests:
        r = observed_for_district(obs, d)
        print(f"  {d:16s} -> found={r['found']} actual={r['actual_mm']}mm normal={r['normal_mm']}mm cat={r['category']}")
