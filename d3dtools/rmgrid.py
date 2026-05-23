"""
Remove (clear) the 2D computational mesh and 1D2D links from a
Deltares D-Flow FM (.dsproj) project while preserving 1D network data (pipes).

What it does
------------
1. Locates the FlowFM.mdu inside <project>.dsproj_data/FlowFM/input/
2. Reads the NetFile path from the [geometry] section of the .mdu
3. If an IniFieldFile is referenced in the MDU, removes any ini-field blocks
   that carry ``locationType = 2d`` (i.e. interpolated roughness / infiltration
   data that is only meaningful when a 2D mesh is present).
4. Backs up the original net file as <name>.nc.bak (skipped if already exists)
5. Writes a new UGRID 1.0 NetCDF file in its place with the 2D mesh emptied
   and all 1D2D link variables removed, while preserving the 1D network.
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

try:
    import netCDF4 as nc
except ImportError:
    sys.exit("Missing package: pip install netCDF4")


# Dimensions belonging to 1D2D links -> dropped entirely
_LINK_DIMS = frozenset({"links_nContacts"})

# Dimensions belonging to 2D mesh data -> set to 0 (emptied)
_MESH2D_DATA_DIMS = frozenset({"Mesh2d_nNodes", "Mesh2d_nEdges", "Mesh2d_nFaces"})

# cf_role values of scalar topology variables that become orphans once the
# 1D2D contacts are removed. These variables (and the dimensions they carry
# that nothing else uses) must be dropped, otherwise Delft3D-FM's
# "Generate Links" GUI refuses to parse the file.
_CONTACT_TOPOLOGY_ROLES = frozenset({
    "mesh_topology_contact",   # the `links` variable
    "parent_mesh_topology",    # the `composite_mesh` variable
})


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


def read_key_from_mdu(mdu_path, section, key):
    """Return the value of *key* inside *section* of an MDU file, or None."""
    in_section = False
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*(.+)", re.IGNORECASE)
    with open(mdu_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if re.match(r"^\[" + re.escape(section) + r"\]", stripped, re.IGNORECASE):
                in_section = True
                continue
            if in_section and stripped.startswith("["):
                break
            if in_section:
                m = pattern.match(stripped)
                if m:
                    return m.group(1).split("#")[0].strip()
    return None


def clean_ini_field_file(mdu_path):
    """
    Remove all sections that contain ``locationType = 2d`` from the IniFieldFile
    referenced by *mdu_path*. The file is edited in-place.
    """
    ini_field_file = read_key_from_mdu(mdu_path, "geometry", "IniFieldFile")
    if not ini_field_file:
        return

    ini_path = Path(ini_field_file)
    if not ini_path.is_absolute():
        ini_path = Path(mdu_path).parent / ini_path
    ini_path = ini_path.resolve()

    if not ini_path.exists():
        print(f"  Warning: IniFieldFile not found at {ini_path} - skipping cleanup.")
        return

    with open(ini_path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()

    section_re = re.compile(r"(?m)^(?=\[)")
    chunks = section_re.split(raw)

    kept = []
    removed_names = []
    loc2d_re = re.compile(r"(?m)^\s*locationType\s*=\s*2d\s*$", re.IGNORECASE)
    header_re = re.compile(r"^\[(\w+)\]")

    for chunk in chunks:
        if loc2d_re.search(chunk):
            hm = header_re.match(chunk.lstrip())
            section_name = hm.group(1) if hm else "?"
            qm = re.search(r"(?m)^\s*quantity\s*=\s*(\S+)", chunk, re.IGNORECASE)
            qty = qm.group(1) if qm else "(unknown)"
            removed_names.append(f"[{section_name}] quantity={qty}")
        else:
            kept.append(chunk)

    if not removed_names:
        print(f"  IniFieldFile: no 2D sections found - nothing removed ({ini_path.name}).")
        return

    new_content = "".join(kept)
    with open(ini_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new_content)

    for name in removed_names:
        print(f"  IniFieldFile: removed 2D section {name}")
    print(f"  IniFieldFile updated -> {ini_path}")


def create_empty_mesh(src_path, dst_path):
    """
    Write a new UGRID NetCDF file with 1D2D links removed and 2D mesh emptied,
    while preserving 1D network (network_*) and mesh1d (*) variables. Also drops
    the scalar `links` and `composite_mesh` topology variables (and their now-
    orphan dimensions) so the Delft3D-FM GUI's "Generate Links" tool will
    accept the result.
    """
    with nc.Dataset(src_path, "r") as src, \
            nc.Dataset(dst_path, "w", format="NETCDF4") as dst:

        drop_vars = set()
        mesh2d_vars = set()
        for vname, var in src.variables.items():
            dims = set(var.dimensions)
            role = getattr(var, "cf_role", "")
            if dims & _LINK_DIMS or role in _CONTACT_TOPOLOGY_ROLES:
                drop_vars.add(vname)
            elif dims & _MESH2D_DATA_DIMS:
                mesh2d_vars.add(vname)

        for k in src.ncattrs():
            setattr(dst, k, getattr(src, k))

        # Only keep dimensions that are still referenced by a surviving variable.
        used_dims = {d for vname, v in src.variables.items()
                     if vname not in drop_vars
                     for d in v.dimensions}
        for dname, dim in src.dimensions.items():
            if dname in _LINK_DIMS:
                continue
            if dname not in used_dims:
                continue
            if dname in _MESH2D_DATA_DIMS:
                dst.createDimension(dname, 0)
            else:
                dst.createDimension(dname, len(dim))

        for vname, src_var in src.variables.items():
            if vname in drop_vars:
                continue

            fill = getattr(src_var, "_FillValue", None)
            kwargs = {"fill_value": fill} if fill is not None else {}
            dst_var = dst.createVariable(
                vname, src_var.dtype, src_var.dimensions, **kwargs
            )

            for attr in src_var.ncattrs():
                if attr == "_FillValue":
                    continue
                dst_var.setncattr(attr, getattr(src_var, attr))

            if vname not in mesh2d_vars:
                if src_var.dimensions:
                    if all(len(src.dimensions[d]) > 0 for d in src_var.dimensions):
                        dst_var[:] = src_var[:]

    print(f"  Written (1D preserved, links removed, mesh emptied) -> {dst_path}")


def restore_mesh(net_path):
    """Restore the net file from its .bak backup."""
    net_path = Path(net_path)
    bak_path = net_path.with_suffix(".nc.bak")
    if not bak_path.exists():
        sys.exit(f"Error: backup not found: {bak_path}")
    shutil.copy2(bak_path, net_path)
    print(f"  Restored {net_path}  (from {bak_path})")


def main():
    """Main function for the command line interface."""
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Remove (clear) the 2D computational mesh from a D-Flow FM .dsproj project.",
        epilog="""
