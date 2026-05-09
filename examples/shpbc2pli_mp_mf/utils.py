"""
Utility functions for D3D tools
"""
import os
import glob
import geopandas as gpd


def find_shapefiles(input_folder):
    """
    Find shapefiles in the input folder

    Parameters:
    -----------
    input_folder : str
        Path to the folder containing shapefiles

    Returns:
    --------
    list
        List of shapefile paths
    """
    fileList = glob.glob(f'{input_folder}/*.shp')
    print(f"Found {len(fileList)} shapefiles in {input_folder}")
    return fileList


def read_shapefiles(fileList):
    """
    Read shapefiles and return a list of GeoDataFrames

    Parameters:
    -----------
    fileList : list
        List of shapefile paths

    Returns:
    --------
    list
        List of GeoDataFrames
    """
    gdfs = []
    for item in fileList:
        gdf = gpd.read_file(item)
        gdfs.append(gdf)
    return gdfs


def extract_geometries(gdfs):
    """
    Extract geometries from GeoDataFrames

    Parameters:
    -----------
    gdfs : list
        List of GeoDataFrames

    Returns:
    --------
    list
        List of lists of WKT strings
    """
    ref_wkts = []
    for gdf in gdfs:
        ref_wkt = [g.wkt for g in gdf['geometry'].values]
        ref_wkts.append(ref_wkt)

    print(f"Total features: {sum(len(wkt) for wkt in ref_wkts)}")
    return ref_wkts


def get_boundary_names(gdfs, id_field=None):
    """
    Get boundary names from GeoDataFrames

    Parameters:
    -----------
    gdfs : list
        List of GeoDataFrames
    id_field : str, optional
        Name of the field to use for boundary names

    Returns:
    --------
    list
        List of lists of boundary names
    """
    bcNames = []
    for gdf in gdfs:
        if id_field and id_field in gdf.columns:  # Check if user-specified field exists
            # Use the user-specified field name
            bcName = [name for name in gdf[id_field].values]
        else:
            # Check for all possible case variations of the ID field
            possible_id_fields = ['ID', 'Id', 'id', 'iD']
            found_id_field = None

            for field in possible_id_fields:
                if field in gdf.columns:
                    found_id_field = field
                    break

            if found_id_field:
                bcName = [name for name in gdf[found_id_field].values]
            else:
                raise KeyError(f"No ID field found in the shapefile. Please ensure your shapefile has one of these fields: {possible_id_fields} or specify the field name using id_field parameter")

        bcNames.append(bcName)

    return bcNames


def ensure_output_folder(output_folder):
    """
    Create output folder if it doesn't exist

    Parameters:
    -----------
    output_folder : str
        Path to the output folder
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        print(f"Created output folder: {output_folder}")


def extract_points_from_wkt(wkt_string):
    """
    Extract points from WKT string

    Parameters:
    -----------
    wkt_string : str
        WKT string

    Returns:
    --------
    list
        List of points [x, y]
    """
    return [
        point.split() for point in wkt_string.replace(
            "LINESTRING (", "").replace(")", "").split(',')
    ]


def write_boundary_file(filepath, boundary_name, points, extension=None):
    """
    Write a boundary file (.pli or .ldb)

    Parameters:
    -----------
    filepath : str
        Path to the output file
    boundary_name : str
        Name of the boundary
    points : list
        List of points [x, y]
    extension : str, optional
        File extension to use in the success message (for logging purposes)

    Returns:
    --------
    bool
        True if successful
    """
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f'{boundary_name}\n')
        f.write(f'{len(points)} {2}\n')
        for k, point in enumerate(points):
            f.write(
                f'{float(point[0]):.6f} {float(point[1]):.6f} {boundary_name}_{k+1:0>4}\n'
            )
        f.write('\n')

    return True