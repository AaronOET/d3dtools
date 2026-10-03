#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
expgrid - export the 2D grid (Mesh2d) of a D-Flow FM (Delft3D FM) UGRID net
file (*_net.nc) to a new, 2D-only net file.

The Mesh2d variables (nodes, edges, faces, ...) and the coordinate-system
variable are copied as-is, together with the global attributes.  The cell
bed levels (Mesh2d_face_z) are cleared unless --face-z is given.  Everything
else is dropped: the 1D network, mesh1d, the 1D2D links and the composite
mesh.  The input file is never modified.

The result can be loaded as the grid of another model, opened in RGFGRID /
the FM Suite, or used with MeshKernel (which has no file I/O of its own).

Usage
-----
    expgrid <input-folder | model.mdu | *_net.nc>
    expgrid FlowFM_net.nc -o grid.nc
    expgrid FlowFM_net.nc --face-z       (keep the Mesh2d_face_z bed levels)
    expgrid FlowFM_net.nc --check        (list what is kept / dropped)

Requires: netCDF4      (pip install netCDF4)
"""

from __future__ import annotations

import argparse
import os
import sys

from .ncutils import find_mesh2d, open_nc, resolve_netfile

SCRIPT = "expgrid"


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def select_variables(ds, mesh=None, face_z=False):
    """
    Return (mesh name, variables to keep, variables to drop).

    Kept are the 2D mesh_topology variable, every variable whose name starts
    with that mesh name (e.g. Mesh2d_node_x) and the coordinate-system
    variable(s) (any variable with grid_mapping_name, or named
    projected_coordinate_system / wgs84).  <mesh>_face_z is dropped unless
    `face_z` is True.
    """
    mesh = mesh or find_mesh2d(ds)
    keep, drop = [], []
    for name, var in ds.variables.items():
        if name == mesh + "_face_z" and not face_z:
            drop.append(name)
        elif name.startswith(mesh) or \
                name in ("projected_coordinate_system", "wgs84") or \
                hasattr(var, "grid_mapping_name"):
            keep.append(name)
        else:
            drop.append(name)
    return mesh, keep, drop


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def export_grid(netfile, output, mesh=None, face_z=False):
    """
    Copy the 2D grid of `netfile` to a new 2D-only net file `output`.

    Parameters
    ----------
    netfile : str
        D-Flow FM UGRID net file (*_net.nc).
    output : str
        Net file to write (overwritten if it exists).
    mesh : str, optional
        Name of the 2D mesh_topology variable (default: detected).
    face_z : bool
        Keep the cell bed levels (<mesh>_face_z); by default they are cleared.

    Returns
    -------
    dict
        mesh (str), kept (list of variable names), dropped (list of variable
        names), dims (dict name -> length of the dimensions written).
    """
    if os.path.abspath(output) == os.path.abspath(netfile):
        raise ValueError("output must differ from the input net file")

    with open_nc(netfile) as src:
        src.set_auto_maskandscale(False)
        mesh, keep, drop = select_variables(src, mesh, face_z)
        dims = {d for name in keep for d in src.variables[name].dimensions}
        # keep the source dimension order
        dims = [d for d in src.dimensions if d in dims]

        with open_nc(output, "w", format=src.data_model) as dst:
            dst.setncatts(src.__dict__)
            for name in dims:
                dim = src.dimensions[name]
                dst.createDimension(name, None if dim.isunlimited() else len(dim))
            for name in keep:
                var = src.variables[name]
                fill = var.__dict__.get("_FillValue")
                out = dst.createVariable(name, var.datatype, var.dimensions,
                                         fill_value=fill)
                out.set_auto_maskandscale(False)
                out.setncatts({k: v for k, v in var.__dict__.items()
                               if k != "_FillValue"})
                out[...] = var[...]
            written = {name: len(dst.dimensions[name]) for name in dims}

    return dict(mesh=mesh, kept=keep, dropped=drop, dims=written)


def print_plan(mesh, keep, drop):
    print("  2D mesh       : %s" % mesh)
    print("  keep    (%3d) : %s" % (len(keep), ", ".join(keep) or "-"))
    print("  drop    (%3d) : %s" % (len(drop), ", ".join(drop) or "-"))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Export the 2D grid (Mesh2d) of a D-Flow FM net file to a new "
                    "2D-only net file, dropping the 1D network, mesh1d, the 1D2D "
                    "links and the composite mesh. The cell bed levels (Mesh2d_face_z) "
                    "are cleared unless --face-z is given. The input is never "
                    "modified.",
        epilog="""
examples:
  %(prog)s dflowfm                      (input folder; output ./<netfile>_2d.nc)
  %(prog)s dflowfm/FlowFM.mdu
  %(prog)s FlowFM_net.nc -o grid.nc
  %(prog)s FlowFM_net.nc --face-z       (keep the Mesh2d_face_z bed levels)
  %(prog)s FlowFM_net.nc --check        (list what is kept / dropped, write nothing)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model",
                    help="model input folder, the .mdu, or the *_net.nc itself")
    ap.add_argument("-o", "--output", default=None,
                    help="output net file (default ./<netfile>_2d.nc in the "
                         "current directory)")
    ap.add_argument("--mesh", default=None,
                    help="name of the 2D mesh variable (default: detected)")
    ap.add_argument("-z", "--face-z", action="store_true",
                    help="keep the cell bed levels (Mesh2d_face_z); "
                         "by default they are cleared")
    ap.add_argument("-f", "--force", action="store_true",
                    help="overwrite the output file if it exists")
    ap.add_argument("--check", action="store_true",
                    help="list the variables kept / dropped, write nothing")
    args = ap.parse_args(argv)

    try:
        netfile = resolve_netfile(args.model)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))
    stem = os.path.splitext(os.path.basename(netfile))[0]
    out = os.path.abspath(args.output or stem + "_2d.nc")

    print("Reading", netfile)
    if args.check:
        with open_nc(netfile) as ds:
            print_plan(*select_variables(ds, args.mesh, args.face_z))
        print("\nCheck only - nothing written.")
        return 0

    if os.path.abspath(out) == os.path.abspath(netfile):
        ap.error("output must differ from the input net file")
    if os.path.exists(out) and not args.force:
        ap.error("output exists: %s (use -f to overwrite)" % out)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    res = export_grid(netfile, out, args.mesh, args.face_z)
    print_plan(res["mesh"], res["kept"], res["dropped"])
    d = res["dims"]
    print("  %s nodes, %s edges, %s faces"
          % tuple(d.get("%s_n%s" % (res["mesh"], k), "?")
                  for k in ("Nodes", "Edges", "Faces")))
    print("\nWrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
