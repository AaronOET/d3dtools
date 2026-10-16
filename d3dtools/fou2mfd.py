"""
Write the flooded FlowFM 2D mesh faces for ONE depth threshold to a shapefile.

Like fou2shp, but instead of the three layers threshold - 0.175, threshold and
threshold + 0.175 m (SIM_thrd125 / 300 / 475) it writes only the layer of the
given threshold: the faces whose Mesh2d_fourier002_max_depth is deeper than it.

Attribute: Mesh2d_fourier002_max_depth (stored as "fou_dep")

Input : FlowFM_fou.nc  (UGRID 2D mesh, EPSG:3826 TWD97/TM2-121)
Output: SIM_thrd<mm>.shp (+.dbf, .shx, .prj), e.g. 0.3 m -> SIM_thrd300
"""

import argparse
import glob as _glob
import os
import sys

import numpy as np

from .fou2shp import (FIELD_NAME, NC_FILE, VAR_NAME, _SHP_DIR, _to_file_polygon,
                      gpd, nc, pd, write_threshold_shp)

SCRIPT = "fou2mfd"
DEFAULT_THRESHOLD = 0.3


def threshold_stem(threshold):
    """Output name for a threshold in metres: 0.3 -> SIM_thrd300, 0.1255 -> SIM_thrd125p5."""
    mm = threshold * 1000
    text = "%d" % round(mm) if abs(mm - round(mm)) < 1e-6 else ("%g" % mm).replace(".", "p")
    return "SIM_thrd" + text


def read_faces(nc_file, var=VAR_NAME):
    """Return (face_nodes, fill value, node_x, node_y, values) from a FOU NetCDF file."""
    with nc.Dataset(nc_file, "r") as ds:
        node_x = ds.variables["Mesh2d_node_x"][:]
        node_y = ds.variables["Mesh2d_node_y"][:]
        fn_var = ds.variables["Mesh2d_face_nodes"]
        fn = np.array(fn_var[:], dtype=np.int32)
        fn_fill = int(fn_var._FillValue)
        values = ds.variables[var][:]
    return fn, fn_fill, node_x, node_y, values


def remove_masked(shp, masks, out_dir):
    """Write a copy of `shp` without the polygons that intersect the `masks` shapefiles."""
    mask_parts = [gpd.read_file(p)[["geometry"]] for p in masks]
    mask_gdf = gpd.GeoDataFrame(pd.concat(mask_parts, ignore_index=True), crs=mask_parts[0].crs)
    gdf = gpd.read_file(shp)
    if mask_gdf.crs != gdf.crs:
        mask_gdf = mask_gdf.to_crs(gdf.crs)
    n_before = len(gdf)
    gdf = gdf[~gdf.geometry.intersects(mask_gdf.geometry.union_all())].copy()
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, os.path.basename(shp))
    _to_file_polygon(gdf, dst)
    print(f"  {dst}  — {n_before - len(gdf)} removed, {len(gdf)} remaining")
    return dst


def fou2mfd(nc_file=NC_FILE, threshold=DEFAULT_THRESHOLD, out_dir=_SHP_DIR, name=None,
            var=VAR_NAME, remove=None):
    """
    Write the faces of `nc_file` deeper than `threshold` (m) to <out_dir>/<name>.shp
    (default name SIM_thrd<mm>).  With `remove` (mask shapefiles), a filtered copy
    goes to <out_dir>_RM.  Return the path(s) written (without .shp).
    """
    stem = name or threshold_stem(threshold)
    if stem.lower().endswith(".shp"):
        stem = stem[:-4]
    os.makedirs(out_dir, exist_ok=True)
    fn, fn_fill, node_x, node_y, values = read_faces(nc_file, var)

    print(f"Input  : {nc_file}")
    print(f"Field  : '{FIELD_NAME}'  <-  {var}")
    print("CRS    : EPSG:3826  (TWD97 / TM2 zone 121)")
    print()
    shp_out = os.path.join(out_dir, stem)
    print(f"Threshold > {threshold:g} m  ->  {stem}")
    write_threshold_shp(shp_out, threshold, fn, fn_fill, node_x, node_y, values)
    written = [shp_out]

    if remove:
        if gpd is None:
            raise ImportError("removing masked polygons requires geopandas: pip install geopandas")
        rm_out_dir = out_dir.rstrip("/\\") + "_RM"
        print(f"\nApplying mask removal  ->  {rm_out_dir}/")
        written.append(remove_masked(shp_out + ".shp", remove, rm_out_dir)[:-4])
    return written


def main():
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Write the FlowFM 2D mesh faces deeper than one flood threshold "
                    "to a shapefile (fou2shp without the +/- 0.175 m layers).",
        epilog="""
examples:
  %(prog)s                                  (NC/FlowFM_fou.nc, 0.3 m -> SHP/SIM_thrd300.shp)
  %(prog)s NC/FlowFM_fou.nc -t 0.5          (-> SHP/SIM_thrd500.shp)
  %(prog)s NC/FlowFM_fou.nc -t 0.5 -o MFD   (-> SHP/MFD.shp)
  %(prog)s NC/FlowFM_fou.nc -of output
  %(prog)s NC/FlowFM_fou.nc --var Mesh2d_fourier001_max_depth -o MFD1
  %(prog)s NC/FlowFM_fou.nc -r SHP/EXCLUDE.shp
  %(prog)s NC/FlowFM_fou.nc -r SHP/ROAD.shp SHP/BUILDING.shp
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", default=None,
                        help=f"Path to the input NetCDF file (default: {NC_FILE})")
    parser.add_argument("-t", "--threshold", type=float, default=DEFAULT_THRESHOLD, metavar="M",
                        help="Flood depth threshold in metres; faces deeper than it are "
                             "written (default: %(default)g)")
    parser.add_argument("-o", "--name", default=None, metavar="NAME",
                        help="Output shapefile name (default: SIM_thrd<threshold in mm>)")
    parser.add_argument("-of", "--output-folder", dest="out_dir", default=_SHP_DIR,
                        metavar="DIR", help="Output directory for the shapefile (default: SHP)")
    parser.add_argument("--var", default=VAR_NAME,
                        help=f"Variable name in the NetCDF file (default: {VAR_NAME})")
    parser.add_argument("-r", "--remove", nargs="+", metavar="MASK.shp",
                        help="Remove output polygons that intersect these mask shapefiles. "
                             "The filtered copy is written to <output-folder>_RM. "
                             "Glob patterns are supported (e.g. --remove SHP/*.shp).")
    args = parser.parse_args()

    nc_file = args.path or NC_FILE
    if not os.path.exists(nc_file):
        print(f"Error: Input NetCDF file does not exist: {nc_file}")
        sys.exit(1)
    if args.threshold < 0:
        parser.error("threshold must be >= 0")

    masks = []
    for pattern in args.remove or []:
        matched = _glob.glob(pattern)
        if not matched:
            print(f"Warning: --remove pattern matched no files: {pattern}")
        masks.extend(matched)

    try:
        fou2mfd(nc_file, args.threshold, args.out_dir, args.name, args.var, masks)
    except KeyError as e:
        print(f"Error: Variable not found in NetCDF file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error processing NetCDF file: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
