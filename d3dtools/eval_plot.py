"""
Plot every IoT sensor of an evaluate_sensor2 (eval_iot) run as a map: the sensor
point on its buffer polygon, over the simulated flood cells and an OpenStreetMap
basemap. One PNG per sensor, each 150 m (W) x 120 m (H) centred on the point.

The simulation layer shown for a sensor follows its observed max depth
(最大深*) field, as in evaluate_sensor2:
    depth >= --thresh-iot (30 cm)  -> --sim-low  (SHP/SIM_thrd125.shp)
    otherwise                      -> --sim-high (SHP/SIM_thrd475.shp)

Each sensor is classified TP / FN / FP / TN with the same rule:
    depth >= threshold: buffer hits --sim-low  -> TP, else FN
    depth <  threshold: buffer hits --sim-high -> FP, else TN
The class is shown as a badge on the plot, the overall confusion matrix in the
upper-right corner, and the depth of every cell under the sensor's buffer is
labelled (m).

The basemap needs `contextily` (pip install contextily); without it, or with
--no-basemap, the maps are drawn without one.
"""

import argparse
import importlib.util
import math
import os
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiPolygon, box

from .evaluate_sensor2 import _resolve_depth_field

SCRIPT = "eval_plot"

SIM_LOW = os.path.join("SHP", "SIM_thrd125.shp")    # used when depth >= threshold
SIM_HIGH = os.path.join("SHP", "SIM_thrd475.shp")   # used otherwise
OBS_FILE = os.path.join("SHP", "IOT_SENSOR.shp")
BUFFER_FILE = os.path.join("SHP", "IOT_BUFFER.gpkg")  # written by evaluate_sensor2
OUT_DIR = "PLOTS"
ID_FIELD = "序號"
DEPTH_THRESHOLD = 30.0      # cm
BUFFER_FALLBACK = 20.0      # m, for a sensor without a buffer polygon
EXTENT_W = 150.0            # m
EXTENT_H = 120.0            # m
METRIC_CRS = "EPSG:3826"    # TWD97 / TM2 zone 121 (metres)
TILE_CACHE = ".tile_cache"

# SIM attribute used to colour the cells; the first one found is used
# (fou2shp / fou2mfd write fou_dep, ncts2shp max_dep, older exports Val_1)
SIM_VALUE_FIELDS = ("Val_1", "fou_dep", "max_dep")
_SIM_VALUE = "_sim_value"
SIM_CMAP = "Blues"
SIM_EDGE_COLOR = "steelblue"
SIM_EDGE_WIDTH = 0.6
CELL_LABEL_SIZE = 7
CELL_LABEL_ZORDER = 10

CLASS_COLORS = {"TP": "#2e7d32", "FN": "#c62828", "FP": "#ef6c00", "TN": "#1565c0"}
CLASS_NAMES = {"TP": "True Positive", "FN": "False Negative",
               "FP": "False Positive", "TN": "True Negative"}

CJK_FONTS = ["Microsoft JhengHei", "Noto Sans CJK TC", "Microsoft YaHei", "DejaVu Sans"]


