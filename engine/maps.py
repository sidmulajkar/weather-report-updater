"""Map rendering to match the analyst reference images.

KEY DESIGN FIX: maps are rendered from the per-city values ALREADY fetched by
run_report.py (10 Maharashtra cities). We do NOT make extra grid-point API calls
(those were ~200 sequential requests and timed out). Rainfall uses matplotlib
tricontourf (Delaunay, built into matplotlib - no scipy needed); wind uses quiver
arrows at the known points. This is fast and fully local.

Two maps (matching your sample):
  1) RAINFALL FORECAST - filled contours of 24h accumulated precipitation,
     IMD-style colour ramp, city dots + labels ("Mumbai 8.8"), title.
  2) WIND FIELD - quiver arrows coloured by speed, station labels with km/h,
     title. Labelled "10 m" (free Open-Meteo gives 10 m wind, not 850 hPa).
"""
from __future__ import annotations
import os
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.tri import Triangulation
from matplotlib.ticker import ScalarFormatter

INDIA_RAMP = ["#f7fbff", "#c6dbef", "#6baed6", "#429c40", "#fee08b",
              "#fc8d59", "#d73027", "#a50026"]
IMD_ALERT_COLORS = {
    "red": "#e74c3c",
    "orange": "#e67e22",
    "yellow": "#f1c40f",
    "green": "none",
}
WIND_RAMP = ["#0000ff", "#00ffff", "#00ff00", "#ffff00", "#ff0000"]


def _norm_name(s: str) -> str:
    return " ".join(s.upper().split())


def overlay_imd_warnings(ax, warnings: dict, fill_alpha: float = 0.18, line_alpha: float = 0.6):
    """Draw semi-transparent IMD warning polygons from an authoritative warnings dict.

    Supports both exact and substring district-name matching so runtime warning
    keys like ``"Mumbai"`` still match cached polygon names like
    ``"Mumbai City"`` / ``"Mumbai Suburban"`` when geometry is available.
    """
    if not warnings:
        return
    norm_map = {_norm_name(k): (k, v) for k, v in warnings.items()}
    matched = set()
    for district, rec in warnings.items():
        color_label = (rec or {}).get("color_label", "").lower()
        if color_label in ("green", "no warning", ""):
            continue
        fill = IMD_ALERT_COLORS.get(color_label)
        if not fill or fill == "none":
            continue
        geom = (rec or {}).get("geometry") or {}
        geom_type = geom.get("type")
        coords = geom.get("coordinates")
        if geom_type not in ("Polygon", "MultiPolygon") or not coords:
            # Fallback: try matching this warning key against polygon district names
            q = _norm_name(district)
            for pd, prec in getattr(overlay_imd_warnings, "_polygons", {}).items():
                if q in pd or pd in q:
                    geom = (prec or {}).get("geometry") or {}
                    geom_type = geom.get("type")
                    coords = geom.get("coordinates")
                    break
        if geom_type not in ("Polygon", "MultiPolygon") or not coords:
            continue
        try:
            if geom_type == "Polygon":
                polys = [coords]
            else:
                polys = coords
            for poly in polys:
                for ring in poly:
                    xs, ys = zip(*ring)
                    ax.fill(xs, ys, color=fill, alpha=fill_alpha, zorder=2)
                    ax.plot(xs, ys, color=fill, lw=1.5, alpha=line_alpha, zorder=2.5)
                    matched.add(district)
        except Exception:
            continue


def _add_imd_header(fig, product_name="", issued_ist="", source="",
                    channel="", domain=""):
    """Add an IMD-style metadata header bar at the top of a figure.

    Thin, clean header mimicking IMD satellite product metadata bars:
    essential product/channel/domain on the left, issued time + source on right.
    """
    from matplotlib.patches import Rectangle
    # thin dark header rectangle (5% of figure height)
    bar = Rectangle((0, 0.95), 1, 0.05, transform=fig.transFigure,
                    facecolor="#1a1a1a", edgecolor="none", zorder=10)
    fig.patches.append(bar)
    # left: product | channel | domain
    left_parts = [p for p in [product_name, channel, domain] if p]
    left = "  |  ".join(left_parts)
    # right: issued time | source
    right_parts = [p for p in [f"Issued {issued_ist} IST" if issued_ist else "", source] if p]
    right = "  |  ".join(right_parts)
    fig.text(0.05, 0.975, left, ha="left", va="top", fontsize=8,
             color="white", fontweight="bold", zorder=11)
    fig.text(0.95, 0.975, right, ha="right", va="top", fontsize=8,
             color="white", zorder=11)


def rainfall_map(locations_with_vals, model_label="ECMWF", out_path="output/rainfall_map.png",
                 issued_ist="", sources="Open-Meteo multi-model fusion"):
    """locations_with_vals: list of dicts {name,lat,lon,accum_24h}."""
    lats = np.array([l["lat"] for l in locations_with_vals])
    lons = np.array([l["lon"] for l in locations_with_vals])
    z = np.array([l.get("accum_24h", 0.0) for l in locations_with_vals], dtype=float)
    fig, ax = plt.subplots(figsize=(8, 8))
    cmap = LinearSegmentedColormap.from_list("imd", INDIA_RAMP)
    if len(z) >= 3:
        tri = Triangulation(lons, lats)
        cs = ax.tricontourf(tri, z, levels=18, cmap=cmap, extend="max")
    else:
        cs = ax.scatter(lons, lats, c=z, cmap=cmap, s=80)
    fig.colorbar(cs, ax=ax, label="24h rainfall (mm)")
    # de-overlap labels: cluster nearby points and fan them vertically
    pts = sorted(locations_with_vals, key=lambda l: (round(l["lat"], 2), l["lon"]))
    clusters, cur = [], []
    for l in pts:
        if cur and (abs(l["lat"] - cur[-1]["lat"]) < 0.15 and abs(l["lon"] - cur[-1]["lon"]) < 0.25):
            cur.append(l)
        else:
            if cur: clusters.append(cur)
            cur = [l]
    if cur: clusters.append(cur)
    for cl in clusters:
        n = len(cl)
        for i, l in enumerate(cl):
            dy = 4 + i * 10 if n > 1 else 4
            ax.plot(l["lon"], l["lat"], "ko", markersize=5)
            ax.annotate(f"{l['name']} {l.get('accum_24h',0):.1f}",
                        (l["lon"], l["lat"]), textcoords="offset points",
                        xytext=(4, dy), fontsize=8)
    ax.set_title(f"Rainfall forecast (24h) - {model_label} - Maharashtra", fontsize=11)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.margins(0.15)
    _add_provenance_footer(fig, issued_ist, sources)
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


