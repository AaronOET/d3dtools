# Changelog

## 0.20.1

- `fou2shp`: Fixed output directory suffix for mask-filtered shapefiles to use `_RM` (uppercase) consistently.

## 0.20.0

- `fou2shp`: Added `--rm MASK.shp [...]` argument to remove output polygons that intersect one or more mask shapefiles. Glob patterns are supported (e.g. `--rm SHP/*.shp`). Filtered copies of all threshold shapefiles are written to `<out-dir>_RM/`. Requires `geopandas`; a clear error is shown if it is not installed.

## 0.19.4

- `evaluate_sensor2` / `eval_iot`: Corrected example values in help documentation — swapped the default buffer radii shown for `EMIC` and `淹水感測` sources to match recommended usage (`EMIC=30`, `淹水感測=20`).

## 0.19.3

- Enhanced `create_empty_mesh` to handle non-ASCII paths by using temporary files.
