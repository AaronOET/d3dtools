"""
Remove (clear) the 2D computational mesh and 1D2D links from a Deltares
D-Flow FM model exported as a **DIMR run folder** (dimr.xml + dflowfm/),
while preserving 1D network data (pipes).

This is the DIMR-folder counterpart of ``rmgrid``: identical processing, but
the model is located through the DIMR export layout instead of a ``.dsproj``
project.

What it does
------------
1. Locates the .mdu:
     * ``dimr.xml``            -> reads <component> workingDir + inputFile
     * a run folder            -> uses its dimr.xml, else its dflowfm/ folder
     * the ``dflowfm`` folder  -> uses the single .mdu inside it
     * an ``.mdu`` file        -> used directly
2. Reads the NetFile path from the [geometry] section of the .mdu.
3. If an IniFieldFile is referenced in the MDU, backs it up as <name>.ini.bak
   and removes any ini-field blocks that carry ``locationType = 2d`` (i.e.
   interpolated bed level / roughness / infiltration data that is only
   meaningful when a 2D mesh is present).
4. Backs up the original net file as <name>.nc.bak (skipped if already exists).
5. Writes a new UGRID 1.0 NetCDF file in its place with the 2D mesh emptied
   and all 1D2D link variables removed, while preserving the 1D network.

``--restore`` reverses steps 3-5: both the net file and the iniField file(s)
are copied back from their .bak backups, so the 2D roughness and infiltration
definitions return.

examples
--------
    rmgriddimr                         # run folder = current directory
    rmgriddimr -i C:/models/PT01
    rmgriddimr -i C:/models/PT01/dimr.xml
    rmgriddimr -i C:/models/PT01/dflowfm
    rmgriddimr -i C:/models/PT01 --restore
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
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

# DIMR configuration file name and the namespace its elements live in.
DIMR_CONFIG_NAME = "dimr.xml"
DIMR_NS = "{http://schemas.deltares.nl/dimr}"

# Conventional name of the D-Flow FM sub-folder inside a DIMR export.
DFLOWFM_DIR_NAME = "dflowfm"


# ---------------------------------------------------------------------------
# DIMR model location
# ---------------------------------------------------------------------------

def find_mdu_in_dir(directory):
    """Return the single .mdu file directly inside *directory*."""
    directory = Path(directory)
    candidates = sorted(directory.glob("*.mdu"))
    if not candidates:
        return None
    if len(candidates) > 1:
        print(f"Warning: multiple .mdu files found in {directory}; "
              f"using {candidates[0].name}")
    return candidates[0]


def read_dimr_config(dimr_path):
    """Return the .mdu paths referenced by the D-Flow FM components in dimr.xml.

    A DIMR component gives the model as ``<workingDir>`` (relative to the
    dimr.xml) plus ``<inputFile>``. Components whose library is not dflowfm
    (RTC, RR, wave, ...) are ignored -- they have no 2D mesh to clear.
    """
    dimr_path = Path(dimr_path)
    try:
        root = ET.parse(dimr_path).getroot()
    except ET.ParseError as e:
        sys.exit(f"Error: cannot parse {dimr_path}: {e}")

    mdus = []
    for tag in (f"{DIMR_NS}component", "component"):
        for comp in root.findall(tag):
            def text(child):
                el = comp.find(f"{DIMR_NS}{child}")
                if el is None:
                    el = comp.find(child)
                return (el.text or "").strip() if el is not None else ""

            library = text("library").lower()
            input_file = text("inputFile")
            if not input_file or not input_file.lower().endswith(".mdu"):
                continue
            if library and "dflowfm" not in library:
                continue
            working_dir = text("workingDir") or "."
            path = Path(working_dir) / input_file
            if not path.is_absolute():
                path = dimr_path.parent / path
            mdus.append(path.resolve())
        if mdus:
            break
    return mdus


def find_mdu(target=None):
    """Locate the D-Flow FM .mdu of a DIMR model.

    *target* may be a dimr.xml, a run folder, a dflowfm folder, or an .mdu
    file. ``None`` means "the current directory". Exits with a message when the
    model cannot be located.
    """
    path = Path(target).resolve() if target else Path(".").resolve()

    if not path.exists():
        sys.exit(f"Error: path does not exist: {path}")

    if path.is_file():
        if path.suffix.lower() == ".mdu":
            return path
        if path.suffix.lower() == ".xml":
            mdus = read_dimr_config(path)
            if not mdus:
                sys.exit(f"Error: no D-Flow FM component with an .mdu input "
                         f"found in {path}")
            if len(mdus) > 1:
                print(f"Warning: {path.name} lists several D-Flow FM "
                      f"components; using {mdus[0]}")
            if not mdus[0].exists():
                sys.exit(f"Error: {path.name} points at a missing .mdu: {mdus[0]}")
            print(f"DIMR    : {path}")
            return mdus[0]
        sys.exit(f"Error: expected a dimr.xml, an .mdu, or a folder, got {path}")

    # --- a directory ---------------------------------------------------------
    dimr_path = path / DIMR_CONFIG_NAME
    if dimr_path.exists():
        return find_mdu(dimr_path)

    mdu = find_mdu_in_dir(path)
    if mdu:
        return mdu

    sub = path / DFLOWFM_DIR_NAME
    if sub.is_dir():
        mdu = find_mdu_in_dir(sub)
        if mdu:
            return mdu

    # Any single sub-folder holding exactly one .mdu (non-standard layouts).
    found = sorted(p for p in path.glob("*/*.mdu"))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        names = ", ".join(str(p.relative_to(path)) for p in found)
        sys.exit(f"Error: several .mdu files under {path} ({names}). "
                 "Point -i at the one you mean.")

    sys.exit(f"Error: no dimr.xml, {DFLOWFM_DIR_NAME}/ folder or .mdu file "
             f"found in {path}")


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


def iter_ini_field_paths(mdu_path):
    """Yield the absolute path of every iniField file referenced by *mdu_path*.

    A DIMR export may list several iniField files separated by ``;``.
    """
    ini_field_file = read_key_from_mdu(mdu_path, "geometry", "IniFieldFile")
    for entry in [p.strip() for p in (ini_field_file or "").split(";") if p.strip()]:
        ini_path = Path(entry)
        if not ini_path.is_absolute():
            ini_path = Path(mdu_path).parent / ini_path
        yield ini_path.resolve()


def _has_2d_section(ini_path):
    """True if *ini_path* still contains a ``locationType = 2d`` block."""
    if not Path(ini_path).exists():
        return False
    with open(ini_path, encoding="utf-8", errors="replace") as fh:
        return bool(re.search(r"(?mi)^\s*locationType\s*=\s*2d\s*$", fh.read()))


def clean_ini_field_file(mdu_path, force_backup=False):
    """
    Remove all sections that contain ``locationType = 2d`` from the IniFieldFile
    referenced by *mdu_path*. The file is edited in-place after a .bak backup
    is made, so ``--restore`` can bring the 2D roughness / infiltration blocks
    back.

    An existing .bak is kept (it holds the pre-removal state, which a newer one
    would not) unless *force_backup* asks for it to be refreshed.
    """
    for ini_path in iter_ini_field_paths(mdu_path):
        _clean_one_ini_field_file(ini_path, force_backup)


def _clean_one_ini_field_file(ini_path, force_backup=False):
    """Strip the ``locationType = 2d`` blocks out of a single iniField file."""
    if not ini_path.exists():
        print(f"  Warning: IniFieldFile not found at {ini_path} - skipping cleanup.")
        return

    # newline="" keeps the original line endings literal, so a CRLF file
    # written by D-HYDRO stays CRLF after the 2D blocks are stripped.
    with open(ini_path, encoding="utf-8", errors="replace", newline="") as fh:
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

    bak_path = ini_path.with_suffix(ini_path.suffix + ".bak")
    if not bak_path.exists():
        shutil.copy2(ini_path, bak_path)
        print(f"  IniFieldFile backed up -> {bak_path.name}")
    elif force_backup:
        shutil.copy2(ini_path, bak_path)
        print(f"  IniFieldFile backup overwritten -> {bak_path.name}")
    else:
        print(f"  IniFieldFile backup kept ({bak_path.name}) - not overwritten.")

    new_content = "".join(kept)
    with open(ini_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new_content)

    for name in removed_names:
        print(f"  IniFieldFile: removed 2D section {name}")
    print(f"  IniFieldFile updated -> {ini_path}")


# ---------------------------------------------------------------------------
# Net file processing
# ---------------------------------------------------------------------------

def create_empty_mesh(src_path, dst_path):
    """
    Write a new UGRID NetCDF file with 1D2D links removed and 2D mesh emptied,
    while preserving 1D network (network_*) and mesh1d (*) variables. Also drops
    the scalar `links` and `composite_mesh` topology variables (and their now-
    orphan dimensions) so the Delft3D-FM GUI's "Generate Links" tool will
    accept the result.
    """
    # netCDF4's C layer cannot open paths with non-ASCII characters on Windows.
    # Copy through the system temp dir (always ASCII) to work around this.
    fd, tmp_src = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    fd, tmp_dst = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        shutil.copy2(src_path, tmp_src)
        _create_empty_mesh_impl(tmp_src, tmp_dst)
        shutil.copy2(tmp_dst, dst_path)
    finally:
        if os.path.exists(tmp_src):
            os.unlink(tmp_src)
        if os.path.exists(tmp_dst):
            os.unlink(tmp_dst)
    print(f"  Written (1D preserved, links removed, mesh emptied) -> {dst_path}")


def _create_empty_mesh_impl(src_path, dst_path):
    """Process the NetCDF file; both paths must be ASCII-safe."""
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


def restore_mesh(net_path, mdu_path=None):
    """Restore the net file (and any iniField file) from the .bak backups."""
    net_path = Path(net_path)
    bak_path = net_path.with_suffix(".nc.bak")
    if not bak_path.exists():
        sys.exit(f"Error: backup not found: {bak_path}")
    shutil.copy2(bak_path, net_path)
    print(f"  Restored {net_path}  (from {bak_path})")

    if mdu_path is None:
        return

    for ini_path in iter_ini_field_paths(mdu_path):
        ini_bak = ini_path.with_suffix(ini_path.suffix + ".bak")
        if ini_bak.exists():
            shutil.copy2(ini_bak, ini_path)
            print(f"  Restored {ini_path}  (from {ini_bak.name})")
            continue

        # No backup: the 2D blocks were stripped by a version of rmgriddimr
        # that did not back the iniField file up, or the .bak was deleted. Say
        # so loudly - otherwise the user sees a "restored" message for the net
        # file and wrongly assumes the 2D fields came back with it.
        print(f"  Warning: no backup for {ini_path.name} ({ini_bak.name} not found).")
        if _has_2d_section(ini_path):
            print("           The file still has its 2D blocks - nothing to restore.")
        else:
            print("           2D roughness / infiltration blocks CANNOT be restored.")
            print("           Re-add them in the GUI, or use rsgriddimr -f to "
                  "re-import the coverage files.")


def main():
    """Main function for the command line interface."""
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Remove (clear) the 2D computational mesh from a D-Flow FM "
                    "model in a DIMR run folder (dimr.xml + dflowfm/).",
        epilog="""