def wind_map(locations_with_vals, model_label="ECMWF", out_path="output/wind_map.png",
             issued_ist="", sources="Open-Meteo multi-model fusion"):
    """locations_with_vals: list of dicts {name,lat,lon,wind_speed,wind_dir}."""
    lats = np.array([l["lat"] for l in locations_with_vals])
    lons = np.array([l["lon"] for l in locations_with_vals])
    spd = np.array([l.get("wind_speed", 0.0) for l in locations_with_vals], dtype=float)
    fig, ax = plt.subplots(figsize=(8, 8))
    cmap = LinearSegmentedColormap.from_list("wind", WIND_RAMP)
    # quiver: meteorological dir is WHERE wind comes FROM; arrow points TO (dir+180)
    u = np.zeros_like(spd); v = np.zeros_like(spd)
    for i, l in enumerate(locations_with_vals):
        d = np.deg2rad(l.get("wind_dir", 0.0))
        u[i] = -np.sin(d) * spd[i]
        v[i] = -np.cos(d) * spd[i]
    q = ax.quiver(lons, lats, u, v, spd, cmap=cmap, scale=200, width=0.004,
                  headwidth=4)
    fig.colorbar(q, ax=ax, label="wind speed (km/h)")
    # de-overlap labels: cluster nearby points and fan them vertically
    pts = sorted(locations_with_vals, key=lambda l: (round(l["lat"], 2), l["lon"]))
    clusters, cur = [], []
    for l in pts:
        if cur and (abs(l["lat"] - cur[-1]["lat"]) < 0.15 and abs(l["lon"] - cur[-1]["lon"]) < 0.25):
            cur.append(l)
        else:
            if cur: clusters.append(cur)
            cur = [l]
    if cur: clusters.append(cur)
    for cl in clusters:
        n = len(cl)
        for i, l in enumerate(cl):
            dy = 6 + i * 11 if n > 1 else 6
            ax.annotate(f"{l['name']} {l.get('wind_speed',0):.0f}",
                        (l["lon"], l["lat"]), textcoords="offset points",
                        xytext=(6, dy), fontsize=8)
    ax.set_title(f"Wind field (10 m) - {model_label} - Maharashtra", fontsize=11)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.margins(0.15)
    _add_provenance_footer(fig, issued_ist, sources)
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sample = [
        {"name": "Mumbai", "lat": 19.076, "lon": 72.877, "accum_24h": 8.8, "wind_speed": 18, "wind_dir": 240,
         "wind850_speed": 45, "wind850_dir": 250},
        {"name": "Pune", "lat": 18.52, "lon": 73.856, "accum_24h": 3.1, "wind_speed": 12, "wind_dir": 250,
         "wind850_speed": 38, "wind850_dir": 245},
        {"name": "Nagpur", "lat": 21.146, "lon": 79.087, "accum_24h": 15.2, "wind_speed": 22, "wind_dir": 230,
         "wind850_speed": 30, "wind850_dir": 235},
    ]
    print("rainfall ->", rainfall_map(sample, out_path=os.path.join(here, "output", "rainfall_map.png")))
    print("wind    ->", wind_map(sample, out_path=os.path.join(here, "output", "wind_map.png")))
    print("wind850 ->", wind850_map(sample, out_path=os.path.join(here, "output", "wind850_map.png")))
    print("nowcast ->", nowcast_map(sample, out_path=os.path.join(here, "output", "nowcast_map.png")))


def wind850_map(locations_with_vals, model_label="ECMWF", out_path="output/wind850_map.png",
                issued_ist="", sources="Open-Meteo multi-model fusion"):
    """Upper-air (850 hPa) wind map -> replicates the analyst image's top panel.
    locations_with_vals: dicts with wind850_speed (km/h) and wind850_dir (deg FROM)."""
    lats = np.array([l["lat"] for l in locations_with_vals])
    lons = np.array([l["lon"] for l in locations_with_vals])
    spd = np.array([l.get("wind850_speed", 0.0) for l in locations_with_vals], dtype=float)
    fig, ax = plt.subplots(figsize=(8, 8))
    cmap = LinearSegmentedColormap.from_list("wind850", WIND_RAMP)
    u = np.zeros_like(spd); v = np.zeros_like(spd)
    for i, l in enumerate(locations_with_vals):
        d = np.deg2rad(l.get("wind850_dir", 0.0))
        u[i] = -np.sin(d) * spd[i]; v[i] = -np.cos(d) * spd[i]
    q = ax.quiver(lons, lats, u, v, spd, cmap=cmap, scale=200, width=0.004, headwidth=4)
    fig.colorbar(q, ax=ax, label="850 hPa wind speed (km/h)")
    # de-overlap labels: cluster nearby points and fan them vertically
    pts = sorted(locations_with_vals, key=lambda l: (round(l["lat"], 2), l["lon"]))
    clusters, cur = [], []
    for l in pts:
        if cur and (abs(l["lat"] - cur[-1]["lat"]) < 0.15 and abs(l["lon"] - cur[-1]["lon"]) < 0.25):
            cur.append(l)
        else:
            if cur: clusters.append(cur)
            cur = [l]
    if cur: clusters.append(cur)
    for cl in clusters:
        n = len(cl)
        for i, l in enumerate(cl):
            dy = 6 + i * 11 if n > 1 else 6
            ax.annotate(f"{l['name']} {l.get('wind850_speed',0):.0f}",
                        (l["lon"], l["lat"]), textcoords="offset points",
                        xytext=(6, dy), fontsize=8)
    ax.set_title(f"Upper-air wind (850 hPa) - {model_label} - Maharashtra", fontsize=11)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.margins(0.15)
    _add_provenance_footer(fig, issued_ist, sources)
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


# IMD nowcast colour -> fill colour for the basemap-style nowcast map
_IMD_FILL = {1: "#2ca02c", 2: "#ffd700", 3: "#ff8c00", 4: "#d62728"}


def nowcast_map(locations_with_vals, model_label="IMD nowcast", out_path="output/nowcast_map.png",
                issued_ist="", sources="IMD official"):
    """3h nowcast map seeded by IMD official district colour/intensity."""
    lats = np.array([l["lat"] for l in locations_with_vals])
    lons = np.array([l["lon"] for l in locations_with_vals])
    fig, ax = plt.subplots(figsize=(8, 8))
    # Geographic base layer: load Maharashtra district outlines from cache
    _add_simple_geo_base(ax, locs=locations_with_vals)
    cmap = LinearSegmentedColormap.from_list("imdcol", ["#2ca02c", "#ffd700", "#ff8c00", "#d62728"])
    # colour by IMD severity level (1..4)
    sev = np.array([max(1, min(4, int(l.get("imd_color", 1) or 1))) for l in locations_with_vals], dtype=float)
    sc = ax.scatter(lons, lats, c=sev, cmap=cmap, s=180, vmin=1, vmax=4,
                    edgecolors="white", linewidths=0.8, zorder=10)
    fig.colorbar(sc, ax=ax, label="IMD nowcast severity (1 Green - 4 Red)", ticks=[1, 2, 3, 4])
    for l in locations_with_vals:
        ax.annotate(f"{l['name']} {l.get('nowcast_label','')}".strip(),
                    (l["lon"], l["lat"]), textcoords="offset points",
                    xytext=(6, 6), fontsize=8)
    ax.set_title(f"District nowcast (next 3h) - {model_label} - Maharashtra", fontsize=11)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.margins(0.15)
    _add_provenance_footer(fig, issued_ist, sources)
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


