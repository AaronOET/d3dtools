"""
Convert shapefile blocks to *.pol files
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
    blockNameList = [os.path.basename(block).split('.')[0]
                     for block in blockList]

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
        # Get wkt
        ref_wkt = [g.wkt for g in blockGdf['geometry'].values]
        print('Processing block: {}'.format(blockNameList[i]))

        # If output folder does not exist, create it
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        try:
            with open(f'{output_folder}/{blockNameList[i]}.pol', 'w') as f:
                for j, wkt in enumerate(ref_wkt):
                    points = [
                        point.split() for point in wkt.replace("POLYGON ((", "").replace(
                            "))", "").split(',')
                    ]
                    # Write to *.pol
                    # Write blockName
                    f.write('{}\n'.format(blockName[j]))
                    f.write('{} {}\n'.format(len(points), 2))

                    for point in points:
                        # convert string to float and format
                        f.write(
                            f'{float(point[0]):.3f} {float(point[1]):.3f}\n')
        except:
            print('Error in block: {}'.format(blockNameList[i]))

    return len(blockList)


def main():
    """
    Command line entry point
    """
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Convert shapefile blocks to *.pol files')
    parser.add_argument('-i', '--input', default='SHP_BLOCK',
                        help='Input folder containing shapefiles (default: SHP_BLOCK)')
    parser.add_argument('-o', '--output', default='POL_BLOCK',
                        help='Output folder for .pol files (default: POL_BLOCK)')

    # Parse arguments
    args = parser.parse_args()

    # Run conversion with provided arguments
    num_converted = convert(input_folder=args.input, output_folder=args.output)

    print(f"Conversion complete: {num_converted} shapefiles processed")


if __name__ == "__main__":
    main()