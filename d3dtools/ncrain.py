"""
Generate a NetCDF file from rainfall data and thiessen polygon shapefile.
"""
import os
import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio as rio
import shutil
from netCDF4 import Dataset
import pyproj
from datetime import datetime

# Add GDAL exception handling
try:
    from osgeo import gdal
    gdal.UseExceptions()  # Explicitly enable exceptions
except ImportError:
    pass  # Skip if GDAL is not installed


def generate(input_shp_folder='SHP',
             input_tab_folder='TAB',
             output_nc_folder='NC',
             intermediate_ras_folder='RAS_RAIN',
             intermediate_shp_folder='SHP_RAIN',
             clean_intermediate=True,
             raster_resolution=320,
             rainfall_file=None,
             verbose=False):
    """
        Generate a NetCDF file from rainfall data and thiessen polygon shapefile.

        Parameters:
        -----------
        input_shp_folder : str
            Path to the folder containing input shapefiles (default: 'SHP')
        input_tab_folder : str
            Path to the folder containing input tabular data (CSV files) (default: 'TAB')
        output_nc_folder : str
            Path to the folder where NetCDF output will be saved (default: 'NC')
        intermediate_ras_folder : str
            Path to the folder where intermediate raster files will be saved (default: 'RAS_RAIN')
        intermediate_shp_folder : str
            Path to the folder where intermediate shapefile files will be saved (default: 'SHP_RAIN')
        clean_intermediate : bool
            Whether to clean up intermediate files after processing (default: True)
        raster_resolution : float
            Resolution of the raster in meters (default: 320)
        rainfall_file : str
            Specific CSV file to process (default: None, which means the first CSV file will be processed)
        verbose : bool
            Whether to print detailed information (default: False)

        Returns:
        --------
        str
            Path to the generated NetCDF file
        """

    # Create output directories if they don't exist
    if not os.path.exists(intermediate_ras_folder):
        os.makedirs(intermediate_ras_folder)

    if not os.path.exists(intermediate_shp_folder):
        os.makedirs(intermediate_shp_folder)

    if not os.path.exists(output_nc_folder):
        os.makedirs(output_nc_folder)

    # Read THIESSEN POLYGON shp file
    thiessen = gpd.read_file(f'{input_shp_folder}/THIESSEN.shp')

    # Add field rainfall to thiessen with datatype float
    thiessen['rainfall'] = 0.0

    # Read the rainfall data
    # If a specific file is provided, use that one; otherwise find the first CSV in the folder
    if rainfall_file:
        rainfall_ts = os.path.splitext(rainfall_file)[0]
        if verbose:
            print(f"Processing file: {rainfall_file}")
    else:
        # Find first CSV file in the folder
        for file in os.listdir(input_tab_folder):
            if file.endswith('.csv'):
                rainfall_ts = file[:-4]
                rainfall_file = file
                if verbose:
                    print(f"Processing file: {file}")
                break

    rainfall = pd.read_csv(f'{input_tab_folder}/{rainfall_file}')

    # Convert 'time' filed to datetime
    rainfall['time'] = pd.to_datetime(rainfall['time'])

    # List the stations
    stations = rainfall.columns[1:].to_list()

    # Assign rainfall at time steps to the thiessen polygons
    # when the 'Station' field matches the station name
    for i, timestep in enumerate(rainfall['time']):
        for station in stations:
            for j in range(len(thiessen)):
                if thiessen.iloc[j, 1] == station:
                    # thiessen['rainfall'][j] = rainfall[station][i]
                    thiessen.loc[j, 'rainfall'] = rainfall[station][i]
                    # Save the rainfall data to the shapefile
                    thiessen.to_file(
                        f'{intermediate_shp_folder}/THIESSEN_{i}.shp')
                    # Open the shapefile
                    thiessen = gpd.read_file(
                        f'{intermediate_shp_folder}/THIESSEN_{i}.shp')
                    # Get the extent of the shapefile
                    xmin, ymin, xmax, ymax = thiessen.total_bounds
                    # Get the resolution of the raster
                    res = raster_resolution
                    # Create the raster
                    raster = f'{intermediate_ras_folder}/THIESSEN_{i}.tif'
                    # Create the raster, set nodata value to -999
                    gdal.Rasterize(raster,
                                   f'{intermediate_shp_folder}/THIESSEN_{i}.shp',
                                   format='GTiff',
                                   outputType=gdal.GDT_Float32,
                                   xRes=res,
                                   yRes=res,
                                   attribute='rainfall',
                                   outputSRS=thiessen.crs,
                                   noData=-999)

    # Get timestamps, including hours, minutes, and seconds
    timestamps = rainfall['time'].dt.strftime('%Y-%m-%d %H:%M:%S').to_list()

    # Read the raster file
    with rio.open(f'{intermediate_ras_folder}/THIESSEN_0.tif') as src:
        # get the resolution of the raster
        res = src.res[0]
        # get the extent of the raster
        xmin, ymin, xmax, ymax = src.bounds
        # Find bounds for extend without bounding elements
        xmin = xmin + res
        ymin = ymin + res
        xmax = xmax - res
        ymax = ymax - res

    # Get X coordinates of the raster
    x = np.arange(xmin, xmax, res)
    # Get Y coordinates of the raster
    y = np.arange(ymin, ymax, res)

    # Shift x, y by half of the resolution
    x = x + res / 2
    y = y + res / 2

    lonList = []
    latList = []

    project = pyproj.Transformer.from_crs("epsg:3826", "epsg:4326")

    # convert x to lat
    for i in x:
        lat, lon = project.transform(i, 0)
        lonList.append(lon)

    # convert y to lon
    for i in y:
        lat, lon = project.transform(0, i)
        latList.append(lat)

    # Create mesh grids
    lonM, latM = np.meshgrid(lonList, latList)
    X, Y = np.meshgrid(x, y)

    # Create netcdf file
    try:
        # just to be safe, make sure dataset is not already open.
        ncfile.close()
    except:
        pass

    nc_file_path = f'{output_nc_folder}/{rainfall_ts}.nc'
    ncFile = Dataset(nc_file_path, 'w', format='NETCDF4')

    # Create the dimensions
    x_dim = ncFile.createDimension('x', len(lonList))
    y_dim = ncFile.createDimension('y', len(latList))
    lat_dim = ncFile.createDimension('lat', len(latList))
    lon_dim = ncFile.createDimension('lon', len(lonList))
    time_dim = ncFile.createDimension('time', None)

    # Creating variables
    x2 = ncFile.createVariable('x', np.float64, ('x', ), fill_value=9.96921E36)
    x2.standard_name = "projection_x_coordinate"
    x2.long_name = "x coordinate according to TWD 1997"
    x2.units = "m"
    x2.axis = "X"
    x2[:] = x

    y2 = ncFile.createVariable('y', np.float64, ('y', ), fill_value=9.96921E36)
    y2.standard_name = "projection_y_coordinate"
    y2.long_name = "y coordinate according to TWD 1997"
    y2.units = "m"
    y2.axis = "Y"
    y2[:] = y

    lat2 = ncFile.createVariable('lat',
                                 np.float64, ('y', 'x'),
                                 fill_value=9.96921E36)
    lat2.standard_name = "latitude"
    lat2.long_name = "latitude"
    lat2.units = "degrees_north"
    lat2[:] = latM

    lon2 = ncFile.createVariable('lon',
                                 np.float64, ('y', 'x'),
                                 fill_value=9.96921E36)
    lon2.standard_name = "longitude"
    lon2.long_name = "longitude"
    lon2.units = "degrees_east"
    lon2[:] = lonM

    time = ncFile.createVariable('time', np.float64, ('time', ))
    time.standard_name = "time"
    time.long_name = "time"
    time.units = "minutes since 1970-01-01 08:00:00.0 +0800"
    time.axis = "T"

    # Convert timestamp to minutes since 1970-01-01 08:00:00.0 +0800
    timestamp = [datetime.strptime(i, '%Y-%m-%d %H:%M:%S') for i in timestamps]
    time[:] = [(i - datetime(1970, 1, 1, 8, 0, 0)).total_seconds() / 60
               for i in timestamp]

    # CRS
    crs = ncFile.createVariable('crs', np.int32)
    crs.long_name = "coordinate reference system"
    crs.crs_wkt = "PROJCS[\"TWD97 / TM2 zone 121\", \r\n  GEOGCS[\"TWD97\", \r\n    DATUM[\"Taiwan Datum 1997\", \r\n      SPHEROID[\"GRS 1980\", 6378137.0, 298.257222101, AUTHORITY[\"EPSG\",\"7019\"]], \r\n      TOWGS84[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], \r\n      AUTHORITY[\"EPSG\",\"1026\"]], \r\n    PRIMEM[\"Greenwich\", 0.0, AUTHORITY[\"EPSG\",\"8901\"]], \r\n    UNIT[\"degree\", 0.017453292519943295], \r\n    AXIS[\"Geodetic longitude\", EAST], \r\n    AXIS[\"Geodetic latitude\", NORTH], \r\n    AUTHORITY[\"EPSG\",\"3824\"]], \r\n  PROJECTION[\"Transverse_Mercator\"], \r\n  PARAMETER[\"central_meridian\", 121.00000000000001], \r\n  PARAMETER[\"latitude_of_origin\", 0.0], \r\n  PARAMETER[\"scale_factor\", 0.9999], \r\n  PARAMETER[\"false_easting\", 250000.0], \r\n  PARAMETER[\"false_northing\", 0.0], \r\n  UNIT[\"m\", 1.0], \r\n  AXIS[\"Easting\", EAST], \r\n  AXIS[\"Northing\", NORTH], \r\n  AUTHORITY[\"EPSG\",\"3826\"]]"
    crs.epsg_code = "EPSG:3826"
    # set crs value to 0
    crs[:] = 0

    # Create the grid mapping variable
    rainfall = ncFile.createVariable('rainfall',
                                     np.float32, ('time', 'y', 'x'),
                                     fill_value=-999.0)
    rainfall.long_name = "rainfall"
    rainfall.units = "mm"
    rainfall.coordinates = "lat lon"
    rainfall.grid_mapping = "crs"

    # Write rainfall data to the netcdf file
    data_arr = np.zeros((len(timestamp), len(latList), len(lonList)))

    for i in range(len(timestamp)):
        with rio.open(f'{intermediate_ras_folder}/THIESSEN_{i}.tif') as src:
            data_arr_tmp = src.read(1)
            # Get raster data without bounding elements
            data_arr_tmp = data_arr_tmp[1:-1, 1:-1]
            # flip in y direction
            data_arr[i, :, :] = np.flipud(data_arr_tmp)

    rainfall[:] = data_arr

    # Close the netcdf file
    ncFile.close()

    # Remove intermediate files if requested
    if clean_intermediate:
        try:
            shutil.rmtree(intermediate_ras_folder)
            shutil.rmtree(intermediate_shp_folder)
        except Exception as e:
            print(f"Warning: Could not clean up intermediate files: {e}")

    if verbose:
        print(f"NetCDF file generated at: {nc_file_path}")

    return nc_file_path


