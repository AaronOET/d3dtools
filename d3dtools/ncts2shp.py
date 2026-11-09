"""
Write threshold flood-extent shapefiles from the MAXIMUM water depth over time
in a Delft3D FM map output file (time series).

Unlike fou2shp (which reads the max depth already computed by the FOU output),
this tool computes the maximum of Mesh2d_waterdepth over all time steps itself,
reading the map file in chunks of time steps to limit memory.

Attribute: max depth over time [m] (stored as "max_dep")

Input : FlowFM_map.nc  (UGRID 2D map output, EPSG:3826 TWD97/TM2-121)
Output: SIM_thrd125.shp, SIM_thrd300.shp, SIM_thrd475.shp (+.dbf, .shx, .prj)
        Each holds the mesh faces whose max depth is >= threshold.
"""

import argparse
import contextlib
import glob as _glob
import os
import sys
import uuid
import warnings

import numpy as np

from .fou2shp import PRJ_WKT, THRESHOLDS, _SHP_DIR, gpd, nc, shapefile
from .fou2mfd import remove_masked, threshold_stem

SCRIPT = "ncts2shp"
NC_FILE = os.path.join("NC", "FlowFM_map.nc")
FIELD_NAME = "max_dep"  # DBF field names are limited to 10 characters
DEFAULT_THRESHOLDS = [t for t, _ in THRESHOLDS]  # 0.125, 0.3, 0.475 m
DEFAULT_CHUNK = 50

# Delft3D FM uses Mesh2d_ or mesh2d_ depending on version
DEPTH_CANDIDATES = ["Mesh2d_waterdepth", "mesh2d_waterdepth"]


def _is_ascii(s):
    return all(ord(c) < 128 for c in s)


