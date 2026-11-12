"""
Merge the shapefiles of SHP0000, SHP0001, ... folders into one SHP folder,
removing duplicated / overlapping polygons (e.g. the ghost cells shared by
D-Flow FM partitions).

Layout expected:
    <root>/SHP0000/xxx.shp
    <root>/SHP0001/xxx.shp
    ...
Shapefiles with the same file name across the SHPNNNN folders are merged and
written to <root>/SHP/xxx.shp (-of for another folder). Each output feature
gets a SRC_PART field with the number of the folder it came from.

Duplicates are removed in two steps:
  1. identical polygons (same geometry after normalising and rounding the
     coordinates to --decimals) keep only the first one;
  2. a polygon that overlaps an already-kept polygon of another folder by more
     than --overlap of the smaller polygon's area is dropped (polygons of the
     same folder are never compared: the cells of one mesh do not overlap).
Folders are taken in number order, so the lower-numbered folder wins.

Based on prototype/mergeshp/merge_shp.py.
"""

import argparse
import os
import re
import sys
from collections import defaultdict

import numpy as np

SCRIPT = "mrgshp"
OUT_DIR = "SHP"
PART_PATTERN = re.compile(r"^SHP(\d{4})$", re.IGNORECASE)
DEFAULT_OVERLAP = 0.5
DEFAULT_DECIMALS = 3
PART_FIELD = "SRC_PART"


def find_inputs(root="."):
    """
    Return (part_dirs, groups): the SHPNNNN folders in `root` (sorted by number)
    and {layer_name: [(part_no, shp_path), ...]}.
    """
    part_dirs = []
    for name in os.listdir(root):
        m = PART_PATTERN.match(name)
        path = os.path.join(root, name)
        if m and os.path.isdir(path):
            part_dirs.append((int(m.group(1)), path))
    part_dirs.sort()
    groups = defaultdict(list)
    for part, d in part_dirs:
        for f in sorted(os.listdir(d)):
            stem, ext = os.path.splitext(f)
            if ext.lower() == ".shp":
                groups[stem].append((part, os.path.join(d, f)))
    return [d for _, d in part_dirs], dict(groups)


def _polygonal(geoms):
    """make_valid the invalid geometries, keeping only their polygon parts."""
    import shapely
    from shapely.geometry import MultiPolygon

    geoms = np.array(geoms, dtype=object)
    bad = np.flatnonzero(~shapely.is_valid(geoms))
    if bad.size:
        fixed = shapely.make_valid(geoms[bad])
        for k, g in zip(bad, fixed):
            if g.geom_type == "GeometryCollection":
                polys = [p for p in shapely.get_parts(g)
                         if p.geom_type in ("Polygon", "MultiPolygon")]
                parts = []
                for p in polys:
                    parts.extend(shapely.get_parts(p))
                g = (parts[0] if len(parts) == 1
                     else MultiPolygon(parts) if parts else shapely.Polygon())
            geoms[k] = g
    return geoms, bad.size


def drop_identical(gdf, decimals=DEFAULT_DECIMALS):
    """Remove polygons whose geometry is identical (after normalising and rounding)."""
    import shapely

    geom = shapely.normalize(shapely.set_precision(gdf.geometry.values, 10.0 ** -decimals))
    key = shapely.to_wkb(geom)
    _, first = np.unique(key, return_index=True)
    keep = np.zeros(len(gdf), dtype=bool)
    keep[first] = True
    return gdf[keep]


def drop_overlapping(gdf, threshold=DEFAULT_OVERLAP, part_field=PART_FIELD):
    """
    Greedy removal: going through the polygons in order, drop each one whose
    interior overlaps an already-kept polygon of another part by more than
    `threshold` of the smaller polygon's area.
    """
    import shapely

    geoms = gdf.geometry.values
    n = len(geoms)
    if n == 0:
        return gdf
    left, right = gdf.sindex.query(geoms)              # bounding boxes intersect
    m = left < right
    if part_field in gdf.columns:
        part = gdf[part_field].to_numpy()
        m &= part[left] != part[right]
    left, right = left[m], right[m]
    if left.size == 0:
        return gdf
    # interiors intersect (shared edges / vertices are fine)
    inter = shapely.relate_pattern(geoms[left], geoms[right], "T********")
    left, right = left[inter], right[inter]
    if left.size == 0:
        return gdf
    a = shapely.area(shapely.intersection(geoms[left], geoms[right]))
    smaller = np.minimum(shapely.area(geoms[left]), shapely.area(geoms[right]))
    ratio = np.divide(a, smaller, out=np.ones_like(a), where=smaller > 0)
    sel = ratio > threshold
    left, right = left[sel], right[sel]

    dropped = np.zeros(n, dtype=bool)
    order = np.lexsort((left, right))                   # by the later polygon
    for i, j in zip(left[order], right[order]):
        if not dropped[i]:                              # i is kept -> j overlaps it
            dropped[j] = True
    return gdf[~dropped]


