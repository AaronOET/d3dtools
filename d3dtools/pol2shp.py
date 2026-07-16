#!/usr/bin/env python3
"""
pol2shp - Convert Delft3D/D-Flow FM polygon files (.pol) to ESRI Shapefiles

Reads a .pol file — one or more rings, each introduced by a name line, a
"num_points num_columns" line, and that many coordinate lines
(X Y [attr3 attr4 ...], whitespace- or comma-delimited) — and writes a
single polygon shapefile:
  {stem}.shp
"""

import argparse
import os
import re
import sys

import geopandas as gpd
from shapely.geometry import Polygon

_TOKEN_RE = re.compile(r'[,\s]+')


def _tokenize(line):
    """Split a header/coordinate line on whitespace and/or commas."""
    return [t for t in _TOKEN_RE.split(line.strip()) if t]


def read_pol_file(file_path, quiet=False):
    """
    Parse a .pol file into one or more polygon rings.

    Each ring starts with a name line, then a line giving
    "num_points num_columns", then that many coordinate lines of the form
    X Y [attr3 attr4 ...]. Comment lines starting with '*' and blank
    lines are skipped.

    Returns:
        list[dict]: [{
            'name': str,
            'num_columns': int,
            'coordinates': [(x, y), ...],
            'attributes': [[attr3, attr4, ...], ...],  # may be empty per point
        }, ...]
    """
    _print(f"Reading pol file: {file_path}", quiet)

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
                extra = [float(t) for t in tokens[2:num_columns]]
                coordinates.append((x, y))
                attributes.append(extra)
            except (ValueError, IndexError):
                _print(f"  Warning: could not parse coordinate line: '{coord_line}'", quiet)
            i += 1

        if len(coordinates) >= 3:
            if coordinates[0] != coordinates[-1]:
                coordinates.append(coordinates[0])
                attributes.append(attributes[0])
            segments.append({
                'name': name,
                'num_columns': num_columns,
                'coordinates': coordinates,
                'attributes': attributes,
            })
            _print(f"  Parsed '{name}': {len(coordinates)} points, {num_columns} columns", quiet)
        else:
            _print(f"  Warning: '{name}' has fewer than 3 points; skipping.", quiet)

    _print(f"  Polygons: {len(segments)}", quiet)
    return segments


def create_polygons(segments, quiet=False):
    """
    Build Shapely Polygon objects from parsed pol rings.

    Returns:
        list[Polygon]
    """
    _print("Creating polygons from pol data...", quiet)
    polygons = [Polygon(seg['coordinates']) for seg in segments]
    _print(f"  Polygons created: {len(polygons)}", quiet)
    return polygons


def create_geodataframe(segments, polygons, source_ext, crs="EPSG:3826"):
    """Build a GeoDataFrame from parsed pol segments and their polygon geometries."""
    max_extra_cols = max((seg['num_columns'] - 2 for seg in segments), default=0)
    max_extra_cols = max(max_extra_cols, 0)

    data = {
        'ID': range(1, len(segments) + 1),
        'POLY_NAME': [seg['name'] for seg in segments],
        'NUM_POINTS': [len(seg['coordinates']) for seg in segments],
        'AREA': [poly.area for poly in polygons],
        'PERIMETER': [poly.length for poly in polygons],
        'SRC_TYPE': [source_ext.lstrip('.').upper()] * len(segments),
    }

    for col_idx in range(max_extra_cols):
        field = f'C{col_idx + 3}_MEAN'
        values = []
        for seg in segments:
            col_vals = [a[col_idx] for a in seg['attributes'] if len(a) > col_idx]
            values.append(sum(col_vals) / len(col_vals) if col_vals else None)
        data[field] = values

    return gpd.GeoDataFrame(data, geometry=polygons, crs=crs)


def pol_to_shp(input_file, output_dir="SHP_POLYGONS", crs="EPSG:3826", quiet=False):
    """
    Convert a .pol file to a polygon shapefile.

    Args:
        input_file (str): Path to the input .pol file.
        output_dir (str): Directory for the output shapefile.
        crs (str): CRS for the output shapefile.
        quiet (bool): Suppress non-error output.

    Returns:
        str: Path to the output polygon shapefile.
    """
    _print("=== POL to Shapefile Converter ===", quiet)

    ext = os.path.splitext(input_file)[1].lower()
    os.makedirs(output_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(input_file))[0]
    out_polygons = os.path.join(output_dir, f"{stem}.shp")

    segments = read_pol_file(input_file, quiet)
    if not segments:
        raise RuntimeError("No pol polygons were parsed from the input file.")

    polygons = create_polygons(segments, quiet)
    gdf = create_geodataframe(segments, polygons, ext, crs)

    _print(f"Saving polygon shapefile: {out_polygons}", quiet)
    gdf.to_file(out_polygons)

    if not quiet:
        bounds = gdf.total_bounds
        print("\n=== SUMMARY ===")
        print(f"  Input:  {input_file}")
        print(f"  Output: {out_polygons}")
        print(f"  Polygons: {len(gdf):,}")
        print(f"  Total points: {gdf['NUM_POINTS'].sum():,}")
        print(f"  CRS: {gdf.crs}")
        print(f"  Area — min: {gdf['AREA'].min():.2f}  max: {gdf['AREA'].max():.2f}  "
              f"total: {gdf['AREA'].sum():.2f}")
        print(f"  Perimeter — min: {gdf['PERIMETER'].min():.2f}  max: {gdf['PERIMETER'].max():.2f}")
        print(f"  X: {bounds[0]:.2f} to {bounds[2]:.2f}")
        print(f"  Y: {bounds[1]:.2f} to {bounds[3]:.2f}")

    return out_polygons


def _print(msg, quiet=False):
    if not quiet:
        print(msg)


def main():
    parser = argparse.ArgumentParser(
        prog='pol2shp',
        description='Convert a Delft3D/D-Flow FM .pol file to a polygon ESRI Shapefile',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pol2shp -i POL_001.pol
  pol2shp -i POL_001.pol -of OUTPUT_DIR --crs EPSG:4326
  pol2shp -i POL_001.pol -q
  pol2shp -if INPUT_DIR -of OUTPUT_DIR
        """,
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '-i', '--input',
        metavar='FILE',
        help='Path to the pol file (.pol)',
    )
    input_group.add_argument(
        '-if', '--input-folder',
        metavar='DIR',
        help='Path to a folder of .pol files; all are converted to the output folder',
    )
    parser.add_argument(
        '-of', '--output-folder',
        dest='output_dir',
        default='SHP_POLYGONS',
        metavar='DIR',
        help='Output directory for the shapefile (default: SHP_POLYGONS)',
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
            if os.path.splitext(f)[1].lower() == '.pol'
        )
        if not input_files:
            print(f"Error: No .pol files found in '{args.input_folder}'.",
                  file=sys.stderr)
            sys.exit(1)

        errors = 0
        for fname in input_files:
            input_file = os.path.join(args.input_folder, fname)
            try:
                out_path = pol_to_shp(
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

    if os.path.splitext(args.input)[1].lower() != '.pol':
        print(f"Error: Unsupported file type '{args.input}'. Expected .pol.",
              file=sys.stderr)
        sys.exit(1)

    try:
        out_path = pol_to_shp(
            args.input, args.output_dir, args.crs, args.quiet
        )
        if args.quiet:
            print(out_path)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