def generate_all(input_shp_folder='SHP',
                 input_tab_folder='TAB',
                 output_nc_folder='NC',
                 intermediate_ras_folder='RAS_RAIN',
                 intermediate_shp_folder='SHP_RAIN',
                 clean_intermediate=True,
                 raster_resolution=320,
                 verbose=False):
    """
      Generate NetCDF files from all rainfall data CSV files in the input folder.

      Parameters are the same as the generate() function except the rainfall_file parameter.

      Returns:
      --------
      list
          Paths to all generated NetCDF files
      """
    nc_files = []

    # Get all CSV files in the input folder
    csv_files = [f for f in os.listdir(input_tab_folder) if f.endswith('.csv')]

    if not csv_files:
        print(f"No CSV files found in {input_tab_folder}")
        return []

    if verbose:
        print(f"Found {len(csv_files)} CSV files to process")

    # Process each CSV file
    for i, csv_file in enumerate(csv_files):
        if verbose:
            print(f"Processing file {i+1}/{len(csv_files)}: {csv_file}")

        # Create unique intermediate folders for each CSV to avoid conflicts
        curr_ras_folder = f"{intermediate_ras_folder}_{i}"
        curr_shp_folder = f"{intermediate_shp_folder}_{i}"

        # Generate NetCDF file for this CSV
        nc_file = generate(
            input_shp_folder=input_shp_folder,
            input_tab_folder=input_tab_folder,
            output_nc_folder=output_nc_folder,
            intermediate_ras_folder=curr_ras_folder,
            intermediate_shp_folder=curr_shp_folder,
            clean_intermediate=clean_intermediate,
            raster_resolution=raster_resolution,
            rainfall_file=csv_file,
            verbose=verbose
        )

        nc_files.append(nc_file)

    if verbose:
        print(f"Completed processing {len(nc_files)} files")

    return nc_files


