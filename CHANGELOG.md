# Changelog

## 0.25.1

- `d3dtools`/`d3dtools-info`: Added the missing **rsgrid** entry to the `d3dtools -h` tool listing and `d3dtools rsgrid` detailed description (it was omitted when rsgrid was added in 0.25.0).

## 0.25.0

- Added **rsgrid**: restores the 2D computational mesh (including `Mesh2d_face_z` bed levels) into a D-Flow FM `.dsproj` project by cloning it from a source project's net file, while preserving the target's own 1D network. This is the inverse of `rmgrid`.

## 0.24.3

- `pli2shp` / `pliz2shp` / `pol2shp` / `xyz2shp`: Corrected the `-of`/`--output-folder` example in the CLI help text, which showed a lowercase `output` placeholder inconsistent with the `OUTPUT_DIR` placeholder used elsewhere.

## 0.24.2

- Renamed the output-folder CLI flag from `-o`/`--output` to `-of`/`--output-folder` for consistency across the package: `shp2ldb`, `shpbc2pli`/`shp2pli`, `shpblock2pol`/`shp2pol`, `shpdike2pliz`/`shp2pliz`, `shp2xyz`, `snorain`, `pli2shp`, `pliz2shp`, `pol2shp`, `xyz2shp`. Not backward compatible with the previous `-o` flags; the Python API (`convert()`/`*_to_shp()` functions) is unchanged.
- `fou2shp`: Renamed `--out-dir` (no short form) to `-of`/`--output-folder` to match the rest of the package. Not backward compatible with the previous `--out-dir` flag.

## 0.24.1

- `d3dtools`/`d3dtools-info`: Added `-v` as a short alias for `--version`.

## 0.24.0

- Changed **pliz2shp**: reworked to support single-file (`-i`) or folder (`-if`) input, `--crs`, and `-q`/`--quiet`; output now includes length, Z range, and per-attribute-column summaries. CLI flags and Python API (`pliz_to_shp`) are not backward compatible with the previous folder-only version.
- Added **pli2shp**: converts Delft3D polyline files (`.pli`/`.ldb`) to ESRI line Shapefiles.
- Added **pol2shp**: converts Delft3D/D-Flow FM `.pol` polygon files to ESRI polygon Shapefiles.
- Added **xyz2shp**: converts XYZ point files (`.xyz`/`.csv`) to ESRI point Shapefiles, with `-d`/`--dimension` to choose 2D or 3D output.

## 0.23.0

- Removed **transzone1** and **transzone2**: these tools and their CLI entry points have been removed from the package.

## 0.22.4

- `getfacez` / `getfacez2`: Fixed the non-UTF-8 shapefile fallback never actually kicking in — the retry read used Python's encoding name `'latin-1'`, which GDAL's Shapefile driver doesn't recognize, so it silently fell back to the `.cpg`-declared encoding and raised the same `UnicodeDecodeError` again. Now uses GDAL's recognized name (`'LATIN1'`), so shapefiles with a mismatched `.cpg`/actual encoding read successfully, dropping only the field(s) with undecodable names instead of failing entirely.
- `getfacez` / `getfacez2`: The warning listing skipped non-UTF-8 field names could itself crash with `UnicodeEncodeError` on consoles using a non-UTF-8 codepage (e.g. Windows cp950). It now prints with unsupported characters replaced instead of raising.

## 0.22.3

- `getfacez` / `getfacez2`: Print total processing time (in seconds) to the console after extraction completes.

## 0.22.2

- `getfacez` / `getfacez2`: Output column now uses the detected/specified ID field name (e.g. `StationName`) instead of always being labeled `Point_ID`, falling back to `Point_ID` only when no name field was found.

## 0.22.1

- `d3dtoolsenv.yaml`: Added missing `scipy` and `openpyxl` dependencies (used by `getfacez`/`getfacez2` and Excel export), bumped `shapely` to `>=2.0.0` to match `requirements.txt`, and removed the unused `glob2` pip dependency.

## 0.22.0

- Changed **getfacez**: now the spatial-index accelerated implementation. Uses a shapely `STRtree` for point-in-polygon matching and a scipy `cKDTree` for nearest-neighbor matching instead of scanning every mesh face for every observation point, which speeds up processing on large meshes. Same CLI arguments, Python API, and output format as before. Requires `scipy` and `shapely>=2.0.0` (bumped from `>=1.8.0`).
- Keep original version of code in **getfacez2** as a fallback option.
- `getfacez` / `getfacez2`: Added `-if`/`--id-field` to specify which shapefile field to use for point names instead of relying on auto-detection (`Name`, `name`, `NAME`, `id`, `ID`, `Id`). Raises a clear error listing available fields if the specified field doesn't exist.

## 0.21.0

- Added **transzone1**: extracts triangle mesh faces from a faces shapefile, buffers and dissolves them into a transition zone (`trans_zone.shp`), then selects and dissolves all faces intersecting that zone (`trans_zone_faces.shp`).
- Added **transzone2**: buffers `trans_zone_faces.shp` inward, selects FlowFM faces that lie fully within the buffered zone, and dissolves them into a transition zone core (`trans_zone_core.shp`).

## 0.20.3

- `fou2shp`: Renamed `--rm` to `-r`/`--remove` for consistency with CLI conventions. Short form `-r` and long form `--remove` are now both accepted.

## 0.20.2

- Expanded README examples and documentation for existing features.

## 0.20.1

- `fou2shp`: Fixed output directory suffix for mask-filtered shapefiles to use `_RM` (uppercase) consistently.

## 0.20.0

- `fou2shp`: Added `--rm MASK.shp [...]` argument to remove output polygons that intersect one or more mask shapefiles. Glob patterns are supported (e.g. `--rm SHP/*.shp`). Filtered copies of all threshold shapefiles are written to `<out-dir>_RM/`. Requires `geopandas`; a clear error is shown if it is not installed.

## 0.19.4

- `evaluate_sensor2` / `eval_iot`: Corrected example values in help documentation — swapped the default buffer radii shown for `EMIC` and `淹水感測` sources to match recommended usage (`EMIC=30`, `淹水感測=20`).

## 0.19.3

- Enhanced `create_empty_mesh` to handle non-ASCII paths by using temporary files.
