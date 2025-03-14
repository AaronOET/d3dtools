# D3DTOOLS

A collection of Python tools for working with shapefiles and converting them for Delft3D modeling.

## Installation

```bash
pip install d3dtools
```

## Features

This package provides several utilities for converting shapefiles to various formats used in Delft3D modeling:

- **ncrain**: Generate a NetCDF file from rainfall data and thiessen polygon shapefiles
- **shpbc2pli**: Convert boundary line shapefiles to PLI files
- **shpblock2pol**: Convert shapefile blocks to POL files
- **shpdike2pliz**: Convert bankline shapefiles to PLIZ files

## Usage Examples

### Generate NetCDF from rainfall data

```python
from d3dtools import ncrain

# Default usage
output_path = ncrain.generate()
print(f"NetCDF file generated at: {output_path}")

# With custom parameters
output_path = ncrain.generate(
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
```

## Command-line Usage

The package also provides command-line utilities:

```bash
# Generate NetCDF from rainfall data
ncrain

# Convert boundary shapefiles to PLI
shpbc2pli

# Convert block shapefiles to POL
shpblock2pol

# Convert dike shapefiles to PLIZ
shpdike2pliz
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