def severe_map(mslp_grid: dict, alerts: list[dict], out_path="output/severe_map.png",
               issued_ist="", sources="Open-Meteo multi-model fusion"):
    """MSLP field over Arabian Sea + Bay of Bengal with detected-system markers."""
    lats, lons, z = [], [], []
    for basin, series in mslp_grid.items():
        for (la, lo), s in series.items():
            if s.get("pressure_msl"):
                lats.append(la); lons.append(lo)
                z.append(s["pressure_msl"][0])
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.set_facecolor("#e6f0ff")  # ocean blue hint
    # Simple land/coastline mask from cached IMD polygons
    _add_simple_geo_base(ax)
    if len(z) >= 3:
        tri = Triangulation(lons, lats)
        cs = ax.tricontourf(tri, z, levels=14, cmap="RdYlBu_r")
        fig.colorbar(cs, ax=ax, label="MSLP (hPa) — lower = stronger low")
        ax.tricontour(tri, z, levels=8, colors="k", linewidths=0.4, linestyles=":")
    ax.scatter([72.8, 73.0, 73.8], [19.1, 18.5, 16.7], c="black", s=20,
               marker="s", label="Maharashtra coast")
    cmap_t = {"cyclone": "red", "low_pressure": "orange", "severe_wind": "magenta"}
    for a in alerts:
        if a["region"] in ("Arabian Sea", "Bay of Bengal"):
            bx = BASIN_CENTROID.get(a["region"], (15, 70))
            ax.scatter([bx[1]], [bx[0]], c=cmap_t.get(a["type"], "red"), s=120,
                       marker="*", edgecolors="k",
                       label=f"{a['type']} ({a['region']}, ~{a.get('eta_days',0)}d)")
    ax.set_title("Severe-system outlook — MSLP field", fontsize=11)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.legend(loc="lower left", fontsize=7)
    _add_provenance_footer(fig, issued_ist, sources)
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


def timing_map(lat, lon, hour_grid, bbox, out_path, issued_ist=""):
    """Peak-rainfall-hour field: colour each cell by the hour-of-day its 24h
    rainfall peaks. Pure model-derived; lets clients see WHEN heavy rain hits
    their zone. No fabricated timing."""
    import numpy as np
    from matplotlib.colors import ListedColormap, BoundaryNorm
    fig, ax = plt.subplots(figsize=(9, 7))
    # 24 discrete hour classes, CYCLIC colormap (23:00 adjacent to 00:00).
    # Sample the HSV colormap through a full cycle so start/end colors match.
    base_colors = plt.cm.hsv(np.linspace(0, 1, 24))
    cmap = ListedColormap(base_colors, name="timing_cyclic")
    norm = BoundaryNorm(np.arange(-0.5, 24.5, 1), cmap.N)
    hour_arr = np.asarray(hour_grid, dtype=float)
    if hour_arr.size:
        from scipy import ndimage
        mask = np.zeros_like(hour_arr, dtype=bool)
        for h in range(24):
            h_eq = (np.round(hour_arr) == h)
            labeled, n = ndimage.label(h_eq)
            for i in range(1, n + 1):
                if np.sum(labeled == i) >= 3:
                    mask |= (labeled == i)
        hour_arr = np.where(mask, hour_arr, np.nan)
    cs = ax.contourf(lon, lat, hour_arr, levels=np.arange(0, 25), cmap=cmap,
                     norm=norm, extend="neither")
    cbar = fig.colorbar(cs, ax=ax, label="Hour of peak 24h rainfall (local model tz)")
    cbar.set_ticks(range(0, 24, 3))
    title = "Maharashtra peak-rainfall timing (model grid)\n[Model-Interpolated Surface Field: Open-Meteo 0.5° Grid]"
    if issued_ist:
        title += f" | Issued {issued_ist} IST"
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    _add_provenance_footer(fig, issued_ist, "Open-Meteo 0.5° grid")
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(out_path, dpi=110, facecolor="white"); plt.close(fig)
    return out_path


def convective_map(lat, lon, cape_grid, bbox, out_path):
    """CAPE field with the published severe-convection threshold (2500 J/kg)
    contoured. CAPE is the only convective proxy we have (GFS). We NEVER infer
    orographic/SST triggers (no data source).

    Honesty: we auto-scale the colour range to the ACTUAL cape values present
    (not a fixed 0-4000), so a low-CAPE day shows a meaningful gradient instead
    of collapsing to flat pale-yellow. The 2500 J/kg severe line is drawn ONLY
    when the data actually reaches it; otherwise we state the max observed CAPE.
    """
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    cmap = LinearSegmentedColormap.from_list(
        "cape", ["#ffffcc", "#fed976", "#fd8d3c", "#e31a1c", "#800026"], N=256)
    arr = np.asarray(cape_grid, dtype=float)
    finite = arr[np.isfinite(arr)] if arr.size else np.array([])
    vmax = float(np.nanmax(finite)) if finite.size else 0.0
    if vmax <= 0:
        # no convective data -> explicit 'unavailable' panel, no fake field
        fig, ax = plt.subplots(figsize=(9, 7))
        ax.text(0.5, 0.5, "CAPE data unavailable\n(GFS convective vars not returned)",
                ha="center", va="center", fontsize=11)
        ax.set_axis_off()
        fig.savefig(out_path, dpi=110); plt.close(fig); return out_path
    # scale to actual data (floor at 2500 only if data reaches it, else data max)
    norm_vmax = max(vmax * 1.05, 50.0)
    norm = Normalize(vmin=0, vmax=norm_vmax)
    fig, ax = plt.subplots(figsize=(9, 7))
    cs = ax.contourf(lon, lat, cape_grid, levels=40, cmap=cmap, norm=norm)
    cbar = fig.colorbar(cs, ax=ax, label="CAPE (J/kg)")
    # Explicit integer ticks in real J/kg (suppress matplotlib's scientific
    # offset notation that appears on near-uniform data, e.g. '1e-11+1e2').
    from matplotlib.ticker import ScalarFormatter
    cbar.set_ticks([0.0, norm_vmax / 2.0, norm_vmax])
    cbar.ax.yaxis.set_major_formatter(ScalarFormatter())
    cbar.ax.set_yticklabels([f"{0:.0f}", f"{norm_vmax/2:.0f}", f"{norm_vmax:.0f}"])
    # 2500 J/kg severe-convection contour — only if data actually reaches it
    if vmax >= 2000:
        cl = ax.contour(lon, lat, cape_grid, levels=[2500], colors="k",
                        linewidths=1.2, linestyles="solid")
        try:
            if cl.levels is not None and any(len(s) for s in cl.allsegs.values()):
                ax.clabel(cl, inline=True, fontsize=7, fmt="%d J/kg")
        except Exception:
            pass
        title = ("Convective available potential energy (CAPE)\n"
                 "Black contour = 2500 J/kg severe-convection threshold (GFS)")
    else:
        title = (f"Convective available potential energy (CAPE)\n"
                 f"Max observed {vmax:.0f} J/kg — below 2500 J/kg severe threshold (GFS)")
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    fig.tight_layout(); fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


