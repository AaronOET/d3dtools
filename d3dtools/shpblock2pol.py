"""
Convert block polygon shapefile to *.pol file

This module can be executed using either 'shpblock2pol' or 'shp2pol' command.
"""
import os
import geopandas as gpd
import argparse
from . import utils


def convert(input_folder='SHP_BLOCK', output_folder='POL_BLOCK'):
    """
    Convert shapefile blocks to *.pol files

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing shapefiles (default: 'SHP_BLOCK')
    output_folder : str
        Path to the folder where .pol files will be saved (default: 'POL_BLOCK')
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
        # Remove data without geometry
        gdf = gdf[gdf['geometry'].notnull()]
        # Assign id (serial number) to each feature in block
        gdf['id'] = range(1, len(gdf) + 1)
        blockName = gdf['id'].values
        print(f'Processing: {base}')

        try:
            out_path = f'{output_folder}/{base}.pol'
            with open(out_path, 'w', encoding='utf-8') as f:
                for j, geom in enumerate(gdf['geometry'].values):
                    # Collect individual polygons from Polygon or MultiPolygon
                    if geom.geom_type == 'MultiPolygon':
                        parts = list(geom.geoms)
                    else:
                        parts = [geom]

                    for k, part in enumerate(parts):
                        coords = list(part.exterior.coords)
                        label = f'{blockName[j]}' if len(parts) == 1 \
                            else f'{blockName[j]}_{k+1}'
                        f.write(f'{label}\n')
                        f.write(f'{len(coords)} {2}\n')
                        for x, y in coords:
                            f.write(f'{x:.3f} {y:.3f}\n')
            file_count += 1
        except Exception as e:
            print(f'Error in block {base}: {e}')

    print(f'Done! Generated {file_count} POL files in {output_folder}')
    return file_count


def main():
    """
    Command line entry point
    """
    parser = argparse.ArgumentParser(
        description='Convert shapefile blocks to *.pol files',
        epilog='''
examples:
  %(prog)s                                # Use default folders (SHP_BLOCK -> POL_BLOCK)
  %(prog)s -i SHP_BLOCK -of POL_BLOCK
  %(prog)s --input my_blocks --output-folder my_polygons
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '-i',
        '--input',
        default='SHP_BLOCK',
        help='Input folder containing shapefiles (default: SHP_BLOCK)')
    parser.add_argument(
        '-of',
        '--output-folder',
        default='POL_BLOCK',
        help='Output folder for .pol files (default: POL_BLOCK)')

    args = parser.parse_args()

    convert(input_folder=args.input, output_folder=args.output_folder)


if __name__ == "__main__":
    main()
