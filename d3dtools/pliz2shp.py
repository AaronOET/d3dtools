#!/usr/bin/env python3
"""
pliz2shp - Convert Delft3D/D-Flow FM weir polyline files (.pliz) to 3D ESRI Shapefiles

Reads a .pliz file — one or more line segments, each introduced by a name
line, a "num_points num_columns" line, and that many coordinate lines
(X Y Z [attr4 attr5 ...], whitespace- or comma-delimited) — and writes a
single 3D (PolylineZ) line shapefile:
  {stem}.shp
"""

import argparse
import os
import re
import sys

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString

_TOKEN_RE = re.compile(r'[,\s]+')


def _tokenize(line):
    """Split a header/coordinate line on whitespace and/or commas."""
    return [t for t in _TOKEN_RE.split(line.strip()) if t]


def read_pliz_file(file_path, quiet=False):
    """
    Parse a .pliz file into one or more 3D line segments.

    Each segment starts with a name line, then a line giving
    "num_points num_columns", then that many coordinate lines of the form
    X Y Z [attr4 attr5 ...]. Comment lines starting with '*' and blank
    lines are skipped.

    Returns:
        list[dict]: [{
            'name': str,
            'num_columns': int,
            'coordinates': [(x, y, z), ...],
            'attributes': [[attr4, attr5, ...], ...],  # may be empty per point
        }, ...]
    """
    _print(f"Reading pliz file: {file_path}", quiet)

    with open(file_path) as f:
        content = f.readlines()

    segments = []
    i = 0
    while i < len(content):
        line = content[i].strip()
        if not line or line.startswith('*'):
            i += 1
            continue

        name = line
        i += 1
        if i >= len(content):
            _print(f"  Warning: '{name}' has no point-count line; skipping.", quiet)
            break

        header = _tokenize(content[i])
        if len(header) < 2:
            _print(f"  Warning: invalid header line for '{name}': '{content[i].strip()}'", quiet)
            i += 1
            continue

        try:
            num_points = int(float(header[0]))
            num_columns = int(float(header[1]))
        except ValueError:
            _print(f"  Warning: could not parse header for '{name}': '{content[i].strip()}'", quiet)
            i += 1
            continue
        i += 1

        coordinates = []
        attributes = []
        while i < len(content) and len(coordinates) < num_points:
            coord_line = content[i].strip()
            if not coord_line or coord_line.startswith('*'):
                i += 1
                continue
            tokens = _tokenize(coord_line)
            try:
                x, y = float(tokens[0]), float(tokens[1])
                z = float(tokens[2]) if len(tokens) > 2 else 0.0
                extra = [float(t) for t in tokens[3:num_columns]]
                coordinates.append((x, y, z))
                attributes.append(extra)
            except (ValueError, IndexError):
                _print(f"  Warning: could not parse coordinate line: '{coord_line}'", quiet)
            i += 1

        if len(coordinates) >= 2:
            segments.append({
                'name': name,
                'num_columns': num_columns,
                'coordinates': coordinates,
                'attributes': attributes,
            })
            _print(f"  Parsed '{name}': {len(coordinates)} points, {num_columns} columns", quiet)
        else:
            _print(f"  Warning: '{name}' has fewer than 2 points; skipping.", quiet)

    _print(f"  Segments: {len(segments)}", quiet)
    return segments


def create_lines(segments, quiet=False):
    """
    Build 3D Shapely LineString objects (with Z) from parsed pliz segments.

    Returns:
        list[LineString]
    """
    _print("Creating 3D lines from pliz data...", quiet)
    lines = [LineString(seg['coordinates']) for seg in segments]
    _print(f"  Lines created: {len(lines)}", quiet)
    return lines


def _line_length_3d(coordinates):
    """Compute true 3D length of a polyline given (x, y, z) vertices."""
    pts = np.asarray(coordinates, dtype=float)
    return float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))


def create_geodataframe(segments, lines, source_ext, crs="EPSG:3826"):
    """Build a GeoDataFrame from parsed pliz segments and their 3D line geometries."""
    z_values = [[c[2] for c in seg['coordinates']] for seg in segments]
    max_extra_cols = max((seg['num_columns'] - 3 for seg in segments), default=0)
    max_extra_cols = max(max_extra_cols, 0)

    data = {
        'ID': range(1, len(segments) + 1),
        'LINE_NAME': [seg['name'] for seg in segments],
        'NUM_POINTS': [len(seg['coordinates']) for seg in segments],
        'LENGTH': [line.length for line in lines],
        'LENGTH_3D': [_line_length_3d(seg['coordinates']) for seg in segments],
        'Z_MIN': [min(z) for z in z_values],
        'Z_MAX': [max(z) for z in z_values],
        'Z_MEAN': [sum(z) / len(z) for z in z_values],
        'SRC_TYPE': [source_ext.lstrip('.').upper()] * len(segments),
    }

    for col_idx in range(max_extra_cols):
        field = f'C{col_idx + 4}_MEAN'
        values = []
        for seg in segments:
            col_vals = [a[col_idx] for a in seg['attributes'] if len(a) > col_idx]
            values.append(sum(col_vals) / len(col_vals) if col_vals else None)
        data[field] = values

    return gpd.GeoDataFrame(data, geometry=lines, crs=crs)


