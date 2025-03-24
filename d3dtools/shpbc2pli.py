"""
Convert boundary line shapefile to *.pli file

This module can be executed using either 'shpbc2pli' or 'shp2pli' command.
"""
import os
import argparse
from . import utils


def convert(input_folder='SHP_BC', output_folder='PLI_BC', id_field=None):
    """
    Convert boundary line shapefile to *.pli file
    Attribute table must contain 'ID', 'Id', 'id', or 'iD' field for boundary name

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing shapefiles with MultiLineString geometry (default: 'SHP_BC')
    output_folder : str
        Path to the output folder for PLI files (default: 'PLI_BC')
    id_field : str, optional
        Name of the field to use for boundary names. If None, will look for 'ID', 'Id', 'id', or 'iD'
    """
    # Find and load shapefiles
    fileList = utils.find_shapefiles(input_folder)
    gdfs = utils.read_shapefiles(fileList)

    # Extract geometries and boundary names
    ref_wkts = utils.extract_geometries(gdfs)
    bcNames = utils.get_boundary_names(gdfs, id_field)

    # Create output folder if needed
    utils.ensure_output_folder(output_folder)

    # Process and write PLI files
    file_count = 0
    for i, ref_wkt in enumerate(ref_wkts):
        for j, item in enumerate(ref_wkt):
            boundary_name = bcNames[i][j]
            points = utils.extract_points_from_wkt(item)
            filepath = f'{output_folder}/{boundary_name}.pli'

            if utils.write_boundary_file(filepath, boundary_name, points):
                file_count += 1

    print(f'Done! Generated {file_count} PLI files in {output_folder}')
    return file_count


def main():
    """
    Command line entry point
    """
    parser = argparse.ArgumentParser(description='Convert boundary line shapefile to *.pli file')
    parser.add_argument('-i', '--input', default='SHP_BC', help='Input folder path (default: SHP_BC)')
    parser.add_argument('-o', '--output', default='PLI_BC', help='Output folder path (default: PLI_BC)')
    parser.add_argument('--id_field', help='Name of the field to use for boundary names (default: looks for ID/Id/id/iD)')

    args = parser.parse_args()

    convert(input_folder=args.input, output_folder=args.output, id_field=args.id_field)


if __name__ == "__main__":
    main()