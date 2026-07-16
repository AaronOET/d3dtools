#!/usr/bin/env python3
"""
pli2shp - Convert Delft3D polyline files (.pli / .ldb) to ESRI Shapefiles

Reads a Delft3D .pli or .ldb polyline file — one or more line segments, each
introduced by a name line, a "num_points num_columns" line, and that many
coordinate lines (whitespace- or comma-delimited) — and writes a single line
shapefile:
  {stem}.shp
"""

import argparse
import os
import re
import sys

import geopandas as gpd
from shapely.geometry import LineString

_TOKEN_RE = re.compile(r'[,\s]+')


def _tokenize(line):
    """Split a header/coordinate line on whitespace and/or commas."""
    return [t for t in _TOKEN_RE.split(line.strip()) if t]


def read_polyline_file(file_path, quiet=False):
    """
    Parse a Delft3D .pli or .ldb file into one or more line segments.

    Both formats share the same structure: a name line, a line giving
    "num_points num_columns", then that many coordinate lines (X Y [...]).
    Comment lines starting with '*' and blank lines are skipped.

    Returns:
        list[dict]: [{'name': str, 'coordinates': [(x, y), ...]}, ...]
    """
    _print(f"Reading polyline file: {file_path}", quiet)

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
        except ValueError:
            _print(f"  Warning: could not parse point count for '{name}': '{content[i].strip()}'", quiet)
            i += 1
            continue
        i += 1

        coordinates = []
        while i < len(content) and len(coordinates) < num_points:
            coord_line = content[i].strip()
            if not coord_line or coord_line.startswith('*'):
                i += 1
                continue
            tokens = _tokenize(coord_line)
            try:
                x, y = float(tokens[0]), float(tokens[1])
                coordinates.append((x, y))
            except (ValueError, IndexError):
                _print(f"  Warning: could not parse coordinate line: '{coord_line}'", quiet)
            i += 1

        if len(coordinates) >= 2:
            segments.append({'name': name, 'coordinates': coordinates})
            _print(f"  Parsed '{name}': {len(coordinates)} points", quiet)
        else:
            _print(f"  Warning: '{name}' has fewer than 2 points; skipping.", quiet)

    _print(f"  Segments: {len(segments)}", quiet)
    return segments


def create_lines(segments, quiet=False):
    """
    Build Shapely LineString objects from parsed polyline segments.

    Returns:
        list[LineString]
    """
    _print("Creating lines from polyline data...", quiet)
    lines = [LineString(seg['coordinates']) for seg in segments]
    _print(f"  Lines created: {len(lines)}", quiet)
    return lines


def create_geodataframe(segments, lines, source_ext, crs="EPSG:3826"):
    """Build a GeoDataFrame from parsed segments and their line geometries."""
    data = {
        'ID': range(1, len(segments) + 1),
        'LINE_NAME': [seg['name'] for seg in segments],
        'NUM_POINTS': [len(seg['coordinates']) for seg in segments],
        'LENGTH': [line.length for line in lines],
        'SRC_TYPE': [source_ext.lstrip('.').upper()] * len(segments),
    }
    return gpd.GeoDataFrame(data, geometry=lines, crs=crs)


def polyline_to_shp(input_file, output_dir="SHP_LINES", crs="EPSG:3826", quiet=False):
    """
    Convert a Delft3D polyline file (.pli or .ldb) to a line shapefile.

    Args:
        input_file (str): Path to the input .pli or .ldb file.
        output_dir (str): Directory for the output shapefile.
        crs (str): CRS for the output shapefile.
        quiet (bool): Suppress non-error output.

    Returns:
        str: Path to the output lines shapefile.
    """
    _print("=== Polyline (PLI/LDB) to Shapefile Converter ===", quiet)

    ext = os.path.splitext(input_file)[1].lower()
    os.makedirs(output_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(input_file))[0]
    out_lines = os.path.join(output_dir, f"{stem}.shp")

    segments = read_polyline_file(input_file, quiet)
    if not segments:
        raise RuntimeError("No polyline segments were parsed from the input file.")

    lines = create_lines(segments, quiet)
    gdf = create_geodataframe(segments, lines, ext, crs)

    _print(f"Saving lines shapefile: {out_lines}", quiet)
    gdf.to_file(out_lines)

    if not quiet:
        bounds = gdf.total_bounds
        print("\n=== SUMMARY ===")
        print(f"  Input:  {input_file}")
        print(f"  Output: {out_lines}")
        print(f"  Lines: {len(gdf):,}")
        print(f"  Total points: {gdf['NUM_POINTS'].sum():,}")
        print(f"  CRS: {gdf.crs}")
        print(f"  Length — min: {gdf['LENGTH'].min():.2f}  max: {gdf['LENGTH'].max():.2f}  "
              f"total: {gdf['LENGTH'].sum():.2f}")
        print(f"  X: {bounds[0]:.2f} to {bounds[2]:.2f}")
        print(f"  Y: {bounds[1]:.2f} to {bounds[3]:.2f}")

    return out_lines


def _print(msg, quiet=False):
    if not quiet:
        print(msg)


def main():
    parser = argparse.ArgumentParser(
        prog='pli2shp',
        description='Convert a Delft3D polyline file (.pli or .ldb) to an ESRI Shapefile',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pli2shp -i boundary.pli
  pli2shp -i LDB_001.ldb -of output --crs EPSG:4326
  pli2shp -i inlet.pli -q
  pli2shp -if INPUT_DIR -of OUTPUT_DIR
        """,
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '-i', '--input',
        metavar='FILE',
        help='Path to the polyline file (.pli or .ldb)',
    )
    input_group.add_argument(
        '-if', '--input-folder',
        metavar='DIR',
        help='Path to a folder of polyline files (.pli/.ldb); all are converted to the output folder',
    )
    parser.add_argument(
        '-of', '--output-folder',
        dest='output_dir',
        default='SHP_LINES',
        metavar='DIR',
        help='Output directory for the shapefile (default: SHP_LINES)',
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
            if os.path.splitext(f)[1].lower() in ('.pli', '.ldb')
        )
        if not input_files:
            print(f"Error: No .pli or .ldb files found in '{args.input_folder}'.",
                  file=sys.stderr)
            sys.exit(1)

        errors = 0
        for fname in input_files:
            input_file = os.path.join(args.input_folder, fname)
            try:
                out_path = polyline_to_shp(
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

    if os.path.splitext(args.input)[1].lower() not in ('.pli', '.ldb'):
        print(f"Error: Unsupported file type '{args.input}'. Expected .pli or .ldb.",
              file=sys.stderr)
        sys.exit(1)

    try:
        out_path = polyline_to_shp(
            args.input, args.output_dir, args.crs, args.quiet
        )
        if args.quiet:
            print(out_path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
