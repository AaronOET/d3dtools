"""
Example script demonstrating how to use the sensor module to extract data from a NetCDF file.

This module can be used in two ways:
1. As a Python module: Import and use the getdata function directly
2. As a command-line tool: Use the 'sensor' command directly from the terminal
"""
from d3dtools import sensor
import matplotlib.pyplot as plt

# Path to NetCDF file
nc_file = 'output/FM_model_map.nc'

# Path to observation points shapefile
obs_shp = 'SHP/OBS_2D.shp'

# Extract data
data = sensor.getdata(
    nc_file=nc_file,
    obs_shp=obs_shp,
    output_csv='water_depth.csv',
    output_excel='water_depth.xlsx',
    plot=True
)

# Print the first few rows of the data
print(data.head())

# You can also process the data further if needed
# For example, get statistics for each observation point
stats = data.describe().transpose()
print('\nStatistics for each observation point:')
print(stats)

"""
Command-line usage example:

After installing the package, you can run the following command in your terminal:

sensor --nc-file output/FM_model_map.nc --obs-shp SHP/OBS_2D.shp --output-csv water_depth.csv --output-excel water_depth.xlsx --plot

For help, use:
sensor --help
"""
