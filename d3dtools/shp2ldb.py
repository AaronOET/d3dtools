"""
Convert boundary line shapefile to *.ldb file
"""
import os
import argparse
from . import utils


def convert(input_folder='SHP_LDB', output_folder='LDB', id_field=None):
    """
    Convert boundary line shapefile to *.ldb file
    Attribute table must contain 'ID', 'Id', 'id', or 'iD' field for boundary name

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing shapefiles with MultiLineString geometry (default: 'SHP_LDB')
    output_folder : str
        Path to the output folder for LDB files (default: 'LDB')
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

    # Process and write LDB files
    file_count = 0
    for i, ref_wkt in enumerate(ref_wkts):
        for j, item in enumerate(ref_wkt):
            boundary_name = bcNames[i][j]
            points = utils.extract_points_from_wkt(item)
            filepath = f'{output_folder}/{boundary_name}.ldb'

            if utils.write_boundary_file(filepath, boundary_name, points):
                file_count += 1

    print(f'Done! Generated {file_count} LDB files in {output_folder}')
    return file_count


def main():
    """
    Command line entry point
    """
    parser = argparse.ArgumentParser(
        description='Convert boundary line shapefile to *.ldb file',
        epilog='''
examples:
  %(prog)s                               # Use default folders (SHP_LDB -> LDB)
  %(prog)s -i custom/SHP_LDB -o custom/LDB
  %(prog)s --id_field BoundaryName
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i', '--input', default='SHP_LDB', help='Input folder path (default: SHP_LDB)')
    parser.add_argument('-o', '--output', default='LDB', help='Output folder path (default: LDB)')
    parser.add_argument('--id_field', help='Name of the field to use for boundary names (default: looks for ID/Id/id/iD)')

    args = parser.parse_args()

    convert(input_folder=args.input, output_folder=args.output, id_field=args.id_field)


if __name__ == "__main__":
    main()