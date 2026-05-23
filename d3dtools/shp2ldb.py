"""
Convert boundary line shapefile to *.ldb file
"""
import os
import geopandas as gpd
import argparse
from . import utils


def convert(input_folder='SHP_LDB', output_folder='LDB'):
    """
    Convert boundary line shapefile to *.ldb file

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing shapefiles with LineString/MultiLineString geometry (default: 'SHP_LDB')
    output_folder : str
        Path to the output folder for LDB files (default: 'LDB')
    """
    fileList = utils.find_shapefiles(input_folder)
    if not fileList:
        print(f'No shapefiles found in {input_folder}. Nothing to do.')
        return 0

    utils.ensure_output_folder(output_folder)

    file_count = 0
    for filepath in fileList:
        base = os.path.splitext(os.path.basename(filepath))[0]
        gdf = gpd.read_file(filepath)
        gdf = gdf[gdf['geometry'].notnull()]
        print(f'Processing: {base}')

        names = [f'{base}_{j+1}' for j in range(len(gdf))]

        for j, geom in enumerate(gdf['geometry'].values):
            # Split MultiLineString into individual parts
            if geom.geom_type in ('MultiLineString', 'MultiLineStringZ'):
                parts = list(geom.geoms)
            else:
                parts = [geom]

            for k, part in enumerate(parts):
                label = str(
                    names[j]) if len(parts) == 1 else f'{names[j]}_{k+1}'
                coords = list(part.coords)
                out_path = f'{output_folder}/{label}.ldb'
                if utils.write_boundary_file(out_path, label, coords):
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
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i',
                        '--input',
                        default='SHP_LDB',
                        help='Input folder path (default: SHP_LDB)')
    parser.add_argument('-o',
                        '--output',
                        default='LDB',
                        help='Output folder path (default: LDB)')

    args = parser.parse_args()

    convert(input_folder=args.input, output_folder=args.output)


if __name__ == "__main__":
    main()
