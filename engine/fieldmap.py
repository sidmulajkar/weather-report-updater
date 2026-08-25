"""Phase-2 spatial field builder: turn sparse grid samples into continuous
rainfall / pressure FIELDS via scipy.interpolate.griddata (LINEAR + light
Gaussian smoothing). Produces a regular lon/lat grid + an IMD 4-band colormap
and contour levels at the official IMD 24h thresholds.

Anti-hallucination notes:
- Linear (NOT cubic) interpolation: cubic overshoots between sparse nodes and
  invents artificial extremes. Linear + mild Gaussian smoothing gives a smooth
  gradient without fabricated peaks.
- The field is MODEL-INTERPOLATED at grid resolution (0.5/0.1 deg). It is NOT
  measured ward-level data. Callers must label outputs accordingly.
- IMD bands strictly follow the verified 4-tier scheme (no 5th "Watch"):
    Green   < 64.5 mm/24h
    Yellow   64.5 - 115.5
    Orange  115.6 - 204.5
    Red     > 204.5
"""
from __future__ import annotations
import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

# IMD official 24h rainfall threshold bands (mm).
IMD_THRESHOLDS = [0.0, 64.5, 115.5, 204.5, 1e9]
# Contour levels we draw on the field map.
CONTOUR_LEVELS = [64.5, 115.5, 204.5]

# IMD-aligned 4-band color map (hex). Below 64.5 = green (no warning).
IMD_COLORS = [
    "#1a9850",  # green  (no warning / light)
    "#fee08b",  # yellow
    "#fc8d59",  # orange
    "#d73027",  # red
]


def build_field(coords, values, bbox, resolution=0.05, smooth_sigma=0.6):
    """Interpolate (lon,lat)->value samples onto a regular grid.

    coords: list[(lat, lon)] | list[(lon, lat)]? We use (lat, lon) consistently.
    values: 1D array of scalar samples aligned to coords.
    bbox: (lat_min, lat_max, lon_min, lon_max)
    Returns: (lon_grid, lat_grid, field_2d) where field_2d has shape
             (lat_grid, lon_grid) and np.nan outside the convex hull of samples.
    """
    pts = np.array([[c[1], c[0]] for c in coords])  # (lon, lat) columns for griddata
    vals = np.asarray(values, dtype=float)
    lat_min, lat_max, lon_min, lon_max = bbox
    lon = np.arange(lon_min, lon_max + resolution / 2, resolution)
    lat = np.arange(lat_min, lat_max + resolution / 2, resolution)
    lon_g, lat_g = np.meshgrid(lon, lat)
    field = griddata(pts, vals, (lon_g, lat_g), method="linear")
    # Light smoothing to avoid hard triangulation facets. Only inside-data.
    if smooth_sigma and np.isfinite(field).any():
        mask = np.isfinite(field)
        filled = np.nan_to_num(field, nan=0.0)
        sm = gaussian_filter(filled, sigma=smooth_sigma)
        field = np.where(mask, sm, np.nan)
    return lon, lat, field


def imd_colormap():
    """IMD 4-band rainfall colormap, keyed to the OFFICIAL 24h thresholds.

    IMPORTANT (honesty): the fill colour is tied to IMD severity BANDS, not an
    aesthetic ramp. Green = below warning (<64.5mm), Yellow = 64.5-115.5,
    Orange = 115.6-204.5, Red = >204.5. A moderate 20-60mm day therefore reads
    GREEN/YELLOW (not orange), so the map never overstates severity. The
    threshold CONTOUR LINES (64.5/115.5/204.5) are drawn on top by the caller.
    """
    from matplotlib.colors import LinearSegmentedColormap, BoundaryNorm
    # IMD 4-band breaks (mm/24h). Below 64.5 = green (no warning).
    bounds = [0.0, 64.5, 115.5, 204.5, 1e9]
    cmap = LinearSegmentedColormap.from_list(
        "imd4", ["#1a9850", "#fee08b", "#fc8d59", "#d73027"], N=256)
    norm = BoundaryNorm(bounds, cmap.N, clip=True)
    return cmap, norm


# NWS standard precipitation level breaks (mm) for contour/colorbar reference
NWS_LEVELS = [0, 1, 2.5, 5, 7.5, 10, 15, 20, 30, 40, 50, 70, 100, 150, 200, 250]


def sample_hourly_field(grid_results, hour_index, var="precipitation"):
    """Extract `var` at `hour_index` across all grid result dicts.

    grid_results: list[dict]{lat, lon, hourly:{var:[...]}} from engine.grid.fetch_grid
    Returns: (coords[(lat,lon),...], values[hour_index]) skipping None/NaN.
    """
    coords, vals = [], []
    for r in grid_results:
        # grid results carry lat/lon at top level (see fetch_grid)
        lat = r.get("lat"); lon = r.get("lon")
        h = r.get("hourly")
        if lat is None or lon is None or not h or var not in h:
            continue
        arr = h[var]
        if not arr or hour_index >= len(arr):
            continue
        v = arr[hour_index]
        if v is None:
            continue
        coords.append((lat, lon)); vals.append(float(v))
    return coords, np.asarray(vals, dtype=float)


def peak_hour_and_field(grid_results, var="precipitation", window=24):
    """For each grid point, find its peak `var` within `window` hours, return
    (coords, peak_values) so we can build the 24h-peak field (used for the
    IMD 24h band map). Skips None hourly."""
    coords, vals = [], []
    for r in grid_results:
        lat = r.get("lat"); lon = r.get("lon")
        h = r.get("hourly")
        if lat is None or lon is None or not h or var not in h:
            continue
        arr = [x for x in (h[var] or [])[:window] if x is not None]
        if not arr:
            continue
        coords.append((lat, lon)); vals.append(float(max(arr)))
    return coords, np.asarray(vals, dtype=float)


if __name__ == "__main__":
    # Offline smoke test: build a synthetic field, no network.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # 9 points on a 3x3 with a peak -> verify interpolation fills smoothly.
    c = [(19.0, 73.0), (19.0, 73.5), (19.0, 74.0),
         (19.5, 73.0), (19.5, 73.5), (19.5, 74.0),
         (20.0, 73.0), (20.0, 73.5), (20.0, 74.0)]
    v = np.array([2.0, 5.0, 3.0, 8.0, 130.0, 7.0, 4.0, 6.0, 3.0])  # one Orange cell
    lon, lat, field = build_field(c, v, (19.0, 20.0, 73.0, 74.0), resolution=0.05)
    print("field shape:", field.shape, "| has nan outside hull:", bool(np.isnan(field).any()))
    print("peak in field ~130?:", float(np.nanmax(field)))
    cmap, norm = imd_colormap()
    print("cmap ok, norm levels:", IMD_THRESHOLDS)
    # quick render to prove colormap bands
    plt.figure(figsize=(4, 4))
    plt.contourf(lon, lat, field, levels=50, cmap=cmap, norm=norm)
    plt.colorbar(ticks=IMD_THRESHOLDS[:-1])
    plt.title("smoke")
    plt.savefig("/tmp/field_smoke.png", dpi=60)
    print("smoke png written /tmp/field_smoke.png")
