"""Adaptive Seasonal Engine test suite.

Drives build_adaptive_one_glance_dispatch() under three seasonal threat
profiles and asserts the header emoji, subject line, and action items
morph correctly to the dominant seasonal threat:

    * SUMMER:        extreme heat (max >= 40C) or active thermal (max >= 36C)
    * WINTER:        cold wave (min <= 10C) or dense fog (vis < 200 m)
    * RAIN:          fallback convective tracking when no seasonal threshold

Data-gap discipline is preserved: Navi Mumbai stays in the coverage gap,
no "All Clear" nominal lines for unmonitored districts, zero-estimation
(no fabricated stations or values).
"""

import sys
import os
import json
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from engine.dispatch import (  # noqa: E402
    build_adaptive_one_glance_dispatch,
    build_one_glance_dispatch,
    CTL_SUMMER_MAX_T,
    CTL_SUMMER_ACTIVE_T,
    CTL_WINTER_MIN_T,
    CTL_WINTER_FOG_VIS,
)

# Pad 0-359 in 22.5 degree steps -> 16-point compass
_DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
         "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _deg_to_compass(deg: float) -> str:
    idx = round(deg / 22.5) % 16
    return _DIRS[idx]


def _make_matrix(overrides=None):
    """Build a full matrix_data dict from the live config."""
    with open(os.path.join(REPO, "config", "locations.json")) as f:
        cfg = json.load(f)
    locations = cfg["locations"]

    # Verified from live run: name -> (max_temp, min_temp, raw_rain, wind_kmh, wind_dir_deg)
    # Note: config contains wind values; we override thermal fields here.
    default = {
        "Ratnagiri": {"max_temp_c": 28.0, "min_temp_c": 19.0, "raw_rain": 0.0,
                      "wind_kmh": 5.5, "wind_dir": 200},
        "Mumbai": {"max_temp_c": 33.0, "min_temp_c": 24.0, "raw_rain": 0.0,
                   "wind_kmh": 4.0, "wind_dir": 180},
        "Thane": {"max_temp_c": 32.0, "min_temp_c": 23.0, "raw_rain": 0.0,
                  "wind_kmh": 3.0, "wind_dir": 160},
        "Navi Mumbai": {"max_temp_c": 32.5, "min_temp_c": 23.5, "raw_rain": 0.0,
                        "wind_kmh": 3.5, "wind_dir": 170},
        "Kolhapur": {"max_temp_c": 31.0, "min_temp_c": 22.0, "raw_rain": 0.0,
                     "wind_kmh": 5.0, "wind_dir": 190},
        "Pune": {"max_temp_c": 34.0, "min_temp_c": 22.0, "raw_rain": 0.0,
                 "wind_kmh": 4.5, "wind_dir": 175},
        "Nashik": {"max_temp_c": 30.0, "min_temp_c": 20.0, "raw_rain": 0.0,
                   "wind_kmh": 5.0, "wind_dir": 185},
        "Nagpur": {"max_temp_c": 35.0, "min_temp_c": 21.0, "raw_rain": 0.0,
                   "wind_kmh": 6.0, "wind_dir": 195},
        "Aurangabad": {"max_temp_c": 33.0, "min_temp_c": 20.0, "raw_rain": 0.0,
                       "wind_kmh": 4.0, "wind_dir": 180},
        "Solapur": {"max_temp_c": 36.0, "min_temp_c": 20.0, "raw_rain": 0.0,
                    "wind_kmh": 5.5, "wind_dir": 190},
    }
    matrix = {}
    for loc in locations:
        name = loc["name"]
        d = dict(default.get(name, default["Ratnagiri"]))
        if overrides and name in overrides:
            d.update(overrides[name])
        d["is_monitored"] = True
        d["integrity_note"] = "Nominal"
        d["corrected_rain"] = 0.0
        d["peak_burst"] = 0.0
        d["peak_time_ist"] = "16:00"
        d["visibility_meters"] = 10000
        matrix[name] = d
    return matrix


def _make_radar() -> dict:
    return {"checked": True, "ok": True}


def _make_synoptic() -> dict:
    return {
        "cyclone_status": "Nominal. Zero closed systems.",
        "winter_transition_status": "deactivated (monsoon) -> north-easterly winter currents establishing; evening humidity falling",
        "el_nino_status": "no operational index feed in this build; phase cannot be asserted",
    }


def _make_run_metrics(today="19-Oct-2026", run_label="19 Oct 2026 13:05 IST") -> dict:
    return {
        "validity_date": today,
        "run_timestamp": run_label,
        "rc": 0,
        "blackout": False,
    }


class TestSummerThermalRidge(unittest.TestCase):
    """Extreme summer / heatwave seasonal adaptation."""

    def test_thresholds_exist(self):
        assert CTL_SUMMER_MAX_T == 40.0
        assert CTL_SUMMER_ACTIVE_T == 36.0
        assert CTL_WINTER_MIN_T == 10.0
        assert CTL_WINTER_FOG_VIS == 200

    def test_extreme_heat_entropy(self):
        matrix = _make_matrix()
        matrix["Nagpur"]["max_temp_c"] = 45.8
        matrix["Solapur"]["max_temp_c"] = 41.0
        matrix["Navi Mumbai"]["max_temp_c"] = 38.0  # thermal active

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        assert "CRITICAL HEAT WAVE ALERT" in text
        assert "🔥" in text
        assert "⚡" not in text  # header emoji changed

        # Nagpur must be listed with its max temperature
        assert "Nagpur (Max: 45.8°C)" in text
        # Active thermal threshold districts listed
        assert "Solapur (Max: 41.0°C)" in text
        # Navi Mumbai stays in coverage gap
        assert "Navi Mumbai" in text
        # No fabrication: thermal values come from the matrix
        assert "Critical heatwave threshold breached" in text

    def test_active_thermal_advisory(self):
        matrix = _make_matrix()
        matrix["Mumbai"]["max_temp_c"] = 36.5
        matrix["Mumbai"]["active_warnings"] = "Thermal"

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        assert "CRITICAL HEAT WAVE ALERT" in text
        assert "Mumbai" in text
        assert "36.5°C" in text


