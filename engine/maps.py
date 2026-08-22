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
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.tri import Triangulation

INDIA_RAMP = ["#f7fbff", "#c6dbef", "#6baed6", "#429c40", "#fee08b",
              "#fc8d59", "#d73027", "#a50026"]
WIND_RAMP = ["#0000ff", "#00ffff", "#00ff00", "#ffff00", "#ff0000"]


def rainfall_map(locations_with_vals, model_label="ECMWF", out_path="output/rainfall_map.png"):
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
    fig.tight_layout(); fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


def wind_map(locations_with_vals, model_label="ECMWF", out_path="output/wind_map.png"):
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
    fig.tight_layout(); fig.savefig(out_path, dpi=110); plt.close(fig)
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


def wind850_map(locations_with_vals, model_label="ECMWF", out_path="output/wind850_map.png"):
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
    fig.tight_layout(); fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


# IMD nowcast colour -> fill colour for the basemap-style nowcast map
_IMD_FILL = {1: "#2ca02c", 2: "#ffd700", 3: "#ff8c00", 4: "#d62728"}


def nowcast_map(locations_with_vals, model_label="IMD nowcast", out_path="output/nowcast_map.png"):
    """3h nowcast map seeded by IMD official district colour/intensity.
    locations_with_vals: dicts with 'imd_color' (1-4) and optional 'nowcast_mm'."""
    lats = np.array([l["lat"] for l in locations_with_vals])
    lons = np.array([l["lon"] for l in locations_with_vals])
    fig, ax = plt.subplots(figsize=(8, 8))
    cmap = LinearSegmentedColormap.from_list("imdcol", ["#2ca02c", "#ffd700", "#ff8c00", "#d62728"])
    # colour by IMD severity level (1..4)
    sev = np.array([max(1, min(4, int(l.get("imd_color", 1) or 1))) for l in locations_with_vals], dtype=float)
    sc = ax.scatter(lons, lats, c=sev, cmap=cmap, s=160, vmin=1, vmax=4, edgecolors="k")
    fig.colorbar(sc, ax=ax, label="IMD nowcast severity (1 Green - 4 Red)", ticks=[1, 2, 3, 4])
    for l in locations_with_vals:
        ax.annotate(f"{l['name']} {l.get('nowcast_label','')}".strip(),
                    (l["lon"], l["lat"]), textcoords="offset points",
                    xytext=(6, 6), fontsize=8)
    ax.set_title(f"District nowcast (next 3h) - {model_label} - Maharashtra", fontsize=11)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.margins(0.15)
    fig.tight_layout(); fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


def severe_map(mslp_grid: dict, alerts: list[dict], out_path="output/severe_map.png"):
    """MSLP field over Arabian Sea + Bay of Bengal with detected-system markers.

    mslp_grid: {basin: {(lat,lon): [hourly_mslp...]}}  (first timestep used for the field)
    alerts: list from severe_systems.build_outlook()['alerts'] (+ official watch)
    Plots contour of mean MSLP across the combined basins and marks alert centres.
    """
    lats, lons, z = [], [], []
    for basin, series in mslp_grid.items():
        for (la, lo), s in series.items():
            if s.get("pressure_msl"):
                lats.append(la); lons.append(lo)
                z.append(s["pressure_msl"][0])
    fig, ax = plt.subplots(figsize=(9, 7))
    if len(z) >= 3:
        tri = Triangulation(lons, lats)
        cs = ax.tricontourf(tri, z, levels=12, cmap="RdYlBu_r")
        fig.colorbar(cs, ax=ax, label="MSLP (hPa) — lower = stronger low")
    # mark Maharashtra coast for context
    ax.scatter([72.8, 73.0, 73.8], [19.1, 18.5, 16.7], c="black", s=20, marker="s",
               label="Maharashtra coast")
    # mark alerts
    cmap_t = {"cyclone": "red", "low_pressure": "orange", "severe_wind": "magenta"}
    for a in alerts:
        # place marker at basin centroid (coarse) — real centroid from grid bounds
        if a["region"] in ("Arabian Sea", "Bay of Bengal"):
            bx = BASIN_CENTROID.get(a["region"], (15, 70))
            ax.scatter([bx[1]], [bx[0]], c=cmap_t.get(a["type"], "red"), s=120,
                       marker="*", edgecolors="k",
                       label=f"{a['type']} ({a['region']}, ~{a.get('eta_days',0)}d)")
    ax.set_title("Severe-system outlook — MSLP field (Arabian Sea + Bay of Bengal)", fontsize=11)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.legend(loc="lower left", fontsize=7)
    fig.tight_layout(); fig.savefig(out_path, dpi=110); plt.close(fig)
    return out_path


# basin centroids for coarse alert markers
BASIN_CENTROID = {"Arabian Sea": (17.0, 68.0), "Bay of Bengal": (15.0, 89.0)}