def merge_layer(name, items, out_dir=OUT_DIR, overlap=DEFAULT_OVERLAP,
                decimals=DEFAULT_DECIMALS):
    """
    Merge the shapefiles `items` ([(part_no, path), ...]) of layer `name` into
    <out_dir>/<name>.shp. Return the output path.
    """
    import geopandas as gpd
    import pandas as pd
    import pyogrio

    frames, crs, empty = [], None, None
    for part, shp in items:
        g = gpd.read_file(shp)
        label = f"{os.path.basename(os.path.dirname(shp))}/{os.path.basename(shp)}"
        print(f"    {label}: {len(g):,} features")
        if crs is None and g.crs is not None:
            crs = g.crs
        if g.empty:
            if empty is None:
                empty = (g, shp)
            continue
        if g.crs is not None and crs is not None and g.crs != crs:
            print(f"    {label}: CRS differs, reprojecting to the first layer's CRS")
            g = g.to_crs(crs)
        g[PART_FIELD] = part
        frames.append(g)

    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{name}.shp")
    if not frames:
        # every input is empty: write an empty layer with the same fields
        g, shp = empty
        g[PART_FIELD] = pd.Series(dtype="int64")
        gtype = pyogrio.read_info(shp).get("geometry_type") or "Polygon"
        g.to_file(out, encoding="utf-8", geometry_type=gtype)
        print(f"    all inputs empty -> empty layer {out}")
        return out

    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=crs)
    n0 = len(gdf)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    geoms, n_fixed = _polygonal(gdf.geometry.values)
    gdf = gdf.set_geometry(gpd.GeoSeries(geoms, index=gdf.index, crs=crs))
    gdf = gdf[~gdf.geometry.is_empty]
    if n_fixed:
        print(f"    repaired {n_fixed:,} invalid geometries")
    n_null = n0 - len(gdf)
    gdf = drop_identical(gdf, decimals).reset_index(drop=True)
    n1 = len(gdf)
    gdf = drop_overlapping(gdf, overlap).reset_index(drop=True)
    n2 = len(gdf)
    print(f"    merged {n0:,} -> "
          + (f"empty removed {n_null:,}, " if n_null else "")
          + f"identical removed {n0 - n_null - n1:,}, "
          f"overlapping removed {n1 - n2:,} -> {n2:,} features")

    gdf.to_file(out, encoding="utf-8")
    print(f"    saved {out}")
    return out


def mrgshp(root=".", out_dir=None, overlap=DEFAULT_OVERLAP, decimals=DEFAULT_DECIMALS,
           layers=None):
    """
    Merge the shapefiles of the SHPNNNN folders in `root` into `out_dir`
    (default <root>/SHP), layer by layer (same file name). `layers` limits it to
    those layer names. Return the output paths.
    """
    if overlap < 0 or overlap > 1:
        raise ValueError("overlap must be between 0 and 1")
    part_dirs, groups = find_inputs(root)
    if not part_dirs:
        raise FileNotFoundError(f"no SHPNNNN folders found in {os.path.abspath(root)}")
    print(f"Found {len(part_dirs)} folders: "
          f"{', '.join(os.path.basename(d) for d in part_dirs)}")
    if layers:
        missing = [n for n in layers if n not in groups]
        for n in missing:
            print(f"Warning: layer {n} not found in any folder")
        groups = {n: groups[n] for n in layers if n in groups}
    if not groups:
        raise FileNotFoundError("no .shp files found in those folders")

    out_dir = os.path.join(root, OUT_DIR) if out_dir is None else out_dir
    written = []
    for name, items in groups.items():
        print(f"\n[{name}] from {len(items)} folder(s)")
        written.append(merge_layer(name, items, out_dir, overlap, decimals))
    return written


def main():
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Merge the shapefiles of SHP0000, SHP0001, ... folders (e.g. one per "
                    "D-Flow FM partition) into one SHP folder, removing duplicated and "
                    "overlapping polygons (ghost cells).",
        epilog="""
examples:
  %(prog)s                          (SHPNNNN folders here -> SHP/)
  %(prog)s D:/run/output            (D:/run/output/SHPNNNN -> D:/run/output/SHP/)
  %(prog)s -of SHP_MERGED
  %(prog)s --layers SIM_thrd125 SIM_thrd475
  %(prog)s --overlap 0              (drop on any interior overlap)
  %(prog)s --decimals 2             (identical to 1 cm)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("root", nargs="?", default=".",
                        help="Folder holding the SHPNNNN folders (default: current folder)")
    parser.add_argument("-of", "--output-folder", "--out", dest="out_dir", default=None,
                        metavar="DIR",
                        help=f"Output folder (default: <root>/{OUT_DIR}; a relative path "
                             "is taken from the current folder)")
    parser.add_argument("--layers", nargs="+", metavar="NAME",
                        help="Only merge these layers (shapefile names without .shp)")
    parser.add_argument("--overlap", type=float, default=DEFAULT_OVERLAP, metavar="F",
                        help="Drop a polygon that overlaps an already-kept polygon of another "
                             "folder by more than this fraction of the smaller polygon's area "
                             "(0-1, default: %(default)s; 0 = any interior overlap)")
    parser.add_argument("--decimals", type=int, default=DEFAULT_DECIMALS, metavar="N",
                        help="Coordinate rounding used to detect identical polygons "
                             "(default: %(default)s = 1 mm for projected coordinates)")
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print(f"Error: folder does not exist: {args.root}")
        sys.exit(1)
    if not 0 <= args.overlap <= 1:
        parser.error("--overlap must be between 0 and 1")
    try:
        mrgshp(args.root, args.out_dir, args.overlap, args.decimals, args.layers)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    print("\nDone.")


if __name__ == "__main__":
    main()