def _short_path(path):
    """Windows 8.3 short path (ASCII) for a file, or None if unavailable."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
        if 0 < n < len(buf) and _is_ascii(buf.value):
            return buf.value
    except Exception:
        pass
    return None


@contextlib.contextmanager
def open_dataset(path):
    """
    Open a NetCDF file whose path may contain non-ASCII characters (e.g. 繁體中文)
    in the folder or the file name. netCDF-C on Windows often cannot open such
    paths, so fall back to:
      1. the Windows 8.3 short path, then
      2. a temporary ASCII-named hard link (no copy; same disk) opened by a
         relative name from inside its folder, removed afterwards.
    """
    path = os.path.abspath(path)
    link = None
    try:
        ds = nc.Dataset(path, "r")
    except (OSError, UnicodeError) as first_err:
        if _is_ascii(path):
            raise
        ds = None
        short = _short_path(path)
        if short:
            try:
                ds = nc.Dataset(short, "r")
                print(f"(non-ASCII path, opened via short path: {short})")
            except OSError:
                ds = None

        if ds is None:
            folder = os.path.dirname(path)
            link_name = f"_tmp_nc_{uuid.uuid4().hex[:8]}.nc"
            link = os.path.join(folder, link_name)
            try:
                os.link(path, link)
            except OSError as e:
                raise OSError(
                    f"Cannot open non-ASCII path {path!r} ({first_err}); "
                    f"hard link failed ({e}). Rename the file/folder to ASCII."
                ) from e
            cwd = os.getcwd()
            try:
                os.chdir(folder)  # relative ASCII name avoids the non-ASCII folder too
                ds = nc.Dataset(link_name, "r")
                print(f"(non-ASCII path, opened via temporary link: {link_name})")
            except Exception:
                os.remove(link)
                raise
            finally:
                os.chdir(cwd)
    try:
        yield ds
    finally:
        ds.close()
        if link and os.path.exists(link):
            os.remove(link)


def find_var(ds, candidates, suffix=None):
    """Return the first variable name found in candidates (or ending with suffix)."""
    for name in candidates:
        if name in ds.variables:
            return name
    if suffix:
        for name in ds.variables:
            if name.lower().endswith(suffix.lower()):
                return name
    raise KeyError(f"none of {candidates} found in file")


def max_over_time(var, chunk=DEFAULT_CHUNK, verbose=True):
    """Max of a (time, faces) variable over time, read in chunks to limit memory."""
    if "time" not in var.dimensions:
        raise ValueError(f"variable {var.name} has no 'time' dimension: {var.dimensions}")
    time_dim = var.dimensions.index("time")
    nt = var.shape[time_dim]
    vmax = None
    for t0 in range(0, nt, chunk):
        t1 = min(t0 + chunk, nt)
        idx = [slice(None)] * var.ndim
        idx[time_dim] = slice(t0, t1)
        block = np.ma.filled(np.ma.asarray(var[tuple(idx)], dtype=np.float64), np.nan)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices (dry faces)
            bmax = np.nanmax(block, axis=time_dim)
        vmax = bmax if vmax is None else np.fmax(vmax, bmax)
        if verbose:
            print(f"\r  time steps {t1}/{nt}", end="", flush=True)
    if verbose:
        print()
    return vmax


def read_mesh(ds):
    """Return (faces, node_x, node_y): faces is a list of 0-based node index arrays."""
    nx = find_var(ds, ["Mesh2d_node_x", "mesh2d_node_x"], suffix="_node_x")
    ny = find_var(ds, ["Mesh2d_node_y", "mesh2d_node_y"], suffix="_node_y")
    fnn = find_var(ds, ["Mesh2d_face_nodes", "mesh2d_face_nodes"], suffix="_face_nodes")
    node_x = np.asarray(ds.variables[nx][:])
    node_y = np.asarray(ds.variables[ny][:])
    fn_var = ds.variables[fnn]
    fn = np.ma.asarray(fn_var[:])
    start = int(getattr(fn_var, "start_index", 1))  # D-Flow FM uses 1-based
    faces = [row.compressed().astype(np.int64) - start for row in fn]
    return faces, node_x, node_y


def write_threshold_shp(shp_out, threshold, faces, node_x, node_y, values):
    """Write faces whose max depth >= threshold as polygons; return the polygon count."""
    w = shapefile.Writer(shp_out, shapefile.POLYGON)
    w.autoBalance = 1
    w.field(FIELD_NAME, "N", size=18, decimal=6)

    n_written = 0
    for i in np.nonzero(np.nan_to_num(values, nan=-np.inf) >= threshold)[0]:
        node_idx = faces[i]
        if len(node_idx) < 3:
            continue
        ring = [(float(node_x[j]), float(node_y[j])) for j in node_idx]
        ring.append(ring[0])
        w.poly([ring])
        w.record(float(values[i]))
        n_written += 1
    w.close()

    with open(shp_out + ".prj", "w") as f:
        f.write(PRJ_WKT)
    print(f"  {shp_out}.shp  - {n_written} polygons")
    return n_written


def max_depth(nc_file=NC_FILE, var=None, chunk=DEFAULT_CHUNK, verbose=True):
    """Return (max_depth, faces, node_x, node_y, var_name) from a map NetCDF file."""
    with open_dataset(nc_file) as ds:
        var_name = var or find_var(ds, DEPTH_CANDIDATES, suffix="_waterdepth")
        if var_name not in ds.variables:
            raise KeyError(var_name)
        faces, node_x, node_y = read_mesh(ds)
        v = ds.variables[var_name]
        if verbose:
            print(f"Input  : {nc_file}")
            print(f"Var    : {var_name}  {v.dimensions}")
            print(f"Faces  : {len(faces)}")
            print("Computing maximum depth over time ...")
        vmax = max_over_time(v, chunk, verbose)
    if vmax.shape[0] != len(faces):
        raise ValueError(f"{var_name} has {vmax.shape[0]} values but the mesh has "
                         f"{len(faces)} faces (not a face variable?)")
    return vmax, faces, node_x, node_y, var_name


def ncts2shp(nc_file=NC_FILE, thresholds=None, out_dir=_SHP_DIR, var=None,
             chunk=DEFAULT_CHUNK, remove=None):
    """
    Compute the max water depth over time in `nc_file` and write, for every
    threshold (m), the faces with depth >= it to <out_dir>/SIM_thrd<mm>.shp.
    With `remove` (mask shapefiles), filtered copies go to <out_dir>_RM.
    Return the paths written (without .shp).
    """
    thresholds = DEFAULT_THRESHOLDS if thresholds is None else list(thresholds)
    if chunk < 1:
        raise ValueError("chunk must be >= 1")
    if remove and gpd is None:
        raise ImportError("removing masked polygons requires geopandas: pip install geopandas")
    os.makedirs(out_dir, exist_ok=True)

    vmax, faces, node_x, node_y, _ = max_depth(nc_file, var, chunk)
    if np.all(np.isnan(vmax)):
        print("Max depth: all values missing")
    else:
        print(f"Max depth range: {np.nanmin(vmax):.3f} - {np.nanmax(vmax):.3f} m")
    print("CRS    : EPSG:3826  (TWD97 / TM2 zone 121)\n")

    written = []
    for threshold in thresholds:
        stem = threshold_stem(threshold)
        print(f"Threshold >= {threshold:g} m  ->  {stem}")
        shp_out = os.path.join(out_dir, stem)
        write_threshold_shp(shp_out, threshold, faces, node_x, node_y, vmax)
        written.append(shp_out)

    if remove:
        rm_out_dir = out_dir.rstrip("/\\") + "_RM"
        print(f"\nApplying mask removal  ->  {rm_out_dir}/")
        written += [remove_masked(p + ".shp", remove, rm_out_dir)[:-4] for p in list(written)]
    return written


def main():
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Compute the maximum water depth over all time steps of a Delft3D FM "
                    "map file and write threshold flood-extent shapefiles "
                    "(like fou2shp, but from the map time series instead of the FOU output).",
        epilog="""
