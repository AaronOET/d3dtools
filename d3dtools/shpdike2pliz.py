"""
Convert dike line shapefile to *.pli file with z values

This module can be executed using either 'shpdike2pliz' or 'shp2pliz' command.
"""
import os
import geopandas as gpd
import argparse
from . import utils


def convert(input_folder='SHP_DIKE',
            output_folder='PLIZ_DIKE',
            output_filename='Dike'):
    """
    Convert shapefile to DIKE PLIZ file

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing dike shapefiles with MultiLineStringZ geometry (default: 'SHP_DIKE')
    output_folder : str
        Output folder path (default: 'PLIZ_DIKE')
    output_filename : str
        Name of the output file without extension (default: 'Dike')

    Returns:
    --------
    str
        Path to the created PLIZ file, or None if no input shapefiles were found
    """
    fileList = utils.find_shapefiles(input_folder)
    if not fileList:
        print(f'No shapefiles found in {input_folder}. Nothing to do.')
        return None

    utils.ensure_output_folder(output_folder)

    output_path = os.path.join(output_folder, f"{output_filename}.pliz")
    with open(output_path, 'w', encoding='utf-8') as f:
        for filepath in fileList:
            base = os.path.splitext(os.path.basename(filepath))[0]
            gdf = gpd.read_file(filepath)
            gdf = gdf[gdf['geometry'].notnull()]
            print(f'Processing: {base}')

            names = [f'{base}_{j+1}' for j in range(len(gdf))]

            for j, geom in enumerate(gdf['geometry'].values):
                if geom.geom_type in ('MultiLineString', 'MultiLineStringZ'):
                    parts = list(geom.geoms)
                else:
                    parts = [geom]

                for k, part in enumerate(parts):
                    label = str(
                        names[j]) if len(parts) == 1 else f'{names[j]}_{k+1}'
                    coords = list(part.coords)
                    f.write(f'{label}\n')
                    f.write(f'{len(coords)} 5\n')
                    for coord in coords:
                        z = coord[2] if len(coord) > 2 else 0.0
                        f.write(
                            f'{coord[0]:.6f} {coord[1]:.6f} {z:.3f} {z:.3f} {z:.3f}\n'
                        )
                    f.write('\n')

    # print(f'Done! Generated PLIZ file at {output_path}')
    print(f'Done! Generated PLIZ file in {output_folder}')
    return output_path


def main():
    """
    Command line entry point
    """
    parser = argparse.ArgumentParser(
        description='Convert bankline shapefile to PLIZ file',
        epilog='''
examples:
  %(prog)s                                # Use default folders (SHP_DIKE -> PLIZ_DIKE)
  %(prog)s -i SHP_DIKE -of PLIZ_DIKE
  %(prog)s -i SHP_DIKE -of PLIZ_DIKE -f MyDike
  %(prog)s --id_field DikeName
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i',
                        '--input',
                        default='SHP_DIKE',
                        help='Input folder path (default: SHP_DIKE)')
    parser.add_argument('-of',
                        '--output-folder',
                        default='PLIZ_DIKE',
                        help='Output folder path (default: PLIZ_DIKE)')
    parser.add_argument(
        '-f',
        '--filename',
        default='Dike',
        help='Output filename without extension (default: Dike)')

    args = parser.parse_args()

    convert(input_folder=args.input,
            output_folder=args.output_folder,
            output_filename=args.filename)


if __name__ == "__main__":
    main()