def _load_geo_overlays():
    """Load static drainage outfall / asset overlays from geo/*.json.
    Returns list of dicts {name, lat, lon, type}. Missing file -> []."""
    import json, os
    here = os.path.dirname(os.path.abspath(__file__))
    geo_dir = os.path.join(os.path.dirname(here), "geo")
    overlays = []
    for fn in ("drainage_outfalls.json",):
        p = os.path.join(geo_dir, fn)
        if not os.path.exists(p):
            continue
        try:
            data = json.load(open(p))
        except Exception:
            continue
        for key in ("outfalls", "assets_placeholder", "assets"):
            for o in data.get(key, []) or []:
                if o.get("lat") is None or o.get("lon") is None:
                    continue  # placeholder with null coords -> skip (do not plot)
                overlays.append({"name": o.get("name", "?"),
                                 "lat": float(o["lat"]), "lon": float(o["lon"]),
                                 "type": o.get("type", "outfall")})
    return overlays


def rainfall_field_map(grid_results, bbox, out_path="output/rainfall_field.png",
                       resolution=0.05, title="Maharashtra 24h rainfall (model grid)",
                       label="model-interpolated 0.5° grid", imd_warnings=None):
    """Render a continuous rainfall FIELD (not scatter) from grid points.

    grid_results: list[dict]{lat,lon,hourly:{precipitation:[...]}} from grid.fetch_grid
    bbox: (lat_min, lat_max, lon_min, lon_max) for the state mesh.
    Uses the 24h PEAK per point (IMD 24h band logic).
    """
    from engine.fieldmap import build_field, imd_colormap, CONTOUR_LEVELS
    import numpy as np
    coords, vals = [], []
    for r in grid_results:
        lat = r.get("lat"); lon = r.get("lon"); h = r.get("hourly")
        if lat is None or lon is None or not h or "precipitation" not in h:
            continue
        arr = [x for x in (h.get("precipitation") or [])[:24] if x is not None]
        if not arr:
            continue
        coords.append((lat, lon)); vals.append(float(max(arr)))
    if len(coords) < 3:
        # not enough data -> blank placeholder
        fig, ax = plt.subplots(figsize=(9, 7))
        ax.text(0.5, 0.5, "insufficient grid data", ha="center")
        fig.savefig(out_path, dpi=110); plt.close(fig); return out_path
    lon, lat, field = build_field(coords, np.asarray(vals), bbox, resolution=resolution)
    cmap, norm = imd_colormap()
    fig, ax = plt.subplots(figsize=(9, 7))
    # Dark header bar (IMD operational style)
    _add_imd_header(fig,
                    product_name="24h RAINFALL FIELD  |  Maharashtra",
                    issued_ist="",
                    source="Unofficial analyst product",
                    channel="IMD 24h warning bands",
                    domain="Open-Meteo multi-model fusion")
    # rainfall field with IMD bands
    cs = ax.contourf(lon, lat, field, levels=60, cmap=cmap, norm=norm,
                     extend="max", zorder=2)
    # IMD official threshold contour lines (marked explicitly, not the fill)
    cl = ax.contour(lon, lat, field, levels=CONTOUR_LEVELS, colors="k",
                    linewidths=1.0, linestyles="solid", zorder=3)
    try:
        if cl.levels and any(len(seg) for seg in cl.allsegs.values()):
            ax.clabel(cl, inline=True, fontsize=6, fmt="%.0fmm")
    except Exception:
        pass
    # IMD authoritative district overlay: transparent fill on top of model field
    try:
        overlay_imd_warnings(ax, imd_warnings)
    except Exception:
        pass
    # IMD-style DISCRETE legend: 4 color blocks matching IMD warning bands
    from matplotlib.patches import Patch
    from matplotlib.colors import ListedColormap, BoundaryNorm
    band_colors = ["#2ecc71", "#f1c40f", "#e67e22", "#e74c3c"]  # G/Y/O/R
    band_labels = ["Green <64.5 mm", "Yellow 64.5–115.5 mm",
                   "Orange 115.6–204.5 mm", "Red >204.5 mm"]
    # Place legend in bottom-right corner, horizontal, small squares
    legend = ax.legend(
        [Patch(facecolor=c, edgecolor="#333333", linewidth=0.5) for c in band_colors],
        band_labels,
        loc="lower right", fontsize=6, framealpha=0.9,
        title="IMD 24h warning bands", title_fontsize=7,
        ncol=2, handlelength=1.2, handleheight=1.0
    )
    legend.get_frame().set_edgecolor("#888888")
    # IMD graticule + clean typography
    apply_imd_style(ax, (bbox[2], bbox[0], bbox[3], bbox[1]),
                    title="", issued_ist="", dark=False)
    # overlay the 10 asset cities for client reference
    assets = getattr(rainfall_field_map, "_assets", [])
    if assets:
        for a in assets:
            ax.scatter(a["lon"], a["lat"], c="white", edgecolors="black", s=35,
                       zorder=5)
            ax.annotate(a["name"], (a["lon"], a["lat"]), fontsize=6,
                        xytext=(3, 3), textcoords="offset points", color="black")
    ax.set_xlim(bbox[2], bbox[3])  # lon
    ax.set_ylim(bbox[0], bbox[1])  # lat
    ax.set_xlabel(""); ax.set_ylabel("")
    fig.tight_layout(pad=2.0)
    fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


def _draw_mumbai_base_layer(ax):
    """Permanent high-contrast dark vector canvas using cached IMD geometries.
    No external tile-server dependencies. Always draws dark schematic."""
    import matplotlib.pyplot as plt
    minlon, minlat, maxlon, maxlat = ax.axis()
    if minlon == maxlon or minlat == maxlat:
        minlon, minlat, maxlon, maxlat = 72.6, 18.7, 73.6, 19.5
    ax.set_facecolor("#111216")
    ax.grid(True, which="both", color="#2c3e50", linestyle="--", linewidth=0.5, alpha=0.5)
    # Local polygon cache path, relative to repo root
    polygon_cache_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "cache", "imd_district_polygons.json"
    )
    if os.path.exists(polygon_cache_path):
        try:
            with open(polygon_cache_path, "r") as f:
                geo_data = json.load(f)
            for feature in geo_data.get("features", []):
                geom = feature.get("geometry", {})
                if geom.get("type") == "Polygon":
                    for coords in geom.get("coordinates", []):
                        xs, ys = zip(*coords)
                        ax.plot(xs, ys, color="#455a64", lw=1.2, linestyle="-", alpha=0.8)
                elif geom.get("type") == "MultiPolygon":
                    for poly in geom.get("coordinates", []):
                        for coords in poly:
                            xs, ys = zip(*coords)
                            ax.plot(xs, ys, color="#455a64", lw=1.2, linestyle="-", alpha=0.8)
        except Exception:
            ax.axhline(19.0, color="#2c3e50", linestyle=":", alpha=0.4)
            ax.axvline(72.8, color="#2c3e50", linestyle=":", alpha=0.4)
    # Superimpose existing schematic features with high-contrast muted tones
    _draw_mumbai_features(ax, dark=True)
    ax.set_xlim(minlon, maxlon)
    ax.set_ylim(minlat, maxlat)
    ax.tick_params(colors="#7f8c8d", labelsize=8)


