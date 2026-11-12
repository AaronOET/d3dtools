"""
Module for calculating flood simulation accuracy and recall by comparing
simulated flood extents with observed sensor data.
"""

import os
import sys
import argparse
import geopandas as gpd
import pandas as pd
import pyogrio
import shapely


SOURCE_FIELD_CANDIDATES = ("來源說", "通報類型")

DEPTH_FIELD_PREFIX = "最大深"

# Default file for the buffered sensor areas (command line --output-buffer)
BUFFER_OUTPUT = os.path.join("SHP", "IOT_BUFFER.geojson")

OBS_SOURCE_EPSG = 4326
OBS_TARGET_EPSG = 3826

# Mesh-based buffer: radius = factor x edge length of the mesh cell that holds
# the sensor point (mesh cells taken from SHP/SIM_ALL.shp)
MESH_PATH = os.path.join("SHP", "SIM_ALL.shp")
MESH_FACTOR = {"淹水感測": 4, "EMIC": 6}


def _cell_edge_length(geom):
    """
    Edge length of a mesh cell polygon used to size the buffer.

    - Triangular cell: the LONGEST edge.
    - Other cells (e.g. squares): the mean edge length, which for a square is
      exactly its side length.

    Collinear vertices (hanging nodes where a coarse cell meets finer ones)
    are removed first, so they do not change the vertex count or edge lengths.
    """
    pts = _corner_vertices(geom)
    n = len(pts)
    if n < 3:
        return float("nan")
    edges = [
        ((pts[i][0] - pts[(i + 1) % n][0]) ** 2
         + (pts[i][1] - pts[(i + 1) % n][1]) ** 2) ** 0.5
        for i in range(n)
    ]
    if n == 3:
        return max(edges)
    return sum(edges) / n


