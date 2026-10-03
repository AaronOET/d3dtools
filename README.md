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
- **evaluate_sensor2** (alias: **eval_iot**): Calculate flood simulation accuracy metrics using sensor data with dual-threshold shapefiles (separate low and high depth threshold simulations)
- **sensor**: Extract time series data from Delft3D FM NetCDF files at observation points
- **getfacez**: Extract Mesh2d_face_z values (bed level/bathymetry) from Delft3D FM NetCDF files at observation points. Uses a spatial index (shapely STRtree for point-in-polygon matching, scipy cKDTree for nearest-neighbor matching) instead of scanning every mesh face for every observation point, which is much faster on large meshes. Supports `-if`/`--id-field` to specify which shapefile field to use for point names, and `-p`/`--project` to resolve the NetCDF file from a D-Flow FM `.dsproj` project instead of passing `--nc-file`
- **getfacez2**: Original brute-force implementation of getfacez (no spatial index), kept as a fallback. Same CLI arguments, Python API, and output format as getfacez, including `-if`/`--id-field`
- **fou2shp**: Reconstruct Delft3D FM 2D mesh face polygons from a FOU (Fourier) NetCDF output file and export threshold-filtered shapefiles; supports `-r`/`--remove` to remove output polygons that intersect mask shapefiles (filtered copies written to `<output-folder>_RM/`)
- **pliz2shp**: Convert Delft3D/D-Flow FM `.pliz` weir/dike polyline files (with Z) to 3D ESRI Shapefiles
- **pli2shp**: Convert Delft3D polyline files (`.pli`/`.ldb`) to ESRI Shapefiles
- **pol2shp**: Convert Delft3D/D-Flow FM `.pol` polygon files to ESRI Shapefiles
- **xyz2shp**: Convert XYZ point files (`.xyz`/`.csv`) to ESRI Shapefiles
- **rmgrid**: Remove (clear) the 2D computational mesh and 1D2D links from a D-Flow FM `.dsproj` project while preserving the 1D network (pipes/branches)
- **rsgrid**: Restore the 2D computational mesh (including `Mesh2d_face_z` bed levels) into a D-Flow FM `.dsproj` project by cloning it from a source project, while preserving the target's 1D network. The inverse of `rmgrid`. Also restores the 2D spatial fields (infiltration capacity, roughness) that are lost along with the mesh, via `-f`/`--fields`
- **rmgriddimr**: `rmgrid` for a model exported as a DIMR run folder (`dimr.xml` + `dflowfm/`) instead of a `.dsproj` project
- **rsgriddimr**: `rsgrid` for a model exported as a DIMR run folder; restores the 2D mesh and/or the 2D spatial fields, handling GeoTIFF coverages as well as `*.xyz` samples
- **makedimr**: Build a DIMR run folder (`dimr_config.xml` + `dflowfm/`) from a Delft3D FM Suite project (`.dsproj`), reading the FM model name and its data folder from the project itself. The counterpart of `rmgriddimr`/`rsgriddimr`, which is what creates the run folder those tools operate on
- **rm1dch**: Remove the open 1D channels (and everything anchored on them - structures, cross sections, 1D2D links, boundary/lateral blocks) from a Delft3D FM model, keeping the sewer system (pipes, sewer connections, manholes) and the 2D grid intact. A kept sewer branch that ran into a removed channel gets a new outfall manhole automatically
- **rm1dsw**: Remove the 1D sewer system (pipes, sewer connections, manholes) from a Delft3D FM model, keeping the 1D channels and the 2D grid intact. Same engine as `rm1dch`, opposite direction
- **mk2d**: Turn a Delft3D FM 1D2D model into a 2D-only model by removing the entire 1D network - channels, sewers, manholes, and every 1D structure. Same engine as `rm1dch`/`rm1dsw`, with `--target all`
- **rmlinks**: Remove only the 1D2D links from a Delft3D FM net file, leaving the 1D network, mesh1d, Mesh2d and every other input file untouched. `--type` restricts removal to specific link kinds (lateral, longitudinal, street_inlet, roof_gutter, embedded)
- **orthochk**: Locate non-orthogonal / problematic 2D cells in a Delft3D FM net file and export them as a polygon shapefile. Computes the RGFGRID / D-Flow FM orthogonality per edge and flags the defects behind "network is not orthogonal" (coincident circumcentres, flow link missing the edge, circumcentre outside the cell, non-convex cells, edges shared by more than 2 cells)
- **expgrid**: Export the 2D grid (Mesh2d) of a Delft3D FM net file to a new 2D-only net file, dropping the 1D network, mesh1d, the 1D2D links and the composite mesh. The input file is never modified
- **alignncrain**: Align the simulation period (RefDate, TStart, TStop) of a Delft3D FM `.mdu` with a NetCDF rainfall file, and point the rainfall `[Meteo]` block of the external forcing file to it
- **otstep**: Show or change the output time step of the his file (`HisInterval`) and map file (`MapInterval`) of a Delft3D FM `.mdu`
- **itstep**: Show or change the user time step (`DtUser`), initial time step (`DtInit`) and maximum time step (`DtMax`) of a Delft3D FM `.mdu`

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

