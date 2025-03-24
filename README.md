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
- **shp2ldb**: Convert boundary line shapefiles to LDB files
- **shpbc2pli**: Convert boundary line shapefiles to PLI files
- **shpblock2pol**: Convert shapefile blocks to POL files
- **shpdike2pliz**: Convert bankline shapefiles to PLIZ files

## Usage Examples

### Generate NetCDF from rainfall data (with unit of mm/hr)

```python
from d3dtools import ncrain

# Default usage
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

## Command-line Usage

The package also provides command-line utilities:

```bash
# Generate NetCDF from rainfall data
ncrain
ncrain --shp-folder custom/SHP --tab-folder custom/TAB --nc-folder custom/NC --resolution 320
ncrain --verbose   # Display additional processing information
ncrain --no-clean  # Keep intermediate files

# Convert boundary shapefiles to LDB
shp2ldb
shp2ldb -i custom/SHP_LDB -o custom/LDB  # Specify input and output folders
shp2ldb --id_field BoundaryName  # Specify custom ID field

# Convert boundary shapefiles to PLI
shpbc2pli
shpbc2pli --id_field BoundaryName  # Specify custom ID field

# Convert block shapefiles to POL
shpblock2pol
shpblock2pol -i custom/SHP_BLOCK -o custom/POL_BLOCK  # Specify input and output folders

# Convert dike shapefiles to PLIZ
shpdike2pliz
shpdike2pliz --id_field DikeName  # Specify custom ID field
```

For more command-line options:

```bash
ncrain --help
shp2ldb --help
shpbc2pli --help
shpblock2pol --help
shpdike2pliz --help
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