def _isnan(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def fmt_depth(v):
    """Depth rounded to the nearest integer (half up), 'N/A' if missing."""
    return "N/A" if _isnan(v) else str(int(math.floor(float(v) + 0.5)))


def _union(geoms):
    return geoms.union_all() if hasattr(geoms, "union_all") else geoms.unary_union


def _to_metric(gdf):
    return gdf.to_crs(METRIC_CRS) if gdf.crs else gdf.set_crs(METRIC_CRS)


def _load_contextily(tile_cache):
    """Import contextily (None if missing), with a usable PROJ and an OSM-friendly setup."""
    # A system-wide PROJ_LIB (e.g. from TauDEM) can point rasterio at an
    # incompatible proj.db; prefer rasterio's bundled PROJ data when available.
    rio = importlib.util.find_spec("rasterio")
    if rio and rio.origin:
        proj_dir = Path(rio.origin).parent / "proj_data"
        if (proj_dir / "proj.db").exists():
            os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"] = str(proj_dir)
            try:  # rasterio is usually imported already, so also tell GDAL directly
                from rasterio.env import set_proj_data_search_path
                set_proj_data_search_path(str(proj_dir))
            except Exception:
                pass
    try:
        import contextily as ctx
    except ImportError:
        print("Warning: contextily is not installed (pip install contextily); "
              "plotting without a basemap")
        return None
    # OSM's tile policy blocks contextily's random default User-Agent (HTTP 403),
    # so send a descriptive one, and cache tiles on disk.
    ctx.tile.USER_AGENT = "d3dtools-eval_plot/1.0 (python; contextily)"
    if tile_cache:
        os.makedirs(tile_cache, exist_ok=True)
        ctx.set_cache_dir(tile_cache)
    return ctx


def classify(pts, polys, sims, use_low, depth_field, id_field,
             threshold=DEPTH_THRESHOLD, fallback=BUFFER_FALLBACK):
    """Return ({index: 'TP'/'FN'/'FP'/'TN'/None}, {index: buffer geometry})."""
    classes, buffers = {}, {}
    for idx, row in pts.iterrows():
        own = polys[polys[id_field] == row[id_field]]
        geom = row.geometry.buffer(fallback) if own.empty else _union(own.geometry)
        buffers[idx] = geom
        depth = row[depth_field]
        if _isnan(depth):
            classes[idx] = None
            continue
        sim = sims.get(bool(use_low.loc[idx]))
        hit = sim is not None and len(sim.sindex.query(geom, predicate="intersects")) > 0
        if depth >= threshold:
            classes[idx] = "TP" if hit else "FN"
        else:
            classes[idx] = "FP" if hit else "TN"
    return classes, buffers


def summarize(classes):
    """Counts, accuracy and recall (%) of a classes dict."""
    counts = {k: sum(1 for c in classes.values() if c == k) for k in CLASS_COLORS}
    n = sum(counts.values())
    pos = counts["TP"] + counts["FN"]
    return {
        **counts,
        "n": n,
        "accuracy": (counts["TP"] + counts["TN"]) / n * 100 if n else 0.0,
        "recall": counts["TP"] / pos * 100 if pos else 0.0,
    }


def plot_sensors(sim_low=SIM_LOW, sim_high=SIM_HIGH, obs=OBS_FILE, buffer_file=BUFFER_FILE,
                 out_dir=OUT_DIR, ids=None, id_field=ID_FIELD, depth_field=None,
                 threshold=DEPTH_THRESHOLD, buffer_fallback=BUFFER_FALLBACK,
                 extent=(EXTENT_W, EXTENT_H), dpi=150, zoom=19, basemap=True,
                 tile_cache=TILE_CACHE):
    """
    Write one PNG map per sensor to `out_dir` (IOT_<id>.png).

    `sim_low` is shown / tested for sensors with depth >= `threshold` (cm),
    `sim_high` for the others. Sensors without a polygon in `buffer_file`
    (or when `buffer_file` is None / missing) get a `buffer_fallback` m circle.
    Return a dict with the confusion matrix summary and the PNG paths.
    """
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib.colors import Normalize

    plt.switch_backend("Agg")
    ctx = _load_contextily(tile_cache) if basemap else None
    ext_w, ext_h = extent

    pts = _to_metric(gpd.read_file(obs))
    if buffer_file and os.path.exists(buffer_file):
        polys = _to_metric(gpd.read_file(buffer_file))
    else:
        if buffer_file:
            print(f"Warning: buffer file not found: {buffer_file}; "
                  f"using {buffer_fallback:g} m circles")
        polys = gpd.GeoDataFrame({id_field: []}, geometry=[], crs=METRIC_CRS)

    for name, layer in (("sensor", pts), ("buffer", polys)):
        if id_field not in layer.columns:
            raise ValueError(f"ID field '{id_field}' not in the {name} layer; available: "
                             f"{[c for c in layer.columns if c != 'geometry']}")
    depth_field = depth_field or _resolve_depth_field(pts.columns)
    if depth_field is None or depth_field not in pts.columns:
        raise ValueError(f"no depth field (最大深*) in {obs}; give one with --depth-field")

    if ids:
        missing = set(ids) - set(pts[id_field].astype(str))
        if missing:
            print(f"Warning: IDs not found: {sorted(missing)}")
        pts = pts[pts[id_field].astype(str).isin(ids)]

    os.makedirs(out_dir, exist_ok=True)
    use_low = pts[depth_field].fillna(-1) >= threshold

    def frame(pt):
        return box(pt.x - ext_w / 2, pt.y - ext_h / 2, pt.x + ext_w / 2, pt.y + ext_h / 2)

    # The SIM files are large; read each once, only the cells inside the frames
    # of the sensors that use it.
    sims, sim_fields, sim_names = {}, {}, {True: Path(sim_low).stem, False: Path(sim_high).stem}
    for is_low, path in ((True, sim_low), (False, sim_high)):
        sel = pts[use_low == is_low]
        if sel.empty:
            continue
        print(f"Reading {path} for {len(sel)} sensor(s) ...")
        sim = gpd.read_file(path, mask=MultiPolygon([frame(g) for g in sel.geometry]))
        field = next((f for f in SIM_VALUE_FIELDS if f in sim.columns), None)
        if field is None:
            raise ValueError(f"{path} has none of {list(SIM_VALUE_FIELDS)}; available: "
                             f"{[c for c in sim.columns if c != 'geometry']}")
        sim[_SIM_VALUE] = sim[field]
        sim_fields[is_low] = field
        sims[is_low] = _to_metric(sim)

    all_vals = [v for g in sims.values() for v in g[_SIM_VALUE].dropna()]
    norm = Normalize(vmin=0, vmax=max(all_vals) if all_vals else 1)

    classes, buffers = classify(pts, polys, sims, use_low, depth_field, id_field,
                                threshold, buffer_fallback)
    s = summarize(classes)
    print("TP={TP} FN={FN} FP={FP} TN={TN}  Accuracy={accuracy:.2f}%  "
          "Recall={recall:.2f}%".format(**s))
    summary_txt = ("Confusion matrix (n={n})\n"
                   "TP {TP:>3}   FN {FN:>3}\n"
                   "FP {FP:>3}   TN {TN:>3}\n"
                   "Acc {accuracy:.1f}%  Recall {recall:.1f}%").format(**s)

    pngs = []
    rc = {"font.sans-serif": CJK_FONTS, "axes.unicode_minus": False}
    with matplotlib.rc_context(rc):
        for idx, row in pts.iterrows():
            sid = row[id_field]
            is_low = bool(use_low.loc[idx])
            sim_name = sim_names[is_low]
            x, y = row.geometry.x, row.geometry.y
            xmin, xmax = x - ext_w / 2, x + ext_w / 2
            ymin, ymax = y - ext_h / 2, y + ext_h / 2

            fig, ax = plt.subplots(figsize=(10, 10 * ext_h / ext_w))

            sim = sims.get(is_low)
            sim_in_view = sim.cx[xmin:xmax, ymin:ymax] if sim is not None else None
            if sim_in_view is not None and not sim_in_view.empty:
                sim_in_view.plot(ax=ax, column=_SIM_VALUE, cmap=SIM_CMAP, norm=norm,
                                 alpha=0.6, edgecolor="none", zorder=2)
                sim_in_view.plot(ax=ax, facecolor="none", edgecolor=SIM_EDGE_COLOR,
                                 linewidth=SIM_EDGE_WIDTH, zorder=2.5)
                # depth labels (m) on the cells under this sensor's buffer
                hit = sim_in_view.sindex.query(buffers[idx], predicate="intersects")
                for _, cell in sim_in_view.iloc[hit].iterrows():
                    val = cell[_SIM_VALUE]
                    if _isnan(val):
                        continue
                    lp = cell.geometry.representative_point()
                    ax.text(lp.x, lp.y, f"{float(val):.2f}", ha="center", va="center",
                            fontsize=CELL_LABEL_SIZE, color="black",
                            zorder=CELL_LABEL_ZORDER, clip_on=True,
                            path_effects=[pe.withStroke(linewidth=2, foreground="white")])
            else:
                print(f"[{sid}] no {sim_name} cells in view")

            sm = plt.cm.ScalarMappable(cmap=SIM_CMAP, norm=norm)
            cbar = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
            cbar.set_label(f"{sim_name} {sim_fields.get(is_low, '')}".strip())

            # buffers as outlines: this sensor's highlighted, neighbours dashed
            others = polys[polys[id_field] != sid].cx[xmin:xmax, ymin:ymax]
            if not others.empty:
                others.plot(ax=ax, facecolor="none", edgecolor="dimgrey",
                            linewidth=1, linestyle="--", zorder=3)
            gpd.GeoSeries([buffers[idx]], crs=METRIC_CRS).plot(
                ax=ax, facecolor="none", edgecolor="navy", linewidth=2.5, zorder=3)
            gpd.GeoSeries([row.geometry], crs=METRIC_CRS).plot(
                ax=ax, color="red", edgecolor="black", markersize=80, zorder=4)

            depth_txt = fmt_depth(row[depth_field])
            ax.annotate(f"{sid}\n{depth_field}: {depth_txt}", xy=(x, y), xytext=(8, 8),
                        textcoords="offset points", fontsize=12, fontweight="bold",
                        color="black", zorder=5,
                        path_effects=[pe.withStroke(linewidth=3, foreground="white")])

            cls = classes.get(idx)
            ax.text(0.015, 0.985, f"{cls}  {CLASS_NAMES[cls]}" if cls else "Unclassified",
                    transform=ax.transAxes, ha="left", va="top", fontsize=13,
                    fontweight="bold", color="white", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.4",
                              facecolor=CLASS_COLORS.get(cls, "grey"), edgecolor="none"))
            ax.text(0.985, 0.985, summary_txt, transform=ax.transAxes, ha="right",
                    va="top", fontsize=9, family="monospace", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                              edgecolor="grey", alpha=0.85))

            ax.set_xlim(xmin, xmax)
            ax.set_ylim(ymin, ymax)
            ax.set_aspect("equal")
            if ctx is not None:
                try:
                    ctx.add_basemap(ax, crs=METRIC_CRS,
                                    source=ctx.providers.OpenStreetMap.Mapnik,
                                    zoom=zoom, attribution_size=6)
                except Exception as e:  # keep going if tiles fail for one plot
                    print(f"[{sid}] basemap failed: {e}")
                ax.set_xlim(xmin, xmax)  # add_basemap can nudge the limits
                ax.set_ylim(ymin, ymax)

            ax.set_title(f"IoT Sensor {sid}  ({depth_field} = {depth_txt}, "
                         f"overlay: {sim_name}, {cls or 'N/A'})\n"
                         f"extent {ext_w:.0f} m x {ext_h:.0f} m")
            ax.set_xlabel("X (m, TWD97)")
            ax.set_ylabel("Y (m, TWD97)")
            ax.ticklabel_format(useOffset=False, style="plain")

            out_png = os.path.join(out_dir, f"IOT_{sid}.png")
            fig.savefig(out_png, dpi=dpi, bbox_inches="tight")
            plt.close(fig)
            print(f"Saved {out_png}")
            pngs.append(out_png)

    return {**s, "classes": {pts.loc[i, id_field]: c for i, c in classes.items()},
            "pngs": pngs}


