"""
Convert point shapefile to *.xyz file

This module can be executed using either 'shpxyz2xyz' or 'shp2xyz' command.
"""
import os
import argparse
from . import utils


def convert(input_folder='SHP_SAMPLE',
            output_folder='XYZ_SAMPLE',
            z_field=None):
    """
    Convert point shapefile to *.xyz file
    Attribute table must contain a Z-value field (default: looks for 'Z', 'z', 'ELEVATION', 'elevation', 'HEIGHT', 'height')

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing shapefiles with Point geometry (default: 'SHP_SAMPLE')
    output_folder : str
        Path to the output folder for XYZ files (default: 'XYZ_SAMPLE')
    z_field : str, optional
        Name of the field to use for Z values. If None, will look for common elevation field names
    """
    # Find and load shapefiles
    fileList = utils.find_shapefiles(input_folder)
    if not fileList:
        print(f'No shapefiles found in {input_folder}. Nothing to do.')
        return 0

    gdfs = utils.read_shapefiles(fileList)

    # Create output folder if needed
    utils.ensure_output_folder(output_folder)

    # Process and write XYZ files
    file_count = 0
    for i, gdf in enumerate(gdfs):
        base = os.path.splitext(os.path.basename(fileList[i]))[0]

        # Ensure we have Point geometries
        if not all(geom.geom_type == 'Point' for geom in gdf.geometry):
            print(
                f"Warning: Skipping {base} - not all geometries are points"
            )
            continue

        # Detect Point Z / Point ZM geometries (shapely exposes Z via has_z)
        use_geom_z = all(geom.has_z for geom in gdf.geometry)

        z_column = None
        if use_geom_z:
            print(f"Detected Point Z/ZM geometry in {base} - using geometry Z values")
        else:
            # Get the Z field if specified or try to find common elevation field names
            if z_field and z_field in gdf.columns:
                z_column = z_field
            else:
                possible_z_fields = [
                    'Z', 'z', 'ELEVATION', 'elevation', 'HEIGHT', 'height', 'DEPTH',
                    'depth', 'ELEV', 'elev', 'DEP', 'dep'
                ]
                for field in possible_z_fields:
                    if field in gdf.columns:
                        z_column = field
                        break

            if z_column is None:
                print(
                    f"Warning: No Z field found in {base}. Please specify using z_field parameter."
                )
                continue

        print(f'Processing: {base}')
        output_filename = f"{output_folder}/{base}.xyz"

        # Write XYZ file
        with open(output_filename, 'w', encoding='utf-8') as f:
            for idx, row in gdf.iterrows():
                x, y = row.geometry.x, row.geometry.y
                z = row.geometry.z if use_geom_z else row[z_column]
                f.write(f"{x:20.6e} {y:20.6e} {z:20.6e}\n")
        file_count += 1

    print(f'Done! Generated {file_count} XYZ files in {output_folder}')
    return file_count


def main():
    """
      Command line entry point
      """
    parser = argparse.ArgumentParser(
        description='Convert point shapefile to *.xyz file',
        epilog='''
examples:
  %(prog)s                                # Use default folders (SHP_SAMPLE -> XYZ_SAMPLE)
  %(prog)s -i custom/SHP_SAMPLE -o custom/XYZ_SAMPLE
  %(prog)s --z_field ELEVATION
  %(prog)s -i points -o xyz_output --z_field HEIGHT
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i',
                        '--input',
                        default='SHP_SAMPLE',
                        help='Input folder path (default: SHP_SAMPLE)')
    parser.add_argument('-o',
                        '--output',
                        default='XYZ_SAMPLE',
                        help='Output folder path (default: XYZ_SAMPLE)')
    parser.add_argument(
        '--z_field',
        help='''Name of the field to use for Z values (default: looks for Z, z, ELEVATION, elevation,
                HEIGHT, height, DEPTH, depth, ELEV, elev, DEP, dep)''',
    )

    args = parser.parse_args()

    convert(input_folder=args.input,
            output_folder=args.output,
            z_field=args.z_field)


if __name__ == "__main__":
    main()
