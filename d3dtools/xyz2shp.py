#!/usr/bin/env python3
"""
xyz2shp - Convert XYZ point data to ESRI Shapefiles

Reads a whitespace-delimited .xyz file or a comma-delimited .csv file
(x, y, z per line, with an optional header row) and writes a single
3D point shapefile:
  {stem}.shp

Also reads SFINCS observation-point (.obs) and discharge-source (.src)
files: x, y per line with an optional quoted name, e.g.
    600803.2   3627499.5  "charleston_mouth"
These are written as 2D point shapefiles {stem}_obs.shp / {stem}_src.shp
with a NAME attribute.
"""

import argparse
import os
import shlex
import sys

import geopandas as gpd
import numpy as np
from shapely.geometry import Point


def read_xyz(file_path, quiet=False):
    """
    Read x, y, (optional) z columns from a whitespace-delimited .xyz file
    or a comma-delimited .csv file. A non-numeric first row (header) is
    skipped automatically.

    Returns:
        tuple: (x, y, z) numpy arrays; z is None if the file has only 2 columns
    """
    _print(f"Reading point file: {file_path}", quiet)

    is_csv = os.path.splitext(file_path)[1].lower() == '.csv'
    delimiter = ',' if is_csv else None

    with open(file_path) as f:
        first_line = f.readline()
    tokens = first_line.strip().split(delimiter) if is_csv else first_line.split()
    skiprows = 0
    try:
        [float(t) for t in tokens]
    except ValueError:
        skiprows = 1

    data = np.loadtxt(file_path, delimiter=delimiter, skiprows=skiprows)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.shape[1] < 2:
        raise ValueError(f"Expected at least 2 columns (x y), got {data.shape[1]}")

    x, y = data[:, 0], data[:, 1]
    z = data[:, 2] if data.shape[1] >= 3 else None

    if not quiet:
        print(f"  Points: {len(x):,}")
        z_range = f"  Z: {z.min():.2f} to {z.max():.2f}" if z is not None else "  Z: (none)"
        print(f"  X: {x.min():.2f} to {x.max():.2f}  "
              f"Y: {y.min():.2f} to {y.max():.2f}" + z_range)

    return x, y, z


SFINCS_EXTS = ('.obs', '.src')
SUPPORTED_EXTS = ('.xyz', '.csv') + SFINCS_EXTS


def _is_float(token):
    try:
        float(token)
        return True
    except ValueError:
        return False


def read_sfincs_points(file_path, quiet=False):
    """
    Read a SFINCS observation-point (.obs) or discharge-source (.src) file.

    Each non-empty line holds x and y (whitespace or tab separated),
    optionally followed by a name (quoted or not). Lines starting with
    '#' or '!' are comments. Points without a name get one from the file
    stem and the point number (e.g. ``sfincs_1``).

    Returns:
        tuple: (x, y, names) with x, y numpy arrays and names a list of str
    """
    _print(f"Reading SFINCS point file: {file_path}", quiet)
    stem = os.path.splitext(os.path.basename(file_path))[0]

    xs, ys, names = [], [], []
    with open(file_path, encoding='utf-8-sig', errors='replace') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line[0] in '#!':
                continue
            try:
                tokens = shlex.split(line, posix=True)
            except ValueError:  # unbalanced quotes
                tokens = line.replace('"', ' ').replace("'", ' ').split()
            if len(tokens) < 2 or not (_is_float(tokens[0]) and _is_float(tokens[1])):
                raise ValueError(
                    f"Line {lineno}: expected 'x y [name]', got: {line!r}")
            xs.append(float(tokens[0]))
            ys.append(float(tokens[1]))
            name_tokens = [t for t in tokens[2:] if not _is_float(t)]
            names.append(' '.join(name_tokens) if name_tokens
                         else f"{stem}_{len(xs)}")

    if not xs:
        raise ValueError(f"No points found in '{file_path}'.")

    x, y = np.array(xs), np.array(ys)
    if not quiet:
        print(f"  Points: {len(x):,}")
        print(f"  X: {x.min():.2f} to {x.max():.2f}  "
              f"Y: {y.min():.2f} to {y.max():.2f}")
    return x, y, names


