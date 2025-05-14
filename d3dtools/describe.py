#!/usr/bin/env python
"""
Command-line utility to display descriptions of d3dtools functionality.
"""

import argparse
import sys
import webbrowser
from textwrap import dedent

TOOL_DESCRIPTIONS = {
    'evaluate':
    """
        Calculate flood simulation accuracy and catch rate metrics.

        This tool compares simulated flood extent shapefiles with observed flood extent
        shapefiles to calculate accuracy and catch rate metrics, which are important for
        validating flood model performance.

        Examples:
            evaluate --sim SHP/SIM.shp --obs SHP/OBS.shp
            evaluate --sim SHP/SIM.shp --obs SHP/OBS.shp --output results.csv
    """,
    'sensor':
    """
        Extract time series data at observation points from Delft3D FM NetCDF files.

        This tool extracts water depth or other parameters at specified observation points
        from Delft3D FM NetCDF output files and exports the results to CSV and/or Excel.

        Examples:
            sensor --nc-file results.nc --obs-shp observation_points.shp
            sensor --nc-file results.nc --obs-shp points.shp --output-csv depth.csv
            sensor --nc-file results.nc --obs-shp points.shp --plot --verbose
    """,
    'ncrain':
    """
        Generate a NetCDF file from rainfall data and thiessen polygon shapefiles.

        This tool processes rainfall data in CSV format along with thiessen polygon
        shapefiles to create NetCDF files for use in Delft3D modeling.

        Note: Currently only works for Taiwan data in EPSG:3826 projection.

        Examples:
            ncrain                      # Process all CSV files in the input folder
            ncrain --single rainfall.csv  # Process only a specific CSV file
            ncrain --verbose            # Display additional processing information
            ncrain --no-clean           # Keep intermediate files
    """,
    'snorain':
    """
        Process rainfall scenario data and generate time series CSV files.

        This tool processes rainfall scenario data from CSV files and generates
        time series files for use in modeling.

        Examples:
            snorain -i rainfall_scenarios.csv -o custom/TAB
            snorain --input rainfall_scenarios.csv --output custom/TAB --verbose
    """,
    'shp2ldb':
    """
        Convert boundary line shapefiles to LDB files for Delft3D.

        This tool converts boundary line shapefiles to the LDB format used in
        Delft3D modeling.

        Examples:
            shp2ldb
            shp2ldb -i custom/SHP_LDB -o custom/LDB
            shp2ldb --id_field BoundaryName
    """,
    'shpbc2pli':
    """
        Convert boundary line shapefiles to PLI files for Delft3D.

        This tool converts boundary line shapefiles to the PLI format used in
        Delft3D modeling.

        Examples:
            shpbc2pli
            shpbc2pli -i custom/SHP_BC -o custom/PLI_BC
            shpbc2pli --id_field BoundaryName
    """,
    'shp2pli':
    """
        Alias for shpbc2pli. Convert boundary line shapefiles to PLI files.

        Examples:
            shp2pli
            shp2pli -i custom/SHP_BC -o custom/PLI_BC
    """,
    'shpblock2pol':
    """
        Convert shapefile blocks to POL files for Delft3D.

        This tool converts polygon shapefiles to the POL format used in
        Delft3D modeling.

        Examples:
            shpblock2pol
            shpblock2pol -i custom/SHP_BLOCK -o custom/POL_BLOCK
    """,
    'shp2pol':
    """
        Alias for shpblock2pol. Convert shapefile blocks to POL files.

        Examples:
            shp2pol
            shp2pol -i custom/SHP_BLOCK -o custom/POL_BLOCK
    """,
    'shpdike2pliz':
    """
        Convert bankline shapefiles to PLIZ files for Delft3D.

        This tool converts bankline/dike shapefiles to the PLIZ format used in
        Delft3D modeling.

        Examples:
            shpdike2pliz
            shpdike2pliz -i custom/SHP_DIKE -o custom/PLIZ_DIKE
            shpdike2pliz --id_field DikeName
    """,
    'shp2pliz':
    """
        Alias for shpdike2pliz. Convert bankline shapefiles to PLIZ files.

        Examples:
            shp2pliz
            shp2pliz -i custom/SHP_DIKE -o custom/PLIZ_DIKE
    """,
    'shp2xyz':
    """
        Convert point shapefiles to XYZ files.

        This tool converts point shapefiles to XYZ files for use in Delft3D modeling.

        Examples:
            shp2xyz
            shp2xyz -i custom/SHP_SAMPLE -o custom/XYZ_SAMPLE
            shp2xyz --z_field ELEVATION
    """
}


def main():
    """Main function to display tool descriptions."""
    parser = argparse.ArgumentParser(
        description='Display descriptions of d3dtools functionality.')

    parser.add_argument('tool',
                        nargs='?',
                        choices=[*TOOL_DESCRIPTIONS.keys(), 'all'],
                        default='all',
                        help='The specific tool to describe (default: all)')

    parser.add_argument('--version',
                        action='store_true',
                        help='Show the d3dtools version')

    parser.add_argument('--pypi',
                        action='store_true',
                        help='Open the d3dtools PyPI project page in a web browser')

    args = parser.parse_args()

    if args.version:
        from . import __version__
        print(f"d3dtools version {__version__}")
        return

    if args.pypi:
        print("Opening d3dtools PyPI project page in your web browser...")
        webbrowser.open("https://pypi.org/project/d3dtools/")
        return

    if args.tool == 'all':
        print(
            "D3DTOOLS - A collection of tools for working with shapefiles and converting them for Delft3D modeling.\n"
        )
        print("Available tools:\n")

        for tool, description in TOOL_DESCRIPTIONS.items():
            short_desc = description.strip().split('\n')[0]
            print(f"  {tool:<12} - {short_desc}")

        print(
            "\nUse 'd3dtools-info <tool_name>' to get detailed information about a specific tool."
        )
        print(
            "Use '<tool_name> --help' to see command-line options for each tool.")
    else:
        print(dedent(TOOL_DESCRIPTIONS[args.tool]).strip())
        print(f"\nUse '{args.tool} --help' to see all command-line options.\n")


if __name__ == "__main__":
    main()
