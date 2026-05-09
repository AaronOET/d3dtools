#!/usr/bin/env python3
"""Convert Delft3D *.pliz files in the PLIZ folder to ESRI Shapefiles.

Requires: geopandas, shapely
    pip install geopandas shapely
"""

import sys
import argparse
from pathlib import Path

# Delft3D missing-value sentinel (-sys.float_info.max)
NODATA = -1.7976931348623157e+308


def _is_nodata(v):
    return abs(v - NODATA) < 1e290


def parse_pliz(filepath):
    """Parse a .pliz file.

    Returns list of dicts:
        name     : polyline identifier string
        num_cols : number of columns per point
        coords   : list of float lists [x, y, z1, ...]
    """
    polylines = []
    with open(filepath, "r") as fh:
        lines = fh.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue

        name = line  # polyline name / ID

        if i >= len(lines):
            break
        header = lines[i].strip().split()
        i += 1
        if len(header) < 2:
            continue

        num_pts = int(header[0])
        num_cols = int(header[1])

        coords = []
        for _ in range(num_pts):
            if i >= len(lines):
                break
            parts = lines[i].strip().split()
            i += 1
            if len(parts) >= 2:
                coords.append([float(v) for v in parts])

        if coords:
            polylines.append({"name": name, "num_cols": num_cols, "coords": coords})

    return polylines


def pliz_to_shp(pliz_path, output_dir=None):
    """Convert one .pliz file to a shapefile."""
    try:
        import geopandas as gpd
        from shapely.geometry import LineString
    except ImportError:
        print("ERROR: geopandas and shapely are required.")
        print("       pip install geopandas shapely")
        sys.exit(1)

    pliz_path = Path(pliz_path)
    out_dir = Path(output_dir) if output_dir else pliz_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    polylines = parse_pliz(pliz_path)
    if not polylines:
        print(f"  [skip] no polylines found in {pliz_path.name}")
        return

    records = []
    for pl in polylines:
        coords = pl["coords"]
        if len(coords) < 2:
            continue

        # Always use z1 (column 3) as Z; fall back to 0.0 if NODATA or missing
        if pl["num_cols"] >= 3:
            pts = [
                (row[0], row[1], row[2] if len(row) > 2 and not _is_nodata(row[2]) else 0.0)
                for row in coords
            ]
            geom = LineString(pts)
        else:
            geom = LineString([(row[0], row[1]) for row in coords])

        rec = {
            "geometry": geom,
            "name": pl["name"],
            "n_pts": len(coords),
            "n_cols": pl["num_cols"],
            "source": pliz_path.name,
        }

        # Store per-extra-column min/max for non-NODATA values
        for ci in range(2, pl["num_cols"]):
            col_idx = ci - 1  # z1, z2, z3 ...
            vals = [row[ci] for row in coords if len(row) > ci and not _is_nodata(row[ci])]
            if vals:
                rec[f"z{col_idx}_min"] = min(vals)
                rec[f"z{col_idx}_max"] = max(vals)

        records.append(rec)

    if not records:
        print(f"  [skip] no valid polylines in {pliz_path.name}")
        return

    gdf = gpd.GeoDataFrame(records, geometry="geometry")
    out_path = out_dir / (pliz_path.stem + ".shp")
    gdf.to_file(out_path)
    print(f"  {pliz_path.name} -> {out_path.name}  ({len(records)} polyline(s))")


def main():
    """
    Command line entry point for the PLIZ to Shapefile conversion tool.
    """
    parser = argparse.ArgumentParser(
        description='Convert Delft3D *.pliz files to ESRI Shapefiles',
        epilog='''
examples:
  %(prog)s                               # Use default folders (PLIZ -> SHP_DIKE)
  %(prog)s -i custom/PLIZ -o custom/SHP
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i', '--input',
                        default='PLIZ',
                        help='Input folder containing *.pliz files (default: PLIZ)')
    parser.add_argument('-o', '--output',
                        default='SHP_DIKE',
                        help='Output folder for shapefiles (default: SHP_DIKE)')

    args = parser.parse_args()

    pliz_dir = Path(args.input)
    if not pliz_dir.exists():
        print(f"ERROR: input folder not found: {pliz_dir}")
        sys.exit(1)

    pliz_files = sorted(pliz_dir.glob("*.pliz"))
    if not pliz_files:
        print(f"No .pliz files found in {pliz_dir}")
        sys.exit(1)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Found {len(pliz_files)} .pliz file(s) in {pliz_dir}")
    for f in pliz_files:
        print(f"Processing {f.name} ...")
        pliz_to_shp(f, output_dir=out_dir)

    print("Done.")


if __name__ == "__main__":
    main()
