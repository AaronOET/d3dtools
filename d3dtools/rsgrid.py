"""
Restore (re-insert) the 2D computational mesh into a Deltares D-Flow FM
(.dsproj) project by cloning the intact 2D mesh from a source project, while
preserving the target project's own 1D network.

What it does
------------
1. Locates the FlowFM.mdu inside <project>.dsproj_data/.../input/ for both the
   target (-i) and the source (-s) projects.
2. Reads the NetFile path from the [geometry] section of each .mdu.
3. Reports the mesh state (2D nodes / edges / faces / face_z and 1D nodes /
   branches) of both net files.
4. Backs up the target net file with a timestamped copy before overwriting.
5. Writes a new net file that keeps EVERYTHING from the target (its 1D network,
   coordinate system, etc.) and injects ONLY the 2D mesh variables
   (``Mesh2d_*``, including the per-face bed levels ``Mesh2d_face_z``) taken
   from the source. The target's 1D network is preserved untouched.

This is the inverse of ``rmgrid``: where ``rmgrid`` empties the 2D mesh while
keeping the 1D network, this tool copies a complete 2D mesh back in from a
project that still has one -- again keeping the (target's) 1D network intact.

Why the merge matters
---------------------
An earlier version cloned the whole source net file over the target, which
wiped out any 1D network the user had added to the target. D-HYDRO then failed
to open the project with errors such as "cross section ... has a branch id
(Channel_1D_1) which is not available in the model", because crsloc.ini /
crsdef.ini still referenced 1D branches that no longer existed in the net file.
Merging only the 2D mesh (this version) avoids that.
"""

import argparse
import datetime
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

try:
    import netCDF4 as nc
except ImportError:
    sys.exit("Missing package: pip install netCDF4")


# Variable that holds the per-face bed levels ("face z values").
FACE_Z_VAR = "Mesh2d_face_z"

# Prefix that identifies all 2D-mesh dimensions and variables in a D-HYDRO
# UGRID net file (Mesh2d, Mesh2d_node_x, Mesh2d_nFaces, Mesh2d_face_z, ...).
MESH2D_PREFIX = "Mesh2d"


def _is_mesh2d_dim(name):
    """True if *name* is a 2D-mesh dimension."""
    return name.startswith(MESH2D_PREFIX)


def _is_mesh2d_var(name, var):
    """True if a variable belongs to the 2D mesh (by name or by dimension)."""
    if name.startswith(MESH2D_PREFIX):
        return True
    return any(d.startswith(MESH2D_PREFIX) for d in var.dimensions)


def find_mdu(dsproj_path):
    """Return the path to the .mdu file inside the dsproj_data directory."""
    dsproj_path = Path(dsproj_path)
    data_dir = dsproj_path.with_suffix("").with_name(
        dsproj_path.stem + ".dsproj_data"
    )
    candidates = sorted(data_dir.rglob("*.mdu"))
    if not candidates:
        sys.exit(f"Error: no .mdu file found under {data_dir}")
    if len(candidates) > 1:
        print(f"Warning: multiple .mdu files found; using {candidates[0]}")
    return candidates[0]


