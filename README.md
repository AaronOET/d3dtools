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
- **evaluate**: Calculate flood simulation accuracy metrics by comparing simulated and observed flood extents
- **evaluate_sensor**: Calculate flood simulation accuracy metrics by comparing simulated flood extents with point-based sensor data (with configurable buffer radius and depth threshold)
- **evaluate_sensor2**: Calculate flood simulation accuracy metrics using sensor data with dual-threshold shapefiles (separate low and high depth threshold simulations)
- **sensor**: Extract time series data from Delft3D FM NetCDF files at observation points
- **getfacez**: Extract Mesh2d_face_z values (bed level/bathymetry) from Delft3D FM NetCDF files at observation points
- **fou2shp**: Reconstruct Delft3D FM 2D mesh face polygons from a FOU (Fourier) NetCDF output file and export threshold-filtered shapefiles; supports `--rm` to remove output polygons that intersect mask shapefiles (filtered copies written to `<out-dir>_RM/`)
- **pliz2shp**: Convert Delft3D PLIZ polyline files to ESRI Shapefiles
- **rmgrid**: Remove (clear) the 2D computational mesh and 1D2D links from a D-Flow FM `.dsproj` project while preserving the 1D network (pipes/branches)

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

### Extract time series data from NetCDF files

```python
from d3dtools import sensor

# Extract data from NetCDF file at observation points
data = sensor.getdata(
    nc_file='path/to/model_output.nc',
    obs_shp='path/to/observation_points.shp',
    output_csv='water_depth.csv',
    output_excel='water_depth.xlsx',
    plot=True  # Display a plot of the time series
)

# Process the data further if needed
print(data.head())
stats = data.describe().transpose()
print(stats)
```

### Extract Mesh2d_face_z values from NetCDF files

```python
from d3dtools import getfacez

# Extract bed level/bathymetry data from NetCDF file at observation points
data = getfacez.extract_mesh2d_face_z(
    nc_file='path/to/model_output.nc',
    obs_shp='path/to/observation_points.shp',
    output_csv='bathymetry.csv',
    output_excel='bathymetry.xlsx',
    verbose=True  # Display additional information during processing
)

# Process the data further if needed
print(data.head())
print(f"Bathymetry range: {data['Mesh2d_face_z'].min():.3f} to {data['Mesh2d_face_z'].max():.3f}")
```

### Calculate flood simulation accuracy using sensor data

```python
from d3dtools import evaluate_sensor

# Compare simulated flood extents with sensor observations
results = evaluate_sensor.confusion_matrix(
    sim_path='path/to/simulated_flood.shp',
    obs_path='path/to/sensor_observations.shp',
    buffer_radius=30,               # Buffer radius around sensor points in meters (default: 30)
    depth_threshold=30,             # Water depth threshold in centimeters (default: 30)
    output_csv='sensor_accuracy.csv'
)

print(f"Accuracy: {results['accuracy']:.2f}%")
print(f"Recall (Catch Rate): {results['recall']:.2f}%")
```

### Calculate flood simulation accuracy using sensor data with dual thresholds

```python
from d3dtools import evaluate_sensor2

# Compare simulated flood extents (low/high threshold) with sensor observations
results = evaluate_sensor2.confusion_matrix(
    low_threshold_sim_path='path/to/simulated_flood_low.shp',
    high_threshold_sim_path='path/to/simulated_flood_high.shp',
    obs_path='path/to/sensor_observations.shp',
    buffer_radius=30,               # Buffer radius around sensor points in meters (default: 30)
    depth_threshold=30,             # Water depth threshold in centimeters (default: 30)
    output_csv='sensor_accuracy2.csv'
)

print(f"Accuracy: {results['accuracy']:.2f}%")
print(f"Recall (Catch Rate): {results['recall']:.2f}%")
```

### Reconstruct FOU mesh faces as threshold shapefiles

