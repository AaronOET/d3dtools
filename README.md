# D3DTOOLS

A collection of Python tools for working with shapefiles and converting them for Delft3D modeling.

> **CAUTION**: The ncrain function currently only works for Taiwan data in EPSG:3826 projection.

> **GDAL Installation**: GDAL is required for this package. For conda environments, use `conda install gdal` to install GDAL. For non-conda environments, download the appropriate wheel file from [https://github.com/cgohlke/geospatial-wheels/releases](https://github.com/cgohlke/geospatial-wheels/releases) to install GDAL.

## Installation

```bash
pip install d3dtools
```

## Features

This package provides several utilities for converting shapefiles to various formats used in Delft3D modeling:

- **ncrain**: Generate a NetCDF file from rainfall data and thiessen polygon shapefiles
- **snorain**: Process rainfall scenario data and generate time series CSV files
- **shp2ldb**: Convert boundary line shapefiles to LDB files
- **shpbc2pli** (alias: **shp2pli**): Convert boundary line shapefiles to PLI files
- **shpblock2pol** (alias: **shp2pol**): Convert shapefile blocks to POL files
- **shpdike2pliz** (alias: **shp2pliz**): Convert bankline shapefiles to PLIZ files
- **shp2xyz**: Convert point shapefiles to XYZ files

## Usage Examples

### Process and generate rainfall scenario data

```python
from d3dtools import snorain

# Process a scenario rainfall CSV file
snorain.generate(
    input_file='rainfall_scenarios.csv',
    output_folder='custom/TAB',
    verbose=True
)
```

### Generate NetCDF from rainfall data (with unit of mm/hr)

```python
from d3dtools import ncrain

# Default usage - processes first CSV file in the input folder
ncrain.generate()

# With custom parameters
ncrain.generate(
    input_shp_folder='custom/SHP',
    input_tab_folder='custom/TAB',
    output_nc_folder='custom/NC',
    intermediate_ras_folder='custom/RAS_RAIN',
    intermediate_shp_folder='custom/SHP_RAIN',
    clean_intermediate=True,
    raster_resolution=320
)

# Process a specific CSV file
ncrain.generate(
    input_tab_folder='custom/TAB',
    rainfall_file='specific_rainfall.csv',
    verbose=True
)

# Process all CSV files in the input folder
ncrain.generate_all(
    input_shp_folder='custom/SHP',
    input_tab_folder='custom/TAB',
    output_nc_folder='custom/NC',
    verbose=True
)
```

### Convert boundary shapefiles to PLI

```python
from d3dtools import shpbc2pli

# Default usage
shpbc2pli.convert()

# With custom parameters
shpbc2pli.convert(
    input_folder='custom/SHP_BC',
    output_folder='custom/PLI_BC'
)

# With custom ID field name
shpbc2pli.convert(
    input_folder='custom/SHP_BC',
    output_folder='custom/PLI_BC',
    id_field='BoundaryName'  # Use 'BoundaryName' column instead of default 'ID'/'Id'/'id'/'iD'
)
```

### Convert block shapefiles to POL

```python
from d3dtools import shpblock2pol

# Default usage
shpblock2pol.convert()

# With custom parameters
shpblock2pol.convert(
    input_folder='custom/SHP_BLOCK',
    output_folder='custom/POL_BLOCK'
)
```

### Convert dike shapefiles to PLIZ

```python
from d3dtools import shpdike2pliz

# Default usage
shpdike2pliz.convert()

# With custom parameters
shpdike2pliz.convert(
    input_folder='custom/SHP_DIKE',
    output_folder='custom/PLIZ_DIKE',
    output_filename='CustomDike'
)

# With custom ID field name
shpdike2pliz.convert(
    input_folder='custom/SHP_DIKE',
    output_folder='custom/PLIZ_DIKE',
    output_filename='CustomDike',
    id_field='DikeName'  # Use 'DikeName' column instead of default 'ID'/'Id'/'id'/'iD'
)
```

### Convert boundary shapefiles to LDB

```python
from d3dtools import shp2ldb

# Default usage
shp2ldb.convert()

# With custom parameters
shp2ldb.convert(
    input_folder='custom/SHP_LDB',
    output_folder='custom/LDB'
)

# With custom ID field name
shp2ldb.convert(
    input_folder='custom/SHP_LDB',
    output_folder='custom/LDB',
    id_field='BoundaryName'  # Use 'BoundaryName' column instead of default 'ID'/'Id'/'id'/'iD'
)
```

### Convert point shapefiles to XYZ

```python
from d3dtools import shp2xyz

# Default usage
shp2xyz.convert()

# With custom parameters
shp2xyz.convert(
    input_folder='custom/SHP_SAMPLE',
    output_folder='custom/XYZ_SAMPLE'
)

# With custom Z-field name
shp2xyz.convert(
    input_folder='custom/SHP_SAMPLE',
    output_folder='custom/XYZ_SAMPLE',
    z_field='ELEVATION'  # Use 'ELEVATION' column instead of default Z-field detection
)
```

## Command-line Usage

The package provides a command-line utility to display information about all available tools:

```bash
# Display description of all available tools
d3dtools-info

# Display detailed information about a specific tool
d3dtools-info ncrain
d3dtools-info shp2xyz

# Display the package version
d3dtools-info --version
```

The package also provides command-line utilities for each specific tool:

```bash
# Generate NetCDF from rainfall data
ncrain                      # Process all CSV files in the input folder
ncrain --shp-folder custom/SHP --tab-folder custom/TAB --nc-folder custom/NC --resolution 320
ncrain --verbose            # Display additional processing information
ncrain --no-clean           # Keep intermediate files
ncrain --single rainfall.csv  # Process only a specific CSV file

# Process rainfall scenario data
snorain -i rainfall_scenarios.csv -o custom/TAB
snorain --input rainfall_scenarios.csv --output custom/TAB --verbose

# Convert boundary shapefiles to LDB
shp2ldb
shp2ldb -i custom/SHP_LDB -o custom/LDB  # Specify input and output folders
shp2ldb --id_field BoundaryName  # Specify custom ID field

# Convert boundary shapefiles to PLI
shpbc2pli  # or use the alias: shp2pli
shpbc2pli --id_field BoundaryName  # Specify custom ID field

# Convert block shapefiles to POL
shpblock2pol  # or use the alias: shp2pol
shpblock2pol -i custom/SHP_BLOCK -o custom/POL_BLOCK  # Specify input and output folders

# Convert dike shapefiles to PLIZ
shpdike2pliz  # or use the alias: shp2pliz
shpdike2pliz --id_field DikeName  # Specify custom ID field

# Convert point shapefiles to XYZ
shp2xyz
shp2xyz -i custom/SHP_SAMPLE -o custom/XYZ_SAMPLE  # Specify input and output folders
shp2xyz --z_field ELEVATION  # Specify custom Z-field name
```

For more command-line options:

```bash
ncrain --help
snorain --help
shp2ldb --help
shpbc2pli --help
shpblock2pol --help
shpdike2pliz --help
shp2xyz --help
```

## Requirements

- numpy>=1.20.0
- pandas>=1.3.0
- geopandas>=0.10.0
- rasterio>=1.2.0
- netCDF4>=1.5.0
- pyproj>=3.0.0
- shapely>=1.8.0
- matplotlib>=3.4.0

## License

MIT
