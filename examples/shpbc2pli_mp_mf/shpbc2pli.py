"""
Convert boundary line shapefile to *.pli file

This module can be executed using either 'shpbc2pli' or 'shp2pli' command.
"""
import os
import glob
import geopandas as gpd
import argparse
# from . import utils
import utils


def convert(input_folder='SHP_BC', output_folder='PLI_BC'):
    """
    Convert boundary line shapefile to *.pli file

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing shapefiles with LineString/MultiLineString geometry (default: 'SHP_BC')
    output_folder : str
        Path to the output folder for PLI files (default: 'PLI_BC')
    """
    fileList = glob.glob(f'{input_folder}/*.shp')
    print(f"Found {len(fileList)} shapefiles in {input_folder}")

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
                label = str(names[j]) if len(parts) == 1 else f'{names[j]}_{k+1}'
                coords = list(part.coords)
                out_path = f'{output_folder}/{label}.pli'
                if utils.write_boundary_file(out_path, label, coords):
                    file_count += 1

    print(f'Done! Generated {file_count} PLI files in {output_folder}')
    return file_count


def main():
    """
    Command line entry point
    """
    parser = argparse.ArgumentParser(
        description='Convert boundary line shapefile to *.pli file',
        epilog='''
examples:
  %(prog)s                               # Use default folders (SHP_BC -> PLI_BC)
  %(prog)s -i custom/SHP_BC -o custom/PLI_BC
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i', '--input', default='SHP_BC', help='Input folder path (default: SHP_BC)')
    parser.add_argument('-o', '--output', default='PLI_BC', help='Output folder path (default: PLI_BC)')
    args = parser.parse_args()

    convert(input_folder=args.input, output_folder=args.output)


if __name__ == "__main__":
    main()