def _draw_mumbai_features(ax, dark=False):
    """Draw simplified Mumbai reference features on top of OSM tiles or dark fallback."""
    edge_color = "#bbbbbb" if dark else "#0066cc"
    rail_color = "#555555" if dark else "#cc0000"
    island = [
        (72.824, 18.950), (72.828, 19.020), (72.838, 19.040),
        (72.858, 19.055), (72.878, 19.065), (72.895, 19.075),
        (72.905, 19.085), (72.912, 19.095), (72.915, 19.100),
        (72.912, 19.110), (72.905, 19.120), (72.892, 19.130),
        (72.880, 19.135), (72.868, 19.130), (72.855, 19.125),
        (72.842, 19.115), (72.830, 19.105), (72.822, 19.095),
        (72.818, 19.080), (72.820, 19.065), (72.822, 19.050),
        (72.824, 18.950),
    ]
    xs, ys = zip(*island)
    ax.plot(xs, ys, color=edge_color, linewidth=2.0, linestyle="-", zorder=15, alpha=0.8)
    # Railway lines (approximate)
    western_line = [
        (72.820, 19.000), (72.825, 19.020), (72.830, 19.040),
        (72.840, 19.060), (72.855, 19.080), (72.870, 19.100),
        (72.885, 19.120), (72.900, 19.140), (72.915, 19.160),
        (72.930, 19.180), (72.945, 19.200),
    ]
    central_line = [
        (72.900, 19.000), (72.910, 19.020), (72.920, 19.040),
        (72.930, 19.060), (72.940, 19.080), (72.950, 19.100),
        (72.960, 19.120), (72.970, 19.140), (72.980, 19.160),
        (72.990, 19.180), (73.000, 19.200),
    ]
    for pts in [western_line, central_line]:
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=rail_color, linewidth=2.5, linestyle="-", zorder=15, alpha=0.85)


def _add_simple_geo_base(ax, locs=None, dark=False):
    """Draw a simple geographic base: coastlines + thin district outlines
    from the cached IMD polygon file, so maps like nowcast_map have spatial
    context without relying on OSM tile fetch."""
    bg = "#111216" if dark else "#ffffff"
    ax.set_facecolor(bg)
    polygon_cache_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "cache", "imd_district_polygons.json"
    )
    if not os.path.exists(polygon_cache_path):
        return
    try:
        with open(polygon_cache_path) as f:
            data = json.load(f)
    except Exception:
        return
    edge = "#455a64" if dark else "#bbbbbb"
    fill = "#1a1d24" if dark else "#f0f0f0"
    drawn = 0
    for name, info in data.items():
        geom = info.get("geometry")
        if not geom:
            continue
        try:
            for patch in _poly_patches(geom, fill, edge):
                patch.set_linewidth(0.6)
                patch.set_zorder(1)
                ax.add_patch(patch)
            drawn += 1
        except Exception:
            pass
        if drawn > 200:
            break
    try:
        coast = [
            (72.8, 18.95), (72.85, 19.0), (72.9, 19.05), (73.0, 19.1),
            (73.1, 19.15), (73.2, 19.2), (73.3, 19.25), (73.4, 19.3),
        ]
        xs, ys = zip(*coast)
        ax.plot(xs, ys, color="#0066cc" if not dark else "#7f8c8d", linewidth=1.2,
                linestyle="-", zorder=2, alpha=0.7)
    except Exception:
        pass