### Extract Mesh2d_face_z values from NetCDF files (spatial-index accelerated)

```python
from d3dtools import getfacez

# Extract bed level/bathymetry data from NetCDF file at observation points.
# Uses an STRtree (point-in-polygon) or cKDTree (nearest neighbor) spatial index
# instead of a per-point full mesh scan, so it stays fast on large meshes.
data = getfacez.extract_mesh2d_face_z(
    nc_file='path/to/model_output.nc',
    obs_shp='path/to/observation_points.shp',
    output_csv='bathymetry.csv',
    output_excel='bathymetry.xlsx',
    id_field='StationName',  # Optional; field to use for point names (default: auto-detect)
    verbose=True  # Display additional information during processing
)

# Alternatively, resolve the NetCDF file from a D-Flow FM project instead of
# passing nc_file. The project's MDU is located under <project>.dsproj_data/ and
# its [geometry] NetFile entry is used. nc_file and project are mutually exclusive.
data = getfacez.extract_mesh2d_face_z(
    project='path/to/MyProject.dsproj',  # Or 'path/to/MyProject', or a directory containing one .dsproj
    obs_shp='path/to/observation_points.shp',
    output_csv='bathymetry.csv',
    output_excel='bathymetry.xlsx',
    verbose=True
)

# Process the data further if needed
print(data.head())
print(f"Bathymetry range: {data['Mesh2d_face_z'].min():.3f} to {data['Mesh2d_face_z'].max():.3f}")
```

### Extract Mesh2d_face_z values from NetCDF files (original brute-force fallback)

```python
from d3dtools import getfacez2

# Same signature and output as getfacez, but uses the original per-point full mesh
# scan (no spatial index). Kept as a fallback in case the spatial-index approach
# ever misbehaves on unusual mesh data.
data = getfacez2.extract_mesh2d_face_z(
    nc_file='path/to/model_output.nc',
    obs_shp='path/to/observation_points.shp',
    output_csv='bathymetry.csv',
    output_excel='bathymetry.xlsx',
    id_field='StationName',  # Optional; field to use for point names (default: auto-detect)
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
# fou2shp --input NC/FlowFM_fou.nc -of SHP
# fou2shp --input NC/FlowFM_fou.nc --var Mesh2d_fourier002_max_depth --output-folder output

# Remove polygons intersecting a mask shapefile; filtered copies go to SHP_RM/
# fou2shp --input NC/FlowFM_fou.nc -r SHP/EXCLUDE.shp
# fou2shp --input NC/FlowFM_fou.nc -r SHP/*.shp
# fou2shp --input NC/FlowFM_fou.nc --remove SHP/ROAD.shp SHP/BUILDING.shp
```

### Convert PLIZ files to Shapefiles

```python
from d3dtools import pliz2shp

# Convert a single .pliz file
pliz2shp.pliz_to_shp(
    input_file='PLIZ/MyDike.pliz',
    output_dir='SHP_LINES3D',       # Optional; default: SHP_LINES3D
    crs='EPSG:3826'                 # Optional; default: EPSG:3826
)

# Batch convert via CLI (recommended for multiple files)
# pliz2shp -i Dike001.pliz
# pliz2shp -if custom/PLIZ -of custom/SHP
```

### Convert PLI/LDB files to Shapefiles

```python
from d3dtools import pli2shp

# Convert a single .pli or .ldb file
pli2shp.polyline_to_shp(
    input_file='PLI/boundary.pli',
    output_dir='SHP_LINES',         # Optional; default: SHP_LINES
    crs='EPSG:3826'                 # Optional; default: EPSG:3826
)

# Batch convert via CLI (recommended for multiple files)
# pli2shp -i boundary.pli
# pli2shp -if custom/PLI -of custom/SHP
```

