"""
Restore (re-insert) the 2D computational mesh into a Deltares D-Flow FM model
exported as a **DIMR run folder** (dimr.xml + dflowfm/), by cloning the intact
2D mesh from a source model, while preserving the target's own 1D network.

This is the DIMR-folder counterpart of ``rsgrid``, and the inverse of
``rmgriddimr``.

What it does
------------
1. Locates the .mdu of the target (-i) and, when given, the source (-s):
     * ``dimr.xml``            -> reads <component> workingDir + inputFile
     * a run folder            -> uses its dimr.xml, else its dflowfm/ folder
     * the ``dflowfm`` folder  -> uses the single .mdu inside it
     * an ``.mdu`` file        -> used directly
     * a ``.nc`` net file      -> used directly (source or target)
2. Reads the NetFile path from the [geometry] section of each .mdu.
3. Reports the mesh state (2D nodes / edges / faces / face_z and 1D nodes /
   branches) of both net files.
4. Backs up the target net file with a timestamped copy before overwriting.
5. Writes a new net file that keeps EVERYTHING from the target (its 1D network,
   coordinate system, etc.) and injects ONLY the 2D mesh variables
   (``Mesh2d_*``, including the per-face bed levels ``Mesh2d_face_z``) taken
   from the source. The target's 1D network is preserved untouched.

Spatial fields (-f / --fields)
------------------------------
Removing and re-adding a 2D grid also drops the *spatial fields* that live on
it: bed level, initial infiltration capacity and the 2D roughness (friction
coefficient). In a D-Flow FM model these are not stored in the net file but as
loose files inside the ``dflowfm`` folder:

    initialFields.ini   ([Initial]/[Parameter] blocks, MDU key IniFieldFile)
      -> *.xyz    sample coverages
      -> *.tif    GeoTIFF coverages   (what a D-HYDRO DIMR export normally uses)
    roughness-*.ini     (1D roughness sections, MDU key FrictFile)

``-f`` restores exactly those. It picks the coverage files up from a directory
(the current directory by default), copies them into the model's ``dflowfm``
folder, and re-registers them in the MDU (``IniFieldFile``, ``FrictFile``, and
``Infiltrationmodel`` when an infiltration field is present). Each coverage is
checked against the 2D mesh extent so you can see straight away whether the
data actually lands on the restored grid.

Only the coverage files are needed. The iniField file itself is handled for you:

* the model has no iniField file  -> a new ``initialFields.ini`` is written,
  with an ``[Initial]`` or ``[Parameter]`` block per coverage;
* the model already has one       -> only the ``dataFile`` of the matching
  quantity is rewritten, so the interpolation / averaging / operand settings
  configured in D-HYDRO survive untouched. Quantities not in the file yet are
  appended as new blocks.

Coverages are matched to a quantity by name (``frictioncoefficient.xyz`` ->
``frictioncoefficient``). When the name says nothing -- which is normal for
GeoTIFFs like ``RHI.tif`` -- the iniField files of the model, of the backups
``rmgriddimr`` left behind, and of the ``-s`` source model are consulted: a file
registered against a quantity in any of them keeps that quantity.
Use ``-q NAME=FILE`` for anything left over. Supplying your own
``initialFields.ini`` in the fields directory still works and takes precedence
over all of this.

examples
--------
    rsgriddimr -s C:/models/Intact             # restore mesh into cwd's model
    rsgriddimr -i C:/models/PT01 -s C:/models/Intact
    rsgriddimr -i C:/models/PT01 -s source_net.nc
    rsgriddimr -i C:/models/PT01 -f -d fields/
    rsgriddimr -i C:/models/PT01 -s Intact -f  # mesh first, then the fields
"""

import argparse
import datetime
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


# Variable that holds the per-face bed levels ("face z values").
FACE_Z_VAR = "Mesh2d_face_z"

# Prefix that identifies all 2D-mesh dimensions and variables in a D-HYDRO
# UGRID net file (Mesh2d, Mesh2d_node_x, Mesh2d_nFaces, Mesh2d_face_z, ...).
MESH2D_PREFIX = "Mesh2d"

# DIMR configuration file name and the namespace its elements live in.
DIMR_CONFIG_NAME = "dimr.xml"
DIMR_NS = "{http://schemas.deltares.nl/dimr}"

# Conventional name of the D-Flow FM sub-folder inside a DIMR export.
DFLOWFM_DIR_NAME = "dflowfm"

# ---------------------------------------------------------------------------
# Spatial-field restore (bed level / infiltration capacity / 2D roughness)
# ---------------------------------------------------------------------------

# [General] fileType values used to classify the *.ini files we are given.
FILETYPE_INIFIELD = "inifield"
FILETYPE_ROUGHNESS = "roughness"

# MDU keys that point at the field files.
MDU_INIFIELD_KEY = "IniFieldFile"
MDU_FRICTFILE_KEY = "FrictFile"
MDU_INFILTRATION_KEY = "Infiltrationmodel"

# Infiltrationmodel = 2 -> constant infiltration capacity, which is what an
# `infiltrationcapacity` field in the iniField file feeds.
INFILTRATION_MODEL_CONSTANT = 2

# Quantity names we report on specifically.
QUANTITY_INFILTRATION = "infiltrationcapacity"
QUANTITY_FRICTION = "frictioncoefficient"

# Name used when a model has no iniField file at all and we have to make one.
DEFAULT_INIFIELD_NAME = "initialFields.ini"