def mmr_zoom_map(mmr_results, bbox, out_path="output/mmr_zoom.png",
                 resolution=0.02,
                 title="Mumbai Metro Region — rainfall + drainage outfalls",
                 district_polys=None, issued_ist=""):
    """OSM-style MMR map with rainfall overlay."""
    from engine.fieldmap import build_field, imd_colormap, CONTOUR_LEVELS
    import numpy as np
    coords, vals = [], []
    for r in mmr_results:
        lat = r.get("lat"); lon = r.get("lon"); h = r.get("hourly")
        if lat is None or lon is None or not h or "precipitation" not in h:
            continue
        arr = [x for x in (h.get("precipitation") or [])[:24] if x is not None]
        if not arr:
            continue
        coords.append((lat, lon)); vals.append(float(max(arr)))
    lon, lat, field = None, None, None
    if len(coords) >= 4:
        lon, lat, field = build_field(coords, np.asarray(vals),
                                      (bbox[1], bbox[3], bbox[0], bbox[2]),
                                      resolution=resolution)
    elif len(coords) >= 1:
        lon = np.array([c[1] for c in coords])
        lat = np.array([c[0] for c in coords])
        field = np.array(vals)
    else:
        # No valid MMR points at all — produce an explicit unavailable panel.
        fig, ax = plt.subplots(figsize=(11, 9), facecolor="white")
        ax.set_facecolor("#ffffff")
        _draw_mumbai_base_layer(ax)
        ax.text(0.5, 0.5, "MMR 0.1° rainfall field unavailable\n(no valid grid points this cycle)",
                ha="center", va="center", fontsize=11, color="#333333",
                bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#cccccc"))
        ax.set_xlim(bbox[0], bbox[2]); ax.set_ylim(bbox[1], bbox[3])
        ax.set_aspect("auto"); ax.axis("off")
        fig.savefig(out_path, dpi=130, bbox_inches="tight", facecolor="white")
        plt.close(fig); return out_path
    cmap, norm = imd_colormap()
    # OSM-style layout: white figure, clean subplot with minimal padding
    fig = plt.figure(figsize=(11, 9), facecolor="white")
    ax = fig.add_axes([0.06, 0.08, 0.88, 0.84])
    # White background
    ax.set_facecolor("#ffffff")
    # Draw base layer FIRST so it acts as true background
    _draw_mumbai_base_layer(ax)
    # Rainfall overlay — semi-transparent so base shows through
    cs = ax.contourf(lon, lat, field, levels=60, cmap=cmap, norm=norm,
                     extend="max", alpha=0.45, zorder=5)
    cl = ax.contour(lon, lat, field, levels=CONTOUR_LEVELS, colors="k",
                    linewidths=0.7, zorder=6, alpha=0.7)
    try:
        if cl.levels and any(len(seg) for seg in cl.allsegs.values()):
            ax.clabel(cl, inline=True, fontsize=6, fmt="%.0fmm",
                      zorder=7, colors="k")
    except Exception:
        pass
    # IMD district boundaries — thin colored outlines on top of everything
    try:
        if cl.levels and any(len(seg) for seg in cl.allsegs.values()):
            ax.clabel(cl, inline=True, fontsize=6, fmt="%.0fmm",
                      zorder=7, colors="k")
    except Exception:
        pass
    # IMD district boundaries — thin colored outlines on top
    if district_polys:
        for d in district_polys:
            code = d.get("color_code") or 0
            edge = _IMD_FILL.get(code, "#333333")
            for patch in _poly_patches(d.get("geometry"), "none", edge):
                patch.set_linewidth(1.8); patch.set_zorder(8); patch.set_alpha(1.0)
                ax.add_patch(patch)
            g = d.get("geometry")
            if g:
                cx = cy = 0.0; n = 0
                t = g.get("type"); c = g.get("coordinates", [])
                rings = [c[0]] if t == "Polygon" else [p[0] for p in c]
                for ring in rings:
                    for pt in ring:
                        try: cx += float(pt[0]); cy += float(pt[1]); n += 1
                        except (TypeError, ValueError, IndexError): pass
                if n:
                    ax.annotate(d.get("name", ""), (cx / n, cy / n),
                                fontsize=8, fontweight="bold", color="#111111",
                                ha="center", va="center",
                                bbox=dict(boxstyle="round,pad=0.3", fc="white",
                                          ec="none", alpha=0.85), zorder=10)
    # Static overlays (outfalls/assets)
    for o in _load_geo_overlays():
        color = "red" if o["type"] != "outfall" else "#222222"
        ax.scatter(o["lon"], o["lat"], c=color, s=45, marker="^",
                   edgecolors="white", linewidths=0.8, zorder=9)
        ax.annotate(o["name"], (o["lon"], o["lat"]), fontsize=7,
                    xytext=(4, 4), textcoords="offset points",
                    bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8),
                    zorder=10)
    # Light graticule grid
    minlon, minlat, maxlon, maxlat = bbox
    lat_step = 1 if (maxlat - minlat) <= 6 else 2
    lon_step = 1 if (maxlon - minlon) <= 6 else 2
    for la in np.arange(np.floor(minlat / lat_step) * lat_step,
                        np.ceil(maxlat / lat_step) * lat_step + 0.1, lat_step):
        if minlat <= la <= maxlat:
            ax.axhline(la, color="#cccccc", linewidth=0.5, linestyle="--", zorder=1)
    for lo in np.arange(np.floor(minlon / lon_step) * lon_step,
                        np.ceil(maxlon / lon_step) * lon_step + 0.1, lon_step):
        if minlon <= lo <= maxlon:
            ax.axvline(lo, color="#cccccc", linewidth=0.5, linestyle="--", zorder=1)
    # Axes styling — clean, light
    ax.tick_params(axis="both", labelsize=7, colors="#333333")
    ax.xaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.1f°E"))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.1f°N"))
    ax.set_xlabel("Longitude", fontsize=7, color="#333333")
    ax.set_ylabel("Latitude", fontsize=7, color="#333333")
    for spine in ax.spines.values():
        spine.set_edgecolor("#888888"); spine.set_linewidth(0.8)
    ax.set_xlim(minlon, maxlon); ax.set_ylim(minlat, maxlat)
    ax.set_aspect("auto")
    # Light horizontal colorbar (OSM-style: thin, clean)
    cbar = fig.colorbar(cs, ax=ax, label="24h rainfall (mm)",
                        ticks=[0.0, round(float(np.nanmax(field)) / 2.0, 1),
                               round(float(np.nanmax(field)), 1)],
                        orientation="horizontal", pad=0.06, aspect=50,
                        shrink=0.6)
    cbar.ax.set_xticklabels([f"{t:.1f}" for t in [
        0.0, round(float(np.nanmax(field)) / 2.0, 1),
        round(float(np.nanmax(field)), 1)
    ]], fontsize=7, color="#333333")
    cbar.set_label("24h rainfall (mm)", fontsize=8, color="#333333", labelpad=6)
    # Provenance footer (light text, small)
    if issued_ist:
        fig.text(0.5, 0.01,
                 f"Issued {issued_ist} IST  |  IMD district warnings + Open-Meteo grid  |  Unofficial analyst product",
                 ha="center", fontsize=7, color="#555555")
    fig.savefig(out_path, dpi=130, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def severe_field_map(mslp_grid, alerts, out_path="output/severe_field.png",
                     title="Severe-system outlook \u2014 MSLP field"):
    """Continuous MSLP FIELD (replaces 14-point scatter) over both basins."""
    lats, lons, z = [], [], []
    for basin, series in mslp_grid.items():
        for (la, lo), s in series.items():
            if s.get("pressure_msl"):
                lats.append(la); lons.append(lo)
                z.append(s["pressure_msl"][0])
    if len(z) < 3:
        # severe-system MSLP field could not be built (e.g. live basin fetch
        # failed / rate-limited). Show an explicit 'unavailable' panel rather
        # than a blank plot with only coast markers.
        fig, ax = plt.subplots(figsize=(9, 7))
        ax.text(0.5, 0.5, "Synoptic MSLP field unavailable\n(severe-system basin fetch failed this cycle)",
                ha="center", va="center", fontsize=10)
        ax.set_axis_off()
        fig.savefig(out_path, dpi=110); plt.close(fig); return out_path
    fig, ax = plt.subplots(figsize=(9, 7))
    if len(z) >= 3:
        tri = Triangulation(lons, lats)
        cs = ax.tricontourf(tri, z, levels=14, cmap="RdYlBu_r")
        fig.colorbar(cs, ax=ax, label="MSLP (hPa) \u2014 lower = stronger low")
        ax.tricontour(tri, z, levels=8, colors="k", linewidths=0.4, linestyles=":")
    ax.scatter([72.8, 73.0, 73.8], [19.1, 18.5, 16.7], c="black", s=20,
               marker="s", label="Maharashtra coast")
    cmap_t = {"cyclone": "red", "low_pressure": "orange", "severe_wind": "magenta"}
    for a in alerts:
        if a["region"] in ("Arabian Sea", "Bay of Bengal"):
            bx = BASIN_CENTROID.get(a["region"], (15, 70))
            ax.scatter([bx[1]], [bx[0]], c=cmap_t.get(a["type"], "red"), s=120,
                       marker="*", edgecolors="k",
                       label=f"{a['type']} ({a['region']}, ~{a.get('eta_days',0)}d)")
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.legend(loc="lower left", fontsize=7)
    fig.tight_layout(); fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Consolidated 2-image visual briefs (Option A: multi-panel subplots)
# ---------------------------------------------------------------------------
def compile_consolidated_visual_briefs(out_dir, grid_maps, issued_ist="", radar_frames=None):
    """Build exactly TWO composite figures by compositing the individual maps
    we already rendered (no re-fetch, no re-render of data). Panels embed the
    existing PNGs so each is a clean multi-subplot figure for a phone screen.

      Image 1 (state_synoptic_brief.png): 2x2
        TL rainfall field | TR peak-timing | BL convective CAPE | BR MSLP
      Image 2 (mmr_asset_brief.png): animated GIF 1x2
        L static MMR 0.1deg zoom (+outfalls) | R cycling IMD radar frames

    `grid_maps` is the list of rendered paths. Returns list of 2 output paths
    (only those it could build). Missing panels are skipped (graceful).
    `issued_ist` adds a provenance footer (issued time + sources).
    `radar_frames`: list of PNG frame paths from radar GIF → assembled into
    an animated GIF for the right panel.
    """
    import matplotlib.image as mpimg
    from matplotlib.patches import Patch
    by_base = {os.path.basename(p): p for p in grid_maps}

    def _place(ax, base_name, title):
        p = by_base.get(base_name)
        if not p or not os.path.exists(p):
            ax.text(0.5, 0.5, "DATA UNAVAILABLE\nNo source data for this panel",
                    ha="center", va="center", fontsize=10, color="#333333")
            ax.set_title(f"{title}\n(unavailable)", fontsize=10)
            ax.axis("off")
            return False
        img = mpimg.imread(p)
        ax.imshow(img); ax.set_title(title, fontsize=10); ax.axis("off")
        return True

    outs = []
    # ---- Image 1: state synoptic 2x2 ----
    fig1, axes1 = plt.subplots(2, 2, figsize=(13, 11), dpi=140)
    _place(axes1[0, 0], "rainfall_field.png", "24h Rainfall Field (IMD bands)")
    _place(axes1[0, 1], "timing_map.png", "Peak-Rainfall Timing (hour-of-day)")
    _place(axes1[1, 0], "convective_map.png", "Convective Field (CAPE, 2500 J/kg line)")
    _place(axes1[1, 1], "severe_field.png", "Synoptic MSLP (hPa)")
    fig1.suptitle("MAHARASHTRA STATE SYNOPTIC BRIEF", fontsize=13, fontweight="bold")
    fig1.tight_layout(pad=3.0)
    if issued_ist:
        fig1.text(0.5, 0.005,
                  f"Issued {issued_ist} IST  |  Sources: IMD district warnings + "
                  f"Open-Meteo grid  |  Unofficial analyst product",
                  ha="center", fontsize=7, color="#444444")
    p1 = os.path.join(out_dir, "state_synoptic_brief.png")
    fig1.savefig(p1, bbox_inches="tight"); plt.close(fig1)
    outs.append(p1)

    # ---- Image 2: MMR asset + live radar (animated GIF) ----
    # Build each frame as a 1x2 composite (MMR zoom | radar frame), then
    # assemble into an animated GIF using PIL. Left panel is static; right
    # panel cycles through radar frames for live observed reflectivity.
    p_mmr = by_base.get("mmr_zoom.png")
    if not p_mmr or not os.path.exists(p_mmr):
        print("[!] mmr_zoom.png missing; skipping animated composite")
        return outs

    _frames_dir = os.path.join(out_dir, "_anim_tmp")
    os.makedirs(_frames_dir, exist_ok=True)
    frame_paths = []
    radar_src = radar_frames or []
    if not radar_src:
        # fallback: use nowcast_map if no radar frames available
        radar_src = [by_base.get("nowcast_map.png")] if by_base.get("nowcast_map.png") else []

    for idx, rf in enumerate(radar_src):
        fig2, axes2 = plt.subplots(1, 2, figsize=(15, 7), dpi=140)
        # Left: static MMR zoom
        try:
            img_mmr = mpimg.imread(p_mmr)
            axes2[0].imshow(img_mmr)
            axes2[0].set_title("Mumbai Metro Region 0.1deg Zoom (+ outfalls)", fontsize=10)
        except Exception:
            axes2[0].set_title("MMR zoom (missing)"); axes2[0].axis("off")
        axes2[0].axis("off")
        # Right: radar frame
        if rf and os.path.exists(rf):
            try:
                img_radar = mpimg.imread(rf)
                axes2[1].imshow(img_radar); axes2[1].axis("off")
                axes2[1].set_title("IMD Doppler Radar Mosaic (observed reflectivity)", fontsize=10)
            except Exception:
                axes2[1].set_title("Radar (missing)"); axes2[1].axis("off")
        else:
            axes2[1].set_title("Radar (unavailable)"); axes2[1].axis("off")
        fig2.suptitle("MMR ASSET & NOWCAST MONITOR", fontsize=13, fontweight="bold")
        fig2.tight_layout(pad=3.0)
        frame_path = os.path.join(_frames_dir, f"frame_{idx:03d}.png")
        fig2.savefig(frame_path, bbox_inches="tight"); plt.close(fig2)
        frame_paths.append(frame_path)

    if len(frame_paths) <= 1:
        # single frame → save as static PNG, skip GIF
        p2 = os.path.join(out_dir, "mmr_asset_brief.png")
        if frame_paths:
            import shutil; shutil.copy(frame_paths[0], p2)
        outs.append(p2)
    else:
        # assemble animated GIF from frames
        try:
            from PIL import Image
            frames = [Image.open(fp).convert("RGB") for fp in frame_paths]
            p2 = os.path.join(out_dir, "mmr_asset_brief.gif")
            frames[0].save(p2, save_all=True, append_images=frames[1:],
                           duration=800, loop=0, optimize=True)
            outs.append(p2)
            print(f"[*] animated mmr_asset_brief.gif: {len(frames)} frames")
        except Exception as e:
            print(f"[!] GIF assembly failed: {e}; falling back to static PNG")
            p2 = os.path.join(out_dir, "mmr_asset_brief.png")
            if frame_paths:
                import shutil; shutil.copy(frame_paths[0], p2)
            outs.append(p2)

    # cleanup temp frames
    try:
        for fp in frame_paths:
            os.remove(fp)
        os.rmdir(_frames_dir)
    except Exception:
        pass
    return outs


# IMD 4-colour fill for district polygons (1=Green,2=Yellow,3=Orange,4=Red)
_IMD_FILL = {1: "#1a9850", 2: "#fee08b", 3: "#fc8d59", 4: "#d73027",
             0: "#cccccc", None: "#cccccc"}


def _poly_patches(geom, fill, edge):
    """Yield matplotlib Polygon patches for a GeoJSON geometry (Polygon/MultiPolygon)."""
    from matplotlib.patches import Polygon as MplPoly
    polys = []
    if not geom:
        return polys
    t = geom.get("type")
    coords = geom.get("coordinates", [])
    if t == "Polygon":
        polys.append(coords[0])  # exterior ring
    elif t == "MultiPolygon":
        for poly in coords:
            polys.append(poly[0])
    out = []
    for ring in polys:
        # ring is [[lon,lat],...]; matplotlib wants (x=lon,y=lat)
        try:
            pts = [(float(pt[0]), float(pt[1])) for pt in ring]
            if len(pts) >= 3:
                out.append(MplPoly(pts, closed=True, facecolor=fill,
                                    edgecolor=edge, linewidth=0.3, zorder=2))
        except (TypeError, ValueError, IndexError):
            continue
    return out


def district_choropleth(geo_warnings, bbox, assets=None,
                         out_path="output/district_choropleth.png",
                         issued_ist="", model_label="IMD district warnings"):
    """Fill Maharashtra districts by IMD official warning colour.

    geo_warnings: {district_upper: {color_code, color_label, geometry}}.
    bbox: (minlon, minlat, maxlon, maxlat). assets: list of {name,lat,lon,level?}.
    Only districts whose geometry intersects bbox are drawn (so we don't render
    all-India). Honest: colours are IMD's authoritative warnings.
    """
    import json as _json
    minlon, minlat, maxlon, maxlat = bbox

    def in_bbox(geom):
        # accept if ANY vertex falls inside bbox (cheap; polygons are small)
        if not geom:
            return False
        t = geom.get("type")
        coords = geom.get("coordinates", [])
        rings = []
        if t == "Polygon":
            rings = [coords[0]]
        elif t == "MultiPolygon":
            rings = [p[0] for p in coords]
        for ring in rings:
            for pt in ring:
                try:
                    lon, lat = float(pt[0]), float(pt[1])
                    if minlon <= lon <= maxlon and minlat <= lat <= maxlat:
                        return True
                except (TypeError, ValueError, IndexError):
                    continue
        return False

    fig, ax = plt.subplots(figsize=(9, 9), dpi=140)
    # IMD operational header bar (dark top, white text)
    _add_imd_header(fig,
                    product_name="MAHARASHTRA DISTRICT WARNINGS",
                    issued_ist=issued_ist or "",
                    source="IMD district_warnings_india (WFS)  |  Unofficial analyst product",
                    channel="IMD 24h colour bands",
                    domain="Maharashtra")
    drawn = 0
    for name, w in geo_warnings.items():
        if not in_bbox(w.get("geometry")):
            continue
        code = w.get("color_code")
        fill = _IMD_FILL.get(code, "#cccccc")
        for patch in _poly_patches(w.get("geometry"), fill, "#333333"):
            ax.add_patch(patch)
            drawn += 1
    # assets overlay (our monitored points)
    if assets:
        for a in assets:
            ax.plot(a["lon"], a["lat"], "k*", markersize=9, zorder=5)
            ax.annotate(a["name"], (a["lon"], a["lat"]),
                        fontsize=7, xytext=(2, 2), textcoords="offset points",
                        zorder=6)
    ax.set_xlim(minlon, maxlon)
    ax.set_ylim(minlat, maxlat)
    ax.set_autoscale_on(False)
    ax.set_aspect("auto")
    # IMD-style horizontal legend bar (swatch strip)
    from matplotlib.patches import Patch
    legend = [Patch(facecolor=_IMD_FILL[1], label="Green (<64.5 mm)"),
              Patch(facecolor=_IMD_FILL[2], label="Yellow (64.5-115.5)"),
              Patch(facecolor=_IMD_FILL[3], label="Orange (115.5-204.5)"),
              Patch(facecolor=_IMD_FILL[4], label="Red (>204.5)")]
    ax.legend(handles=legend, loc="lower left", fontsize=8, framealpha=0.95,
              title="IMD 24h warning", title_fontsize=8)
    apply_imd_style(ax, (minlon, minlat, maxlon, maxlat),
                    title="", issued_ist=issued_ist or "", dark=False)
    ax.set_xlabel(""); ax.set_ylabel("")
    fig.tight_layout(pad=2.0)
    fig.savefig(out_path, bbox_inches="tight"); plt.close(fig)
    return out_path


def _add_provenance_footer(fig, issued_ist, sources, valid_from="", valid_to=""):
    """Add a small provenance footer text to a composite figure."""
    lines = []
    if issued_ist:
        lines.append(f"Issued {issued_ist} IST")
    if valid_from or valid_to:
        lines.append(f"Valid {valid_from or '...'} – {valid_to or '...'} IST")
    lines.append("PROVENANCE NOTE: CAPE / Lifted Index / Soil Moisture: DATA UNAVAILABLE on current multi-model run.")
    lines.append("OROGRAPHIC BIAS WARNING: 0.5° Sparse source resolution limits precision along Western Ghats ridges.")
    if sources:
        lines.append(sources)
    lines.append("Unofficial analyst product")
    fig.subplots_adjust(bottom=0.10)
    fig.text(0.5, 0.005,
             "  |  ".join(lines),
             ha="center", fontsize=6, color="#555555", wrap=True)


def apply_imd_style(ax, bbox, title="", issued_ist="", product_name="",
                    dark=False):
    """Apply IMD operational styling to a map axes.

    IMD satellite uses light text on dark imagery; our generated color maps use
    dark text on light backgrounds. `dark=True` switches to IMD's white style.
    """
    grid_c = "white" if dark else "#444444"
    label_c = "white" if dark else "#222222"
    title_c = "white" if dark else "#111111"
    spine_c = "white" if dark else "#888888"

    minlon, minlat, maxlon, maxlat = bbox
    lat_step = 1 if (maxlat - minlat) <= 6 else 2
    lon_step = 1 if (maxlon - minlon) <= 6 else 2
    lats = np.arange(np.floor(minlat / lat_step) * lat_step,
                     np.ceil(maxlat / lat_step) * lat_step + 0.1, lat_step)
    lons = np.arange(np.floor(minlon / lon_step) * lon_step,
                     np.ceil(maxlon / lon_step) * lon_step + 0.1, lon_step)
    for la in lats:
        if minlat <= la <= maxlat:
            ax.axhline(la, color=grid_c, linewidth=0.7, linestyle="--",
                       alpha=0.7 if dark else 0.5, zorder=5)
    for lo in lons:
        if minlon <= lo <= maxlon:
            ax.axvline(lo, color=grid_c, linewidth=0.7, linestyle="--",
                       alpha=0.7 if dark else 0.5, zorder=5)
    ax.tick_params(axis="both", labelsize=7, colors=label_c)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.1f°E"))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.1f°N"))
    ax.set_xlabel("Longitude", fontsize=7, color=label_c)
    ax.set_ylabel("Latitude", fontsize=7, color=label_c)
    if title:
        t = title
        if issued_ist:
            t += f"\nIssued {issued_ist} IST"
        ax.set_title(t, fontsize=9, fontweight="bold", color=title_c)
    for spine in ax.spines.values():
        spine.set_edgecolor(spine_c)
        spine.set_linewidth(0.8)
        spine.set_alpha(0.7)