def read_net_file_from_mdu(mdu_path):
    """Parse [geometry] NetFile from an MDU file and return the absolute path."""
    net_file = None
    in_geometry = False
    with open(mdu_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if re.match(r"^\[geometry\]", stripped, re.IGNORECASE):
                in_geometry = True
                continue
            if in_geometry and stripped.startswith("["):
                break
            if in_geometry:
                m = re.match(r"^\s*NetFile\s*=\s*(.+)", stripped, re.IGNORECASE)
                if m:
                    net_file = m.group(1).split("#")[0].strip()
                    break

    if not net_file:
        sys.exit("Error: NetFile not found in [geometry] section of MDU.")

    net_path = Path(net_file)
    if not net_path.is_absolute():
        net_path = Path(mdu_path).parent / net_path
    return net_path.resolve()


def resolve_net_file(path):
    """Resolve *path* to a net file.

    Accepts either a ``.dsproj`` project (the net file is located via its MDU)
    or a ``.nc`` net file given directly. Returns the resolved net file Path.
    """
    p = Path(path).resolve()
    if not p.exists():
        sys.exit(f"Error: path does not exist: {p}")
    if p.suffix.lower() == ".dsproj":
        mdu_path = find_mdu(p)
        print(f"MDU     : {mdu_path}")
        return read_net_file_from_mdu(mdu_path)
    # Treat anything else as a net file given directly.
    return p


def net_summary(path):
    """Return a dict summarising 2D and 1D content of a net file.

    Reads through an ASCII-safe temp copy, because netCDF4's C layer cannot
    open paths with non-ASCII characters on Windows.
    """
    fd, tmp = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        shutil.copy2(path, tmp)
        with nc.Dataset(tmp, "r") as ds:
            dims = {k: len(v) for k, v in ds.dimensions.items()}
            return {
                "nodes": dims.get("Mesh2d_nNodes", 0),
                "edges": dims.get("Mesh2d_nEdges", 0),
                "faces": dims.get("Mesh2d_nFaces", 0),
                "has_face_z": FACE_Z_VAR in ds.variables,
                "mesh1d_nodes": dims.get("mesh1d_nNodes", 0),
                "branches": dims.get("network_nEdges", 0),
                "has_1d": "mesh1d" in ds.variables or "network" in ds.variables,
            }
    finally:
        os.unlink(tmp)


def _fmt_2d(s):
    return (f"{s['nodes']} nodes, {s['edges']} edges, {s['faces']} faces, "
            f"face_z={'yes' if s['has_face_z'] else 'NO'}")


def _fmt_1d(s):
    return (f"1D {'yes' if s['has_1d'] else 'NO'} "
            f"(branches={s['branches']}, mesh1d nodes={s['mesh1d_nodes']})")


def merge_mesh(src_path, tgt_path, out_path):
    """Write *out_path* = target with the source's 2D mesh injected.

    netCDF4's C layer cannot open paths with non-ASCII characters on Windows,
    so the work is done through ASCII-safe temp files.
    """
    fd, tmp_src = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    fd, tmp_tgt = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    fd, tmp_out = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        shutil.copy2(src_path, tmp_src)
        shutil.copy2(tgt_path, tmp_tgt)
        _merge_mesh_impl(tmp_src, tmp_tgt, tmp_out)
        shutil.copy2(tmp_out, out_path)
    finally:
        for t in (tmp_src, tmp_tgt, tmp_out):
            if os.path.exists(t):
                os.unlink(t)


def _merge_mesh_impl(src_path, tgt_path, out_path):
    """Merge: keep all non-2D content from target, take 2D mesh from source.

    All three paths must be ASCII-safe.
    """
    with nc.Dataset(src_path) as src, nc.Dataset(tgt_path) as tgt, \
            nc.Dataset(out_path, "w", format=tgt.data_model) as out:

        # Global attributes: preserve the target's (project/model settings).
        out.setncatts({a: tgt.getncattr(a) for a in tgt.ncattrs()})

        def ensure_dim(ds, name):
            """Create *name* in out from ds if it isn't there yet."""
            if name in out.dimensions:
                return
            d = ds.dimensions[name]
            out.createDimension(name, None if d.isunlimited() else len(d))

        def copy_var(ds, name):
            """Copy variable *name* (attrs + data) from ds into out."""
            var = ds.variables[name]
            for d in var.dimensions:
                ensure_dim(ds, d)
            fill = var.getncattr("_FillValue") if "_FillValue" in var.ncattrs() else None
            out_var = out.createVariable(
                name, var.datatype, var.dimensions, fill_value=fill
            )
            out_var.setncatts({a: var.getncattr(a) for a in var.ncattrs()
                               if a != "_FillValue"})
            # Only copy data when every dimension has a positive length; a
            # zero-length dimension means there is nothing to write. Scalar
            # variables (no dimensions) satisfy this vacuously and are copied.
            if all(len(out.dimensions[d]) > 0 for d in var.dimensions):
                out_var[:] = var[:]

        # 1) Non-2D dimensions from the target (1D network, CRS helpers, ...).
        for name, dim in tgt.dimensions.items():
            if _is_mesh2d_dim(name):
                continue
            out.createDimension(name, None if dim.isunlimited() else len(dim))

        # 2) 2D-mesh dimensions from the source.
        for name, dim in src.dimensions.items():
            if not _is_mesh2d_dim(name):
                continue
            if name in out.dimensions:
                continue
            out.createDimension(name, None if dim.isunlimited() else len(dim))

        # 3) Non-2D variables from the target (preserve the 1D network etc.).
        for name, var in tgt.variables.items():
            if _is_mesh2d_var(name, var):
                continue
            copy_var(tgt, name)

        # 4) 2D-mesh variables from the source.
        for name, var in src.variables.items():
            if not _is_mesh2d_var(name, var):
                continue
            if name in out.variables:
                continue
            copy_var(src, name)


def main():
    """Main function for the command line interface."""
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Restore the 2D computational mesh (incl. Mesh2d_face_z bed "
                    "levels) into a D-Flow FM .dsproj project by cloning it from "
                    "a source project, while preserving the target's 1D network.",
        epilog="""
examples:
  %(prog)s -s Intact.dsproj                 # restore into first .dsproj in cwd
  %(prog)s -i Stripped.dsproj -s Intact.dsproj
  %(prog)s -i target_net.nc -s source_net.nc
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        help="Path to the target .dsproj (or .nc) to restore the mesh INTO "
             "(default: first .dsproj found in current directory)",
    )
    parser.add_argument(
        "-s",
        "--source",
        required=True,
        help="Path to the source .dsproj (or .nc) with the intact 2D mesh to "
             "restore the mesh FROM",
    )
    args = parser.parse_args()

    # Resolve the target project / net file.
    if args.input:
        target = Path(args.input).resolve()
        if not target.exists():
            print(f"Error: target does not exist: {target}")
            sys.exit(1)
    else:
        matches = list(Path(".").glob("*.dsproj"))
        if not matches:
            print("Error: no .dsproj file found in current directory.")
            sys.exit(1)
        if len(matches) > 1:
            names = ", ".join(p.name for p in matches)
            print(
                f"Error: multiple .dsproj files found ({names}). "
                "Specify one explicitly with -i."
            )
            sys.exit(1)
        target = matches[0].resolve()

    print(f"Target  : {target}")
    print(f"Source  : {args.source}")

    try:
        dst = resolve_net_file(target)
        print(f"NetFile (target) : {dst}")
        src = resolve_net_file(args.source)
        print(f"NetFile (source) : {src}")

        if not src.exists():
            print(f"Error: source NetFile not found at {src}")
            sys.exit(1)
        if not dst.exists():
            print(f"Error: target NetFile not found at {dst}")
            sys.exit(1)

        # Report state before.
        s = net_summary(src)
        d = net_summary(dst)
        print(f"\nSource : 2D {_fmt_2d(s)}")
        print(f"         {_fmt_1d(s)}")
        print(f"Target : 2D {_fmt_2d(d)}   (before restore)")
        print(f"         {_fmt_1d(d)}")

        if s["faces"] == 0 or not s["has_face_z"]:
            sys.exit("\nError: source net file has no 2D mesh / no face_z values. "
                     "Aborting.")

        if d["faces"] == s["faces"] and d["has_face_z"]:
            print("\nTarget already contains a 2D mesh with the same face count "
                  "and face_z values. Nothing to do.")
            return

        # Back up the target before overwriting.
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = f"{dst}.bak_{stamp}"
        shutil.copy2(dst, backup)
        print(f"\nBackup written: {backup}")

        # Merge the source's 2D mesh into the target, writing atomically.
        tmp_path = dst.with_suffix(".nc.tmp")
        try:
            merge_mesh(src, dst, tmp_path)
            if dst.exists():
                dst.unlink()
            tmp_path.rename(dst)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        print(f"  2D mesh restored (1D network preserved) -> {dst}")

        # Verify.
        d2 = net_summary(dst)
        print(f"Target : 2D {_fmt_2d(d2)}   (after restore)")
        print(f"         {_fmt_1d(d2)}")

        mesh_ok = (d2["nodes"] == s["nodes"] and d2["edges"] == s["edges"]
                   and d2["faces"] == s["faces"] and d2["has_face_z"])
        one_d_ok = (d2["branches"] == d["branches"]
                    and d2["mesh1d_nodes"] == d["mesh1d_nodes"]
                    and d2["has_1d"] == d["has_1d"])

        if mesh_ok and one_d_ok:
            print("\nSuccess: 2D mesh (including face z values) restored and the "
                  "target's 1D network preserved.")
            print("Open the target .dsproj in D-HYDRO to confirm the grid is back.")
        else:
            if not mesh_ok:
                print("\nWarning: post-restore 2D mesh does not match the source.")
            if not one_d_ok:
                print("\nWarning: the target's 1D network changed during restore.")
            print(f"Original target preserved at:\n    {backup}")
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"Error processing project: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
