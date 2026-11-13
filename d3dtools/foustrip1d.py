"""
Copy Delft3D FM NetCDF output files (e.g. *_fou.nc, *_map.nc) without the 1D
network and 1D2D links, keeping only the 2D mesh and its results, so they open
as a pure 2D UGRID file (e.g. a QGIS mesh layer, or fou2shp / fou2mfd).

Removed variables:
  mesh1d*         1D mesh topology and all 1D results
  network*        1D network branches, nodes and geometry
  links*          1D2D contact links
  composite_mesh  combined 1D+2D mesh topology

Everything else (Mesh2d*, projection, time, global attributes) is copied
unchanged and bit-identical (no masking / scaling), in the same NetCDF format
as the input. Only the dimensions still used by a kept variable are created;
unlimited dimensions stay unlimited.

Input : one or more files, folders (every *_fou.nc in them) or glob patterns
Output: FOU_2D/<name>.nc, same name as the input (FOU_2D in the current folder;
        -of DIR for another folder, -o FILE for a single input, --suffix to add
        e.g. _2d to the names; -of "" for next to each input needs a --suffix)
"""

import argparse
import glob as _glob
import os
import sys

import numpy as np

from .ncutils import open_nc

SCRIPT = "foustrip1d"
SUFFIX = ""  # added to the output names (e.g. "_2d"); none by default
OUT_DIR = "FOU_2D"  # default output folder (in the current folder)
FOLDER_PATTERN = "*_fou.nc"
# variable name prefixes / names of the 1D part (compared case-insensitively)
DROP_PREFIXES = ("mesh1d", "network", "links")
DROP_NAMES = ("composite_mesh",)
CHUNK_BYTES = 256 * 1024 ** 2  # max bytes read per slab when copying a variable


def is_1d_var(name):
    """True for a variable of the 1D network / 1D2D links / composite mesh."""
    low = name.lower()
    return low.startswith(DROP_PREFIXES) or low in DROP_NAMES


def default_output(src, out_dir=OUT_DIR, suffix=SUFFIX):
    """<name><suffix>.nc in `out_dir` (default FOU_2D), or next to `src` when `out_dir` is empty."""
    stem, ext = os.path.splitext(os.path.basename(src))
    return os.path.join(out_dir or os.path.dirname(src), f"{stem}{suffix}{ext or '.nc'}")