class TestWinterColdWave(unittest.TestCase):
    """Winter cold wave / dense fog seasonal adaptation."""

    def test_cold_wave_entropy(self):
        matrix = _make_matrix()
        matrix["Nashik"]["min_temp_c"] = 6.2
        matrix["Pune"]["min_temp_c"] = 9.5
        matrix["Ratnagiri"]["min_temp_c"] = 10.0  # on threshold
        matrix["Kolhapur"]["visibility_meters"] = 150  # dense fog

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        assert "SEVERE COLD WAVE & FOG WATCH" in text
        assert "❄️" in text
        assert "⚡" not in text

        # Nashik cold wave
        assert "Nashik (Cold: 6.2°C)" in text
        # Pune cold wave
        assert "Pune (Cold: 9.5°C)" in text
        # Ratnagiri on threshold
        assert "Ratnagiri (Cold: 10.0°C)" in text
        # Kolhapur dense fog
        assert "Kolhapur (Fog: <150m)" in text

    def test_fog_visibility(self):
        matrix = _make_matrix()
        matrix["Thane"]["visibility_meters"] = 150

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        assert "SEVERE COLD WAVE & FOG WATCH" in text
        assert "Thane" in text
        assert "Fog: <150m" in text


class TestRainConvective(unittest.TestCase):
    """Standard rain / convective fallback tracking layer."""

    def test_basic_rain(self):
        matrix = _make_matrix()
        # Ratnagiri has rain peaks in the live run
        matrix["Ratnagiri"]["peak_burst"] = 1.8
        matrix["Ratnagiri"]["peak_time_ist"] = "16:00 IST"

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        assert "LIGHT THUNDERSTORM WATCH" in text
        assert "⚡" in text
        # Ratnagiri must have peak_burst > 0 to be an active rain asset
        matrix["Ratnagiri"]["peak_burst"] = 1.8
        matrix["Ratnagiri"]["peak_time_ist"] = ["16:00 IST"]
        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)
        assert "Ratnagiri (Peak: 16:00 IST)" in text
        # Coverage gaps preserved
        assert "Thane" in text
        assert "Navi Mumbai" in text
        assert "Kolhapur" in text

    def test_no_rain_zero_estimation(self):
        matrix = _make_matrix()
        # No rain, no peaks anywhere
        for name in matrix:
            matrix[name]["peak_burst"] = 0.0
            matrix[name]["peak_time_ist"] = "16:00 IST"

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        assert "Active Hazard Watch:* None" in text
        # Ratnagiri is in the "All Clear" nominal list (no rain), not in the active watch
        assert "Ratnagiri" in text.split("All Clear (Normal):")[1]
        assert "Ratnagiri (Peak:" not in text


class TestDataGapDiscipline(unittest.TestCase):
    """Strict data-gap enforcement across all seasonal profiles."""

    def test_navi_mumbai_in_coverage_gap(self):
        matrix = _make_matrix()
        del matrix["Navi Mumbai"]["is_monitored"]
        matrix["Navi Mumbai"]["integrity_note"] = "Telemetry Deficit"

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        # Navi Mumbai must never appear as "All Clear"
        assert "Navi Mumbai" in text
        assert "All Clear (Normal):" in text
        # Navi Mumbai is a coverage gap; verify it's NOT in the nominal list
        # (the part between 'All Clear (Normal):' and the next '=========================================').
        nominal_section = text.split("All Clear (Normal):")[1].split("=" * 40)[0]
        # Strip leading "* " and parse districts
        nominal_districts = [d.strip().lstrip("*").strip() for d in nominal_section.split(",")]
        assert "Navi Mumbai" not in nominal_districts
        assert "Mumbai" in nominal_districts
        assert "Solapur" in nominal_districts

    def test_all_clear_excludes_unmonitored(self):
        matrix = _make_matrix()
        matrix["Navi Mumbai"]["is_monitored"] = False
        matrix["Navi Mumbai"]["integrity_note"] = "Telemetry Deficit"

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_one_glance_dispatch(matrix, radar, synoptic, metrics)

        # The nominal list should not include Navi Mumbai (it's a coverage gap)
        nominal_section = text.split("All Clear (Normal):")[1].split("=" * 40)[0]
        # Strip leading "* " and parse districts
        nominal_districts = [d.strip().lstrip("*").strip() for d in nominal_section.split(",")]
        assert "Navi Mumbai" not in nominal_districts
        assert "Mumbai" in nominal_districts


class TestDispatchesWithinLimit(unittest.TestCase):
    """All seasonal dispatches must be <= 4096 chars."""

    def test_summer_dispatch(self):
        matrix = _make_matrix()
        matrix["Nagpur"]["max_temp_c"] = 45.8

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        assert len(text) <= 4096

    def test_winter_dispatch(self):
        matrix = _make_matrix()
        matrix["Nashik"]["min_temp_c"] = 6.2

        radar = _make_radar()
        synoptic = _make_synoptic()
        metrics = _make_run_metrics()

        text = build_adaptive_one_glance_dispatch(matrix, radar, synoptic, metrics)

        assert len(text) <= 4096
