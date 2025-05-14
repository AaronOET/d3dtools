# Sensor Module

This module provides functionality to extract time series data from Delft3D FM NetCDF output files at specified observation points.

## Usage

### As a Python Module

```python
from d3dtools import sensor

# Extract data from NetCDF file at observation points
data = sensor.getdata(
    nc_file='path/to/nc_file.nc',
    obs_shp='path/to/observation_points.shp',
    output_csv='output.csv',  # optional
    output_excel='output.xlsx',  # optional
    plot=True  # optional
)
```

### As a Command-Line Tool

```bash
# Basic usage
sensor --nc-file path/to/nc_file.nc --obs-shp path/to/observation_points.shp

# With all options
sensor --nc-file path/to/nc_file.nc --obs-shp path/to/observation_points.shp --output-csv output.csv --output-excel output.xlsx --plot --verbose
```

To see all available options:

```bash
sensor --help
```

## Required Input Files

1. NetCDF file (nc_file): A Delft3D FM output NetCDF file containing time series data
2. Shapefile (obs_shp): A shapefile containing observation points with a 'Name' field

## Output

The function returns a pandas DataFrame with time as the first column and water depth values at each observation point in subsequent columns. Optionally, it can:

- Save the data to a CSV file
- Save the data to an Excel file
- Generate a plot of the time series data

## Example

See the example script in the examples/sensor directory for a complete demonstration.