def create_points(x, y, z, quiet=False, dimension='3d'):
    """
    Build Shapely Point objects from x, y, (optional) z coordinate arrays.

    Args:
        dimension (str): '3' to emit x,y,z points (requires z), '2' to
            emit x,y points only, dropping any z values.

    Returns:
        list[Point]
    """
    _print("Creating points from XYZ data...", quiet)

    use_z = dimension == '3' and z is not None
    if dimension == '3' and z is None:
        _print("  Warning: no Z column found; writing 2D points instead.", quiet)

    if use_z:
        points = [Point(float(x[i]), float(y[i]), float(z[i])) for i in range(len(x))]
    else:
        points = [Point(float(x[i]), float(y[i])) for i in range(len(x))]

    _print(f"  Points created: {len(points)}", quiet)
    return points


def _depth_category(z):
    """Classify a Z value into a depth/elevation bucket."""
    if z < -10:
        return "Deep (< -10m)"
    if z < -5:
        return "Medium (-10 to -5m)"
    if z < 0:
        return "Shallow (-5 to 0m)"
    return "Above sea level"


def create_geodataframe(points, z, crs="EPSG:3826", names=None):
    """Build a GeoDataFrame from a list of Shapely points. Adds Z_VALUE and
    DEPTH_CAT attribute columns when source z values are available, even if
    the point geometries themselves are 2D, and a NAME column when names
    are given (SFINCS .obs/.src)."""
    data = {
        'ID': range(1, len(points) + 1),
    }
    if names is not None:
        data['NAME'] = list(names)
    data.update({
        'X_COORD': [p.x for p in points],
        'Y_COORD': [p.y for p in points],
    })
    if z is not None:
        data['Z_VALUE'] = [float(v) for v in z]
        data['DEPTH_CAT'] = [_depth_category(v) for v in z]
    return gpd.GeoDataFrame(data, geometry=points, crs=crs)


def xyz_to_shp(input_file, output_dir="SHP_XYZ", crs="EPSG:3826", quiet=False, dimension='3d'):
    """
    Convert a point file (.xyz, .csv, or SFINCS .obs/.src) to a point
    shapefile. SFINCS files have no z and are always written as 2D points
    with a NAME attribute.

    Args:
        input_file (str): Path to the input .xyz, .csv, .obs or .src file.
        output_dir (str): Directory for the output shapefile.
        crs (str): CRS for the output shapefile.
        quiet (bool): Suppress non-error output.
        dimension (str): '3' to write x,y,z point geometries (default),
            '2' to write x,y point geometries only, regardless of whether
            the source file has a z column.

    Returns:
        str: Path to the output points shapefile.
    """
    _print("=== XYZ to Shapefile Converter ===", quiet)

    os.makedirs(output_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(input_file))
    if ext.lower() in SFINCS_EXTS:
        # sfincs.obs and sfincs.src share a stem: keep both outputs apart
        stem = f"{stem}_{ext[1:].lower()}"
    out_points = os.path.join(output_dir, f"{stem}.shp")

    names = None
    if os.path.splitext(input_file)[1].lower() in SFINCS_EXTS:
        x, y, names = read_sfincs_points(input_file, quiet)
        z = None
        points = create_points(x, y, z, quiet, '2')
    else:
        x, y, z = read_xyz(input_file, quiet)
        points = create_points(x, y, z, quiet, dimension)

    if not points:
        raise RuntimeError("No points were created from the input file.")

    gdf = create_geodataframe(points, z, crs, names)
    _print(f"Saving points shapefile: {out_points}", quiet)
    gdf.to_file(out_points)

    if not quiet:
        bounds = gdf.total_bounds
        print("\n=== SUMMARY ===")
        print(f"  Input:  {input_file}")
        print(f"  Output: {out_points}")
        print(f"  Points: {len(gdf):,}")
        print(f"  Geometry: {'3D (x,y,z)' if points[0].has_z else '2D (x,y)'}")
        print(f"  CRS: {gdf.crs}")
        if 'DEPTH_CAT' in gdf.columns:
            counts = gdf['DEPTH_CAT'].value_counts()
            for cat, n in counts.items():
                print(f"  {cat}: {n} ({n/len(gdf)*100:.1f}%)")
        if z is not None:
            print(f"  Z — min: {z.min():.2f}  max: {z.max():.2f}  mean: {z.mean():.2f}")
        print(f"  X: {bounds[0]:.2f} to {bounds[2]:.2f}")
        print(f"  Y: {bounds[1]:.2f} to {bounds[3]:.2f}")

    return out_points


