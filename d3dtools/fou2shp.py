"""
Reconstruct FlowFM 2D mesh faces as polygons in a shapefile.

Attribute: Mesh2d_fourier002_max_depth (stored as "fou_dep" — DBF field
           names are limited to 10 characters)

Input : FlowFM_fou.nc  (UGRID 2D mesh, EPSG:3826 TWD97/TM2-121)
Output: FlowFM_fou_faces.shp  (+.dbf, .shx, .prj)
"""

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


def write_threshold_shp(shp_out, threshold, fn, fn_fill, node_x, node_y, values):
    """Write faces whose depth value exceeds *threshold* to *shp_out*."""
    w = shapefile.Writer(shp_out, shapefile.POLYGON)
    w.autoBalance = 1
    w.field(FIELD_NAME, "N", size=18, decimal=6)

    n_written = 0
    n_skipped = 0

    for i in range(fn.shape[0]):
        row      = fn[i, :]
        valid    = row != fn_fill
        node_idx = row[valid].astype(int) - 1

        val = values[i]

        # Skip masked values or values not exceeding the threshold
        if np.ma.is_masked(val) or float(val) <= threshold:
            n_skipped += 1
            continue

        if len(node_idx) < 3:
            n_skipped += 1
            continue

        ring = [(float(node_x[j]), float(node_y[j])) for j in node_idx]
        ring.append(ring[0])

        w.poly([ring])
        w.record(float(val))
        n_written += 1

    w.close()

    with open(shp_out + ".prj", "w") as f:
        f.write(PRJ_WKT)

    print(f"  {shp_out}.shp  — {n_written} polygons written, {n_skipped} skipped")


def main():
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Reconstruct FlowFM 2D mesh faces as polygons in threshold shapefiles.",
        epilog="""
examples:
  %(prog)s -i NC/FlowFM_fou.nc (default output dir: SHP)
  %(prog)s --input NC/FlowFM_fou.nc --out-dir SHP
  %(prog)s --input NC/FlowFM_fou.nc --var Mesh2d_fourier002_max_depth --out-dir output
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        help="Path to the input NetCDF file (e.g., NC/FlowFM_fou.nc)",
    )
    parser.add_argument(
        "--out-dir",
        default=_SHP_DIR,
        help="Output directory for shapefiles (default: SHP)",
    )
    parser.add_argument(
        "--var",
        default=VAR_NAME,
        help=f"Variable name in the NetCDF file (default: {VAR_NAME})",
    )

    args = parser.parse_args()

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
            print(f"Threshold > {threshold:.3f}m  ->  {stem}")
            write_threshold_shp(shp_out, threshold, fn, fn_fill, node_x, node_y, values)
    except KeyError as e:
        print(f"Error: Variable not found in NetCDF file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error processing NetCDF file: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
