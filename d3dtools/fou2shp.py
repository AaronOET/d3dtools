"""
Reconstruct FlowFM 2D mesh faces as polygons in a shapefile.

Attribute: Mesh2d_fourier002_max_depth (stored as "fou_dep" — DBF field
           names are limited to 10 characters)

Input : FlowFM_fou.nc  (UGRID 2D mesh, EPSG:3826 TWD97/TM2-121)
Output: SIM_thrd125.shp, SIM_thrd300.shp, SIM_thrd475.shp  (+.dbf, .shx, .prj)
        with the faces whose depth is >= 0.125 / 0.3 / 0.475 m
        SIM_ALL.shp (optional, --all): every mesh face with its max depth
"""

import glob as _glob
import os
import sys
import numpy as np

import argparse

try:
    import netCDF4 as nc
except ImportError:
    sys.exit("Missing package: pip install netCDF4")
try:
    import shapefile
except ImportError:
    sys.exit("Missing package: pip install pyshp")

try:
    import geopandas as gpd
    import pandas as pd
except ImportError:
    gpd = None
    pd = None

NC_FILE  = os.path.join("NC", "FlowFM_fou.nc")
_SHP_DIR = "SHP"
VAR_NAME   = "Mesh2d_fourier002_max_depth"
FIELD_NAME = "fou_dep"   # 10-char DBF limit; full name stored in long_name attr above

# Threshold outputs: (threshold_value, output_stem)
THRESHOLDS = [
    (0.125, "SIM_thrd125"),
    (0.300, "SIM_thrd300"),
    (0.475, "SIM_thrd475"),
]

# Output stem for the whole mesh (written with --all)
ALL_STEM = "SIM_ALL"

# WKT for EPSG:3826  (TWD97 / TM2 zone 121)
PRJ_WKT = (
    'PROJCS["TWD97 / TM2 zone 121",'
    'GEOGCS["TWD97",'
    'DATUM["Taiwan_Datum_1997",'
    'SPHEROID["GRS 1980",6378137,298.257222101,'
    'AUTHORITY["EPSG","7019"]],'
    'AUTHORITY["EPSG","1026"]],'
    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AUTHORITY["EPSG","3824"]],'
    'PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],'
    'PARAMETER["central_meridian",121],'
    'PARAMETER["scale_factor",0.9999],'
    'PARAMETER["false_easting",250000],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AUTHORITY["EPSG","3826"]]'
)


def _to_file_polygon(gdf, path, encoding="utf-8"):
    """Save a GeoDataFrame as a shapefile, forcing Polygon type when empty."""
    kwargs = {"encoding": encoding}
    if gdf.empty:
        kwargs["geometry_type"] = "Polygon"
    gdf.to_file(path, **kwargs)


def _fmt_dbf_values(vals, masked):
    """DBF 'N' field text, identical to pyshp: format(v, '.6f')[:18].rjust(18)."""
    out = [format(v, ".6f")[:18].rjust(18) for v in vals.tolist()]
    if masked is not None and masked.any():
        null = "*" * 18
        for i in np.flatnonzero(masked).tolist():
            out[i] = null
    return out


def _compact_face_nodes(fn, fn_fill):
    """Return *fn* with each face's valid nodes moved to the front, in order.

    Standard UGRID files already store them that way (then *fn* is returned
    unchanged); a face with a fill value between valid nodes is compacted the
    same way the old per-face writer did (row[row != fill]).
    """
    valid = fn != fn_fill
    nvert = valid.sum(axis=1)
    first_fill = np.argmin(np.c_[valid, np.zeros(fn.shape[0], bool)], axis=1)
    if np.array_equal(nvert, first_fill):
        return fn
    order = np.argsort(~valid, axis=1, kind="stable")
    return np.take_along_axis(fn, order, axis=1)