# Coverage file extensions we know how to install, mapped to the iniField
# ``dataFileType`` D-HYDRO writes for them.
COVERAGE_TYPES = {
    ".xyz": "sample",
    ".tif": "GeoTIFF",
    ".tiff": "GeoTIFF",
    ".asc": "arcinfo",
}

# Which iniField block a quantity belongs in: [Initial] is the model's starting
# state, [Parameter] is a spatially varying model parameter.
INIFIELD_SECTION_BY_QUANTITY = {
    "bedlevel": "Initial",
    "waterlevel": "Initial",
    "initialwaterlevel": "Initial",
    "waterdepth": "Initial",
    "initialwaterdepth": "Initial",
    "initialsalinity": "Initial",
    "initialtemperature": "Initial",
    "initialunsaturatedzonethickness": "Initial",
    "infiltrationcapacity": "Initial",
    "interceptionlayerthickness": "Initial",
    "frictioncoefficient": "Parameter",
    "ifrctyponarea": "Parameter",
    "horizontaleddyviscositycoefficient": "Parameter",
    "horizontaleddydiffusivitycoefficient": "Parameter",
    "internaltidesfrictioncoefficient": "Parameter",
    "potentialevaporation": "Parameter",
}

# [General] block written into a freshly created iniField file.
DEFAULT_INIFIELD_GENERAL = (
    ("fileVersion", "2.00"),
    ("fileType", "iniField"),
)

# Keys added alongside dataFile when a field block has to be created from
# scratch, per dataFileType. These are the D-HYDRO defaults for a 2D coverage;
# existing blocks keep whatever the user configured instead.
DEFAULT_KEYS_BY_TYPE = {
    "sample": (
        ("dataFileType", "sample"),
        ("interpolationMethod", "averaging"),
        ("operand", "O"),
        ("averagingType", "nearestNb"),
        ("averagingRelSize", "1.0000000e+000"),
        ("averagingNumMin", "1"),
        ("averagingPercentile", "0.0000000e+000"),
        ("locationType", "2d"),
    ),
    "GeoTIFF": (
        ("dataFileType", "GeoTIFF"),
        ("interpolationMethod", "triangulation"),
        ("operand", "O"),
        ("extrapolationMethod", "no"),
        ("locationType", "2d"),
    ),
    "arcinfo": (
        ("dataFileType", "arcinfo"),
        ("interpolationMethod", "triangulation"),
        ("operand", "O"),
        ("extrapolationMethod", "no"),
        ("locationType", "2d"),
    ),
}

# Column layout D-HYDRO uses when it writes a Deltares .ini file.
INI_KEY_WIDTH = 22
INI_VALUE_WIDTH = 20


def _is_mesh2d_dim(name):
    """True if *name* is a 2D-mesh dimension."""
    return name.startswith(MESH2D_PREFIX)


def _is_mesh2d_var(name, var):
    """True if a variable belongs to the 2D mesh (by name or by dimension)."""
    if name.startswith(MESH2D_PREFIX):
        return True
    return any(d.startswith(MESH2D_PREFIX) for d in var.dimensions)


def coverage_type(path):
    """Return the iniField ``dataFileType`` for a coverage file, or None."""
    return COVERAGE_TYPES.get(Path(path).suffix.lower())


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
    (RTC, RR, wave, ...) are ignored -- they carry no 2D mesh.
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