def main():
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Plot every IoT sensor of an evaluate_sensor2 (eval_iot) run as a map "
                    "(sensor, buffer, simulated flood cells, OpenStreetMap basemap) with "
                    "its TP / FN / FP / TN class. One PNG per sensor.",
        epilog="""
examples:
  %(prog)s                                  (SHP/SIM_thrd125 + SIM_thrd475, SHP/IOT_SENSOR.shp,
                                            SHP/IOT_BUFFER.gpkg -> PLOTS/IOT_<id>.png)
  %(prog)s --ids U0197 U0273                (only these sensors)
  %(prog)s -of PLOTS --dpi 200
  %(prog)s --id-field SENSOR_ID
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp
  %(prog)s --thresh-iot 10                  (same threshold as eval_iot --thresh-iot 10)
  %(prog)s --no-basemap                     (offline, no OpenStreetMap tiles)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sim-low", default=SIM_LOW,
                        help="Low-threshold SIM shapefile, used for sensors with depth >= "
                             "--thresh-iot (default: %(default)s)")
    parser.add_argument("--sim-high", default=SIM_HIGH,
                        help="High-threshold SIM shapefile, used for the other sensors "
                             "(default: %(default)s)")
    parser.add_argument("--obs", default=OBS_FILE,
                        help="Sensor point shapefile or GeoPackage (default: %(default)s)")
    parser.add_argument("--buffer-file", default=BUFFER_FILE,
                        help="Buffered sensor areas written by eval_iot --output-buffer "
                             "(default: %(default)s). Use '' to draw --buffer circles instead.")
    parser.add_argument("--buffer", type=float, default=BUFFER_FALLBACK, metavar="M",
                        help="Buffer radius (m) for sensors without a buffer polygon "
                             "(default: %(default)g)")
    parser.add_argument("--thresh-iot", type=float, default=DEPTH_THRESHOLD, metavar="CM",
                        help="Water depth threshold in cm (default: %(default)g)")
    parser.add_argument("--id-field", default=ID_FIELD,
                        help="Sensor ID column shared by the sensor and buffer layers "
                             "(default: %(default)s)")
    parser.add_argument("--depth-field", default=None,
                        help="Observed max depth column (default: first column starting "
                             "with 最大深)")
    parser.add_argument("--ids", nargs="+", metavar="ID", help="Sensor IDs to plot (default: all)")
    parser.add_argument("-of", "--output-folder", dest="out_dir", default=OUT_DIR, metavar="DIR",
                        help="Output folder for the PNGs (default: %(default)s)")
    parser.add_argument("--extent", type=float, nargs=2, default=(EXTENT_W, EXTENT_H),
                        metavar=("W", "H"), help="Map extent in metres (default: 150 120)")
    parser.add_argument("--dpi", type=int, default=150, help="PNG resolution (default: 150)")
    parser.add_argument("--zoom", type=int, default=19,
                        help="OpenStreetMap tile zoom level, max 19 (default: 19)")
    parser.add_argument("--no-basemap", action="store_true",
                        help="Do not download / draw the OpenStreetMap basemap")
    parser.add_argument("--tile-cache", default=TILE_CACHE, metavar="DIR",
                        help="Folder to cache basemap tiles in (default: %(default)s)")
    args = parser.parse_args()

    for label, path in (("SIM (low threshold)", args.sim_low),
                        ("SIM (high threshold)", args.sim_high),
                        ("sensor", args.obs)):
        if not os.path.exists(path):
            print(f"Error: {label} file does not exist: {path}")
            sys.exit(1)
    if min(args.extent) <= 0:
        parser.error("--extent values must be > 0")

    try:
        plot_sensors(args.sim_low, args.sim_high, args.obs, args.buffer_file or None,
                     args.out_dir, args.ids, args.id_field, args.depth_field,
                     args.thresh_iot, args.buffer, tuple(args.extent), args.dpi,
                     args.zoom, not args.no_basemap, args.tile_cache)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
