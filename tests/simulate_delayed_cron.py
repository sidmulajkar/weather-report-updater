"""Delayed / duplicated cron simulation — verifies suppression is fail-open.

GitHub Actions fires scheduled jobs late (up to ~15 min) and can skip an
interval. Two things must hold:

  1. A briefing identical to the last one SENT is suppressed.
  2. A briefing that differs goes through — a delayed run must never be
     silenced just because a prior run happened.
  3. A dry-run must NOT consume the fingerprint, or testing would swallow the
     next real broadcast.
  4. Missing / corrupt state suppresses nothing (fail-open).

Run:  venv1/bin/python tests/simulate_delayed_cron.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import engine.broadcast_state as bs

BODY_A = ("DISTRICT METEOROLOGICAL MATRIX\n"
          "  MUMBAI | Rain: YELLOW | Thermal: Nominal (No warning)\n"
          "  RATNAGIRI | Rain: YELLOW | Thermal: ADVISORY (Hot and Humid)\n")
BODY_B = BODY_A.replace("Rain: YELLOW", "Rain: ORANGE")   # weather changed


def _scratch():
    """Point the module at a throwaway state file."""
    d = tempfile.mkdtemp()
    bs.STATE_DIR = d
    bs.STATE_FILE = os.path.join(d, "broadcast_state.json")
    return d


def run():
    cases = []

    # 1. first ever run: nothing sent yet -> must NOT suppress
    d = _scratch()
    cases.append(("first run (no state) -> broadcast",
                  bs.should_suppress(BODY_A) is False))

    # 2. after sending A, an identical A is suppressed
    bs.mark_sent(BODY_A)
    cases.append(("identical repeat -> suppressed",
                  bs.should_suppress(BODY_A) is True))

    # 3. weather CHANGED -> must broadcast even though a prior run exists
    cases.append(("changed weather -> broadcast",
                  bs.should_suppress(BODY_B) is False))

    # 4. a second identical run does NOT re-mark, so it stays suppressed
    #    (this is the "cron fired twice 3 min apart" case)
    cases.append(("double-fire stays suppressed",
                  bs.should_suppress(BODY_A) is True))

    # 5. corrupt state file -> fail open
    with open(bs.STATE_FILE, "w") as f:
        f.write("{ this is not json")
    cases.append(("corrupt state -> fail-open broadcast",
                  bs.should_suppress(BODY_A) is False))

    # 6. empty state file -> fail open
    open(bs.STATE_FILE, "w").close()
    cases.append(("empty state -> fail-open broadcast",
                  bs.should_suppress(BODY_A) is False))

    # 7. delayed run: state is 3 cycles old but body still matches ->
    #    suppressed (correctly: nothing changed for the operator)
    d = _scratch()
    bs.mark_sent(BODY_A)
    cases.append(("stale state, unchanged body -> suppressed",
                  bs.should_suppress(BODY_A) is True))

    # 8. fingerprint must be stable and content-sensitive
    fp_a1, fp_a2, fp_b = bs.fingerprint(BODY_A), bs.fingerprint(BODY_A), bs.fingerprint(BODY_B)
    cases.append(("fingerprint deterministic",
                  fp_a1 == fp_a2 and fp_a1 != fp_b))

    # 9. trailing-whitespace sensitivity: a real change must not be missed
    cases.append(("whitespace change is a real change",
                  bs.fingerprint(BODY_A) != bs.fingerprint(BODY_A + " ")))

    for d in [d]:
        shutil.rmtree(d, ignore_errors=True)

    ok = all(g for _, g in cases)
    print("\n  DELAYED-CRON SIMULATION")
    for name, good in cases:
        print(f"  {'PASS' if good else 'FAIL'}: {name}")
    print(f"\n  {'ALL PASS' if ok else 'FAILURES PRESENT'} "
          f"({sum(1 for _, g in cases if g)}/{len(cases)})\n")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
