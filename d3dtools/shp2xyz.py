"""
Convert point shapefile to *.xyz file

This module can be executed using either 'shpxyz2xyz' or 'shp2xyz' command.
"""
import os
import argparse
from . import utils
import geopandas as gpd


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
    gdfs = utils.read_shapefiles(fileList)

    # Create output folder if needed
    utils.ensure_output_folder(output_folder)

    # Process and write XYZ files
    file_count = 0
    for i, gdf in enumerate(gdfs):
        # Ensure we have Point geometries
        if not all(geom.geom_type == 'Point' for geom in gdf.geometry):
            print(
                f"Warning: Skipping file {os.path.basename(fileList[i])} - not all geometries are points"
            )
            continue

        # Get the Z field if specified or try to find common elevation field names
        if z_field and z_field in gdf.columns:
            z_column = z_field
        else:
            # Check for common Z field names
            possible_z_fields = [
                'Z', 'z', 'ELEVATION', 'elevation', 'HEIGHT', 'height', 'DEPTH',
                'depth', 'ELEV', 'elev', 'DEP', 'dep'
            ]
            found_z_field = None

            for field in possible_z_fields:
                if field in gdf.columns:
                    found_z_field = field
                    break

            if found_z_field:
                z_column = found_z_field
            else:
                print(
                    f"Warning: No Z field found in {os.path.basename(fileList[i])}. Please specify using z_field parameter."
                )
                continue

        # Get base filename without extension
        base_filename = os.path.splitext(os.path.basename(fileList[i]))[0]
        output_filename = f"{output_folder}/{base_filename}.xyz"

        # Write XYZ file
        with open(output_filename, 'w', encoding='utf-8') as f:
            for idx, row in gdf.iterrows():
                x, y = row.geometry.x, row.geometry.y
                z = row[z_column]
                f.write(f"{x:20.6e} {y:20.6e} {z:20.6e}\n")
        print(f"Created {output_filename}")
        file_count += 1

    print(f'Done! Generated {file_count} XYZ files in {output_folder}')
    return file_count


def main():
    """
      Command line entry point
      """
    parser = argparse.ArgumentParser(
        description='Convert point shapefile to *.xyz file')
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
