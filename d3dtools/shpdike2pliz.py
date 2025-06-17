"""
Convert dike line shapefile to *.pli file with z values

This module can be executed using either 'shpdike2pliz' or 'shp2pliz' command.
"""
import os
import glob
import geopandas as gpd
import argparse


def convert(input_folder='SHP_DIKE',
            output_folder='PLIZ_DIKE',
            output_filename='Dike',
            id_field=None):
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
    id_field : str, optional
        Name of the field to use for dike names. If None, will look for 'ID', 'Id', 'id', or 'iD'

    Returns:
    --------
    str
        Path to the created PLIZ file
    """
    # Specify file source
    fileList = glob.glob(f'{input_folder}/*.shp')
    print(f"Found {len(fileList)} files: {fileList}")

    # Read files
    gdfs = []
    for i, item in enumerate(fileList):
        gdf = gpd.read_file(item)
        gdfs.append(gdf)

    # Read wkt
    ref_wkts = []
    for i, gdf in enumerate(gdfs):
        ref_wkt = [g.wkt for g in gdf['geometry'].values]
        ref_wkts.append(ref_wkt)

    # Get dike name
    dikeNames = []
    for i, gdf in enumerate(gdfs):
        if id_field and id_field in gdf.columns:
            # Use the user-specified field name
            dikeName = [name for name in gdf[id_field].values]
            print(f"Using '{id_field}' column for dike names in file {i+1}")
        else:
            # Check for all possible case variations of the ID column
            possible_id_columns = ['ID', 'id', 'Id', 'iD']
            dikeName = None

            for col in possible_id_columns:
                if col in gdf.columns:
                    dikeName = [name for name in gdf[col].values]
                    print(f"Using '{col}' column for dike names in file {i+1}")
                    break

            # If no matching column found, raise an error
            if dikeName is None:
                raise ValueError(f"No ID column found in file {i+1}. Expected one of: {possible_id_columns} or specify the column name using id_field parameter")

        dikeNames.append(dikeName)

    # Create output folder if not exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Write to .pliz
    output_path = os.path.join(output_folder, f"{output_filename}.pliz")
    with open(output_path, 'w', encoding='utf-8') as f:
        for k in range(len(gdfs)):
            for i, item in enumerate(ref_wkts[k]):
                f.write('{}\n'.format(dikeNames[k][i]))
                # Remove heading "LINESTRING Z (" and trailing ")" characters using replace
                points = [
                    point.split() for point in item.replace(
                        "LINESTRING Z (", "").replace(")", "").split(',')
                ]
                f.write('{} {}\n'.format(len(points), 5))
                for j, jtem in enumerate(points):
                    f.write('{:.6f} {:.6f} {} {} {}\n'.format(float(jtem[0]),
                                                            float(jtem[1]),
                                                            float(jtem[2]),
                                                            float(jtem[2]),
                                                            float(jtem[2])))
                f.write('\n')
    print(f'Done! PLIZ file created at: {output_path}')
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
  %(prog)s -i custom/SHP_DIKE -o custom/PLIZ_DIKE
  %(prog)s -i SHP_DIKE -o PLIZ_DIKE -f MyDike
  %(prog)s --id_field DikeName
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-i', '--input', default='SHP_DIKE', help='Input folder path (default: SHP_DIKE)')
    parser.add_argument('-o', '--output', default='PLIZ_DIKE', help='Output folder path (default: PLIZ_DIKE)')
    parser.add_argument('-f', '--filename', default='Dike', help='Output filename without extension (default: Dike)')
    parser.add_argument('--id_field', help='Name of the field to use for dike names (default: looks for ID/Id/id/iD)')

    args = parser.parse_args()

    convert(input_folder=args.input, output_folder=args.output, output_filename=args.filename, id_field=args.id_field)


if __name__ == "__main__":
    main()