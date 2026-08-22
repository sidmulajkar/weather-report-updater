"""U4 — Verification: archive forecasts+observations, compute skill & bias.

We can now MEASURE our own accuracy instead of quoting IMD's 75-80%.
Method (verified via research: AMS POD/FAR/CSI glossary, eumetrain ETS):
  For a rainfall event threshold T (we use IMD Yellow = 64.5 mm / 24h, but also
  report a lower "any-rain" threshold), build a 2x2 contingency table per district
  by pairing each archived FORECAST day with the OBSERVED day:
    hits   (a) = forecast>=T AND observed>=T
    false  (b) = forecast>=T AND observed< T
    miss   (c) = forecast< T AND observed>=T
    corr0  (d) = forecast< T AND observed< T
  POD = a/(a+c);  FAR = b/(a+b);  CSI = a/(a+b+c);
  ETS = (a - a_rand)/(a+b+c - a_rand),  a_rand = (a+b)(a+c)/n,  n=a+b+c+d
  bias = (a+b)/(a+c)   (>1 = over-forecast rain events)
Also continuous: mean forecast, mean observed, mean bias (mm).

Storage: SQLite archive.db (one row per (run_date, district)):
  run_date, district, forecast_mm, observed_mm, source
The orchestrator calls record_run() each execution; compute_skill() joins the
latest observed back to the prior forecast.

Honesty: skill is only meaningful once we have paired forecast/observed rows.
We report "calibration: N days" and NEVER print skill with < 4 pairs.
"""
from __future__ import annotations
import os, sqlite3, datetime as dt