### Convert POL files to Shapefiles

```python
from d3dtools import pol2shp

# Convert a single .pol file
pol2shp.pol_to_shp(
    input_file='POL/POL_001.pol',
    output_dir='SHP_POLYGONS',      # Optional; default: SHP_POLYGONS
    crs='EPSG:3826'                 # Optional; default: EPSG:3826
)

# Batch convert via CLI (recommended for multiple files)
# pol2shp -i POL_001.pol
# pol2shp -if custom/POL -of custom/SHP
```

### Convert XYZ/CSV point files to Shapefiles

```python
from d3dtools import xyz2shp

# Convert a single .xyz or .csv point file
xyz2shp.xyz_to_shp(
    input_file='XYZ/XYZ_001.xyz',
    output_dir='SHP_XYZ',           # Optional; default: SHP_XYZ
    crs='EPSG:3826',                # Optional; default: EPSG:3826
    dimension='3'                   # Optional; '3' for x,y,z points, '2' for x,y only
)

# Batch convert via CLI (recommended for multiple files)
# xyz2shp -i XYZ_001.xyz
# xyz2shp -if custom/XYZ -of custom/SHP
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

### Restore the 2D mesh into a D-Flow FM project

```python
# Recommended usage via the command line (operates on .dsproj projects)
# rsgrid -s Intact.dsproj                   # Restore the mesh into first .dsproj in cwd
# rsgrid -i Stripped.dsproj -s Intact.dsproj
# rsgrid -s source_net.nc                   # Source given directly as a net file
# rsgrid -i target_net.nc -s source_net.nc

# Restore the 2D spatial fields (infiltration capacity, roughness) as well/instead
# rsgrid -f                                 # Restore fields from the current directory
# rsgrid -i Target.dsproj -f -d fields/     # Take the *.xyz files from fields/
# rsgrid -i Target.dsproj -s Intact.dsproj -f   # Mesh first, then the fields
# rsgrid -f -q frictioncoefficient=rough2024.xyz  # Map an oddly named sample file
```

The tool clones the 2D mesh (including `Mesh2d_face_z` bed levels) from a source project's
net file into the target's net file, keeping the target's own 1D network, coordinate system,
and other settings intact. It backs up the target net file with a timestamped copy before
overwriting. This is the inverse of `rmgrid`.

Removing and re-adding a 2D grid also drops the *spatial fields* that live on it: the initial
infiltration capacity and the 2D roughness (friction coefficient), which live in loose
`*.xyz` sample files next to the MDU rather than in the net file. `-f`/`--fields` restores
these: it copies the `*.xyz` sample files (default: from the current directory, or `-d DIR`)
into the model's input folder and re-registers them in the MDU (`IniFieldFile`, `FrictFile`,
and `Infiltrationmodel` when an infiltration field is present). The project's
`initialFields.ini` is created if it doesn't have one, or updated in place (just the
`dataFile` entries, leaving interpolation/averaging settings alone) if it does. Sample files
are matched to a quantity by name; use `-q NAME=FILE` for files named something else, e.g.
`-q frictioncoefficient=rough2024.xyz`.

### Remove / restore the 2D mesh in a DIMR run folder

`rmgrid` and `rsgrid` work on a D-HYDRO `.dsproj` project. When the model has been exported
as a **DIMR run folder** (`dimr.xml` + `dflowfm/`) there is no `.dsproj` to point at, so use
`rmgriddimr` and `rsgriddimr` instead. The processing is identical; only the way the model is
located differs.

```python
# Clear the 2D mesh (operates on a DIMR run folder)
# rmgriddimr                                # Run folder = current directory
# rmgriddimr -i C:/models/PT01              # A run folder
# rmgriddimr -i C:/models/PT01/dimr.xml     # The DIMR config directly
# rmgriddimr -i C:/models/PT01/dflowfm      # The dflowfm folder
# rmgriddimr -i C:/models/PT01 --restore    # Restore mesh + iniField from the .bak files
# rmgriddimr -i C:/models/PT01 --force-backup

