"""
Convert block polygon shapefile to *.pol file

This module can be executed using either 'shpblock2pol' or 'shp2pol' command.
"""
import numpy as np
import pandas as pd
import geopandas as gpd
from glob import glob
import os
import shapely.wkt
import matplotlib.pyplot as plt
from shapely.geometry import Point, LineString
import argparse


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
    # Read shapefile data
    blockList = glob(f'{input_folder}/*.shp')
    blockNameList = [
        os.path.basename(block).split('.')[0] for block in blockList
    ]

    # Extract wkt from blocks and convert to pol
    for i, block in enumerate(blockList):
        # Read block
        blockGdf = gpd.read_file(block)
        # Remove data without geometry
        blockGdf = blockGdf[blockGdf['geometry'].notnull()]
        # Assign id (serial number) to each feature in block
        blockGdf['id'] = range(1, len(blockGdf) + 1)
        # Get blockName
        blockName = blockGdf['id'].values
        print('Processing block: {}'.format(blockNameList[i]))

        # If output folder does not exist, create it
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        try:
            with open(f'{output_folder}/{blockNameList[i]}.pol', 'w') as f:
                for j, geom in enumerate(blockGdf['geometry'].values):
                    # Collect individual polygons from Polygon or MultiPolygon
                    if geom.geom_type == 'MultiPolygon':
                        parts = list(geom.geoms)
                    else:
                        parts = [geom]

                    for k, part in enumerate(parts):
                        coords = list(part.exterior.coords)
                        label = '{}'.format(blockName[j]) if len(parts) == 1 \
                            else '{}_{}'.format(blockName[j], k + 1)
                        f.write('{}\n'.format(label))
                        f.write('{} {}\n'.format(len(coords), 2))
                        for x, y in coords:
                            f.write(f'{x:.3f} {y:.3f}\n')
        except:
            print('Error in block: {}'.format(blockNameList[i]))

    return len(blockList)


def main():
    """
    Command line entry point
    """
    # Set up argument parser
    parser = argparse.ArgumentParser(
        description='Convert shapefile blocks to *.pol files',
        epilog='''
examples:
  %(prog)s                                # Use default folders (SHP_BLOCK -> POL_BLOCK)
  %(prog)s -i SHP_BLOCK -o POL_BLOCK
  %(prog)s --input my_blocks --output my_polygons
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '-i',
        '--input',
        default='SHP_BLOCK',
        help='Input folder containing shapefiles (default: SHP_BLOCK)')
    parser.add_argument(
        '-o',
        '--output',
        default='POL_BLOCK',
        help='Output folder for .pol files (default: POL_BLOCK)')

    # Parse arguments
    args = parser.parse_args()

    # Run conversion with provided arguments
    num_converted = convert(input_folder=args.input, output_folder=args.output)

    print(f"Conversion complete: {num_converted} shapefiles processed")


if __name__ == "__main__":
    main()