def find_inputs(targets, pattern=FOLDER_PATTERN, suffix=SUFFIX):
    """
    Expand files, folders (every `pattern` file in them) and glob patterns to a
    sorted list of files. With a `suffix`, outputs of an earlier run
    (*<suffix>.nc) are skipped when found through a folder or glob.
    """
    files = []
    for t in targets:
        if os.path.isdir(t):
            found = _glob.glob(os.path.join(t, pattern))
        elif os.path.isfile(t):
            files.append(t)
            continue
        else:
            found = _glob.glob(t)
            if not found:
                print(f"Warning: no file matches {t}")
        files += [f for f in found
                  if os.path.isfile(f)
                  and not (suffix and os.path.splitext(f)[0].endswith(suffix))]
    seen, out = set(), []
    for f in files:
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _copy_data(v, o, chunk_bytes=CHUNK_BYTES):
    """Copy the values of variable v to o, in slabs along the first dimension."""
    if v.ndim == 0:
        o.assignValue(v.getValue())
        return
    n = v.shape[0]
    if n == 0:
        return
    itemsize = getattr(v.dtype, "itemsize", 0) or 8  # vlen strings: a guess
    row = max(1, int(np.prod(v.shape[1:], dtype=np.int64)) * itemsize)
    step = max(1, chunk_bytes // row)
    for i in range(0, n, step):
        j = min(i + step, n)  # clamp: slicing past the end would grow an unlimited dim
        o[i:j] = v[i:j]


def strip1d(src, dst=None, overwrite=True, check=False, verbose=True):
    """
    Copy `src` to `dst` (default FOU_2D/<name>.nc) without the 1D
    network, 1D2D links and composite mesh. Return (dst, kept, removed) with
    the lists of variable names. With `check`, nothing is written.
    """
    dst = dst or default_output(src)
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        raise ValueError("output must differ from the input (it would be truncated)")
    if os.path.exists(dst) and not overwrite and not check:
        raise FileExistsError(f"output exists (use --overwrite): {dst}")

    s = open_nc(src)
    try:
        keep = [k for k in s.variables if not is_1d_var(k)]
        removed = [k for k in s.variables if is_1d_var(k)]
        if verbose:
            print(f"{src}  ->  {dst}")
            print(f"  {len(keep)} variable(s) kept, {len(removed)} removed"
                  + ("" if removed else " (no 1D part found)"))
        if check:
            return dst, keep, removed
        if s.groups:
            print("  Warning: groups are not copied: " + ", ".join(s.groups))

        out_dir = os.path.dirname(os.path.abspath(dst))
        os.makedirs(out_dir, exist_ok=True)
        d = open_nc(dst, "w", format=s.data_model)
        try:
            d.setncatts({a: s.getncattr(a) for a in s.ncattrs()})
            dims = []
            for k in keep:
                for dn in s[k].dimensions:
                    if dn not in dims:
                        dims.append(dn)
            for n in [n for n in s.dimensions if n in dims]:  # keep the input order
                dim = s.dimensions[n]
                d.createDimension(n, None if dim.isunlimited() else len(dim))
            for k in keep:
                v = s[k]
                v.set_auto_maskandscale(False)
                fv = v.getncattr("_FillValue") if "_FillValue" in v.ncattrs() else None
                o = d.createVariable(k, v.datatype, v.dimensions, fill_value=fv, zlib=False)
                o.setncatts({a: v.getncattr(a) for a in v.ncattrs() if a != "_FillValue"})
                o.set_auto_maskandscale(False)
                _copy_data(v, o)
        except BaseException:
            d.close()
            if os.path.exists(dst):
                os.remove(dst)  # no half-written output
            raise
        d.close()
    finally:
        s.close()
    if verbose:
        mb_in, mb_out = os.path.getsize(src) / 1024 ** 2, os.path.getsize(dst) / 1024 ** 2
        print(f"  {mb_in:.1f} MB -> {mb_out:.1f} MB")
    return dst, keep, removed


def main():
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Copy Delft3D FM NetCDF output (*_fou.nc, *_map.nc) without the 1D "
                    "network and 1D2D links, keeping only the 2D mesh and its results "
                    "(output FOU_2D/<name>.nc, same name as the input).",
        epilog="""
examples:
  %(prog)s                                       (every *_fou.nc in the current folder -> FOU_2D)
  %(prog)s FOU\\TPE_TM_0000_fou.nc               (-> FOU_2D\\TPE_TM_0000_fou.nc)
  %(prog)s FOU\\TPE_TM_0000_fou.nc -o 2D.nc      (another output name)
  %(prog)s FOU                                   (every *_fou.nc in FOU -> FOU_2D)
  %(prog)s FOU -of OUT_2D                        (outputs into OUT_2D instead)
  %(prog)s FOU --suffix _2d                      (-> FOU_2D\\*_fou_2d.nc)
  %(prog)s FOU -of "" --suffix _2d               (outputs next to the inputs: FOU\\*_fou_2d.nc)
  %(prog)s "FOU/*_fou.nc" output/*_map.nc        (glob patterns)
  %(prog)s FOU --pattern "*_map.nc"              (other files in a folder)
  %(prog)s FOU --check                           (list what would be removed, write nothing)
  %(prog)s FOU --no-overwrite                    (skip outputs that exist)

notes:
  - The output folder (default FOU_2D, in the current folder) is created if missing.
  - The output has the same name as the input, so it must go to another folder
    (-of "" needs a --suffix). With a --suffix, outputs of an earlier run
    (*<suffix>.nc) are skipped when found through a folder or glob.
  - The output is not compressed, so it is about the size of the 2D part of the input.
  - Global attributes are copied as they are; any reference to the removed 1D or
    composite mesh in them is out of date (this does not affect the 2D mesh).
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="*", default=["."], metavar="INPUT",
                        help="NetCDF file(s), folder(s) or glob pattern(s) "
                             "(default: every *_fou.nc in the current folder)")
    parser.add_argument("-o", "--output", default=None, metavar="FILE",
                        help="Output file (only with a single input; "
                             "default: FOU_2D/<name>.nc)")
    parser.add_argument("-of", "--output-folder", dest="out_dir", default=OUT_DIR, metavar="DIR",
                        help="Folder for the outputs, created if missing (default: %(default)s "
                             "in the current folder; \"\" for next to each input)")
    parser.add_argument("--pattern", default=FOLDER_PATTERN,
                        help="Files taken from a folder input (default: %(default)s)")
    parser.add_argument("--suffix", default=SUFFIX,
                        help="Added to the output names, e.g. _2d (default: none)")
    parser.add_argument("--no-overwrite", action="store_true",
                        help="Skip an input whose output already exists")
    parser.add_argument("--check", action="store_true",
                        help="Only list the variables that would be removed; write nothing")
    args = parser.parse_args()

    if not args.suffix and not args.out_dir and not args.output:
        parser.error('-of "" (next to the inputs) needs a --suffix, e.g. --suffix _2d '
                     "(the input would be overwritten)")
    files = find_inputs(args.inputs, args.pattern, args.suffix)
    if not files:
        print(f"Error: no input files found ({', '.join(args.inputs)}; "
              f"folders are searched for {args.pattern})")
        sys.exit(1)
    if args.output and len(files) > 1:
        parser.error(f"-o needs a single input, found {len(files)} files; use -of instead")

    n_ok = n_skip = n_err = 0
    for f in files:
        dst = args.output or default_output(f, args.out_dir, args.suffix)
        if args.no_overwrite and os.path.exists(dst) and not args.check:
            print(f"{f}  ->  {dst}  (exists, skipped)")
            n_skip += 1
            continue
        try:
            _, _, removed = strip1d(f, dst, check=args.check)
            if args.check and removed:
                print("  removed: " + ", ".join(removed))
            n_ok += 1
        except Exception as e:
            print(f"  Error: {e}")
            n_err += 1
    if len(files) > 1:
        print(f"\n{n_ok} file(s) {'checked' if args.check else 'written'}"
              + (f", {n_skip} skipped" if n_skip else "")
              + (f", {n_err} failed" if n_err else ""))
    if n_err:
        sys.exit(1)


if __name__ == "__main__":
    main()