def find_mdu(target=None, quiet=False):
    """Locate the D-Flow FM .mdu of a DIMR model.

    *target* may be a dimr.xml, a run folder, a dflowfm folder, or an .mdu
    file. ``None`` means "the current directory".
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
            if not quiet:
                print(f"DIMR    : {path}")
            return mdus[0]
        sys.exit(f"Error: expected a dimr.xml, an .mdu, or a folder, got {path}")

    # --- a directory ---------------------------------------------------------
    dimr_path = path / DIMR_CONFIG_NAME
    if dimr_path.exists():
        return find_mdu(dimr_path, quiet=quiet)

    mdu = find_mdu_in_dir(path)
    if mdu:
        return mdu

    sub = path / DFLOWFM_DIR_NAME
    if sub.is_dir():
        mdu = find_mdu_in_dir(sub)
        if mdu:
            return mdu

    found = sorted(p for p in path.glob("*/*.mdu"))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        names = ", ".join(str(p.relative_to(path)) for p in found)
        sys.exit(f"Error: several .mdu files under {path} ({names}). "
                 "Point at the one you mean.")

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


def resolve_net_file(path):
    """Resolve *path* to a net file.

    Accepts a DIMR run folder, a dimr.xml, a dflowfm folder, an .mdu (the net
    file is then located via the MDU) or a ``.nc`` net file given directly.
    """
    p = Path(path).resolve() if path else Path(".").resolve()
    if not p.exists():
        sys.exit(f"Error: path does not exist: {p}")
    if p.is_file() and p.suffix.lower() in (".nc", ".bak", ".tmp"):
        return p
    mdu_path = find_mdu(p)
    print(f"MDU     : {mdu_path}")
    return read_net_file_from_mdu(mdu_path)


# ---------------------------------------------------------------------------
# Net file inspection and merging
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Deltares .ini helpers
# ---------------------------------------------------------------------------

def parse_ini(path):
    """Parse a Deltares-style ``.ini`` file.

    Returns a list of ``(section_name, [(key, value), ...])`` tuples, in file
    order. Comments (``#`` to end of line) and blank lines are dropped. Keys and
    section names keep their original casing; compare case-insensitively.
    """
    sections = []
    current = None
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                current = (line[1:-1].strip(), [])
                sections.append(current)
                continue
            if current is None or "=" not in line:
                continue
            key, value = line.split("=", 1)
            current[1].append((key.strip(), value.strip()))
    return sections


def ini_get(sections, section_name, key):
    """Return the first value of *key* in *section_name* (case-insensitive)."""
    for name, items in sections:
        if name.lower() != section_name.lower():
            continue
        for k, v in items:
            if k.lower() == key.lower():
                return v
    return None


def ini_file_type(path):
    """Return the lower-cased ``[General] fileType`` of an .ini file, or None.

    Used to tell an iniField file (``fileType = iniField``) from a 1D roughness
    file (``fileType = roughness``) without relying on file names.
    """
    try:
        value = ini_get(parse_ini(path), "General", "fileType")
    except OSError:
        return None
    return value.lower() if value else None


def inifield_entries(path):
    """Return the field entries of an iniField file.

    Each entry is a dict with ``section`` (``Initial`` / ``Parameter``),
    ``quantity`` and ``dataFile`` (may be None).
    """
    entries = []
    for name, items in parse_ini(path):
        if name.lower() not in ("initial", "parameter"):
            continue
        entry = {"section": name, "quantity": None, "dataFile": None}
        for k, v in items:
            kl = k.lower()
            if kl == "quantity":
                entry["quantity"] = v
            elif kl == "datafile":
                entry["dataFile"] = v
        entries.append(entry)
    return entries


def format_ini_line(key, value):
    """Format one ``key = value`` line the way D-HYDRO lays out .ini files."""
    return f"    {key:<{INI_KEY_WIDTH}}= {value:<{INI_VALUE_WIDTH}}"


def write_ini(path, sections, newline="\r\n"):
    """Write *sections* (as returned by :func:`parse_ini`) back out to *path*.

    Blocks are separated by a blank line and the file ends with one, matching
    what D-HYDRO itself produces. Note that comments are not round-tripped --
    Deltares writes these files machine-generated, and every key/value pair is
    preserved, so in practice only stray hand-written comments are lost.
    """
    out = []
    for name, items in sections:
        out.append(f"[{name}]")
        out.extend(format_ini_line(k, v) for k, v in items)
        out.append("")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(newline.join(out) + newline)


def _set_ini_item(items, key, value, after=None):
    """Set ``key`` in the ``[(key, value)]`` list *items*; return the old value.

    An existing key is updated in place (keeping its original casing and
    position). A new key is inserted just after *after* if given, else appended.
    """
    for i, (k, v) in enumerate(items):
        if k.lower() == key.lower():
            items[i] = (k, value)
            return v
    pos = len(items)
    if after:
        for i, (k, _) in enumerate(items):
            if k.lower() == after.lower():
                pos = i + 1
                break
    items.insert(pos, (key, value))
    return None


def apply_fields_to_inifield(sections, fields):
    """Point the iniField *sections* at the coverages described by *fields*.

    *fields* is a list of dicts with ``section``, ``quantity``, ``dataFile`` and
    ``dataFileType``. For a quantity that already has an ``[Initial]`` /
    ``[Parameter]`` block, only ``dataFile`` (and ``dataFileType``, when the
    coverage format changed) is rewritten -- the interpolation, averaging and
    operand settings the user configured are left alone. A quantity with no
    block yet gets a new one using the defaults for its coverage type.

    Returns ``(sections, changes)`` where *changes* is a list of human-readable
    descriptions of what was modified.
    """
    sections = [(name, list(items)) for name, items in sections]
    changes = []

    # A file we just created (or an .ini missing its header) needs [General].
    if not any(name.lower() == "general" for name, _ in sections):
        sections.insert(0, ("General", list(DEFAULT_INIFIELD_GENERAL)))
        changes.append("added [General] block (fileType = iniField)")

    for field in fields:
        quantity = field["quantity"]
        data_file = field["dataFile"]
        file_type = field.get("dataFileType")
        defaults = DEFAULT_KEYS_BY_TYPE.get(file_type,
                                            DEFAULT_KEYS_BY_TYPE["sample"])

        block = None
        for name, items in sections:
            if name.lower() not in ("initial", "parameter"):
                continue
            for k, v in items:
                if k.lower() == "quantity" and v.lower() == quantity.lower():
                    block = (name, items)
                    break
            if block:
                break

        if block is None:
            template = field.get("template")
            if template:
                # The block existed before the grid was removed: put it back as
                # it was, so hand-tuned keys (ifrctyp, averaging, ...) survive.
                section, items = template[0], list(template[1])
                _set_ini_item(items, "quantity", quantity)
                _set_ini_item(items, "dataFile", data_file, after="quantity")
                if file_type:
                    _set_ini_item(items, "dataFileType", file_type,
                                  after="dataFile")
                note = " (settings recovered from the previous block)"
            else:
                section = field["section"]
                items = [("quantity", quantity), ("dataFile", data_file)]
                items.extend(defaults)
                note = ""
            sections.append((section, items))
            changes.append(
                f"added [{section}] block for {quantity} -> {data_file}{note}"
            )
            continue

        name, items = block
        # A block may define a uniform `value` instead of a coverage file; the
        # coverage wins, so the uniform value has to go.
        had_value = any(k.lower() == "value" for k, _ in items)
        if had_value:
            items[:] = [(k, v) for k, v in items if k.lower() != "value"]

        old = _set_ini_item(items, "dataFile", data_file, after="quantity")
        if old is None:
            # Block had no dataFile yet: fill in the keys it is missing, but
            # never overwrite a setting that is already there.
            present = {k.lower() for k, _ in items}
            for k, v in defaults:
                if k.lower() not in present:
                    _set_ini_item(items, k, v)
            changes.append(
                f"[{name}] {quantity}: "
                f"{'replaced uniform value with' if had_value else 'set'} "
                f"dataFile = {data_file}"
            )
        elif old != data_file:
            changes.append(
                f"[{name}] {quantity}: dataFile '{old}' -> '{data_file}'"
            )

        # Swapping e.g. a .xyz sample for a .tif means the declared
        # dataFileType no longer matches the file, so that one key must follow.
        if file_type:
            old_type = _set_ini_item(items, "dataFileType", file_type,
                                     after="dataFile")
            if old_type and old_type.lower() != file_type.lower():
                changes.append(
                    f"[{name}] {quantity}: dataFileType '{old_type}' "
                    f"-> '{file_type}'"
                )

    return sections, changes


def locate_project_inifield(mdu_path, input_dir):
    """Return the model's iniField file path -- which may not exist yet.

    Preference order: whatever ``IniFieldFile`` in the MDU already points at,
    then any ``fileType = iniField`` file already sitting in the dflowfm folder,
    then :data:`DEFAULT_INIFIELD_NAME` (to be created).
    """
    configured = split_mdu_list(read_mdu_value(mdu_path, MDU_INIFIELD_KEY))
    if configured:
        path = Path(configured[0])
        return path if path.is_absolute() else input_dir / path

    for path in sorted(input_dir.glob("*.ini")):
        if ini_file_type(path) == FILETYPE_INIFIELD:
            return path

    return input_dir / DEFAULT_INIFIELD_NAME


def quantity_from_coverage_name(path):
    """Guess the iniField quantity a coverage holds, from its file name.

    ``frictioncoefficient.xyz`` and ``frictioncoefficient_2024.tif`` both map to
    ``frictioncoefficient``. Returns None when the name matches no known
    quantity -- normal for GeoTIFFs named after their source data -- in which
    case the caller falls back to the model's own iniField file.
    """
    stem = Path(path).stem.lower()
    if stem in INIFIELD_SECTION_BY_QUANTITY:
        return stem
    for quantity in sorted(INIFIELD_SECTION_BY_QUANTITY, key=len, reverse=True):
        if stem.startswith(quantity):
            return quantity
    return None


def _harvest_inifield(ini_path, quantities, blocks):
    """Record what one iniField file knows, into *quantities* and *blocks*.

    *quantities* maps ``datafile name (lower-case) -> quantity``; *blocks* maps
    ``quantity (lower-case) -> (section, [(key, value), ...])``, i.e. the whole
    field block so a removed one can be put back exactly as it was.
    """
    try:
        sections = parse_ini(ini_path)
    except OSError:
        return
    for name, items in sections:
        if name.lower() not in ("initial", "parameter"):
            continue
        quantity = data_file = None
        for k, v in items:
            if k.lower() == "quantity":
                quantity = v
            elif k.lower() == "datafile":
                data_file = v
        if not quantity:
            continue
        if data_file:
            quantities[Path(data_file).name.lower()] = quantity
        blocks[quantity.lower()] = (name, list(items))


def known_fields(mdu_path, input_dir, source=None):
    """Return ``(quantities, blocks)`` gathered from every iniField we can find.

    This is what lets a coverage whose name says nothing about its contents
    (``RHI.tif``) keep the quantity -- and the interpolation settings -- it had
    before the grid was removed.

    Consulted in increasing order of authority (later wins):

    1. the source model's iniField file, when ``-s`` was given;
    2. the backups ``rmgriddimr`` leaves behind (``initialFields.ini.bak*``) --
       the current file no longer lists the 2D blocks it stripped;
    3. the target model's current iniField file.
    """
    quantities, blocks = {}, {}

    if source:
        try:
            src_mdu = find_mdu(source, quiet=True)
            src_ini = locate_project_inifield(src_mdu, src_mdu.parent)
            if src_ini and src_ini.exists():
                _harvest_inifield(src_ini, quantities, blocks)
        except SystemExit:
            pass          # not a model folder (a bare .nc, say) -- nothing to read

    try:
        ini_path = locate_project_inifield(mdu_path, input_dir)
    except OSError:
        return quantities, blocks
    if not ini_path:
        return quantities, blocks

    for bak in sorted(ini_path.parent.glob(ini_path.name + ".bak*")):
        _harvest_inifield(bak, quantities, blocks)

    if ini_path.exists():
        _harvest_inifield(ini_path, quantities, blocks)

    return quantities, blocks


# ---------------------------------------------------------------------------
# MDU read / write helpers
# ---------------------------------------------------------------------------

def read_mdu_value(mdu_path, key):
    """Return the value of *key* in an MDU file, or None if the key is absent.

    An empty value (``Key =``) is returned as an empty string, which is how the
    MDU says "no file configured".
    """
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=(.*)$", re.IGNORECASE)
    with open(mdu_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = pattern.match(line)
            if m:
                return m.group(1).split("#")[0].strip()
    return None


def _rewrite_mdu_line(line, value):
    """Return *line* with its value replaced by *value*, keeping the layout.

    MDU files are column-aligned (``Key<pad>= value<pad># comment``). The
    trailing comment is kept at its original column whenever the new value is
    short enough to allow it, and the original line ending (CRLF on the files
    D-HYDRO writes) is preserved.
    """
    body = line.rstrip("\r\n")
    newline = line[len(body):]
    eq = body.index("=")
    hash_pos = body.find("#", eq)
    head = body[:eq + 1] + " "
    if hash_pos == -1:
        return head + value + newline
    comment = body[hash_pos:]
    text = head + value
    pad = max(hash_pos - len(text), 1)
    return text + " " * pad + comment + newline


def set_mdu_value(mdu_path, key, value):
    """Set *key* to *value* in an MDU file, preserving column alignment.

    Returns ``(old_value, changed)``. The key must already exist -- MDU keys are
    position-sensitive within their section, so this never appends new ones.
    """
    # newline="" on both read and write: MDU files use CRLF and D-HYDRO's own
    # diffs get noisy if we silently rewrite the whole file with LF.
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=", re.IGNORECASE)
    with open(mdu_path, encoding="utf-8", errors="replace", newline="") as fh:
        lines = fh.readlines()

    for i, line in enumerate(lines):
        if not pattern.match(line):
            continue
        old = line.split("=", 1)[1].split("#")[0].strip()
        if old == value:
            return old, False
        lines[i] = _rewrite_mdu_line(line, value)
        with open(mdu_path, "w", encoding="utf-8", newline="") as fh:
            fh.writelines(lines)
        return old, True

    raise KeyError(f"{key} not found in {mdu_path}")


def split_mdu_list(value):
    """Split a semicolon-separated MDU file list into its non-empty entries."""
    if not value:
        return []
    return [p.strip() for p in value.split(";") if p.strip()]


# ---------------------------------------------------------------------------
# Coverage and mesh-extent helpers
# ---------------------------------------------------------------------------

def read_xyz(path):
    """Read a D-Flow FM sample file and return ``(points, bad_lines)``.

    ``points`` is a list of ``(x, y, z)`` tuples; ``bad_lines`` counts lines that
    could not be parsed as three numbers.
    """
    points = []
    bad = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.split("#", 1)[0].split("*", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                bad += 1
                continue
            try:
                points.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                bad += 1
    return points, bad


def raster_extent(path):
    """Return ``(xmin, xmax, ymin, ymax)`` of a raster coverage, or None.

    GeoTIFF / ArcInfo bounds need a raster reader. rasterio and GDAL are both
    tried; when neither is installed the extent check is simply skipped rather
    than made a hard requirement, since it is only a sanity report.
    """
    path = str(path)
    try:
        import rasterio
        with rasterio.open(path) as ds:
            b = ds.bounds
            return (float(b.left), float(b.right), float(b.bottom), float(b.top))
    except ImportError:
        pass
    except Exception:
        return None

    try:
        from osgeo import gdal
        ds = gdal.Open(path)
        if ds is None:
            return None
        gt = ds.GetGeoTransform()
        xs = [gt[0], gt[0] + gt[1] * ds.RasterXSize + gt[2] * ds.RasterYSize]
        ys = [gt[3], gt[3] + gt[4] * ds.RasterXSize + gt[5] * ds.RasterYSize]
        return (min(xs), max(xs), min(ys), max(ys))
    except ImportError:
        return None
    except Exception:
        return None


def mesh2d_extent(net_path):
    """Return ``(xmin, xmax, ymin, ymax)`` of the 2D mesh nodes, or None.

    Reads through an ASCII-safe temp copy, for the same Windows/netCDF4 reason
    as :func:`net_summary`.
    """
    fd, tmp = tempfile.mkstemp(suffix=".nc")
    os.close(fd)
    try:
        shutil.copy2(net_path, tmp)
        with nc.Dataset(tmp, "r") as ds:
            if "Mesh2d_node_x" not in ds.variables:
                return None
            xs = ds.variables["Mesh2d_node_x"][:]
            ys = ds.variables["Mesh2d_node_y"][:]
            if len(xs) == 0:
                return None
            return (float(xs.min()), float(xs.max()),
                    float(ys.min()), float(ys.max()))
    finally:
        os.unlink(tmp)


def samples_in_extent(points, extent):
    """Count how many of *points* fall inside the bounding box *extent*."""
    if extent is None:
        return None
    xmin, xmax, ymin, ymax = extent
    return sum(1 for x, y, _ in points if xmin <= x <= xmax and ymin <= y <= ymax)


def extents_overlap(a, b):
    """True if two ``(xmin, xmax, ymin, ymax)`` boxes intersect."""
    if a is None or b is None:
        return None
    return not (a[1] < b[0] or b[1] < a[0] or a[3] < b[2] or b[3] < a[2])


# ---------------------------------------------------------------------------
# Field restore
# ---------------------------------------------------------------------------

def collect_field_inputs(src_dir):
    """Classify the ``*.ini`` and coverage files in *src_dir*.

    Returns a dict with ``inifield`` (list of Paths), ``roughness`` (list of
    Paths), ``coverage`` (dict of lower-cased name -> Path) and ``skipped``
    (list of ``(Path, reason)`` for .ini files of an unrecognised type).
    """
    src_dir = Path(src_dir)
    result = {"inifield": [], "roughness": [], "coverage": {}, "skipped": []}

    for ext in COVERAGE_TYPES:
        for path in sorted(src_dir.glob(f"*{ext}")):
            result["coverage"][path.name.lower()] = path

    for path in sorted(src_dir.glob("*.ini")):
        ftype = ini_file_type(path)
        if ftype == FILETYPE_INIFIELD:
            result["inifield"].append(path)
        elif ftype == FILETYPE_ROUGHNESS:
            result["roughness"].append(path)
        else:
            result["skipped"].append((path, f"fileType={ftype or 'unknown'}"))

    return result


def _backup_existing(path, stamp):
    """Copy *path* aside as ``<path>.bak_<stamp>`` if it exists. Returns bool."""
    if not path.exists():
        return False
    shutil.copy2(path, f"{path}.bak_{stamp}")
    return True


def plan_fields(src_dir, found, quantity_map=None, registered=None,
                templates=None, verbose=True):
    """Work out which coverage feeds which iniField quantity.

    Returns ``(plan, extra)``. *plan* is a list of dicts with ``section``,
    ``quantity``, ``dataFile``, ``dataFileType`` and ``coveragePath``; *extra*
    holds coverages whose quantity could not be determined (copied along, but
    not registered).

    Resolution order for a coverage's quantity: an explicit ``-q`` mapping, the
    file name, then the quantity the model's own iniField file already has that
    file registered against (*registered*). When an iniField ``.ini`` is
    supplied in *src_dir*, its blocks drive the plan instead.
    """
    quantity_map = {k.lower(): v for k, v in (quantity_map or {}).items()}
    registered = {k.lower(): v for k, v in (registered or {}).items()}
    templates = templates or {}
    plan = []
    claimed = set()

    if found["inifield"]:
        # An iniField file was handed to us: it is authoritative.
        missing = []
        for ini_path in found["inifield"]:
            for entry in inifield_entries(ini_path):
                data_file = entry["dataFile"]
                coverage = None
                if data_file:
                    coverage = found["coverage"].get(Path(data_file).name.lower())
                    if coverage is None:
                        coverage = src_dir / data_file
                        if not coverage.exists():
                            missing.append((ini_path.name, data_file))
                            continue
                    claimed.add(coverage.name.lower())
                plan.append({
                    "section": entry["section"],
                    "quantity": entry["quantity"],
                    "dataFile": data_file,
                    "dataFileType": coverage_type(data_file) if data_file else None,
                    "coveragePath": coverage,
                })
        if missing:
            for ini_name, data_file in missing:
                print(f"Error: {ini_name} references '{data_file}', "
                      f"which is not in {src_dir}")
            sys.exit("Aborting: referenced coverage file(s) missing.")
    else:
        for name, path in sorted(found["coverage"].items()):
            quantity = (quantity_map.get(name)
                        or quantity_from_coverage_name(path)
                        or registered.get(name))
            if not quantity:
                continue
            claimed.add(name)
            template = templates.get(quantity.lower())
            plan.append({
                "section": template[0] if template
                           else INIFIELD_SECTION_BY_QUANTITY.get(
                               quantity.lower(), "Initial"),
                "quantity": quantity,
                "dataFile": path.name,
                "dataFileType": coverage_type(path),
                "coveragePath": path,
                "template": template,
            })
        # D-HYDRO lists the [Initial] blocks before the [Parameter] ones; match
        # that so a generated file looks like one the GUI wrote itself.
        plan.sort(key=lambda f: (f["section"].lower() != "initial",
                                 f["quantity"].lower()))

    extra = [p for name, p in sorted(found["coverage"].items())
             if name not in claimed]
    if verbose:
        for path in extra:
            print(f"  note: cannot tell which quantity {path.name} holds; "
                  "copying it but not registering it (use -q NAME=FILE)")

    return plan, extra


def report_fields(plan, extent, verbose=True):
    """Print each planned field and how it sits relative to the 2D mesh."""
    if not verbose:
        return
    if extent:
        print(f"\n2D mesh extent : x [{extent[0]:.1f}, {extent[1]:.1f}]  "
              f"y [{extent[2]:.1f}, {extent[3]:.1f}]")
    else:
        print("\n2D mesh extent : unavailable (no 2D mesh in the net file?) "
              "-- restore the grid first with -s.")

    print("\nFields to restore:")
    for field in plan:
        label = f"  [{field['section']}] {field['quantity']}"
        path = field["coveragePath"]
        if not path:
            print(f"{label}: no dataFile (uniform value)")
            continue

        ftype = field.get("dataFileType")
        if ftype == "sample":
            points, bad = read_xyz(path)
            inside = samples_in_extent(points, extent)
            detail = f"{len(points)} samples"
            if bad:
                detail += f" ({bad} unparsable line(s))"
            if inside is not None:
                pct = 100.0 * inside / len(points) if points else 0.0
                detail += f", {inside} inside the 2D mesh extent ({pct:.0f}%)"
            zs = [p[2] for p in points]
            if zs:
                detail += f", value range {min(zs):g}..{max(zs):g}"
            print(f"{label}: {field['dataFile']} -- {detail}")
            if inside == 0 and points:
                print("      WARNING: no sample falls inside the 2D mesh; "
                      "check the coordinate system.")
            continue

        # Raster coverage (GeoTIFF / ArcInfo).
        box = raster_extent(path)
        detail = ftype or Path(path).suffix.lstrip(".")
        if box is None:
            detail += " (extent check skipped: install rasterio or GDAL)"
        else:
            detail += (f", extent x [{box[0]:.1f}, {box[1]:.1f}] "
                       f"y [{box[2]:.1f}, {box[3]:.1f}]")
        print(f"{label}: {field['dataFile']} -- {detail}")
        if extents_overlap(box, extent) is False:
            print("      WARNING: the raster does not overlap the 2D mesh; "
                  "check the coordinate system.")


def restore_fields(target, src_dir, quantity_map=None, source=None, verbose=True):
    """Restore the 2D spatial fields of *target* from the files in *src_dir*.

    *target* is a DIMR run folder, dimr.xml, dflowfm folder or .mdu file. The
    coverage files in *src_dir* are copied into the model's dflowfm folder and
    wired up in the model's iniField file, which is **created if the model has
    none and updated in place if it has one** -- an ``initialFields.ini`` among
    the inputs is optional. Roughness ``.ini`` files found in *src_dir* are
    copied and registered in ``FrictFile``. Everything overwritten is backed up
    with a timestamp first.

    Returns a dict summarising what was restored.
    """
    src_dir = Path(src_dir).resolve()

    mdu_path = find_mdu(target)
    input_dir = mdu_path.parent
    if verbose:
        print(f"MDU     : {mdu_path}")
        print(f"Model dir   : {input_dir}")
        print(f"Fields from : {src_dir}")

    if input_dir.resolve() == src_dir:
        sys.exit("Error: the field source directory is the model's dflowfm "
                 "folder; nothing to restore. Run from the folder holding the "
                 "coverage/.ini files, or pass -d.")

    found = collect_field_inputs(src_dir)
    for path, reason in found["skipped"]:
        if verbose:
            print(f"  skipping {path.name} ({reason})")

    if not found["coverage"] and not found["inifield"] and not found["roughness"]:
        exts = ", ".join(sorted(COVERAGE_TYPES))
        sys.exit(f"Error: no coverage files ({exts}) and no field/roughness "
                 f"*.ini files found in {src_dir}.")

    registered, templates = known_fields(mdu_path, input_dir, source)
    plan, extra = plan_fields(src_dir, found, quantity_map, registered,
                              templates, verbose)

    if not plan and not found["roughness"]:
        sys.exit(f"Error: none of the coverage files in {src_dir} could be "
                 "matched to an iniField quantity. Map them explicitly with "
                 "-q NAME=FILE, e.g. -q frictioncoefficient=RHI.tif.")

    # ---- report the fields against the restored 2D mesh --------------------
    extent = None
    try:
        net_path = read_net_file_from_mdu(mdu_path)
        if net_path.exists():
            extent = mesh2d_extent(net_path)
    except SystemExit:
        raise
    except Exception as e:                                  # pragma: no cover
        print(f"  warning: could not read the 2D mesh extent ({e})")

    report_fields(plan, extent, verbose)

    # ---- copy the files ----------------------------------------------------
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    copied, backed_up = [], []

    def install(src):
        dst = input_dir / src.name
        if dst.resolve() == Path(src).resolve():
            return
        if dst.exists() and _backup_existing(dst, stamp):
            backed_up.append(dst.name)
        shutil.copy2(src, dst)
        copied.append(dst.name)

    for src in found["inifield"] + found["roughness"]:
        install(src)
    coverages = [f["coveragePath"] for f in plan if f["coveragePath"]] + extra
    for src in sorted(set(coverages), key=lambda p: p.name):
        install(src)

    if verbose and copied:
        print(f"\nCopied into the dflowfm folder: {', '.join(copied)}")
        if backed_up:
            print(f"  (previous versions backed up as *.bak_{stamp}: "
                  f"{', '.join(backed_up)})")

    # ---- create or update the model's iniField file ------------------------
    ini_path = None
    ini_changes = []
    if found["inifield"]:
        # The supplied file was copied verbatim; nothing left to write.
        ini_path = input_dir / found["inifield"][0].name
    elif plan:
        ini_path = locate_project_inifield(mdu_path, input_dir)
        existed = ini_path.exists()
        sections = parse_ini(ini_path) if existed else []
        sections, ini_changes = apply_fields_to_inifield(sections, plan)
        if existed:
            _backup_existing(ini_path, stamp)
        write_ini(ini_path, sections)
        if verbose:
            verb = "Updated" if existed else "Created"
            print(f"\n{verb} iniField file: {ini_path.name}")
            for change in ini_changes:
                print(f"  {change}")
            if existed and not ini_changes:
                print("  (already pointed at these coverage files)")

    # ---- re-register everything in the MDU ---------------------------------
    _backup_existing(mdu_path, stamp)
    changes = []

    if ini_path is not None:
        value = ";".join(p.name for p in found["inifield"]) or ini_path.name
        old, changed = set_mdu_value(mdu_path, MDU_INIFIELD_KEY, value)
        if changed:
            changes.append(f"{MDU_INIFIELD_KEY}: '{old}' -> '{value}'")

    if found["roughness"]:
        existing = split_mdu_list(read_mdu_value(mdu_path, MDU_FRICTFILE_KEY))
        merged = list(existing)
        for p in found["roughness"]:
            if p.name not in merged:
                merged.append(p.name)
        value = ";".join(merged)
        old, changed = set_mdu_value(mdu_path, MDU_FRICTFILE_KEY, value)
        if changed:
            changes.append(f"{MDU_FRICTFILE_KEY}: '{old}' -> '{value}'")

    # An infiltrationcapacity field only takes effect when the infiltration
    # model is switched on; restoring the file without this is a silent no-op.
    has_infiltration = any(
        (f["quantity"] or "").lower() == QUANTITY_INFILTRATION for f in plan
    )
    if has_infiltration:
        current = read_mdu_value(mdu_path, MDU_INFILTRATION_KEY)
        if current is not None and current in ("", "0"):
            old, changed = set_mdu_value(
                mdu_path, MDU_INFILTRATION_KEY, str(INFILTRATION_MODEL_CONSTANT)
            )
            if changed:
                changes.append(
                    f"{MDU_INFILTRATION_KEY}: '{old}' -> "
                    f"'{INFILTRATION_MODEL_CONSTANT}' (constant capacity)"
                )

    if verbose:
        if changes:
            print("\nMDU updated:")
            for c in changes:
                print(f"  {c}")
        else:
            print("\nMDU already pointed at these files; no keys changed.")

    return {
        "mdu": mdu_path,
        "input_dir": input_dir,
        "iniField": ini_path,
        "copied": copied,
        "backed_up": backed_up,
        "fields": plan,
        "ini_changes": ini_changes,
        "mdu_changes": changes,
        "extent": extent,
    }


def restore_mesh(target, source):
    """Restore the 2D mesh of *target* from *source*, keeping target's 1D net."""
    dst = resolve_net_file(target)
    print(f"NetFile (target) : {dst}")
    src = resolve_net_file(source)
    print(f"NetFile (source) : {src}")

    if not src.exists():
        print(f"Error: source NetFile not found at {src}")
        sys.exit(1)
    if not dst.exists():
        print(f"Error: target NetFile not found at {dst}")
        sys.exit(1)
    if src.resolve() == dst.resolve():
        sys.exit("Error: source and target resolve to the same net file.")

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
        print("Run the DIMR model (or open the folder in D-HYDRO) to confirm "
              "the grid is back.")
    else:
        if not mesh_ok:
            print("\nWarning: post-restore 2D mesh does not match the source.")
        if not one_d_ok:
            print("\nWarning: the target's 1D network changed during restore.")
        print(f"Original target preserved at:\n    {backup}")
        sys.exit(1)


def main():
    """Main function for the command line interface."""
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Restore the 2D computational mesh (incl. Mesh2d_face_z bed "
                    "levels) and/or the 2D spatial fields (bed level, "
                    "infiltration capacity, roughness) of a D-Flow FM model in "
                    "a DIMR run folder, while preserving the target's 1D "
                    "network.",
        epilog="""
examples:
  %(prog)s -s C:/models/Intact              # restore mesh into the model in cwd
  %(prog)s -i C:/models/PT01 -s C:/models/Intact
  %(prog)s -i C:/models/PT01 -s C:/models/Intact/dimr.xml
  %(prog)s -s source_net.nc                 # source given directly as a net file
  %(prog)s -i target_net.nc -s source_net.nc

  %(prog)s -f                               # restore fields from cwd
  %(prog)s -i C:/models/PT01 -f -d fields/  # take the coverages from fields/
  %(prog)s -i C:/models/PT01 -s Intact -f   # mesh first, then the fields
  %(prog)s -f -q frictioncoefficient=RHI.tif

Coverage files (*.xyz, *.tif, *.asc) are all that is required: the iniField
file (initialFields.ini) is created if the model has none, and otherwise
updated in place -- just its dataFile entries, leaving your interpolation and
averaging settings alone.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-i",
        "--input",
        default=None,
        help="Target to restore INTO: DIMR run folder, dimr.xml, dflowfm "
             "folder, .mdu or .nc file (default: current directory)",
    )
    parser.add_argument(
        "-s",
        "--source",
        default=None,
        help="Source with the intact 2D mesh to restore the mesh FROM: DIMR run "
             "folder, dimr.xml, dflowfm folder, .mdu or .nc file",
    )
    parser.add_argument(
        "-f",
        "--fields",
        action="store_true",
        help="Restore the 2D spatial fields (bed level, infiltration capacity "
             "and roughness): copy the coverage and *.ini files from the fields "
             "directory into the dflowfm folder and re-register them in the MDU",
    )
    parser.add_argument(
        "-d",
        "--fields-dir",
        default=".",
        metavar="DIR",
        help="Directory holding the coverage / *.ini field files used by -f "
             "(default: current directory)",
    )
    parser.add_argument(
        "-q",
        "--quantity",
        action="append",
        default=[],
        metavar="NAME=FILE",
        help="Map a coverage file to an iniField quantity when its name does "
             "not say so, e.g. -q frictioncoefficient=RHI.tif. Repeatable.",
    )
    args = parser.parse_args()

    quantity_map = {}
    for item in args.quantity:
        if "=" not in item:
            parser.error(f"--quantity expects NAME=FILE, got '{item}'")
        name, _, filename = item.partition("=")
        if not name.strip() or not filename.strip():
            parser.error(f"--quantity expects NAME=FILE, got '{item}'")
        quantity_map[filename.strip()] = name.strip()

    if not args.source and not args.fields:
        parser.error("nothing to do: give -s to restore the 2D mesh, "
                     "-f to restore the spatial fields, or both.")

    target = Path(args.input).resolve() if args.input else Path(".").resolve()
    if not target.exists():
        print(f"Error: target does not exist: {target}")
        sys.exit(1)

    print(f"Target  : {target}")
    if args.source:
        print(f"Source  : {args.source}")

    try:
        if args.source:
            restore_mesh(target, args.source)
        if args.fields:
            if args.source:
                print("\n" + "-" * 70)
            restore_fields(target, args.fields_dir, quantity_map, args.source)
            print("\nSuccess: spatial fields restored and registered in the MDU.")
            print("Check the dflowfm folder to confirm the coverages are back "
                  "on the 2D grid.")
    except SystemExit:
        raise
    except Exception as e:
        print(f"Error processing model: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
