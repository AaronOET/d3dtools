"""
Plot every IoT sensor of an evaluate_sensor2 (eval_iot) run as a map: the sensor
point on its buffer polygon, over the simulated flood cells and an OpenStreetMap
basemap. One PNG per sensor, each 150 m (W) x 120 m (H) centred on the point.

The simulation layer shown for a sensor follows its observed max depth
(最大深*) field, as in evaluate_sensor2:
    depth >= --thresh-iot (30 cm)  -> --sim-low  (SHP/SIM_thrd125.shp)
    otherwise                      -> --sim-high (SHP/SIM_thrd475.shp)

TP / FN / FP / TN are counted on the buffer layer (--buffer-file /
--buffer-layer), feature by feature with each feature's own depth, exactly as
evaluate_sensor2 counts them:
    depth >= threshold: buffer hits --sim-low  -> TP, else FN
    depth <  threshold: buffer hits --sim-high -> FP, else TN
Accuracy is (TP + TN) over all buffer features (also those without a depth).
The matrix always covers the whole buffer layer, also with --ids. Without a
buffer file, --buffer m circles around every sensor are counted instead.
Each map shows its sensor's class as a badge ("not counted" when the sensor has
no buffer feature), the overall confusion matrix in the upper-right corner, and
the depth (m) of every cell under the sensor's buffer.

--obs can be a shapefile, a GeoPackage (*.gpkg; --obs-layer picks the layer
of a multi-layer file) or a GeoJSON (*.geojson). The buffers are read by
default from SHP/IOT_BUFFER.geojson, or GPKG/IOT_BUFFER.geojson for a GeoPackage
--obs, where eval_iot writes them (the IOT_BUFFER.gpkg of eval_iot < 0.38.1 is
used when there is no .geojson).

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
from shapely import union_all
from shapely.geometry import box

from .evaluate_sensor2 import _resolve_depth_field, numeric_depth

SCRIPT = "eval_plot"

SIM_LOW = os.path.join("SHP", "SIM_thrd125.shp")    # used when depth >= threshold
SIM_HIGH = os.path.join("SHP", "SIM_thrd475.shp")   # used otherwise
OBS_FILE = os.path.join("SHP", "IOT_SENSOR.shp")
BUFFER_FILE = os.path.join("SHP", "IOT_BUFFER.geojson")  # written by evaluate_sensor2
# evaluate_sensor2 writes the buffers into a GPKG folder when --obs is a GeoPackage
BUFFER_FILE_GPKG = os.path.join("GPKG", "IOT_BUFFER.geojson")
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


def is_gpkg(path):
    return bool(path) and os.path.splitext(str(path))[1].lower() == ".gpkg"


def _list_layers(path):
    """Layer names of a vector file ([] if they cannot be listed)."""
    try:
        if hasattr(gpd, "list_layers"):  # geopandas >= 1.0
            return list(gpd.list_layers(path)["name"])
        import pyogrio
        return [row[0] for row in pyogrio.list_layers(path)]
    except Exception:
        try:
            import fiona
            return list(fiona.listlayers(path))
        except Exception:
            return []


def read_layer(path, layer=None, **kw):
    """
    Read a shapefile or GeoPackage. For a GeoPackage with several layers and no
    `layer` given, the first layer is used (with a note listing the others).
    """
    if layer is None and is_gpkg(path):
        layers = _list_layers(path)
        if len(layers) > 1:
            print(f"Note: {path} has {len(layers)} layers {layers}; "
                  f"using '{layers[0]}' (choose another with --obs-layer / --buffer-layer)")
            layer = layers[0]
    elif layer is not None and is_gpkg(path):
        layers = _list_layers(path)
        if layers and layer not in layers:
            raise ValueError(f"layer '{layer}' not in {path}; available: {layers}")
    return gpd.read_file(path, layer=layer, **kw) if layer is not None else gpd.read_file(path, **kw)


def default_buffer_file(obs):
    """
    Where eval_iot writes the buffers for this --obs (GPKG/ folder for a
    GeoPackage). eval_iot < 0.38.1 wrote IOT_BUFFER.gpkg instead of
    IOT_BUFFER.geojson; that file is used when there is no .geojson.
    """
    candidates = [BUFFER_FILE_GPKG] if is_gpkg(obs) else []
    candidates.append(BUFFER_FILE)
    for path in candidates:
        for p in (path, os.path.splitext(path)[0] + ".gpkg"):
            if os.path.exists(p):
                return p
    return BUFFER_FILE


def _as_points(gdf):
    """Sensor locations as Points (MultiPoint / polygon features -> a point inside them)."""
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    not_point = gdf.geom_type != "Point"
    if not_point.any():
        gdf.loc[not_point, "geometry"] = gdf.geometry[not_point].representative_point()
    return gdf


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


def classify(layer, sims, use_low, depth_field, threshold=DEPTH_THRESHOLD):
    """
    {index: 'TP'/'FN'/'FP'/'TN'/None} for every feature of `layer` (the buffer
    areas), with the same rule as evaluate_sensor2:
        depth >= threshold: geometry hits sims[True]  -> TP, else FN
        depth <  threshold: geometry hits sims[False] -> FP, else TN
    Features without a depth value are None (not classified).
    """
    classes = {}
    for idx, row in layer.iterrows():
        depth = row[depth_field]
        if _isnan(depth) or row.geometry is None or row.geometry.is_empty:
            classes[idx] = None
            continue
        sim = sims.get(bool(use_low.loc[idx]))
        hit = sim is not None and len(sim.sindex.query(row.geometry, predicate="intersects")) > 0
        if depth >= threshold:
            classes[idx] = "TP" if hit else "FN"
        else:
            classes[idx] = "FP" if hit else "TN"
    return classes


def summarize(classes):
    """
    Counts, accuracy and recall (%) of a classes dict. As in evaluate_sensor2,
    accuracy is (TP + TN) over ALL features, including unclassified ones.
    """
    counts = {k: sum(1 for c in classes.values() if c == k) for k in CLASS_COLORS}
    n = len(classes)
    pos = counts["TP"] + counts["FN"]
    return {
        **counts,
        "n": n,
        "unclassified": n - sum(counts.values()),
        "accuracy": (counts["TP"] + counts["TN"]) / n * 100 if n else 0.0,
        "recall": counts["TP"] / pos * 100 if pos else 0.0,
    }


def plot_sensors(sim_low=SIM_LOW, sim_high=SIM_HIGH, obs=OBS_FILE, buffer_file="auto",
                 out_dir=OUT_DIR, ids=None, id_field=ID_FIELD, depth_field=None,
                 threshold=DEPTH_THRESHOLD, buffer_fallback=BUFFER_FALLBACK,
                 extent=(EXTENT_W, EXTENT_H), dpi=150, zoom=19, basemap=True,
                 tile_cache=TILE_CACHE, obs_layer=None, buffer_layer=None):
    """
    Write one PNG map per sensor to `out_dir` (IOT_<id>.png).

    `obs` is a shapefile, GeoPackage (`obs_layer` picks the layer of a
    multi-layer GeoPackage) or GeoJSON. `sim_low` is shown / tested for sensors with
    depth >= `threshold` (cm), `sim_high` for the others. `buffer_file="auto"`
    uses where eval_iot writes the buffers: GPKG/IOT_BUFFER.geojson for a
    GeoPackage `obs` (if it exists), else SHP/IOT_BUFFER.geojson (or the
    older IOT_BUFFER.gpkg in the same folders). Sensors without
    a buffer polygon (or when `buffer_file` is None / missing) get a
    `buffer_fallback` m circle.
    The confusion matrix is counted on the buffer features (see the module
    docstring), not on the sensor points, and covers the whole buffer layer
    whatever `ids` selects.
    Return a dict with the confusion matrix summary, the class per buffer ID
    and the PNG paths.
    """
    if buffer_file == "auto":
        buffer_file = default_buffer_file(obs)
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    from matplotlib.colors import Normalize

    plt.switch_backend("Agg")
    ctx = _load_contextily(tile_cache) if basemap else None
    ext_w, ext_h = extent

    pts = _as_points(_to_metric(read_layer(obs, obs_layer)))
    print(f"Sensors: {obs}{f' (layer {obs_layer})' if obs_layer else ''}  - {len(pts)} point(s)")
    if buffer_file and os.path.exists(buffer_file):
        polys = _to_metric(read_layer(buffer_file, buffer_layer))
        print(f"Buffers: {buffer_file}")
    else:
        if buffer_file:
            print(f"Warning: buffer file not found: {buffer_file}; "
                  f"using {buffer_fallback:g} m circles")
        polys = gpd.GeoDataFrame({id_field: []}, geometry=[], crs=METRIC_CRS)

    for name, layer in (("sensor", pts), ("buffer", polys)):
        if id_field not in layer.columns:
            raise ValueError(f"ID field '{id_field}' not in the {name} layer; available: "
                             f"{[c for c in layer.columns if c != 'geometry']}")
    # compare IDs as text, so e.g. integer IDs in a GeoPackage match the --ids given
    pts[id_field] = pts[id_field].astype(str)
    polys[id_field] = polys[id_field].astype(str)
    depth_field = depth_field or _resolve_depth_field(pts.columns)
    if depth_field is None or depth_field not in pts.columns:
        raise ValueError(f"no depth field (最大深*) in {obs}; give one with --depth-field")
    pts[depth_field] = numeric_depth(pts[depth_field], depth_field)

    # The confusion matrix is counted on the buffer layer, feature by feature,
    # as evaluate_sensor2 counts it; without one, on buffer_fallback m circles
    # around every sensor.
    if not polys.empty:
        if depth_field not in polys.columns:
            print(f"Note: no '{depth_field}' in the buffer layer; "
                  f"taking it from the sensor layer by {id_field}")
            depth_by_id = pts.drop_duplicates(id_field).set_index(id_field)[depth_field]
            polys[depth_field] = polys[id_field].map(depth_by_id)
        polys[depth_field] = numeric_depth(polys[depth_field], f"buffer {depth_field}")
        counted = polys
        count_src = f"{len(polys)} buffer feature(s) in {buffer_file}"
    else:
        counted = pts.copy()
        counted["geometry"] = pts.geometry.buffer(buffer_fallback)
        count_src = f"{buffer_fallback:g} m circles around {len(pts)} sensor(s)"
    counted_low = counted[depth_field].fillna(-1) >= threshold

    if ids:
        ids = [str(i) for i in ids]
        missing = set(ids) - set(pts[id_field])
        if missing:
            print(f"Warning: IDs not found: {sorted(missing)}")
        pts = pts[pts[id_field].isin(ids)]

    os.makedirs(out_dir, exist_ok=True)
    use_low = pts[depth_field].fillna(-1) >= threshold

    def frame(pt):
        return box(pt.x - ext_w / 2, pt.y - ext_h / 2, pt.x + ext_w / 2, pt.y + ext_h / 2)

    # The SIM files are large; read each once, only the cells inside the frames
    # of the plotted sensors and under the counted buffers that use it.
    sims, sim_fields, sim_names = {}, {}, {True: Path(sim_low).stem, False: Path(sim_high).stem}
    for is_low, path in ((True, sim_low), (False, sim_high)):
        sel = pts[use_low == is_low]
        cnt = counted[counted_low == is_low]
        if sel.empty and cnt.empty:
            continue
        print(f"Reading {path} for {len(sel)} plotted sensor(s) / {len(cnt)} counted buffer(s) ...")
        parts = [frame(g) for g in sel.geometry]
        parts += [g for g in cnt.geometry if g is not None and not g.is_empty]
        sim = gpd.read_file(path, mask=union_all(parts))
        field = next((f for f in SIM_VALUE_FIELDS if f in sim.columns), None)
        if field is None:
            raise ValueError(f"{path} has none of {list(SIM_VALUE_FIELDS)}; available: "
                             f"{[c for c in sim.columns if c != 'geometry']}")
        sim[_SIM_VALUE] = sim[field]
        sim_fields[is_low] = field
        sims[is_low] = _to_metric(sim)

    all_vals = [v for g in sims.values() for v in g[_SIM_VALUE].dropna()]
    norm = Normalize(vmin=0, vmax=max(all_vals) if all_vals else 1)

    classes = classify(counted, sims, counted_low, depth_field, threshold)
    s = summarize(classes)
    print(f"Counted: {count_src}")
    print("TP={TP} FN={FN} FP={FP} TN={TN}  Accuracy={accuracy:.2f}%  "
          "Recall={recall:.2f}%".format(**s)
          + (f"  ({s['unclassified']} without depth)" if s["unclassified"] else ""))
    summary_txt = ("Confusion matrix (n={n})\n"
                   "TP {TP:>3}   FN {FN:>3}\n"
                   "FP {FP:>3}   TN {TN:>3}\n"
                   "Acc {accuracy:.1f}%  Recall {recall:.1f}%").format(**s)

    # Per plotted sensor: its counted buffer feature(s) and their class
    buffers, sensor_class = {}, {}
    for idx, row in pts.iterrows():
        own = counted[counted[id_field] == row[id_field]]
        geoms = [g for g in own.geometry if g is not None and not g.is_empty]
        buffers[idx] = union_all(geoms) if geoms else row.geometry.buffer(buffer_fallback)
        own_cls = [classes[i] for i in own.index if classes.get(i)]
        sensor_class[idx] = own_cls[0] if own_cls else ("Unclassified" if len(own) else None)
        if len(set(own_cls)) > 1:
            print(f"Warning: [{row[id_field]}] has {len(own)} buffer features with "
                  f"classes {own_cls}; the badge shows {own_cls[0]}")

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

            cls = sensor_class.get(idx)
            badge = (f"{cls}  {CLASS_NAMES[cls]}" if cls in CLASS_NAMES
                     else cls or "Not in buffer layer (not counted)")
            ax.text(0.015, 0.985, badge,
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

    return {**s, "classes": {counted.loc[i, id_field]: c for i, c in classes.items()},
            "pngs": pngs}


def main():
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Plot every IoT sensor of an evaluate_sensor2 (eval_iot) run as a map "
                    "(sensor, buffer, simulated flood cells, OpenStreetMap basemap) with "
                    "its TP / FN / FP / TN class. One PNG per sensor.",
        epilog="""