# Restore the 2D mesh and/or the 2D spatial fields
# rsgriddimr -s C:/models/Intact            # Clone the mesh into the cwd's model
# rsgriddimr -i C:/models/PT01 -s C:/models/Intact
# rsgriddimr -i C:/models/PT01 -s source_net.nc
# rsgriddimr -i C:/models/PT01 -f -d fields/    # Coverage files from fields/
# rsgriddimr -i C:/models/PT01 -s Intact -f     # Mesh first, then the fields
# rsgriddimr -f -q frictioncoefficient=RHI.tif  # Map an oddly named coverage
```

`-i`/`-s` accept a run folder, a `dimr.xml`, a `dflowfm` folder or an `.mdu` file (and, for
`rsgriddimr -s`, a `.nc` net file). A DIMR export normally carries its 2D coverages as
GeoTIFFs rather than `*.xyz` samples, so `rsgriddimr -f` handles both. Because a name like
`RHI.tif` says nothing about its quantity, it consults the iniField files of the model, of the
backups `rmgriddimr` left behind, and of the `-s` source model to work out which quantity a
coverage belongs to; `-q NAME=FILE` settles anything left over.

`rmgriddimr` backs up the net file as `<name>.nc.bak` and the iniField file as
`<name>.ini.bak`, so `--restore` brings back the 2D mesh together with the 2D roughness and
infiltration blocks.

### Build a DIMR run folder from a .dsproj project

`makedimr` creates the DIMR run folder that `rmgriddimr`/`rsgriddimr` operate on, from a
Delft3D FM Suite project (`.dsproj`). It reads the FM model name and its data folder directly
from the `.dsproj` file (a SQLite database), so it works for any project with one D-Flow FM
model (`--model` picks one when there are several).

```python
# makedimr 2DOF_KS.dsproj                          # Output: DIMR/ next to the .dsproj
# makedimr 2DOF_KS.dsproj --out DIMR --threads 1 --force
# makedimr 2DOF_KS.dsproj --model FlowFM1           # Project has several FM models
```

The output folder gets a `dimr_config.xml` (with `creationDate` set to the time the tool is
run) and a `dflowfm/` folder holding a copy of `<project>.dsproj_data/<FM model>/input`.

### Split a 1D2D model: remove channels, sewers, or the whole 1D network

`rm1dch`, `rm1dsw` and `mk2d` remove one part of the 1D network - and everything anchored on
it (structures, cross sections, 1D2D links, boundary/lateral blocks, forcing records) - from a
Delft3D FM (D-HYDRO / FM Suite) model, leaving the rest and the 2D grid intact. All three run
the same engine and only differ in what `--target` removes by default:

```python
# rm1dch <input-folder-or-mdu>                 # Remove the open 1D channels (default)
# rm1dch <input-folder-or-mdu> --check         # Report what is still there, write nothing
# rm1dch <input-folder-or-mdu> --dry-run       # Report the plan, write nothing
# rm1dsw <input-folder-or-mdu>                 # Remove the sewer system (pipes, connections, manholes)
# mk2d <input-folder-or-mdu>                   # Remove the entire 1D network -> 2D-only model
# rm1dch <input-folder-or-mdu> --target sewer  # Any of the three also takes --target directly
```

Where a kept sewer branch ran into a branch that is removed, a manhole is added automatically
so the sewer keeps a proper outfall compartment instead of a pipe ending in mid-air; its
levels follow the sewer branch it closes off (`--manhole-levels`), and it can be switched off
with `--no-outfall-manholes`. `mk2d` (`--target all`) additionally blanks the `.mdu` keys that
only 1D used (`--keep-1d-mdu-keys` to leave them) and refuses to run when the net file has no
2D grid, since the result would be an empty model (`--allow-empty-2d` to continue anyway).
Every file that is rewritten is first backed up to `<name>.bak` (or `.bak2`, `.bak3` ... so an
existing backup is never lost). **Close the project in the FM Suite before running** - a
loaded project holds the network in memory and the next Save writes it straight back.

### Remove only the 1D2D links

`rmlinks` removes just the 1D2D links (mesh contacts) from a Delft3D FM net file; the 1D
network, mesh1d, Mesh2d and every other input file are left untouched.

```python
# rmlinks <input-folder | model.mdu | *_net.nc>                    # Remove every 1D2D link
# rmlinks <input-folder | model.mdu | *_net.nc> --check            # List the links, write nothing
# rmlinks <input-folder> --type street_inlet roof_gutter           # Remove only these kinds
```

`--type` accepts `lateral`, `longitudinal`, `street_inlet`, `roof_gutter` and `embedded`
(default: `all`). If the `.mdu` has a non-empty `1D2DLinkFile` key and every link is removed,
that key is blanked as well, unless `--keep-linkfile` is given.

### Find non-orthogonal 2D cells

`orthochk` checks the 2D mesh of a Delft3D FM net file and writes the problem cells to a
polygon shapefile (default `./<netfile>_orthochk/<netfile>_nonortho_cells.shp`, i.e. a
folder in the directory the command is run from), with the worst cells and a summary
printed to the console.

```python
# orthochk <input-folder | model.mdu | *_net.nc>                 # Cells with ortho > 0.1 or a defect
# orthochk FlowFM_net.nc -t 0.05 -o bad_cells.shp --edges        # Stricter threshold + bad-edge lines
# orthochk FlowFM_net.nc --check                                 # Summary only, write nothing
# orthochk FlowFM_net.nc --all                                   # Every cell with its attributes