examples:
  %(prog)s -i MyProject.dsproj
  %(prog)s -i MyProject.dsproj --restore
  %(prog)s -i MyProject.dsproj --force-backup
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        help="Path to the .dsproj file (default: first .dsproj found in current directory)",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restore the original mesh from the .bak backup instead of removing it.",
    )
    parser.add_argument(
        "--force-backup",
        action="store_true",
        help="Overwrite an existing .bak file with the current net file before processing.",
    )
    args = parser.parse_args()

    if args.input:
        dsproj_path = Path(args.input).resolve()
        if not dsproj_path.exists():
            print(f"Error: .dsproj file does not exist: {dsproj_path}")
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
        dsproj_path = matches[0].resolve()

    print(f"Project : {dsproj_path}")

    try:
        mdu_path = find_mdu(dsproj_path)
        print(f"MDU     : {mdu_path}")

        net_path = read_net_file_from_mdu(mdu_path)
        print(f"NetFile : {net_path}")

        if not net_path.exists():
            print(f"Error: NetFile not found at {net_path}")
            sys.exit(1)

        if args.restore:
            restore_mesh(net_path)
            return

        clean_ini_field_file(mdu_path)

        bak_path = net_path.with_suffix(".nc.bak")
        if bak_path.exists() and not args.force_backup:
            print(f"  Warning: backup already exists ({bak_path.name}) - skipping backup step.")
            print(f"           If the backup is stale, re-run with --force-backup to overwrite it.")
        else:
            shutil.copy2(net_path, bak_path)
            if args.force_backup:
                print(f"  Backup overwritten  -> {bak_path}")
            else:
                print(f"  Backed up original  -> {bak_path}")

        tmp_path = net_path.with_suffix(".nc.tmp")
        try:
            create_empty_mesh(net_path, tmp_path)
            if net_path.exists():
                net_path.unlink()
            tmp_path.rename(net_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        with nc.Dataset(net_path, "r") as ds:
            n_nodes = len(ds.dimensions["Mesh2d_nNodes"]) if "Mesh2d_nNodes" in ds.dimensions else 0
            n_edges = len(ds.dimensions["Mesh2d_nEdges"]) if "Mesh2d_nEdges" in ds.dimensions else 0
            n_faces = len(ds.dimensions["Mesh2d_nFaces"]) if "Mesh2d_nFaces" in ds.dimensions else 0
            n_links = len(ds.dimensions["links_nContacts"]) if "links_nContacts" in ds.dimensions else 0
            n1d_nd = len(ds.dimensions["mesh1d_nNodes"]) if "mesh1d_nNodes" in ds.dimensions else 0
            n1d_edg = len(ds.dimensions["mesh1d_nEdges"]) if "mesh1d_nEdges" in ds.dimensions else 0
            n_br = len(ds.dimensions["network_nEdges"]) if "network_nEdges" in ds.dimensions else 0
            has_1d = "mesh1d" in ds.variables
            has_links_topo = "links" in ds.variables
            has_comp = "composite_mesh" in ds.variables
        print(
            f"\nDone:\n"
            f"  2D mesh cleared : nodes={n_nodes}  edges={n_edges}  faces={n_faces}\n"
            f"  1D2D links      : {n_links} remaining   (links topo present={has_links_topo})\n"
            f"  composite_mesh  : present={has_comp}\n"
            f"  1D network kept : {has_1d}  "
            f"(branches={n_br}  mesh1d nodes={n1d_nd}  mesh1d edges={n1d_edg})"
        )
    except SystemExit:
        raise
    except Exception as e:
        print(f"Error processing project: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