examples:
  %(prog)s                                 (SHP/SIM_thrd125 + SIM_thrd475, SHP/IOT_SENSOR.shp,
                                            SHP/IOT_BUFFER.geojson -> PLOTS/IOT_<id>.png)
  %(prog)s --ids U0197 U0273               (only these sensors)
  %(prog)s -of PLOTS --dpi 200
  %(prog)s --id-field SENSOR_ID
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp
  %(prog)s --obs SHP/IOT_SENSOR.gpkg       (GeoPackage sensors; buffers from GPKG/IOT_BUFFER.geojson,
                                            where eval_iot --obs *.gpkg writes them)
  %(prog)s --obs sensors.gpkg --obs-layer IOT_SENSOR
  %(prog)s --obs SHP/IOT_SENSOR.geojson    (GeoJSON sensors, EPSG:4326 -> EPSG:3826)
  %(prog)s --buffer-file SHP/B50.geojson   (count TP/FN/FP/TN on another buffer layer, e.g. from
                                            eval_iot --buffer 50 --output-buffer SHP/B50.geojson)
  %(prog)s --buffer-file SHP/IOT_BUFFER.gpkg (buffers of eval_iot < 0.38.1, GeoPackage)
  %(prog)s --buffer-file SHP/MY_BUFFER.shp (buffers as a shapefile)
  %(prog)s --buffer-file layers.gpkg --buffer-layer IOT_BUFFER
                                            (one layer of a multi-layer GeoPackage)
  %(prog)s --buffer-file "" --buffer 30    (no buffer file: count 30 m circles around the sensors)
  %(prog)s --thresh-iot 10                 (same threshold as eval_iot --thresh-iot 10)
  %(prog)s --no-basemap                    (offline, no OpenStreetMap tiles)
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
                        help="Sensor point shapefile, GeoPackage (*.gpkg) or GeoJSON (*.geojson) "
                             "(default: %(default)s)")
    parser.add_argument("--obs-layer", default=None, metavar="LAYER",
                        help="Layer of a multi-layer --obs GeoPackage (default: the first)")
    parser.add_argument("--buffer-file", default="auto",
                        help="Buffered sensor areas written by eval_iot --output-buffer "
                             f"(default: {BUFFER_FILE}, or {BUFFER_FILE_GPKG} when --obs is "
                             "a GeoPackage, as eval_iot writes it there). "
                             "Use '' to draw --buffer circles instead.")
    parser.add_argument("--buffer-layer", default=None, metavar="LAYER",
                        help="Layer of a multi-layer --buffer-file GeoPackage (default: the "
                             "first). TP / FN / FP / TN are counted on this layer.")
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
                     args.zoom, not args.no_basemap, args.tile_cache,
                     args.obs_layer, args.buffer_layer)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
