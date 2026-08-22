"""IMD official district warning check (honest, no fabrication).

Reality (from research): IMD's district warning is a JS/GeoJSON web app
(districtWiseWarningGIS.php). A keyword scan of the raw HTML is NOT a reliable
parse and MUST NOT be treated as ground truth -> it produces fabricated colours.

So this module returns status="unavailable" by default. It attempts a fetch only
to confirm reachability, and deliberately does NOT assert a colour from a keyword
scan. The classifier therefore runs in MODEL-ONLY mode and labels itself clearly
as "unofficial". When a proper IMD GeoJSON/API parser is added later, set status
to "ok" and return the real colour.

We NEVER fabricate an IMD colour.
"""
from __future__ import annotations
import requests

DISTRICT_WARNING_URL = "https://mausam.imd.gov.in/responsive/districtWiseWarningGIS.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (weather-updater research; +local)"}


def fetch_district_warning(district: str, state: str, timeout: int = 15) -> dict:
    """Return {status, colour, text, source}. colour is ALWAYS None here (no fabrication)."""
    out = {"status": "unavailable", "colour": None,
           "text": "IMD colour not parsed (web app; keyword-scan disabled to avoid fabrication)",
           "source": DISTRICT_WARNING_URL}
    # Optional reachability probe (does NOT assign a colour).
    try:
        r = requests.get(DISTRICT_WARNING_URL, headers=HEADERS, timeout=timeout)
        out["text"] = f"IMD page reachable (HTTP {r.status_code}); colour parse not implemented"
    except requests.RequestException as e:
        out["text"] = f"IMD page unreachable: {e}"
    return out


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "Mumbai"
    s = sys.argv[2] if len(sys.argv) > 2 else "Maharashtra"
    print(fetch_district_warning(d, s))