def _print(msg, quiet=False):
    if not quiet:
        print(msg)


def main():
    parser = argparse.ArgumentParser(
        prog='xyz2shp',
        description='Convert a point file (.xyz, .csv, or SFINCS .obs/.src) '
                    'to an ESRI Shapefile',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  xyz2shp -i XYZ_001.xyz
  xyz2shp -i XYZ_001.csv -of OUTPUT_DIR --crs EPSG:4326
  xyz2shp -i XYZ_001.xyz -d 2
  xyz2shp -i XYZ_001.xyz -q
  xyz2shp -i sfincs.obs
  xyz2shp -i sfincs.src -of SHP_SFINCS
  xyz2shp -if INPUT_DIR -of OUTPUT_DIR
        """,
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '-i', '--input',
        metavar='FILE',
        help='Path to the point file (.xyz, .csv, .obs or .src)',
    )
    input_group.add_argument(
        '-if', '--input-folder',
        metavar='DIR',
        help='Path to a folder of point files (.xyz/.csv/.obs/.src); '
             'all are converted to the output folder',
    )
    parser.add_argument(
        '-of', '--output-folder',
        dest='output_dir',
        default='SHP_XYZ',
        metavar='DIR',
        help='Output directory for the shapefile (default: SHP_XYZ)',
    )
    parser.add_argument(
        '--crs',
        default='EPSG:3826',
        help='Coordinate reference system (default: EPSG:3826)',
    )
    parser.add_argument(
        '-d', '--dimension',
        choices=['2', '3'],
        default='3',
        help="Output point dimension: '2d' (x,y only) or '3d' (x,y,z). "
             "'2d' drops any z column even if present (default: 3d)",
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='Suppress non-error output',
    )

    args = parser.parse_args()

    if args.input_folder:
        if not os.path.isdir(args.input_folder):
            print(f"Error: Folder '{args.input_folder}' not found.", file=sys.stderr)
            sys.exit(1)

        input_files = sorted(
            f for f in os.listdir(args.input_folder)
            if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS
        )
        if not input_files:
            print(f"Error: No .xyz, .csv, .obs or .src files found in '{args.input_folder}'.",
                  file=sys.stderr)
            sys.exit(1)

        errors = 0
        for fname in input_files:
            input_file = os.path.join(args.input_folder, fname)
            try:
                points = xyz_to_shp(
                    input_file, args.output_dir, args.crs, args.quiet, args.dimension
                )
                if args.quiet:
                    print(points)
            except Exception as e:
                print(f"Error converting '{input_file}': {e}", file=sys.stderr)
                errors += 1

        if errors:
            sys.exit(1)
        return

    if not os.path.isfile(args.input):
        print(f"Error: File '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)

    if os.path.splitext(args.input)[1].lower() not in SUPPORTED_EXTS:
        print(f"Error: Unsupported file type '{args.input}'. "
              f"Expected .xyz, .csv, .obs or .src.",
              file=sys.stderr)
        sys.exit(1)

    try:
        points = xyz_to_shp(
            args.input, args.output_dir, args.crs, args.quiet, args.dimension
        )
        if args.quiet:
            print(points)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