from d3dtools import orthochk
res = orthochk.check_orthogonality("FlowFM_net.nc", threshold=0.1)
print(res["flagged"].sum(), res["ortho"].max())
```

Orthogonality is `|cos|` of the angle between a net link and the flow link joining the two
cell circumcentres: `< 0.02` good, `0.02-0.1` acceptable, `> 0.1` poor. Each cell record
carries `FACE_ID` (0-based), `MAX_ORTHO`, `N_BAD_EDG`, the defect flags `ZERO_LINK`,
`SAMESIDE`, `CC_OUT`, `NONCONVX`, `OVERLAP`, plus `AREA_M2` and circumcentre `CX`/`CY`.
`--check` exits with code 1 when any cell is flagged.

### Export the 2D grid of a net file

`expgrid` copies the Mesh2d part of a Delft3D FM net file (plus the coordinate system and
global attributes) to a new 2D-only net file (default `./<netfile>_2d.nc`, e.g.
`FlowFM_net.nc` -> `FlowFM_net_2d.nc`). The 1D network, mesh1d, 1D2D links and composite
mesh are dropped, and so are the cell bed levels (`Mesh2d_face_z`) unless `-z`/`--face-z`
is given; the input is left untouched.

```python
# expgrid <input-folder | model.mdu | *_net.nc>                  # -> ./FlowFM_net_2d.nc
# expgrid FlowFM_net.nc -o grid.nc -f                             # Explicit output, overwrite
# expgrid FlowFM_net.nc --face-z                                  # Keep the Mesh2d_face_z bed levels
# expgrid FlowFM_net.nc --check                                   # List kept / dropped variables

from d3dtools import expgrid
res = expgrid.export_grid("FlowFM_net.nc", "FlowFM_net_2d.nc", face_z=False)
print(res["kept"], res["dropped"])
```

### Align the simulation period with a rainfall file

`alignncrain` sets `RefDate`, `TStart` and `TStop` in the `[time]` section of the `.mdu`
(and `StartDateTime` / `StopDateTime` when filled in) so the run starts at the first
rainfall time stamp and stops one rainfall time step after the last one (`--pad-end` to
change). It also points the rainfall `[Meteo]` block of `ExtForceFileNew` to the NetCDF
file, using `rainfall` for depth units (mm) and `rainfall_rate` for rate units. Changed
files are backed up as `<file>.bak`.

```python
# alignncrain <input-folder | model.mdu> rain.nc
# alignncrain FlowFM.mdu rain.nc --pad-end 3600                   # Stop 1 h after the last stamp
# alignncrain FlowFM.mdu rain.nc --no-ext                         # Only the [time] section
# alignncrain FlowFM.mdu rain.nc --check                          # Show the changes, write nothing

from d3dtools import alignncrain
res = alignncrain.align("FlowFM.mdu", "rain.nc", pad_end=None)
print(res["start"], res["stop"], res["mdu_changes"], res["ext_changes"])
```

### Show or change the his / map output time step

`otstep` reads and writes `HisInterval` and `MapInterval` in the `[output]` section of the
`.mdu`. Without `--his` / `--map` it prints the current intervals, the simulation length and
the resulting number of output steps. New steps are given in seconds or with a unit
(`s`, `m`, `h`, `d`); `0` switches the output off. Only the interval is replaced, so an
output start / stop after it is kept. The `.mdu` is backed up as `<file>.bak`.

```python
# otstep <input-folder | model.mdu>                                # Show the current steps
# otstep FlowFM.mdu --his 60 --map 3600                            # Set, in seconds
# otstep FlowFM.mdu --his 1m --map 1h                              # Set, with a unit
# otstep FlowFM.mdu --map 30m --check                              # Show the change, write nothing