def main():
    """
      Command line entry point for the ncrain tool.      Example usage:
          ncrain --shp-folder SHP --tab-folder TAB --nc-folder NC --resolution 320
      """
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate NetCDF files from rainfall data and thiessen polygon shapefiles",
        epilog='''
examples:
  %(prog)s                           # Process all CSV files in the input folder
  %(prog)s --single rainfall.csv     # Process only a specific CSV file
  %(prog)s --verbose                 # Display additional processing information
  %(prog)s --no-clean                # Keep intermediate files
  %(prog)s --shp-folder custom/SHP --tab-folder custom/TAB --nc-folder custom/NC
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('--shp-folder', dest='input_shp_folder', default='SHP',
                        help='Path to the folder containing input shapefiles (default: SHP)')
    parser.add_argument('--tab-folder', dest='input_tab_folder', default='TAB',
                        help='Path to the folder containing input tabular data (CSV files) (default: TAB)')
    parser.add_argument('--nc-folder', dest='output_nc_folder', default='NC',
                        help='Path to the folder where NetCDF output will be saved (default: NC)')
    parser.add_argument('--ras-folder', dest='intermediate_ras_folder', default='RAS_RAIN',
                        help='Path to the folder where intermediate raster files will be saved (default: RAS_RAIN)')
    parser.add_argument('--tmp-shp-folder', dest='intermediate_shp_folder', default='SHP_RAIN',
                        help='Path to the folder where intermediate shapefile files will be saved (default: SHP_RAIN)')
    parser.add_argument('--no-clean', dest='clean_intermediate', action='store_false',
                        help='Do not clean up intermediate files after processing')
    parser.add_argument('--resolution', dest='raster_resolution', type=float, default=320,
                        help='Resolution of the raster in meters (default: 320)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Display additional information during processing')
    parser.add_argument('--single', dest='single_file',
                        help='Process only a specific CSV file instead of all files')

    args = parser.parse_args()

    # Print arguments if verbose
    if args.verbose:
        print("Processing with parameters:")
        for arg, value in vars(args).items():
            print(f"  {arg}: {value}")

    # Process a single file or all files based on arguments
    if args.single_file:
        result = generate(
            input_shp_folder=args.input_shp_folder,
            input_tab_folder=args.input_tab_folder,
            output_nc_folder=args.output_nc_folder,
            intermediate_ras_folder=args.intermediate_ras_folder,
            intermediate_shp_folder=args.intermediate_shp_folder,
            clean_intermediate=args.clean_intermediate,
            raster_resolution=args.raster_resolution,
            rainfall_file=args.single_file,
            verbose=args.verbose
        )

        if args.verbose:
            print(f"Completed processing: {result}")
    else:
        # Process all CSV files in the folder
        results = generate_all(
            input_shp_folder=args.input_shp_folder,
            input_tab_folder=args.input_tab_folder,
            output_nc_folder=args.output_nc_folder,
            intermediate_ras_folder=args.intermediate_ras_folder,
            intermediate_shp_folder=args.intermediate_shp_folder,
            clean_intermediate=args.clean_intermediate,
            raster_resolution=args.raster_resolution,
            verbose=args.verbose
        )

        if args.verbose:
            print(f"Completed processing {len(results)} files")


if __name__ == "__main__":
    main()