examples:
  %(prog)s                                  (NC/FlowFM_map.nc -> SHP/SIM_thrd125/300/475.shp)
  %(prog)s B01-TPE_TM_EVENT.nc -of SHP
  %(prog)s FlowFM_map.nc -t 0.3             (-> SHP/SIM_thrd300.shp only)
  %(prog)s FlowFM_map.nc -t 0.1 0.3 0.5
  %(prog)s FlowFM_map.nc --var Mesh2d_waterdepth --chunk 100
  %(prog)s FlowFM_map.nc -r SHP/EXCLUDE.shp (filtered copies -> SHP_RM/)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", default=NC_FILE,
                        help=f"Input map NetCDF file (default: {NC_FILE})")
    parser.add_argument("-t", "--threshold", type=float, nargs="+", default=None, metavar="M",
                        help="Flood depth threshold(s) in metres; one shapefile "
                             "SIM_thrd<mm> per threshold (default: 0.125 0.3 0.475)")
    parser.add_argument("-of", "--output-folder", dest="out_dir", default=_SHP_DIR,
                        metavar="DIR", help="Output directory for the shapefiles (default: SHP)")
    parser.add_argument("--var", default=None,
                        help="Water depth variable (default: auto-detect Mesh2d_waterdepth)")
    parser.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, metavar="N",
                        help="Time steps read per chunk (default: %(default)s)")
    parser.add_argument("-r", "--remove", nargs="+", metavar="MASK.shp",
                        help="Remove output polygons that intersect these mask shapefiles. "
                             "Filtered copies are written to <output-folder>_RM. "
                             "Glob patterns are supported (e.g. --remove SHP/*.shp).")
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"Error: Input NetCDF file does not exist: {args.path}")
        sys.exit(1)
    if args.threshold and any(t < 0 for t in args.threshold):
        parser.error("thresholds must be >= 0")
    if args.chunk < 1:
        parser.error("--chunk must be >= 1")

    masks = []
    for pattern in args.remove or []:
        matched = _glob.glob(pattern)
        if not matched:
            print(f"Warning: --remove pattern matched no files: {pattern}")
        masks.extend(matched)

    try:
        ncts2shp(args.path, args.threshold, args.out_dir, args.var, args.chunk, masks)
    except KeyError as e:
        print(f"Error: Variable not found in NetCDF file: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error processing NetCDF file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