from d3dtools import otstep
print(otstep.get_steps("FlowFM.mdu"))
otstep.set_steps("FlowFM.mdu", his=60, map=3600)
```

### Show or change the user / initial / max time step

`itstep` reads and writes `DtUser`, `DtInit` and `DtMax` in the `[time]` section of the
`.mdu`. Without `--user` / `--init` / `--max` it prints the current values (or the D-Flow FM
default when a key is absent) and warns when `DtMax` > `DtUser`, `DtInit` > `DtMax`, or the
his / map output interval is not a multiple of `DtUser`. New steps are given in seconds or
with a unit (`s`, `m`, `h`, `d`); a key missing from the `.mdu` is added. The `.mdu` is
backed up as `<file>.bak`.

```python
# itstep <input-folder | model.mdu>                                # Show the current steps
# itstep FlowFM.mdu --user 60 --max 30 --init 1                    # Set, in seconds
# itstep FlowFM.mdu --user 1m --max 30s                            # Set, with a unit
# itstep FlowFM.mdu --max 10 --check                               # Show the change, write nothing

from d3dtools import itstep
print(itstep.get_steps("FlowFM.mdu"))
itstep.set_steps("FlowFM.mdu", user=60, init=1, max=30)
```

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
d3dtools-info -v

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
d3dtools-info eval_iot
d3dtools-info getfacez
d3dtools-info getfacez2
d3dtools-info fou2shp
d3dtools-info pliz2shp
d3dtools-info pli2shp
d3dtools-info pol2shp
d3dtools-info xyz2shp
d3dtools-info rmgrid
d3dtools-info rsgrid
d3dtools-info rmgriddimr
d3dtools-info rsgriddimr
d3dtools-info makedimr
d3dtools-info rm1dch
d3dtools-info rm1dsw
d3dtools-info mk2d
d3dtools-info rmlinks
d3dtools-info orthochk
d3dtools-info expgrid
d3dtools-info alignncrain
d3dtools-info otstep
d3dtools-info itstep

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
eval_iot --help
getfacez --help
getfacez2 --help
fou2shp --help
pliz2shp --help
pli2shp --help
pol2shp --help
xyz2shp --help
rmgrid --help
rsgrid --help
rmgriddimr --help
rsgriddimr --help
makedimr --help
rm1dch --help
rm1dsw --help
mk2d --help
rmlinks --help
orthochk --help
expgrid --help
alignncrain --help
otstep --help
itstep --help
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
snorain -i rainfall_scenarios.csv -of custom/TAB
snorain --input rainfall_scenarios.csv --output-folder custom/TAB --verbose

# Convert boundary shapefiles to LDB
shp2ldb
shp2ldb -i custom/SHP_LDB -of custom/LDB  # Specify input and output folders
shp2ldb --id_field BoundaryName  # Specify custom ID field

# Convert boundary shapefiles to PLI
shpbc2pli  # or use the alias: shp2pli
shpbc2pli --id_field BoundaryName  # Specify custom ID field

# Convert block shapefiles to POL
shpblock2pol  # or use the alias: shp2pol
shpblock2pol -i custom/SHP_BLOCK -of custom/POL_BLOCK  # Specify input and output folders

# Convert dike shapefiles to PLIZ
shpdike2pliz  # or use the alias: shp2pliz
shpdike2pliz --id_field DikeName  # Specify custom ID field

# Convert point shapefiles to XYZ
shp2xyz
shp2xyz -i custom/SHP_SAMPLE -of custom/XYZ_SAMPLE  # Specify input and output folders
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
evaluate_sensor --sim path/to/simulated_flood.shp --obs path/to/sensor_points.shp --buffer 30 --thresh-iot 30 --output sensor_accuracy.csv

# Calculate flood simulation accuracy using sensor data with dual-threshold shapefiles
evaluate_sensor2 --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp
evaluate_sensor2 --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp --buffer 50 --thresh-iot 20 --output sensor_accuracy2.csv
eval_iot --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp  # Alias for evaluate_sensor2
eval_iot --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp --buffer 30 --thresh-iot 20 --output sensor_accuracy2.csv

# Extract Mesh2d_face_z values at observation points (spatial-index accelerated)
getfacez --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp
getfacez --obs-shp path/to/observation_points.shp                    # Auto-detect a single .dsproj in the current directory
getfacez -p MyProject.dsproj --obs-shp path/to/observation_points.shp  # Resolve the NetCDF from a project's MDU NetFile
getfacez -p MyProject --obs-shp path/to/observation_points.shp         # Project name without the .dsproj extension
getfacez --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp --output-csv bathymetry.csv --output-excel bathymetry.xlsx
getfacez --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp -if StationName  # Specify custom id field
getfacez --verbose  # Display additional processing information

# Extract Mesh2d_face_z values at observation points (original brute-force fallback)
getfacez2 --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp
getfacez2 --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp --output-csv bathymetry.csv --output-excel bathymetry.xlsx
getfacez2 --nc-file path/to/model_output.nc --obs-shp path/to/observation_points.shp -if StationName  # Specify custom id field
getfacez2 --verbose  # Display additional processing information

# Reconstruct FOU mesh faces as threshold-filtered shapefiles
fou2shp                                         # Use defaults (NC/FlowFM_fou.nc -> SHP/)
fou2shp --input NC/FlowFM_fou.nc -of SHP        # Specify input and output directory
fou2shp --input NC/FlowFM_fou.nc --var Mesh2d_fourier002_max_depth --output-folder output
fou2shp --input NC/FlowFM_fou.nc -r SHP/EXCLUDE.shp             # Remove polygons intersecting a mask; output -> SHP_RM/
fou2shp --input NC/FlowFM_fou.nc -r SHP/*.shp                   # Glob pattern for multiple masks
fou2shp --input NC/FlowFM_fou.nc --remove SHP/ROAD.shp SHP/BUILDING.shp  # Multiple explicit masks

# Convert a Delft3D/D-Flow FM .pliz file (weir/dike polyline with Z) to a 3D ESRI Shapefile
pliz2shp -i Dike001.pliz
pliz2shp -i Dike001.pliz -of output --crs EPSG:4326  # Specify output folder and CRS
pliz2shp -if custom/PLIZ -of custom/SHP              # Convert every .pliz file in a folder
pliz2shp --help

# Convert a Delft3D polyline file (.pli/.ldb) to an ESRI Shapefile
pli2shp -i boundary.pli
pli2shp -i LDB_001.ldb -of output --crs EPSG:4326
pli2shp -if custom/PLI -of custom/SHP
pli2shp --help

# Convert a Delft3D/D-Flow FM .pol file to a polygon ESRI Shapefile
pol2shp -i POL_001.pol
pol2shp -i POL_001.pol -of output --crs EPSG:4326
pol2shp -if custom/POL -of custom/SHP
pol2shp --help

# Convert an XYZ/CSV point file to an ESRI Shapefile
xyz2shp -i XYZ_001.xyz
xyz2shp -i XYZ_001.csv -of output --crs EPSG:4326
xyz2shp -i XYZ_001.xyz -d 2                         # Write 2D (x,y) points instead of 3D
xyz2shp -if custom/XYZ -of custom/SHP
xyz2shp --help

# Remove the 2D computational mesh from a D-Flow FM .dsproj project
rmgrid                                # Auto-detect the .dsproj in the current folder
rmgrid -i MyProject.dsproj            # Specify the project explicitly
rmgrid -i MyProject.dsproj --force-backup  # Overwrite an existing .nc.bak
rmgrid -i MyProject.dsproj --restore  # Restore the original net file from .nc.bak

# Restore the 2D computational mesh into a D-Flow FM .dsproj project
rsgrid -s Intact.dsproj                   # Restore into first .dsproj in cwd
rsgrid -i Stripped.dsproj -s Intact.dsproj # Specify target and source explicitly
rsgrid -s source_net.nc                   # Source given directly as a net file
rsgrid -i target_net.nc -s source_net.nc  # Operate directly on net files

# Restore the 2D spatial fields (infiltration capacity, roughness) too
rsgrid -f                                 # Restore fields from the current directory
rsgrid -i Target.dsproj -f -d fields/     # Take the *.xyz files from fields/
rsgrid -i Target.dsproj -s Intact.dsproj -f    # Mesh first, then the fields
rsgrid -f -q frictioncoefficient=rough2024.xyz # Map an oddly named sample file

# Same two operations on a DIMR run folder (dimr.xml + dflowfm/) instead of a .dsproj
rmgriddimr                                # Run folder = current directory
rmgriddimr -i C:/models/PT01              # A run folder
rmgriddimr -i C:/models/PT01/dimr.xml     # The DIMR config directly
rmgriddimr -i C:/models/PT01/dflowfm      # The dflowfm folder
rmgriddimr -i C:/models/PT01 --restore    # Restore mesh + iniField from the .bak files
rmgriddimr -i C:/models/PT01 --force-backup

rsgriddimr -s C:/models/Intact            # Clone the mesh into the cwd's model
rsgriddimr -i C:/models/PT01 -s C:/models/Intact
rsgriddimr -i C:/models/PT01 -s source_net.nc
rsgriddimr -i C:/models/PT01 -f -d fields/     # Coverage files (*.xyz, *.tif) from fields/
rsgriddimr -i C:/models/PT01 -s Intact -f      # Mesh first, then the fields
rsgriddimr -f -q frictioncoefficient=RHI.tif   # Map an oddly named GeoTIFF coverage

# Build a DIMR run folder from a .dsproj project
makedimr 2DOF_KS.dsproj                          # Output: DIMR/ next to the .dsproj
makedimr 2DOF_KS.dsproj --out DIMR --threads 1 --force
makedimr 2DOF_KS.dsproj --model FlowFM1          # Project has several FM models

# Remove the open 1D channels from a Delft3D FM model (keeps sewer + 2D grid)
rm1dch <input-folder-or-mdu>
rm1dch <input-folder-or-mdu> --check             # Report what is still there, write nothing
rm1dch <input-folder-or-mdu> --dry-run           # Report the plan, write nothing
rm1dch <input-folder-or-mdu> --no-outfall-manholes

# Remove the 1D sewer system from a Delft3D FM model (keeps channels + 2D grid)
rm1dsw <input-folder-or-mdu>
rm1dsw <input-folder-or-mdu> --target channel    # Every rm1d*/mk2d command also takes --target

# Turn a Delft3D FM 1D2D model into a 2D-only model
mk2d <input-folder-or-mdu>
mk2d <input-folder-or-mdu> --keep-1d-mdu-keys    # Leave FrictFile / 1dField keys in place
mk2d <input-folder-or-mdu> --allow-empty-2d      # Continue even without a 2D grid

# Remove only the 1D2D links from a Delft3D FM net file
rmlinks <input-folder | model.mdu | *_net.nc>
rmlinks <input-folder | model.mdu | *_net.nc> --check
rmlinks <input-folder> --type street_inlet roof_gutter   # Remove only these link kinds

# Find non-orthogonal / problematic 2D cells in a Delft3D FM net file
orthochk <input-folder | model.mdu | *_net.nc>
orthochk FlowFM_net.nc -t 0.05 --edges           # Stricter threshold + offending edges
orthochk FlowFM_net.nc --check                   # Summary only, exit 1 if any cell flagged

# Export the 2D grid of a Delft3D FM net file to a 2D-only net file
expgrid <input-folder | model.mdu | *_net.nc>
expgrid FlowFM_net.nc --face-z                   # Keep the Mesh2d_face_z bed levels
expgrid FlowFM_net.nc --check                    # List kept / dropped variables, write nothing

# Align the .mdu simulation period with a NetCDF rainfall file
alignncrain <input-folder | model.mdu> rain.nc
alignncrain FlowFM.mdu rain.nc --pad-end 3600    # Simulate 1 h after the last rainfall stamp
alignncrain FlowFM.mdu rain.nc --check           # Show the changes, write nothing

# Show or change the his / map output time step
otstep <input-folder | model.mdu>                # Show the current steps
otstep FlowFM.mdu --his 1m --map 1h              # Set (seconds or with a unit s/m/h/d)

# Show or change the user / initial / max time step
itstep <input-folder | model.mdu>                # Show DtUser, DtInit, DtMax
itstep FlowFM.mdu --user 60 --max 30 --init 1    # Set (seconds or with a unit s/m/h/d)
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

## Requirements

- numpy>=1.20.0
- pandas>=1.3.0
- geopandas>=0.10.0
- rasterio>=1.2.0
- netCDF4>=1.5.0
- pyproj>=3.0.0
- shapely>=2.0.0
- scipy>=1.7.0
- matplotlib>=3.4.0
- openpyxl>=3.0.0

## License

MIT
