"""Duplicate-broadcast suppression for the cron briefing.

WHY: GitHub Actions schedules fire on shared runners and can be delayed by
~15 min or skip an interval outright. When a run lands close to the previous
one, the district matrix is often byte-identical, and re-sending it to Telegram
is pure noise -- an operator who already read the briefing learns nothing from
a second copy.

HOW: the current matrix payload is fingerprinted and compared against the last
one we actually sent. On a match the broadcast is suppressed; on a difference
(or on any doubt) it goes through. This is deliberately fail-open: a missing or
unreadable state file suppresses nothing, because sending a briefing twice is a
far smaller sin than failing to send one at all.

NOTE: state lives in its own file, NOT in cache/forecast_cache.json. That cache
holds Open-Meteo grid+city payloads with a 6h TTL and is rewritten wholesale by
engine/forecast_cache.save() on every fetch -- mixing operator state into it
would silently clobber the forecast data this whole pipeline depends on.

STATE FILE: cache/broadcast_state.json (git-ignored, restored by
actions/cache alongside forecast_cache.json under the same branch key).
"""
from __future__ import annotations

import hashlib
import json
import os

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")
STATE_FILE = os.path.join(STATE_DIR, "broadcast_state.json")


def fingerprint(payload: str) -> str:
    """Stable digest of the briefing body we would actually send."""
    return hashlib.sha256((payload or "").encode("utf-8")).hexdigest()


def last_sent_fingerprint() -> str | None:
    """Fingerprint of the previous broadcast, or None if unknown.

    Never raises: a corrupt/missing/unreadable state file is reported as
    "unknown", which forces the next briefing through rather than suppressing it.
    """
    try:
        if not os.path.exists(STATE_FILE):
            return None
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        fp = data.get("last_sent_fingerprint")
        return fp if isinstance(fp, str) and fp else None
    except Exception:
        return None


def should_suppress(payload: str) -> bool:
    """True only when this exact briefing was already sent and nothing changed."""
    prev = last_sent_fingerprint()
    if prev is None:
        return False
    return prev == fingerprint(payload)


def mark_sent(payload: str) -> bool:
    """Record that `payload` was broadcast. Never raises."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        state = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
            except Exception:
                state = {}
        state["last_sent_fingerprint"] = fingerprint(payload)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
        return True
    except Exception:
        return False