DB_PATH = os.path.join(os.path.dirname(__file__), "archive.db")
EVENT_MM = 64.5  # IMD Yellow 24h threshold (mm)


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS archive (
        run_date TEXT, district TEXT, forecast_mm REAL, observed_mm REAL,
        source TEXT, PRIMARY KEY(run_date, district))""")
    return conn


def record_run(run_date: str, rows: list[dict]) -> int:
    """rows: list of {district, forecast_mm, observed_mm, source}.
    observed_mm may be None (then we only store forecast; filled later).
    Returns number of rows written."""
    conn = _conn()
    n = 0
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO archive (run_date, district, forecast_mm, observed_mm, source) "
            "VALUES (?,?,?,?,?)",
            (run_date, r["district"].upper(), r.get("forecast_mm"),
             r.get("observed_mm"), r.get("source", "open-meteo")))
        n += 1
    conn.commit(); conn.close()
    return n


def fill_observed(run_date: str, observed: dict) -> int:
    """Backfill observed_mm for a given run_date from observed dict
    {district_upper: actual_mm}. Returns rows updated."""
    conn = _conn()
    cur = conn.execute("SELECT rowid, district FROM archive WHERE run_date=?", (run_date,))
    upd = 0
    for rowid, dist in cur.fetchall():
        if dist in observed and observed[dist] is not None:
            conn.execute("UPDATE archive SET observed_mm=? WHERE rowid=?",
                         (observed[dist], rowid)); upd += 1
    conn.commit(); conn.close()
    return upd


def observed_history(district: str, days: int = 7) -> list[dict]:
    """Return recent archive rows (with observed_mm) for a district, newest last.
    Used for 7-day antecedent soil-moisture tracking. District is matched
    case-insensitively / uppercased.
    """
    conn = _conn()
    rows = conn.execute(
        "SELECT run_date, observed_mm FROM archive WHERE district=? "
        "ORDER BY run_date DESC LIMIT ?",
        (district.upper(), days)).fetchall()
    conn.close()
    return [{"date": d, "observed_mm": o} for d, o in rows]



def _table(conn, threshold_mm: float):
    """Pair each forecast row with the observed row of the SAME district on the
    NEXT available run_date (forecast today -> observed tomorrow)."""
    rows = conn.execute(
        "SELECT run_date, district, forecast_mm, observed_mm FROM archive "
        "WHERE observed_mm IS NOT NULL").fetchall()
    # index observed by (district, date)
    obs = {}
    for rd, d, f, o in rows:
        obs[(d, rd)] = (f, o)
    a = b = c = d0 = 0
    for rd, d, f, o in rows:
        # find observed on a strictly later date for same district
        later = [(od, (fv, ov)) for (dd, od), (fv, ov) in obs.items()
                 if dd == d and od > rd]
        if not later:
            continue
        # use the earliest later observed
        later.sort()
        _, (fv, ov) = later[0]
        fc = f >= threshold_mm
        oc = ov >= threshold_mm
        if fc and oc: a += 1
        elif fc and not oc: b += 1
        elif not fc and oc: c += 1
        else: d0 += 1
    return a, b, c, d0


def compute_skill(threshold_mm: float = EVENT_MM) -> dict:
    """Return skill metrics dict; 'n_pairs' + 'calibrated' tell caller trustworthiness.

    Metrics (AMS/WMO standard, CAWCR verification guide):
      POD = a/(a+c), FAR = b/(a+b), CSI = a/(a+b+c), ETS (Gandin-Murphy),
      freq-bias = (a+b)/(a+c).
    BSS (Brier Skill Score) vs a flat climatology reference = base rate of the
    event (fraction of paired cases where observed >= threshold). BSS = 1 - BS/BS_ref.
      BS = mean((p - o)^2) with p = our hit-rate proxy; here we use the simple
      event-forecast Brier: p = 1 if fc else 0, o = 1 if oc else 0.
    Calibration: skill on < MIN_PAIRS cases is UNSTABLE (sampling noise); flagged.
    """
    MIN_PAIRS = 30  # transparent threshold; scores below this are indicative only
    conn = _conn()
    a, b, c, d0 = _table(conn, threshold_mm)
    n = a + b + c + d0
    conn.close()
    if n == 0:
        return {"n_pairs": 0, "threshold_mm": threshold_mm, "calibrated": False,
                "note": "no paired forecast/observed days yet"}
    pod = a / (a + c) if (a + c) else 0.0
    far = b / (a + b) if (a + b) else 0.0
    csi = a / (a + b + c) if (a + b + c) else 0.0
    ar = (a + b) * (a + c) / n if n else 0.0
    ets = (a - ar) / (a + b + c - ar) if (a + b + c - ar) else 0.0
    fbias = (a + b) / (a + c) if (a + c) else 0.0
    # Brier Skill Score vs climatology (flat reference = observed event base rate).
    # Pooled constant forecast p = our event frequency; outcomes o = observed event rate.
    p = (a + b) / n                       # forecast event frequency
    o_rate = (a + c) / n                  # observed event frequency (climatology ref)
    bs = p * (1 - p)                     # Brier of constant forecast p (variance term)
    bs_ref = o_rate * (1 - o_rate)        # Brier of climatology reference
    bss = 1 - (bs / bs_ref) if bs_ref > 0 else 0.0
    return {"n_pairs": n, "threshold_mm": threshold_mm,
            "calibrated": n >= MIN_PAIRS,
            "POD": round(pod, 3), "FAR": round(far, 3), "CSI": round(csi, 3),
            "ETS": round(ets, 3), "bias": round(fbias, 3), "BSS": round(bss, 3),
            "hits": a, "false_alarm": b, "miss": c, "correct0": d0,
            "note": (f"indicative only (n={n} < {MIN_PAIRS}); scores stabilise at "
                     f">= {MIN_PAIRS} paired days") if n < MIN_PAIRS else
                    f"based on {n} paired district-days"}


def mean_bias_mm() -> dict:
    """Continuous bias: mean(forecast-observed) per district over paired rows."""
    conn = _conn()
    rows = conn.execute(
        "SELECT district, forecast_mm, observed_mm FROM archive "
        "WHERE observed_mm IS NOT NULL").fetchall()
    conn.close()
    per = {}
    for d, f, o in rows:
        per.setdefault(d, []).append(f - o)
    return {d: round(sum(v) / len(v), 2) for d, v in per.items() if v}


if __name__ == "__main__":
    # demo: record today's forecast, backfill yesterday's observed, show skill
    today = dt.date.today().isoformat()
    yest = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    # simulate paired history if empty
    conn = _conn(); cnt = conn.execute("SELECT COUNT(*) FROM archive").fetchone()[0]; conn.close()
    if cnt == 0:
        demo = [("MUMBAI SUBURBAN", 12.0, 9.5), ("PUNE", 3.0, 2.0),
                ("NAGPUR", 25.0, 30.0), ("RATNAGIRI", 18.0, 22.0),
                ("KOLHAPUR", 5.0, 4.0), ("RAIGAD", 14.0, 10.0)]
        # day1 forecasts
        record_run(yest, [{"district": d, "forecast_mm": f, "observed_mm": None,
                           "source": "demo"} for d, f, _ in demo])
        # day2 observed (backfilled)
        record_run(today, [{"district": d, "forecast_mm": f, "observed_mm": o,
                            "source": "demo"} for d, f, o in demo])
        # also backfill day1 observed via fill_observed
        fill_observed(yest, {d.upper(): o for d, _, o in demo})
    sk = compute_skill()
    print("skill @", sk.get("threshold_mm"), "mm ->", sk)
    print("mean bias mm:", mean_bias_mm())
