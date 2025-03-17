"""
Convert boundary line shapefile to *.pli file
"""
import os
import glob
import geopandas as gpd


def convert(input_folder='SHP_BC', output_folder='PLI_BC'):
    """
    Convert boundary line shapefile to *.pli file
    Attribute table must contain 'ID', 'Id', 'id', or 'iD' field for boundary name

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing shapefiles with MultiLineString geometry (default: 'SHP_BC')
    output_folder : str
        Path to the output folder for PLI files (default: 'PLI_BC')
    """
    # Specify file source
    fileList = glob.glob(f'{input_folder}/*.shp')
    print(f"Found {len(fileList)} shapefiles in {input_folder}")

    gdfs = []
    for i, item in enumerate(fileList):
        gdf = gpd.read_file(item)
        gdfs.append(gdf)

    # Read wkt
    ref_wkts = []
    for i, gdf in enumerate(gdfs):
        ref_wkt = [g.wkt for g in gdf['geometry'].values]
        ref_wkts.append(ref_wkt)

    print(f"Total features: {len(ref_wkts)}")

    # Get boundary names
    bcNames = []
    for i, gdf in enumerate(gdfs):
        # Check for all possible case variations of the ID field
        possible_id_fields = ['ID', 'Id', 'id', 'iD']
        found_id_field = None

        for id_field in possible_id_fields:
            if id_field in gdf.columns:
                found_id_field = id_field
                break

        if found_id_field:
            bcName = [name for name in gdf[found_id_field].values]
        else:
            raise KeyError(f"No ID field found in the shapefile. Please ensure your shapefile has one of these fields: {possible_id_fields}")

        bcNames.append(bcName)

    # Create output folder if not exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        print(f"Created output folder: {output_folder}")

    # For loop gdfs, create a .pli file with id as its name
    file_count = 0
    for i, ref_wkt in enumerate(ref_wkts):
        for j, item in enumerate(ref_wkt):
            with open(f'{output_folder}/{bcNames[i][j]}.pli', 'w',
                    encoding='utf-8') as f:
                f.write('{}\n'.format(bcNames[i][j]))
                points = [
                    point.split() for point in item.replace(
                        "LINESTRING (", "").replace(")", "").split(',')
                ]
                f.write('{} {}\n'.format(len(points), 2))
                for k, ktem in enumerate(points):
                    f.write(
                        f'{float(ktem[0]):.6f} {float(ktem[1]):.6f} {bcNames[i][j]}_{k+1:0>4}\n'
                    )
                f.write('\n')
            file_count += 1

    print(f'Done! Generated {file_count} PLI files in {output_folder}')
    return file_count


def main():
    """
    Command line entry point
    """
    convert()


if __name__ == "__main__":
    main()