examples:
  %(prog)s                              # DIMR run folder = current directory
  %(prog)s -i C:/models/PT01
  %(prog)s -i C:/models/PT01/dimr.xml
  %(prog)s -i C:/models/PT01/dflowfm
  %(prog)s -i C:/models/PT01 --restore
  %(prog)s -i C:/models/PT01 --force-backup
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        help="DIMR run folder, dimr.xml, dflowfm folder or .mdu file "
             "(default: current directory)",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restore the original mesh and iniField file (roughness / infiltration) "
             "from the .bak backups instead of removing them.",
    )
    parser.add_argument(
        "--force-backup",
        action="store_true",
        help="Overwrite the existing .bak files (net file and iniField file) "
             "with their current contents before processing.",
    )
    args = parser.parse_args()

    try:
        mdu_path = find_mdu(args.input)
        print(f"MDU     : {mdu_path}")

        net_path = read_net_file_from_mdu(mdu_path)
        print(f"NetFile : {net_path}")

        if not net_path.exists():
            print(f"Error: NetFile not found at {net_path}")
            sys.exit(1)

        if args.restore:
            restore_mesh(net_path, mdu_path)
            return

        clean_ini_field_file(mdu_path, args.force_backup)

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

        fd, tmp_verify = tempfile.mkstemp(suffix=".nc")
        os.close(fd)
        try:
            shutil.copy2(net_path, tmp_verify)
            with nc.Dataset(tmp_verify, "r") as ds:
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
        finally:
            os.unlink(tmp_verify)
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
        print(f"Error processing model: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