def pliz_to_shp(input_file, output_dir="SHP_LINES3D", crs="EPSG:3826", quiet=False):
    """
    Convert a .pliz file to a 3D (PolylineZ) line shapefile.

    Args:
        input_file (str): Path to the input .pliz file.
        output_dir (str): Directory for the output shapefile.
        crs (str): CRS for the output shapefile.
        quiet (bool): Suppress non-error output.

    Returns:
        str: Path to the output lines shapefile.
    """
    _print("=== PLIZ to 3D Shapefile Converter ===", quiet)

    ext = os.path.splitext(input_file)[1].lower()
    os.makedirs(output_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(input_file))[0]
    out_lines = os.path.join(output_dir, f"{stem}.shp")

    segments = read_pliz_file(input_file, quiet)
    if not segments:
        raise RuntimeError("No pliz segments were parsed from the input file.")

    lines = create_lines(segments, quiet)
    gdf = create_geodataframe(segments, lines, ext, crs)

    _print(f"Saving 3D lines shapefile: {out_lines}", quiet)
    gdf.to_file(out_lines)

    if not quiet:
        bounds = gdf.total_bounds
        print("\n=== SUMMARY ===")
        print(f"  Input:  {input_file}")
        print(f"  Output: {out_lines}")
        print(f"  Lines: {len(gdf):,}")
        print(f"  Total points: {gdf['NUM_POINTS'].sum():,}")
        print(f"  CRS: {gdf.crs}")
        print(f"  Length (2D) — min: {gdf['LENGTH'].min():.2f}  max: {gdf['LENGTH'].max():.2f}  "
              f"total: {gdf['LENGTH'].sum():.2f}")
        print(f"  Z — min: {gdf['Z_MIN'].min():.3f}  max: {gdf['Z_MAX'].max():.3f}")
        print(f"  X: {bounds[0]:.2f} to {bounds[2]:.2f}")
        print(f"  Y: {bounds[1]:.2f} to {bounds[3]:.2f}")

    return out_lines


def _print(msg, quiet=False):
    if not quiet:
        print(msg)


def main():
    parser = argparse.ArgumentParser(
        prog='pliz2shp',
        description='Convert a Delft3D/D-Flow FM .pliz file to a 3D (PolylineZ) ESRI Shapefile',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pliz2shp -i Dike001.pliz
  pliz2shp -i Dike001.pliz -of OUTPUT_DIR --crs EPSG:4326
  pliz2shp -i Dike001.pliz -q
  pliz2shp -if INPUT_DIR -of OUTPUT_DIR
        """,
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '-i', '--input',
        metavar='FILE',
        help='Path to the pliz file (.pliz)',
    )
    input_group.add_argument(
        '-if', '--input-folder',
        metavar='DIR',
        help='Path to a folder of .pliz files; all are converted to the output folder',
    )
    parser.add_argument(
        '-of', '--output-folder',
        dest='output_dir',
        default='SHP_LINES3D',
        metavar='DIR',
        help='Output directory for the shapefile (default: SHP_LINES3D)',
    )
    parser.add_argument(
        '--crs',
        default='EPSG:3826',
        help='Coordinate reference system (default: EPSG:3826)',
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
            if os.path.splitext(f)[1].lower() == '.pliz'
        )
        if not input_files:
            print(f"Error: No .pliz files found in '{args.input_folder}'.",
                  file=sys.stderr)
            sys.exit(1)

        errors = 0
        for fname in input_files:
            input_file = os.path.join(args.input_folder, fname)
            try:
                out_path = pliz_to_shp(
                    input_file, args.output_dir, args.crs, args.quiet
                )
                if args.quiet:
                    print(out_path)
            except Exception as e:
                print(f"Error converting '{input_file}': {e}", file=sys.stderr)
                errors += 1

        if errors:
            sys.exit(1)
        return

    if not os.path.isfile(args.input):
        print(f"Error: File '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)

    if os.path.splitext(args.input)[1].lower() != '.pliz':
        print(f"Error: Unsupported file type '{args.input}'. Expected .pliz.",
              file=sys.stderr)
        sys.exit(1)

    try:
        out_path = pliz_to_shp(
            args.input, args.output_dir, args.crs, args.quiet
        )
        if args.quiet:
            print(out_path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