def _corner_vertices(geom, tol=1e-6):
    """
    Corner vertices of a polygon's exterior ring: repeated and collinear
    vertices (hanging nodes) removed. ``tol`` is a distance in CRS units.
    """
    pts = list(geom.exterior.coords)[:-1]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        n = len(pts)
        for i in range(n):
            (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[(i + 1) % n]
            base = ((x2 - x0) ** 2 + (y2 - y0) ** 2) ** 0.5
            cross = abs((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0))
            # distance of the middle vertex from the line through its neighbours
            if base == 0 or cross / base <= tol:
                del pts[i]
                changed = True
                break
    return pts


def _has_spatial_index(path):
    """True for a GeoPackage (built-in R-tree) or a shapefile with a .qix index."""
    stem, ext = os.path.splitext(path)
    ext = ext.lower()
    return ext == ".gpkg" or (ext == ".shp" and (
        os.path.exists(stem + ".qix") or os.path.exists(stem + ".QIX")))


def _fast_mesh_path(mesh_path):
    """
    Use an indexed GeoPackage copy of a shapefile mesh (same name, .gpkg) when
    it exists and is newer than the shapefile: a plain shapefile has no
    spatial index, so every read scans the whole file.
    """
    stem, ext = os.path.splitext(mesh_path)
    gpkg = stem + ".gpkg"
    if (ext.lower() == ".shp" and os.path.exists(gpkg)
            and os.path.getmtime(gpkg) >= os.path.getmtime(mesh_path)):
        print(f"Using indexed mesh copy {gpkg}")
        return gpkg
    return mesh_path


def mesh_edge_length(points, mesh_path):
    """
    Edge length (m) of the mesh cell in which each point lies.

    Triangular cells use their longest edge, other cells their mean edge.
    Only the cells around the points are read from ``mesh_path``. A point on
    a shared edge/corner touches several cells; the smallest edge length is
    used. A point outside the mesh gets the nearest cell, with a warning.
    """
    # CRS from the file header only (gpd.read_file(..., rows=0) would read
    # every feature of a shapefile)
    mesh_path = _fast_mesh_path(mesh_path)
    mesh_crs = pyogrio.read_info(mesh_path)["crs"]
    pts = points[["geometry"]].copy()
    if mesh_crs and pts.crs is not None and pts.crs != mesh_crs:
        pts = pts.to_crs(mesh_crs)

    # 1) cells that contain / touch the points
    # (union of small buffers, so MultiPoint sensors, e.g. from a GeoPackage, work too)
    if _has_spatial_index(mesh_path):
        # indexed file: one small window per sensor (a single mask spanning
        # all sensors would pull in most of the mesh through its bounding box)
        parts = [gpd.read_file(mesh_path, mask=g.buffer(1.0))
                 for g in pts.geometry if g is not None and not g.is_empty]
        parts = [m for m in parts if len(m)]
        mesh = (pd.concat(parts, ignore_index=True) if parts
                else gpd.GeoDataFrame(geometry=[], crs=mesh_crs))
    else:
        # plain shapefile: one pass over the whole file
        mask = shapely.union_all(pts.geometry.buffer(1.0).values)
        mesh = gpd.read_file(mesh_path, mask=mask)
    edge = pd.Series(float("nan"), index=pts.index)
    if len(mesh):
        mesh["mesh_edge"] = [_cell_edge_length(g) for g in mesh.geometry]
        hit = gpd.sjoin(pts, mesh[["mesh_edge", "geometry"]], predicate="intersects")
        edge.update(hit.groupby(level=0)["mesh_edge"].min())

    # 2) points outside the mesh: nearest cell within a growing search window
    missing = edge.index[edge.isna()]
    if len(missing):
        print(f"Warning: {len(missing)} sensor(s) outside the mesh {mesh_path}; "
              "using the nearest mesh cell.")
        for idx in missing:
            pt = pts.geometry.loc[idx]
            for dist in (50, 200, 1000, 5000):
                near = gpd.read_file(mesh_path, mask=pt.buffer(dist))
                if len(near):
                    nearest = near.geometry.distance(pt).idxmin()
                    edge.loc[idx] = _cell_edge_length(near.geometry.loc[nearest])
                    break
    return edge


def _resolve_radius(source_value, buffer_radius):
    """Return the buffer radius for a given sensor source value."""
    if isinstance(buffer_radius, dict):
        return float(buffer_radius.get(source_value, 0))
    return float(buffer_radius)


def _resolve_source_field(columns):
    """Return the first matching source-field name found in columns, or None."""
    for name in SOURCE_FIELD_CANDIDATES:
        if name in columns:
            return name
    return None


def _resolve_depth_field(columns):
    """Return the first column whose name starts with 最大深, or None."""
    for name in columns:
        if str(name)[:len(DEPTH_FIELD_PREFIX)] == DEPTH_FIELD_PREFIX:
            return name
    return None


def numeric_depth(values, label="depth"):
    """
    Depth values as numbers. GeoJSON (and some other) exports often store them
    as text ("37", "37.0"); text that is not a number becomes NaN, with a warning.
    """
    if pd.api.types.is_numeric_dtype(values):
        return values
    num = pd.to_numeric(values, errors="coerce")
    bad = values.notna() & (values.astype(str).str.strip() != "") & num.isna()
    if bad.any():
        print(f"Warning: {int(bad.sum())} non-numeric {label} value(s) treated as missing: "
              f"{sorted(set(values[bad].astype(str)))[:5]}")
    return num


def _vector_driver(path):
    """OGR driver for an output file, from its extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".gpkg":
        return "GPKG"
    if ext in (".geojson", ".json"):
        return "GeoJSON"
    return None  # shapefile


def confusion_matrix(
    low_threshold_sim_path="SHP/SIM_thrd125.shp",
    high_threshold_sim_path="SHP/SIM_thrd475.shp",
    obs_path="SHP/IOT_SENSOR.shp",
    buffer_radius=20,
    depth_threshold=30,
    output_csv=None,
    output_buffer_area=None,
    mesh_path=MESH_PATH,
    mesh_factor=None,
):
    """
    Calculate the accuracy and recall of a flood simulation using sensor data.

    Parameters
    ----------
    low_threshold_sim_path : str
        Path to the simulated flood extent shapefile with low threshold
    high_threshold_sim_path : str
        Path to the simulated flood extent shapefile with high threshold
    obs_path : str
        Path to the observed sensor point file: shapefile, GeoPackage
        (``*.gpkg``) or GeoJSON (``*.geojson`` / ``*.json``). If its CRS is
        EPSG:4326 (always the case for GeoJSON), it is reprojected to
        EPSG:3826 before use. Depth values stored as text are converted to
        numbers.
    buffer_radius : float or dict, optional
        Buffer radius around sensor points in meters. Set to 0 to use point
        data directly. Pass a dict mapping source-field values (``來源說``
        or ``通報類型``, whichever is present) to radii to use different
        radii per source, e.g. ``{"EMIC": 30, "淹水感測": 20}``
        (sources missing from the dict get a radius of 0). Default: 20.
        Ignored when ``mesh_path`` is given.
    depth_threshold : float, optional
        Water depth threshold in centimeters (default: 30)
    output_csv : str, optional
        Path to the output CSV file
    output_buffer_area : str, optional
        Path to a shapefile, GeoPackage (``*.gpkg``) or GeoJSON
        (``*.geojson``) where the buffered
        sensor geometries (with the applied radius and computed area) will
        be written. The format is chosen from the file extension. A GeoJSON
        is written in EPSG:4326 (WGS84, as the GeoJSON standard RFC 7946
        requires); ``buf_r`` / ``buf_area`` stay in metres / m2. If
        ``obs_path`` is a GeoPackage (``*.gpkg``), the file is written into
        a ``GPKG`` folder (created if missing) instead of its own directory.

    mesh_path : str, optional
        Mesh polygon file (default: ``SHP/SIM_ALL.shp``). When given, each
        sensor's buffer radius is ``mesh_factor[source] x edge length`` of
        the mesh cell the sensor lies in, and ``buffer_radius`` is ignored.
        Set to ``None`` or ``""`` to use ``buffer_radius`` instead. If the
        default ``SHP/SIM_ALL.shp`` does not exist, ``buffer_radius`` is used
        (with a note); another path that does not exist is an error.
    mesh_factor : dict, optional
        Multiplier of the mesh edge length per source-field value
        (``來源說`` or ``通報類型``). Default: ``{"淹水感測": 4, "EMIC": 6}``.
        Sources missing from the dict get a radius of 0 (point geometry).

    Returns
    -------
    dict
        Dictionary containing the accuracy, recall, and confusion matrix values
    """
    # Load the data
    low_sim_gdf = gpd.read_file(low_threshold_sim_path)
    high_sim_gdf = gpd.read_file(high_threshold_sim_path)
    obs_gdf = gpd.read_file(obs_path)

    # Reproject observation data from WGS84 to TWD97 / TM2 zone 121 if needed
    if obs_gdf.crs is not None and obs_gdf.crs.to_epsg() == OBS_SOURCE_EPSG:
        obs_gdf = obs_gdf.to_crs(epsg=OBS_TARGET_EPSG)

    # Create a buffer around observation points (per-source radius supported).
    # Rows whose resolved radius is 0 keep their original point geometry.
    obs_buffer = obs_gdf.copy()
    edges = None
    if mesh_path and not os.path.exists(mesh_path):
        if os.path.normpath(mesh_path) != os.path.normpath(MESH_PATH):
            raise FileNotFoundError(f"Mesh file does not exist: {mesh_path}")
        print(f"Note: no mesh file {mesh_path}; using the fixed buffer radius instead "
              "(--buffer / --buffer-by-source).")
        mesh_path = None
    if mesh_path:
        factors = MESH_FACTOR if mesh_factor is None else mesh_factor
        source_field = _resolve_source_field(obs_buffer.columns)
        if source_field is None:
            raise ValueError(
                "Mesh-based buffer requires one of the fields "
                f"{SOURCE_FIELD_CANDIDATES} in {obs_path}"
            )
        edges = mesh_edge_length(obs_buffer, mesh_path)
        radii = pd.Series(
            [float(factors.get(src, 0)) * e
             for src, e in zip(obs_buffer[source_field], edges)],
            index=obs_buffer.index,
        ).fillna(0.0)
        obs_buffer["geometry"] = [
            geom.buffer(r) if r > 0 else geom
            for geom, r in zip(obs_buffer.geometry, radii)
        ]
        print("Mesh-based buffer radius (factor x mesh edge length): "
              + ", ".join(f"{k}={v}x" for k, v in factors.items()))
    elif isinstance(buffer_radius, dict):
        source_field = _resolve_source_field(obs_buffer.columns)
        if source_field is None:
            raise ValueError(
                "Per-source buffer requires one of the fields "
                f"{SOURCE_FIELD_CANDIDATES} in {obs_path}"
            )
        radii = obs_buffer[source_field].map(
            lambda s: _resolve_radius(s, buffer_radius)
        )
        obs_buffer["geometry"] = [
            geom.buffer(r) if r > 0 else geom
            for geom, r in zip(obs_buffer.geometry, radii)
        ]
    elif buffer_radius > 0:
        obs_buffer["geometry"] = obs_buffer.geometry.buffer(buffer_radius)
        radii = pd.Series([float(buffer_radius)] * len(obs_buffer), index=obs_buffer.index)
    else:
        radii = pd.Series([0.0] * len(obs_buffer), index=obs_buffer.index)

    # Record mesh edge length (if used), applied radius and buffer area
    if edges is not None:
        obs_buffer["mesh_edge"] = edges.astype(float).values
    obs_buffer["buf_r"] = radii.astype(float).values
    obs_buffer["buf_area"] = obs_buffer.geometry.area

    # Export buffered geometries if requested
    if output_buffer_area:
        # GeoPackage observation input goes to a dedicated GPKG folder
        if os.path.splitext(obs_path)[1].lower() == ".gpkg":
            output_buffer_area = os.path.join(
                "GPKG", os.path.basename(output_buffer_area)
            )
        out_dir = os.path.dirname(output_buffer_area)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        driver = _vector_driver(output_buffer_area)
        if driver == "GeoJSON":
            # GeoJSON is WGS84 by definition (RFC 7946)
            out = obs_buffer.to_crs(epsg=OBS_SOURCE_EPSG) if obs_buffer.crs else obs_buffer
            if os.path.exists(output_buffer_area):
                os.remove(output_buffer_area)  # the GeoJSON driver cannot overwrite
            out.to_file(output_buffer_area, driver=driver)
        elif driver:
            obs_buffer.to_file(output_buffer_area, driver=driver)
        else:
            obs_buffer.to_file(output_buffer_area, encoding="utf-8")
        print(f"Buffer geometries saved to {output_buffer_area}")
        total_area = float(obs_buffer.geometry.area.sum())
        print(f"Total buffer area: {total_area:.2f} (CRS units squared)")

    # Resolve the depth field: the column whose name's first three
    # characters are 最大深 (e.g. 最大深度cm, 最大深度(公分), ...)
    depth_field = _resolve_depth_field(obs_buffer.columns)
    if depth_field is None:
        raise ValueError(
            f"No column starting with '{DEPTH_FIELD_PREFIX}' found in {obs_path}"
        )
    obs_buffer[depth_field] = numeric_depth(obs_buffer[depth_field], depth_field)

    # Calculate True Positive (TP) and False Positive (FP)
    # Sensors with depth >= threshold that intersect with simulated flood
    True_Positive = 0
    False_Negative = 0

    for _, obs in obs_buffer.iterrows():
        if obs[depth_field] >= depth_threshold:
            # For both point and buffered data, intersects works appropriately
            if low_sim_gdf.intersects(obs.geometry).any():
                True_Positive += 1
            else:
                False_Negative += 1

    # Calculate False Positive (FP)
    # Sensors with depth < threshold that intersect with simulated flood
    False_Positive = 0
    True_Negative = 0

    for _, obs in obs_buffer.iterrows():
        if obs[depth_field] < depth_threshold:
            # For both point and buffered data, intersects works appropriately
            if high_sim_gdf.intersects(obs.geometry).any():
                False_Positive += 1
            else:
                True_Negative += 1

    # Calculate accuracy and recall
    accuracy = (True_Positive + True_Negative) / len(obs_buffer) * 100
    recall = (
        True_Positive / (True_Positive + False_Negative) * 100
        if (True_Positive + False_Negative) > 0
        else 0
    )
    # Comment out precision and f1_score as they're not used in the current output
    # precision = True_Positive / \
    #     (True_Positive + False_Positive) * \
    #     100 if (True_Positive + False_Positive) > 0 else 0
    # f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

    # Print the results
    print(f"True Positive: {True_Positive}")
    print(f"True Negative: {True_Negative}")
    print(f"False Positive: {False_Positive}")
    print(f"False Negative: {False_Negative}")
    print(f"Accuracy: {accuracy:.2f}%")
    print(f"Recall: {recall:.2f}%")
    # print(f"Precision: {precision:.2f}%")
    # print(f"F1 Score: {f1_score:.2f}%")

    # Save results to CSV if requested
    if output_csv:
        # Create a DataFrame to store the results
        results_df = pd.DataFrame(
            {
                "Simulation low threshold": [os.path.basename(low_threshold_sim_path)],
                "Simulation high threshold": [os.path.basename(high_threshold_sim_path)],
                "Observation": [os.path.basename(obs_path)],
                "True Positive": [True_Positive],
                "True Negative": [True_Negative],
                "False Positive": [False_Positive],
                "False Negative": [False_Negative],
                "Accuracy (%)": [round(accuracy, 2)],
                "Recall (%)": [round(recall, 2)],
                # 'Precision (%)': [round(precision, 2)],
                # 'F1 Score (%)': [round(f1_score, 2)]
            }
        )

        results_df.to_csv(output_csv, index=False)
        print(f"Results saved to {output_csv}")  # Return the results as a dictionary
    return {
        "accuracy": accuracy,
        "recall": recall,
        "true_positive": True_Positive,
        "true_negative": True_Negative,
        "false_positive": False_Positive,
        "false_negative": False_Negative,
    }


def main():
    """
    Main function for the command line interface.
    """
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Calculate flood simulation accuracy and recall using sensor data.",
        epilog="""
examples:
  %(prog)s              # Use default shapefiles and parameters (input SHP/SIM_thrd125.shp, SHP/SIM_thrd475.shp, SHP/IOT_SENSOR.shp; mesh-based buffer from SHP/SIM_ALL.shp; threshold 30cm)
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp (default arguments)
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp --mesh "" --buffer 50 --thresh-iot 20
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp --mesh "" --buffer 0 --thresh-iot 10 # Point data, 10 cm threshold
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp --output results.csv
  %(prog)s --obs SHP/IOT_SENSOR.gpkg # --obs also accepts a GeoPackage (*.gpkg) file
  %(prog)s --obs SHP/IOT_SENSOR.geojson # ... or a GeoJSON (*.geojson) file (EPSG:4326, reprojected to EPSG:3826)
  %(prog)s --mesh-factor 淹水感測=4 EMIC=6 # Radius = factor x edge length of the SHP/SIM_ALL.shp cell holding the sensor (default)
  %(prog)s --mesh "" --buffer-by-source 淹水感測=20 EMIC=30 # Use different buffer radii per "來源說"/"通報類型" source
  %(prog)s --output-buffer SHP/MY_BUFFER.gpkg # Save buffered sensor geometries to a custom GeoJSON, GeoPackage or shapefile
  %(prog)s --output-buffer "" # Do not save buffered sensor geometries
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sim-low",
        default=os.path.join("SHP", "SIM_thrd125.shp"),
        help="Path to low-threshold simulated flood extent shapefile (default: SHP/SIM_thrd125.shp)",
    )
    parser.add_argument(
        "--sim-high",
        default=os.path.join("SHP", "SIM_thrd475.shp"),
        help="Path to high-threshold simulated flood extent shapefile (default: SHP/SIM_thrd475.shp)",
    )
    parser.add_argument(
        "--obs",
        default=os.path.join("SHP", "IOT_SENSOR.shp"),
        help=(
            "Path to observed sensor point file: shapefile, GeoPackage "
            "(*.gpkg) or GeoJSON (*.geojson) (default: SHP/IOT_SENSOR.shp). If its CRS is "
            "EPSG:4326, it is reprojected to EPSG:3826 automatically."
        ),
    )
    parser.add_argument(
        "--mesh",
        default=MESH_PATH,
        help=(
            "Mesh polygon file used to size the buffers (default: SHP/SIM_ALL.shp, "
            "e.g. from 'fou2shp --all' or 'fou2mfd -t 0 -o SIM_ALL'). "
            "Radius = --mesh-factor x edge length of the mesh cell holding the sensor. "
            "Use '' to disable and use --buffer / --buffer-by-source instead. "
            "If the default file does not exist, --buffer / --buffer-by-source is used. "
            "A shapefile mesh is read through SIM_ALL.gpkg next to it (spatial index, "
            "much faster for a large mesh) when that copy exists and is newer."
        ),
    )
    parser.add_argument(
        "--mesh-factor",
        nargs="+",
        metavar="SOURCE=FACTOR",
        help=(
            "Multiplier of the mesh edge length per '來源說'/'通報類型' source "
            "(default: 淹水感測=4 EMIC=6). Sources not listed get radius 0."
        ),
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=20,
        help="Buffer radius around sensor points in meters. Set to 0 to use point data directly (default: 20). Used only with --mesh ''; ignored when --buffer-by-source is given.",
    )
    parser.add_argument(
        "--buffer-by-source",
        nargs="+",
        metavar="SOURCE=RADIUS",
        help=(
            "Per-source buffer radii using the '來源說' or '通報類型' field "
            "(whichever is present), e.g. "
            "--buffer-by-source EMIC=30 淹水感測=20. Sources not listed get radius 0."
        ),
    )
    parser.add_argument(
        "--thresh-iot",
        type=float,
        default=30,
        help="Water depth threshold in cm (default: 30)",
    )
    parser.add_argument("--output", help="Path to output CSV file (optional)")
    parser.add_argument(
        "--output-buffer",
        default=BUFFER_OUTPUT,
        help=(
            "Path to output shapefile, GeoPackage (*.gpkg) or GeoJSON (*.geojson) of the "
            "buffered sensor geometries (default: SHP/IOT_BUFFER.geojson, "
            "written in EPSG:4326). "
            "If --obs is a GeoPackage, the file is written into a GPKG "
            "folder (created if missing) instead. Use '' to disable."
        ),
    )

    args = parser.parse_args()

    # Check if the input files exist
    if not os.path.exists(args.sim_low):
        print(
            "Error: Low-threshold simulated flood extent file does not exist: "
            f"{args.sim_low}"
        )
        sys.exit(1)
    if not os.path.exists(args.sim_high):
        print(
            "Error: High-threshold simulated flood extent file does not exist: "
            f"{args.sim_high}"
        )
        sys.exit(1)
    if not os.path.exists(args.obs):
        print(f"Error: Observed sensor point file does not exist: {args.obs}")
        sys.exit(1)

    try:
        # Build buffer parameter: dict for per-source radii, else scalar
        if args.buffer_by_source:
            buffer_param = {}
            for item in args.buffer_by_source:
                if "=" not in item:
                    print(
                        f"Error: --buffer-by-source expects SOURCE=RADIUS, got: {item}"
                    )
                    sys.exit(1)
                key, value = item.split("=", 1)
                buffer_param[key.strip()] = float(value)
        else:
            buffer_param = args.buffer

        # Mesh-based radius factors
        mesh_factor = None
        if args.mesh_factor:
            mesh_factor = {}
            for item in args.mesh_factor:
                if "=" not in item:
                    print(f"Error: --mesh-factor expects SOURCE=FACTOR, got: {item}")
                    sys.exit(1)
                key, value = item.split("=", 1)
                mesh_factor[key.strip()] = float(value)
        if (args.mesh and not os.path.exists(args.mesh)
                and os.path.normpath(args.mesh) != os.path.normpath(MESH_PATH)):
            print(f"Error: Mesh file does not exist: {args.mesh}")
            sys.exit(1)

        # Run the confusion matrix calculation (output is already handled in the function)
        confusion_matrix(
            low_threshold_sim_path=args.sim_low,
            high_threshold_sim_path=args.sim_high,
            obs_path=args.obs,
            buffer_radius=buffer_param,
            depth_threshold=args.thresh_iot,
            output_csv=args.output,
            output_buffer_area=args.output_buffer,
            mesh_path=args.mesh or None,
            mesh_factor=mesh_factor,
        )
    except Exception as e:
        print(f"Error processing sensor evaluation: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