def write_threshold_shp(shp_out, threshold, fn, fn_fill, node_x, node_y, values,
                        chunk=200_000, exclude=None):
    """Write faces whose depth value is >= *threshold* to *shp_out*.

    If *threshold* is None, every face of the mesh is written; faces with a
    masked (missing) value get a NULL attribute.

    Vectorised writer: builds the .shp/.shx/.dbf bytes with numpy instead of
    looping over faces in Python. Output is byte-identical to the pyshp
    version (apart from the date stamp in the DBF header).
    """
    import struct, time

    # Plain ndarrays: element access on MaskedArray is very slow
    nx = np.ma.getdata(node_x).astype("<f8", copy=False)
    ny = np.ma.getdata(node_y).astype("<f8", copy=False)
    vdata = np.ma.getdata(values).astype(np.float64, copy=False)
    vmask = np.ma.getmaskarray(values)

    fn = _compact_face_nodes(fn, fn_fill)
    valid = fn != fn_fill
    nvert = valid.sum(axis=1)
    keep = nvert >= 3
    if threshold is not None:
        keep &= ~vmask & ~(vdata < threshold)
    n_removed = 0
    if exclude is not None:
        n_removed = int((keep & exclude).sum())
        keep &= ~exclude
    sel = np.flatnonzero(keep)
    n_written = sel.size
    n_skipped = fn.shape[0] - n_written - n_removed

    npts_all = nvert[sel] + 1                                   # closed ring
    content_len = 44 + 4 + 16 * npts_all                        # bytes, excl. 8-byte rec header
    rec_len = 8 + content_len

    shp = open(shp_out + ".shp", "wb")
    shx = open(shp_out + ".shx", "wb")
    shp.write(b"\0" * 100)
    shx.write(b"\0" * 100)

    gxmin = gymin = np.inf
    gxmax = gymax = -np.inf
    offset = 100
    for c0 in range(0, n_written, chunk):
        s = sel[c0:c0 + chunk]
        npts = npts_all[c0:c0 + chunk]
        rl = rec_len[c0:c0 + chunk]
        starts = np.zeros(s.size, np.int64)
        np.cumsum(rl[:-1], out=starts[1:])
        buf = np.zeros(int(rl.sum()), np.uint8)
        recno = np.arange(c0 + 1, c0 + 1 + s.size)

        for k in np.unique(nvert[s]):
            g = np.flatnonzero(nvert[s] == k)
            idx = fn[s[g], :k].astype(np.int64) - 1
            idx = np.concatenate([idx, idx[:, :1]], axis=1)      # close ring
            px, py = nx[idx], ny[idx]
            dt = np.dtype([("rn", ">i4"), ("cl", ">i4"), ("st", "<i4"),
                           ("bb", "<f8", 4), ("np", "<i4"), ("nv", "<i4"),
                           ("pt", "<i4"), ("xy", "<f8", (k + 1, 2))])
            rec = np.zeros(g.size, dt)
            rec["rn"] = recno[g]
            rec["cl"] = (44 + 4 + 16 * (k + 1)) // 2
            rec["st"] = 5
            rec["bb"] = np.c_[px.min(1), py.min(1), px.max(1), py.max(1)]
            rec["np"] = 1
            rec["nv"] = k + 1
            rec["pt"] = 0
            rec["xy"][..., 0] = px
            rec["xy"][..., 1] = py
            raw = rec.view(np.uint8).reshape(g.size, dt.itemsize)
            buf[(starts[g][:, None] + np.arange(dt.itemsize)).ravel()] = raw.ravel()
            gxmin = min(gxmin, px.min()); gymin = min(gymin, py.min())
            gxmax = max(gxmax, px.max()); gymax = max(gymax, py.max())

        shp.write(buf.tobytes())
        ix = np.empty(s.size, [("o", ">i4"), ("l", ">i4")])
        ix["o"] = (offset + starts) // 2
        ix["l"] = (rl - 8) // 2
        shx.write(ix.tobytes())
        offset += buf.size

    bbox = (gxmin, gymin, gxmax, gymax) if n_written else (0.0, 0.0, 0.0, 0.0)
    for f, length in ((shp, offset), (shx, 100 + 8 * n_written)):
        f.seek(0)
        f.write(struct.pack(">6iI", 9994, 0, 0, 0, 0, 0, length // 2))
        f.write(struct.pack("<2i4d4d", 1000, 5, *bbox, 0, 0, 0, 0))
        f.close()

    # DBF: one numeric field, size 18, 6 decimals
    year, month, day = time.localtime()[:3]
    with open(shp_out + ".dbf", "wb") as f:
        f.write(struct.pack("<BBBBLHH20x", 3, year - 1900, month, day,
                            n_written, 65, 19))
        f.write(struct.pack("<11sc4xBB14x", FIELD_NAME.encode("ascii"), b"N", 18, 6))
        f.write(b"\r")
        txt = _fmt_dbf_values(vdata[sel], vmask[sel] if threshold is None else None)
        f.write((" " + " ".join(txt)).encode("ascii") if txt else b"")

    with open(shp_out + ".prj", "w") as f:
        f.write(PRJ_WKT)

    if exclude is None:
        print(f"  {shp_out}.shp  — {n_written} polygons written, {n_skipped} skipped")
    else:
        print(f"  {shp_out}.shp  — {n_removed} removed, {n_written} remaining")
    return n_written


def faces_intersecting_masks(mask_paths, fn, fn_fill, node_x, node_y):
    """Boolean array: True for mesh faces that intersect any mask polygon.

    Builds the face polygons once in memory, indexes them with an STRtree and
    queries it with each mask part (same 'intersects' predicate as before).
    """
    import shapely

    nx = np.ma.getdata(node_x).astype(np.float64, copy=False)
    ny = np.ma.getdata(node_y).astype(np.float64, copy=False)
    fn = _compact_face_nodes(fn, fn_fill)
    nvert = (fn != fn_fill).sum(axis=1)

    faces = np.empty(fn.shape[0], dtype=object)
    for k in np.unique(nvert):
        if k < 3:
            continue
        g = np.flatnonzero(nvert == k)
        idx = fn[g, :k].astype(np.int64) - 1
        idx = np.concatenate([idx, idx[:, :1]], axis=1)
        faces[g] = shapely.polygons(np.stack([nx[idx], ny[idx]], axis=-1))
    ok = np.flatnonzero(nvert >= 3)

    parts = []
    for p in mask_paths:
        m = gpd.read_file(p)
        if m.crs is not None and m.crs.to_epsg() != 3826:
            m = m.to_crs(3826)
        geoms = m.geometry.values
        geoms = geoms[~(shapely.is_missing(geoms) | shapely.is_empty(geoms))]
        parts.append(shapely.get_parts(geoms))
    parts = np.concatenate(parts) if parts else np.empty(0, dtype=object)
    shapely.prepare(parts)

    tree = shapely.STRtree(faces[ok])
    _, hit_local = tree.query(parts, predicate="intersects")
    hit = np.zeros(fn.shape[0], dtype=bool)
    hit[ok[np.unique(hit_local)]] = True
    return hit


def main():
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Reconstruct FlowFM 2D mesh faces as polygons in threshold shapefiles.",
        epilog="""
examples:
  %(prog)s                                  (NC/FlowFM_fou.nc -> SHP)
  %(prog)s NC/FlowFM_fou.nc
  %(prog)s NC/FlowFM_fou.nc -of SHP
  %(prog)s NC/FlowFM_fou.nc --all           (also writes SHP/SIM_ALL.shp)
  %(prog)s NC/FlowFM_fou.nc --var Mesh2d_fourier002_max_depth --output-folder output
  %(prog)s NC/FlowFM_fou.nc -r SHP/EXCLUDE.shp
  %(prog)s NC/FlowFM_fou.nc -r SHP/*.shp
  %(prog)s NC/FlowFM_fou.nc --remove SHP/ROAD.shp SHP/BUILDING.shp
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=f"Path to the input NetCDF file (default: {NC_FILE})",
    )
    parser.add_argument(
        "-i", "--input",
        default=None,
        help="Same as the positional path (kept for backwards compatibility)",
    )
    parser.add_argument(
        "-of", "--output-folder",
        dest="out_dir",
        default=_SHP_DIR,
        metavar="DIR",
        help="Output directory for shapefiles (default: SHP)",
    )
    parser.add_argument(
        "--var",
        default=VAR_NAME,
        help=f"Variable name in the NetCDF file (default: {VAR_NAME})",
    )
    parser.add_argument(
        "-a", "--all",
        dest="write_all",
        action="store_true",
        help=f"Also write the entire mesh with its max depth values as {ALL_STEM}.shp",
    )
    parser.add_argument(
        "-r", "--remove",
        nargs="+",
        metavar="MASK.shp",
        help=(
            "Remove output polygons that intersect these mask shapefiles. "
            "Filtered copies are written to <output-folder>_RM. "
            "Glob patterns are supported (e.g. --remove SHP/*.shp)."
        ),
    )

    args = parser.parse_args()
    if args.path and args.input and args.path != args.input:
        parser.error("give the input file either positionally or with -i, not both")
    args.input = args.input or args.path or NC_FILE

    if not os.path.exists(args.input):
        print(f"Error: Input NetCDF file does not exist: {args.input}")
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)

    try:
        ds = nc.Dataset(args.input, "r")

        node_x = ds.variables["Mesh2d_node_x"][:]
        node_y = ds.variables["Mesh2d_node_y"][:]

        fn_var  = ds.variables["Mesh2d_face_nodes"]
        fn      = np.array(fn_var[:], dtype=np.int32)
        fn_fill = int(fn_var._FillValue)

        attr_var = ds.variables[args.var]
        values   = attr_var[:]

        ds.close()

        print(f"Input  : {args.input}")
        print(f"Field  : '{FIELD_NAME}'  <-  {args.var}")
        print(f"CRS    : EPSG:3826  (TWD97 / TM2 zone 121)")
        print()

        for threshold, stem in THRESHOLDS:
            shp_out = os.path.join(args.out_dir, stem)
            print(f"Threshold >= {threshold:.3f}m  ->  {stem}")
            write_threshold_shp(shp_out, threshold, fn, fn_fill, node_x, node_y, values)

        out_stems = [stem for _, stem in THRESHOLDS]
        if args.write_all:
            shp_out = os.path.join(args.out_dir, ALL_STEM)
            print(f"Entire mesh  ->  {ALL_STEM}")
            write_threshold_shp(shp_out, None, fn, fn_fill, node_x, node_y, values)
            out_stems.append(ALL_STEM)

        if args.remove:
            if gpd is None:
                print("Error: --remove requires geopandas: pip install geopandas")
                sys.exit(1)

            remove_mask_paths = []
            for pattern in args.remove:
                matched = _glob.glob(pattern)
                if not matched:
                    print(f"Warning: --remove pattern matched no files: {pattern}")
                remove_mask_paths.extend(matched)

            if remove_mask_paths:
                rm_out_dir = args.out_dir.rstrip("/\\") + "_RM"
                os.makedirs(rm_out_dir, exist_ok=True)

                print(f"\nApplying mask removal  ->  {rm_out_dir}/")
                hit = faces_intersecting_masks(remove_mask_paths, fn, fn_fill, node_x, node_y)
                for threshold, stem in [t for t in THRESHOLDS] + (
                        [(None, ALL_STEM)] if args.write_all else []):
                    dst = os.path.join(rm_out_dir, stem)
                    write_threshold_shp(dst, threshold, fn, fn_fill, node_x, node_y,
                                        values, exclude=hit)

    except KeyError as e:
        print(f"Error: Variable not found in NetCDF file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error processing NetCDF file: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