```python
# Run via command line (recommended)
# fou2shp --input NC/FlowFM_fou.nc --out-dir SHP
# fou2shp --input NC/FlowFM_fou.nc --var Mesh2d_fourier002_max_depth --out-dir output

# Remove polygons intersecting a mask shapefile; filtered copies go to SHP_RM/
# fou2shp --input NC/FlowFM_fou.nc --rm SHP/EXCLUDE.shp
# fou2shp --input NC/FlowFM_fou.nc --rm SHP/*.shp
# fou2shp --input NC/FlowFM_fou.nc --rm SHP/ROAD.shp SHP/BUILDING.shp
```

### Convert PLIZ files to Shapefiles

```python
from d3dtools import pliz2shp

# Convert a single .pliz file
pliz2shp.pliz_to_shp(
    pliz_path='PLIZ/MyDike.pliz',
    output_dir='SHP_DIKE'           # Optional; defaults to same folder as input
)

# Batch convert all .pliz files in a folder via CLI (recommended for multiple files)
# pliz2shp
# pliz2shp -i custom/PLIZ -o custom/SHP
```

### Remove the 2D mesh from a D-Flow FM project

```python
# Recommended usage via the command line (operates on a .dsproj project)
# rmgrid                                  # Auto-detect the .dsproj in the current folder
# rmgrid -i MyProject.dsproj              # Specify the project explicitly
# rmgrid -i MyProject.dsproj --force-backup  # Overwrite an existing .nc.bak
# rmgrid -i MyProject.dsproj --restore    # Restore the original net file from .nc.bak
```

The tool empties the 2D mesh in the project's UGRID NetCDF net file while preserving the
1D network (pipes/branches), strips 2D-specific blocks from the `IniFieldFile`, and creates
a `<name>.nc.bak` backup so the change can be reverted with `--restore`.

### Calculate flood simulation accuracy

```python
from d3dtools import evaluate

# Compare simulated and observed flood extents
results = evaluate.confusion_matrix(
    sim_path='path/to/simulated_flood.shp',
    obs_path='path/to/observed_flood.shp',
    output_path='accuracy_results.csv'
)

print(f"Accuracy: {results['accuracy']:.2f}%")
print(f"Recall (Catch Rate): {results['recall']:.2f}%")
```

## Command-line Usage

### d3dtools-info: Access Tool Information

The package provides the `d3dtools-info` command-line utility that serves as a central information hub for all available tools:

```bash
# Display the package version
d3dtools-info --version

# Get help on d3dtools-info itself
d3dtools-info --help

# Display description of all available tools
d3dtools-info

# Display detailed information about a specific tool
d3dtools-info ncrain
d3dtools-info snorain
d3dtools-info shp2ldb
d3dtools-info shp2pli
d3dtools-info shp2pliz
d3dtools-info shp2pol
d3dtools-info shp2xyz
d3dtools-info shpbc2pli
d3dtools-info shpblock2pol
d3dtools-info shpdike2pliz
d3dtools-info sensor
d3dtools-info evaluate
d3dtools-info evaluate_sensor
d3dtools-info evaluate_sensor2
d3dtools-info getfacez
d3dtools-info fou2shp
d3dtools-info pliz2shp
d3dtools-info rmgrid

# Display help for specific tools
ncrain --help
snorain --help
shp2ldb --help
shp2pli --help
shp2pliz --help
shp2pol --help
shp2xyz --help
shpbc2pli --help
shpblock2pol --help
shpdike2pliz --help
sensor --help
evaluate --help
evaluate_sensor --help
evaluate_sensor2 --help
getfacez --help
fou2shp --help
pliz2shp --help
rmgrid --help
```

The `d3dtools-info` tool helps you discover available functionality, learn about tool options, and access usage examples without having to remember all command-line parameters.

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

# Extract time series data at observation points
sensor --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp
sensor --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp --output-csv water_depth.csv --output-excel water_depth.xlsx --plot
sensor --verbose  # Display additional processing information

# Calculate flood simulation accuracy metrics
evaluate --sim path/to/simulated_flood.shp --obs path/to/observed_flood.shp
evaluate --sim path/to/simulated_flood.shp --obs path/to/observed_flood.shp --output accuracy_results.csv

# Calculate flood simulation accuracy using sensor data
evaluate_sensor --sim path/to/simulated_flood.shp --obs path/to/sensor_points.shp
evaluate_sensor --sim path/to/simulated_flood.shp --obs path/to/sensor_points.shp --buffer 30 --threshold 30 --output sensor_accuracy.csv

# Calculate flood simulation accuracy using sensor data with dual-threshold shapefiles
evaluate_sensor2 --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp
evaluate_sensor2 --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp --buffer 50 --threshold 20 --output sensor_accuracy2.csv

# Extract Mesh2d_face_z values at observation points
getfacez --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp
getfacez --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp --output-csv bathymetry.csv --output-excel bathymetry.xlsx
getfacez --verbose  # Display additional processing information

# Reconstruct FOU mesh faces as threshold-filtered shapefiles
fou2shp                                         # Use defaults (NC/FlowFM_fou.nc -> SHP/)
fou2shp --input NC/FlowFM_fou.nc --out-dir SHP  # Specify input and output directory
fou2shp --input NC/FlowFM_fou.nc --var Mesh2d_fourier002_max_depth --out-dir output
fou2shp --input NC/FlowFM_fou.nc --rm SHP/EXCLUDE.shp          # Remove polygons intersecting a mask; output -> SHP_RM/
fou2shp --input NC/FlowFM_fou.nc --rm SHP/*.shp                 # Glob pattern for multiple masks
fou2shp --input NC/FlowFM_fou.nc --rm SHP/ROAD.shp SHP/BUILDING.shp  # Multiple explicit masks

# Convert Delft3D PLIZ files to ESRI Shapefiles
pliz2shp                             # Use defaults (PLIZ/ -> SHP_DIKE/)
pliz2shp -i custom/PLIZ -o custom/SHP  # Specify custom input and output folders
pliz2shp --help

# Remove the 2D computational mesh from a D-Flow FM .dsproj project
rmgrid                                # Auto-detect the .dsproj in the current folder
rmgrid -i MyProject.dsproj            # Specify the project explicitly
rmgrid -i MyProject.dsproj --force-backup  # Overwrite an existing .nc.bak
rmgrid -i MyProject.dsproj --restore  # Restore the original net file from .nc.bak
```

## Changelog

### 0.20.1

- **fou2shp**: Fixed output directory suffix for mask-filtered shapefiles to use `_RM` consistently.

### 0.20.0

- **fou2shp**: Added `--rm MASK.shp [...]` to remove output polygons that intersect one or more mask shapefiles. Glob patterns are supported (e.g. `--rm SHP/*.shp`). Filtered copies of all threshold shapefiles are written to `<out-dir>_RM/`. Requires `geopandas`.

### 0.19.4

- **evaluate_sensor2** / **eval_iot**: Corrected example buffer radii ordering in help documentation (`EMIC=30`, `淹水感測=20`).

### 0.19.3

- **create_empty_mesh**: Handle non-ASCII paths by using temporary files.

### 0.19.0
- Added **rmgrid**: removes (clears) the 2D computational mesh and 1D2D links from a D-Flow FM `.dsproj` project while preserving the 1D network. Supports automatic `.nc.bak` backup, `--force-backup`, and `--restore`, and cleans `locationType = 2d` blocks from any referenced `IniFieldFile`.

### 0.18.1
- Added **pliz2shp**: converts Delft3D `*.pliz` polyline files to ESRI Shapefiles

### 0.18.0
- Added **fou2shp**: reconstructs Delft3D FM 2D mesh face polygons from FOU (Fourier) NetCDF output and exports threshold-filtered shapefiles
- Added **pliz2shp** module (internal)
- Added **evaluate_sensor2**: flood accuracy evaluation with dual-threshold shapefiles

## Requirements

- numpy>=1.20.0
- pandas>=1.3.0
- geopandas>=0.10.0
- rasterio>=1.2.0
- netCDF4>=1.5.0
- pyproj>=3.0.0
- shapely>=1.8.0
- matplotlib>=3.4.0
- openpyxl>=3.0.0

## License

MIT
