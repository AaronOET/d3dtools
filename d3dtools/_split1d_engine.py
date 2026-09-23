#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared engine behind the ``rm1dch``, ``rm1dsw`` and ``mk2d`` console commands.

Split a Delft3D FM (D-HYDRO / FM Suite) 1D2D model by removing one half of the
1D network and everything anchored on it, leaving the other half and the 2D
grid completely intact.

    --target channel   (rm1dch default)  remove the open 1D CHANNELS,
                                  keep pipes, sewer connections and manholes
    --target sewer     (rm1dsw default)  remove the SEWER system - pipes,
                                  sewer connections and all manholes /
                                  storage nodes - keep the 1D channels
    --target all       (mk2d default)    remove the ENTIRE 1D network -
                                  channels, sewers, manholes and every 1D
                                  structure - and leave a 2D-only model

``rm1dch``, ``rm1dsw`` and ``mk2d`` are thin wrappers around this module that
only set the ``--target`` default (and the report/log file name) before
calling :func:`main`, so all three commands run identical, tested code.

Everything removed with a branch goes with it: structures (pump, bridge, weir,
orifice, culvert, ...), cross sections and their definitions, 1D2D links,
mesh1d nodes and edges, network nodes, storage nodes, boundary/lateral blocks
and forcing records.  Mesh2d is copied through untouched.

Where the two halves were joined, a manhole is put in.  Every network node at
which a sewer branch that stays ran into a branch that goes is found BEFORE the
branch is removed, and a [StorageNode] is written on it in nodeFile.ini, so the
sewer keeps a proper outfall compartment instead of a pipe ending in mid-air.
Its levels follow the sewer branch it closes off (--manhole-levels), and the new
name is filled in as that branch's source/targetCompartmentName in branches.gui.
Switch it off with --no-outfall-manholes.

Written for:
    G:\\WORK\\2026_WRPB\\TN\\TNN_YA_GAEMIv2_001\\YAGM0912.dsproj_data\\TNN_YA\\input

but it is generic: every file it touches is discovered from the .mdu.

What it touches
---------------
  FlowFM_net.nc      network / mesh1d branches, nodes, edges, geometry,
                     1D2D contacts (link1d2d).  Mesh2d is copied verbatim.
  structures.ini     [Structure] blocks whose branchId is a removed branch
  crsloc.ini         [CrossSection] blocks on removed branches
  crsdef.ini         [Definition] blocks that nothing references any more
  nodeFile.ini       [StorageNode] blocks on network nodes that disappeared
                     (with --target sewer: every storage node), plus a NEW
                     [StorageNode] on every node where a sewer branch that
                     stays ran into a branch that is removed
  branches.gui       [Branch] blocks for the removed branches; the
                     source/targetCompartmentName of a sewer branch that now
                     ends in one of the new manholes
  routes.gui         [Route] blocks on removed branches
  *.bc               forcing blocks belonging to removed structures
  *.ext              [Lateral]/[Boundary] blocks on removed branches/nodes
  roughness-*.ini    [Branch] sections of removed branches
  1dField files      [Branch] sections of removed branches
  <model>.mdu        blanks the keys whose file became empty

Every file it rewrites is first backed up to <name>.bak (or .bak2, .bak3 ...
if a backup already exists, so an existing FlowFM_net.nc.bak is never lost).

How a branch is classified
--------------------------
1. branches.gui  ->  branchType  (0 = Channel, 1 = SewerConnection, 2 = Pipe).
   This is the authoritative source and is used whenever the branch is listed.
2. For branches missing from branches.gui, the codes in the net-file variable
   `network_branch_type` are used.  The code -> class mapping is *learned*
   from the branches that appear in both files (majority vote), so it works
   even though the numeric codes differ between FM Suite versions
   (these projects use 4 = channel, 3 = pipe/sewer connection).
3. Anything still unknown falls back to the branch name
   ("Channel_1D_12" -> channel, "Pipe_..."/"SewerConnection_..." -> sewer).
4. Anything still unclassified is KEPT (change with --unknown-as).
5. --remove-ids / --keep-ids override everything.

IMPORTANT - close the project in the FM Suite before running.  A loaded
DeltaShell project holds the whole network in memory; these files are only its
last export, so editing them changes nothing on screen and the next Save writes
the old network straight back over them.  Run --check to see who wrote the
model last.

Usage
-----
    rm1dch <input-folder-or-mdu> --check
    rm1dch <input-folder-or-mdu> --dry-run
    rm1dch <input-folder-or-mdu>
    rm1dsw <input-folder-or-mdu> --no-outfall-manholes
    mk2d <input-folder-or-mdu>

Requires: numpy, netCDF4      (pip install numpy netCDF4)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter, OrderedDict
from datetime import datetime

import numpy as np

try:
    import netCDF4 as nc
except ImportError:  # pragma: no cover
    sys.exit("netCDF4 is required:  pip install netCDF4")


# --------------------------------------------------------------------------- #
#  small helpers
# --------------------------------------------------------------------------- #

LOG_LINES = []


def log(msg=""):
    print(msg)
    LOG_LINES.append(str(msg))


def norm_id(s):
    """Branch / node ids: strip padding and the trailing '#' the GUI adds."""
    if s is None:
        return None
    return str(s).strip().rstrip("#").strip()


BACKED_UP = {}


def backup(path, dry=False):
    """Copy path -> path.bak, never overwriting an existing backup.

    A file this run already backed up is not copied again: several steps may
    rewrite the same file (nodeFile.ini is filtered and then gets the new
    outfall manholes) and the backup must stay the state before the run."""
    if not os.path.isfile(path):
        return None
    key = os.path.abspath(path)
    if key in BACKED_UP:
        return BACKED_UP[key]
    cand = path + ".bak"
    n = 2
    while os.path.exists(cand):
        cand = "%s.bak%d" % (path, n)
        n += 1
    if not dry:
        shutil.copy2(path, cand)
    BACKED_UP[key] = cand
    return cand


# --------------------------------------------------------------------------- #
#  Deltares ini-style files ([Section] + key = value), formatting preserving
# --------------------------------------------------------------------------- #

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KEY_RE = re.compile(r"^\s*([A-Za-z_][\w\-.]*)\s*=\s*(.*?)\s*$")


class Block(object):
    """One [Section] with its raw text, so rewriting keeps the original layout."""

    __slots__ = ("name", "lines", "kv")

    def __init__(self, name, lines):
        self.name = name
        self.lines = lines            # includes the '[Section]' line itself
        self.kv = {}
        for ln in lines[1:]:
            ln = ln.split("#", 1)[0]  # strip trailing comment
            m = _KEY_RE.match(ln)
            if m:
                self.kv[m.group(1).lower()] = m.group(2).strip()

    def get(self, key, default=None):
        return self.kv.get(key.lower(), default)

    def text(self):
        return "".join(self.lines)


class IniFile(object):
    """Parsed Deltares ini file: a preamble plus an ordered list of Blocks."""

    def __init__(self, path):
        self.path = path
        self.preamble = []
        self.blocks = []
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            lines = fh.readlines()
        cur = None
        for ln in lines:
            m = _SECTION_RE.match(ln)
            if m:
                if cur is not None:
                    self.blocks.append(Block(cur[0], cur[1]))
                cur = (m.group(1).strip(), [ln])
            elif cur is None:
                self.preamble.append(ln)
            else:
                cur[1].append(ln)
        if cur is not None:
            self.blocks.append(Block(cur[0], cur[1]))

    def of(self, name):
        low = name.lower()
        return [b for b in self.blocks if b.name.lower() == low]

    def data_blocks(self, header_names=("general",)):
        """Everything that is not a file header, i.e. the actual content."""
        hdr = {h.lower() for h in header_names}
        return [b for b in self.blocks if b.name.lower() not in hdr]

    def dumps(self):
        return "".join(self.preamble) + "".join(b.text() for b in self.blocks)

    def save(self, path=None):
        path = path or self.path
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.dumps())


def set_kv_line(line, value):
    """Replace the value of a `key = value` line, keeping its column layout."""
    nl = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
    body = line[:len(line) - len(nl)] if nl else line
    head, sep, comment = body.partition("#")
    if "=" not in head:
        return line
    eq = head.index("=")
    width = len(head) - (eq + 1)
    val = " " + str(value)
    return head[:eq + 1] + val.ljust(max(width, len(val))) + \
        (sep + comment if sep else "") + nl


def new_kv_line(sample, key, value, comment=None):
    """Build a `key = value` line aligned like `sample`, with its line ending."""
    sample = sample or "    key                   = value\n"
    nl = "\r\n" if sample.endswith("\r\n") else "\n"
    body = sample.rstrip("\r\n")
    head = body.partition("#")[0]
    indent = len(head) - len(head.lstrip())
    eq = head.index("=") if "=" in head else len(head)
    width = max(len(head) - (eq + 1), 1)
    val = " " + str(value)
    line = " " * indent + str(key).ljust(max(eq - indent, len(str(key)) + 1)) + \
        "=" + val.ljust(max(width, len(val)))
    if comment:
        return line + "# " + comment + nl
    return line.rstrip() + nl


# --------------------------------------------------------------------------- #
#  MDU
# --------------------------------------------------------------------------- #

class Mdu(IniFile):
    """The .mdu is ini-shaped; we only need to read keys and blank a few."""

    def value(self, section, key):
        for b in self.of(section):
            v = b.get(key)
            if v is not None:
                return v.strip()
        return None

    def files(self, section, key):
        """Split a value that may list several files (space / ; / , separated)."""
        v = self.value(section, key)
        if not v:
            return []
        return [p for p in re.split(r"[;,\s]+", v) if p]

    def blank(self, section, key):
        """Blank a key's value, keeping the alignment and trailing comment."""
        low = key.lower()
        for b in self.of(section):
            for i, ln in enumerate(b.lines):
                m = _KEY_RE.match(ln.split("#", 1)[0])
                if m and m.group(1).lower() == low:
                    head, sep, comment = ln.partition("#")
                    eq = head.index("=")
                    pad = len(head.rstrip("\r\n")) - eq - 1
                    newhead = head[:eq + 1] + " " * max(pad, 1)
                    b.lines[i] = newhead + (sep + comment if sep else
                                            ("\n" if not head.endswith("\n") else ""))
                    if not b.lines[i].endswith("\n"):
                        b.lines[i] += "\n"
                    b.kv[low] = ""
                    return True
        return False

    def set_value(self, section, key, value):
        """Write a value into an existing key, or add the key to the section."""
        low = key.lower()
        blocks = self.of(section)
        if not blocks:
            return False
        for b in blocks:
            for i, ln in enumerate(b.lines):
                m = _KEY_RE.match(ln.split("#", 1)[0])
                if m and m.group(1).lower() == low:
                    b.lines[i] = set_kv_line(ln, value)
                    b.kv[low] = str(value)
                    return True
        b = blocks[0]
        sample = next((ln for ln in b.lines[1:] if _KEY_RE.match(ln.split("#", 1)[0])),
                      None)
        at = max((i for i, ln in enumerate(b.lines)
                  if _KEY_RE.match(ln.split("#", 1)[0])), default=0)
        b.lines.insert(at + 1, new_kv_line(sample, key, value))
        b.kv[low] = str(value)
        return True


# --------------------------------------------------------------------------- #
#  branch classification
# --------------------------------------------------------------------------- #

CHANNEL, SEWER, ALL = "channel", "sewer", "all"
GUI_TYPE = {0: CHANNEL, 1: SEWER, 2: SEWER}   # Channel / SewerConnection / Pipe

# Overridden by the rm1dch/rm1dsw/mk2d wrappers before calling main(); the
# engine itself is direction-agnostic.
DEFAULT_TARGET = CHANNEL

# Command name used in the report header, the report/log file name and the
# net file's history attribute. Overridden by the wrapper modules.
SCRIPT = "rm1dch"

# argparse description. Overridden by the wrapper modules.
DESCRIPTION = ("Remove the open 1D channels from a Delft3D FM model, keeping "
              "pipes, sewer connections, manholes and the 2D grid intact.  "
              "See --target to remove something else instead.")

WORDS = {
    CHANNEL: ("1D channel", "1D channels", "pipes, sewer connections and manholes"),
    SEWER: ("sewer branch", "sewers (pipes + sewer connections) and manholes",
            "the 1D channels"),
    ALL: ("1D branch", "the whole 1D network - channels, sewers, manholes and "
          "every 1D structure", "the 2D grid"),
}


def classify_branches(branch_ids, nc_types, gui_path, force_remove, force_keep,
                      unknown_as, target=CHANNEL):
    """Return (cls dict id->CHANNEL/SEWER, report dict)."""
    cls = {}
    src = {}

    # 1. branches.gui -------------------------------------------------------
    gui = {}
    if gui_path and os.path.isfile(gui_path):
        f = IniFile(gui_path)
        for b in f.of("Branch"):
            name = norm_id(b.get("name"))
            bt = b.get("branchType")
            if name is not None and bt is not None:
                try:
                    gui[name] = GUI_TYPE.get(int(float(bt)), SEWER)
                except ValueError:
                    pass
    for bid in branch_ids:
        if bid in gui:
            cls[bid] = gui[bid]
            src[bid] = "branches.gui"

    # 2. learn the net-file code -> class mapping from the overlap ----------
    code_map = {}
    if nc_types is not None:
        votes = {}
        for bid, code in zip(branch_ids, nc_types):
            if bid in gui:
                votes.setdefault(int(code), Counter())[gui[bid]] += 1
        for code, cnt in votes.items():
            code_map[code] = cnt.most_common(1)[0][0]
        for bid, code in zip(branch_ids, nc_types):
            if bid not in cls and int(code) in code_map:
                cls[bid] = code_map[int(code)]
                src[bid] = "network_branch_type=%d" % int(code)

    # 3. name heuristic -----------------------------------------------------
    for bid in branch_ids:
        if bid in cls:
            continue
        low = bid.lower()
        if "channel" in low:
            cls[bid], src[bid] = CHANNEL, "name"
        elif "pipe" in low or "sewer" in low or "manhole" in low:
            cls[bid], src[bid] = SEWER, "name"
        else:
            if unknown_as == "keep":
                cls[bid] = SEWER if target == CHANNEL else CHANNEL
            else:
                cls[bid] = CHANNEL if unknown_as == "channel" else SEWER
            src[bid] = "unclassified->%s" % cls[bid]

    # 4. explicit overrides -------------------------------------------------
    other = SEWER if target == CHANNEL else CHANNEL
    for bid in force_remove:
        if bid in cls:
            cls[bid], src[bid] = target, "--remove-ids"
    for bid in force_keep:
        if bid in cls:
            cls[bid], src[bid] = other, "--keep-ids"

    return cls, {"gui": gui, "code_map": code_map, "source": src}


# --------------------------------------------------------------------------- #
#  netCDF surgery
# --------------------------------------------------------------------------- #

def _chars_to_str(arr):
    return [str(s).strip() for s in nc.chartostring(arr)]


class NetTopology(object):
    """Resolve the UGRID topology variables of a D-Flow FM _net.nc."""

    def __init__(self, ds):
        self.ds = ds
        self.network = self.mesh1d = self.mesh2d = None
        self.contact_topo = None
        for name, var in ds.variables.items():
            role = getattr(var, "cf_role", "")
            if role == "mesh_topology":
                dim = int(getattr(var, "topology_dimension", 0))
                if dim == 2:
                    self.mesh2d = name
                elif dim == 1:
                    if hasattr(var, "coordinate_space") or hasattr(var, "node_id") \
                            and not hasattr(var, "branch_id"):
                        if hasattr(var, "coordinate_space"):
                            self.mesh1d = name
                        else:
                            self.network = name
                    if hasattr(var, "branch_id"):
                        self.network = name
            elif role == "mesh_topology_contact":
                self.contact_topo = name

        def att(v, a, default=None):
            return getattr(ds.variables[v], a, default) if v else default

        self.branch_dim = att(self.network, "edge_dimension")
        self.netnode_dim = att(self.network, "node_dimension")
        self.m1d_node_dim = att(self.mesh1d, "node_dimension")
        self.m1d_edge_dim = att(self.mesh1d, "edge_dimension")
        self.net_edge_nodes = att(self.network, "edge_node_connectivity")
        self.m1d_edge_nodes = att(self.mesh1d, "edge_node_connectivity")
        self.branch_id_var = att(self.network, "branch_id")
        self.netnode_id_var = att(self.network, "node_id")
        nc_coords = (att(self.network, "node_coordinates", "") or "").split()
        self.netnode_x = nc_coords[0] if len(nc_coords) > 0 else None
        self.netnode_y = nc_coords[1] if len(nc_coords) > 1 else None

        # geometry (the polyline of every branch)
        geom = att(self.network, "edge_geometry")
        self.geom_count_var = self.geom_dim = None
        if geom and geom in ds.variables:
            g = ds.variables[geom]
            self.geom_count_var = getattr(g, "node_count", None)
            coords = getattr(g, "node_coordinates", "")
            for cv in coords.split():
                if cv in ds.variables and ds.variables[cv].dimensions:
                    self.geom_dim = ds.variables[cv].dimensions[0]
                    break
        if self.geom_count_var is None:
            for cand in ("network_geom_node_count", "network1d_geom_node_count"):
                if cand in ds.variables:
                    self.geom_count_var = cand
                    self.geom_dim = ds.variables.get(
                        cand.replace("node_count", "x"),
                        ds.variables[cand]).dimensions[0]
        if self.geom_count_var and self.geom_dim is None:
            for name, var in ds.variables.items():
                if "geom_x" in name and var.dimensions:
                    self.geom_dim = var.dimensions[0]
                    break

        # which variable holds the branch index of every mesh1d node / edge
        self.m1d_node_branch = self._find_branch_index(self.m1d_node_dim)
        self.m1d_edge_branch = self._find_branch_index(self.m1d_edge_dim)

        # 1D2D contacts
        self.contact_var, self.contact_dim, self.m1d_col = None, None, 0
        self._resolve_contacts()

    def _find_branch_index(self, dim):
        if not dim:
            return None
        for name, var in self.ds.variables.items():
            if var.dimensions == (dim,) and "branch" in name.lower() \
                    and np.issubdtype(var.dtype, np.integer):
                return name
        return None

    def _contact_column_from_attrs(self):
        """UGRID: the topology's `contact` attribute lists the two meshes in
        column order, e.g. "mesh1d: node Mesh2d: face"."""
        if not self.contact_topo or not self.mesh1d:
            return None
        spec = getattr(self.ds.variables[self.contact_topo], "contact", "")
        meshes = re.findall(r"([A-Za-z_]\w*)\s*:\s*\w+", spec)
        for col, name in enumerate(meshes[:2]):
            if name.lower() == self.mesh1d.lower():
                return col
        return None

    def _resolve_contacts(self):
        ds = self.ds
        n_m1d = len(ds.dimensions[self.m1d_node_dim]) if self.m1d_node_dim else 0
        n_2d = 0
        if self.mesh2d:
            fd = getattr(ds.variables[self.mesh2d], "face_dimension", "")
            if fd in ds.dimensions:
                n_2d = len(ds.dimensions[fd])

        cand = []
        if self.contact_topo:
            c = ds.variables[self.contact_topo]
            for a in ("contact_connectivity", "contact_id", "contact"):
                v = getattr(c, a, None)
                if v and v in ds.variables and ds.variables[v].ndim == 2 \
                        and np.issubdtype(ds.variables[v].dtype, np.integer):
                    cand.append(v)
            if c.ndim == 2:
                cand.append(self.contact_topo)
        for name, var in ds.variables.items():
            if var.ndim == 2 and var.shape[1] == 2 \
                    and np.issubdtype(var.dtype, np.integer) \
                    and re.search(r"(link1d2d|contact)", name, re.I) \
                    and name not in (self.net_edge_nodes, self.m1d_edge_nodes):
                cand.append(name)

        seen = set()
        for name in cand:
            if name in seen:
                continue
            seen.add(name)
            var = ds.variables[name]
            self.contact_var, self.contact_dim = name, var.dimensions[0]
            col = self._contact_column_from_attrs()
            if col is None and var.shape[0]:
                data = np.asarray(var[:], dtype=np.int64) - \
                    int(getattr(var, "start_index", 0))
                c0_1d = n_m1d and data[:, 0].max() < n_m1d
                c1_1d = n_m1d and data[:, 1].max() < n_m1d
                c0_2d = n_2d and data[:, 0].max() < n_2d
                c1_2d = n_2d and data[:, 1].max() < n_2d
                if c0_1d and not c1_1d:
                    col = 0
                elif c1_1d and not c0_1d:
                    col = 1
                elif c0_1d and c1_2d and not c0_2d:
                    col = 0
                elif c1_1d and c0_2d and not c1_2d:
                    col = 1
            self.m1d_col = 0 if col is None else col
            return


def _find_var(ds, dims, word, float_only=False, chars=False):
    """First variable whose leading dimensions are `dims` and whose name
    contains `word` (e.g. network_edge_length, mesh1d_node_offset)."""
    dims = tuple(dims)
    if not all(dims):
        return None
    for name, var in ds.variables.items():
        if word not in name.lower() or var.dimensions[:len(dims)] != dims:
            continue
        if chars:
            if var.dtype.kind in "SU" or var.dtype == str:
                return name
            continue
        if len(var.dimensions) != len(dims):
            continue
        if float_only and not np.issubdtype(var.dtype, np.floating):
            continue
        return name
    return None


def _override_rows(data, var, values, row_map, remap_entry):
    """Put new values on the rows (original mesh1d node indices) of `data`,
    which has already been subset/reordered with row_map."""
    if isinstance(data, np.ma.MaskedArray):
        data = data.copy()
    else:
        data = np.array(data, copy=True)
    for m, val in values.items():
        row = int(row_map[m])
        if row < 0:
            continue
        if var.dtype.kind in "SU" and data.ndim == 2:
            n = data.shape[1]
            s = str(val)[:n].ljust(n)
            data[row, :] = np.array(list(s), dtype="S1")
        elif data.ndim == 1 and (var.dtype.kind in "SU" or var.dtype == str):
            data[row] = str(val)
        else:
            if remap_entry is not None:
                val = int(val) + int(remap_entry[1])   # remap subtracts start
            data[row] = val
    return data


def _branch_name(ds, t, i):
    if t.branch_id_var:
        return norm_id(_chars_to_str(ds.variables[t.branch_id_var][i:i + 1])[0])
    return "branch#%d" % i


def _node_name(ds, t, m):
    vn = _find_var(ds, (t.m1d_node_dim,), "node_id", chars=True)
    if vn:
        return norm_id(_chars_to_str(ds.variables[vn][m:m + 1])[0])
    return "mesh1d#%d" % m


def rebuild_net(src_path, dst_path, keep_branch, topo_out, tag="1D channel"):
    """Write a copy of src_path with the branches where keep_branch is False
    (and everything anchored on them) removed.  Mesh2d is copied unchanged."""
    ds = nc.Dataset(src_path, "r")
    t = NetTopology(ds)
    if not t.network or not t.branch_dim:
        ds.close()
        raise RuntimeError("no 1D network found in %s" % src_path)

    n_branch = len(ds.dimensions[t.branch_dim])
    keep_branch = np.asarray(keep_branch, dtype=bool)
    if keep_branch.size != n_branch:
        ds.close()
        raise RuntimeError("branch mask size %d != %d" % (keep_branch.size, n_branch))

    b_idx = np.where(keep_branch)[0]
    b_map = np.full(n_branch, -1, dtype=np.int64)
    b_map[b_idx] = np.arange(b_idx.size)

    # ---- network nodes: keep the ones still used by a kept branch ---------
    edge_nodes = ds.variables[t.net_edge_nodes][:] if t.net_edge_nodes else None
    en_start = int(getattr(ds.variables[t.net_edge_nodes], "start_index", 0)) \
        if t.net_edge_nodes else 0
    n_netnode = len(ds.dimensions[t.netnode_dim])
    keep_netnode = np.zeros(n_netnode, dtype=bool)
    if edge_nodes is not None:
        used = np.asarray(edge_nodes)[keep_branch] - en_start
        keep_netnode[np.unique(used)] = True
    nn_idx = np.where(keep_netnode)[0]
    nn_map = np.full(n_netnode, -1, dtype=np.int64)
    nn_map[nn_idx] = np.arange(nn_idx.size)

    # ---- geometry nodes ---------------------------------------------------
    keep_geom = geom_idx = None
    new_counts = None
    if t.geom_count_var and t.geom_dim:
        counts = np.asarray(ds.variables[t.geom_count_var][:], dtype=np.int64)
        ends = np.cumsum(counts)
        starts = ends - counts
        keep_geom = np.zeros(len(ds.dimensions[t.geom_dim]), dtype=bool)
        for i in b_idx:
            keep_geom[starts[i]:ends[i]] = True
        geom_idx = np.where(keep_geom)[0]
        new_counts = counts[keep_branch]

    # ---- mesh1d nodes / edges --------------------------------------------
    keep_m1n = keep_m1e = None
    m1n_idx = m1n_map = None
    if t.m1d_node_dim and t.m1d_node_branch:
        nb = np.asarray(ds.variables[t.m1d_node_branch][:], dtype=np.int64)
        nb -= int(getattr(ds.variables[t.m1d_node_branch], "start_index", 0))
        keep_m1n = keep_branch[nb]
    own_m1n = None if keep_m1n is None else keep_m1n.copy()

    # ---- mesh1d nodes on a junction owned by a removed branch -------------
    # A calculation point on a network node that several branches share is
    # stored ONCE, with the branch index of just one of them.  When that
    # branch is removed, the point is still the end of a grid cell of a branch
    # that stays; dropping it drops that cell as well, and a short branch whose
    # only cell ran into the junction is left with no grid at all
    # ("No computational grid cells defined for branch ...").  Such a point is
    # kept and handed over to the kept branch, at offset 0 or at its length.
    rescued = OrderedDict()          # mesh1d node -> (new branch, offset, netnode)
    node_over = {}                   # variable -> {mesh1d node: new value}
    if keep_m1n is not None and t.m1d_edge_dim and t.m1d_edge_nodes \
            and t.m1d_edge_branch:
        eb0 = np.asarray(ds.variables[t.m1d_edge_branch][:], dtype=np.int64) - \
            int(getattr(ds.variables[t.m1d_edge_branch], "start_index", 0))
        en0 = np.asarray(ds.variables[t.m1d_edge_nodes][:], dtype=np.int64) - \
            int(getattr(ds.variables[t.m1d_edge_nodes], "start_index", 0))
        blen = _find_var(ds, (t.branch_dim,), "length", float_only=True)
        blen = np.asarray(ds.variables[blen][:], dtype=float) if blen else None
        noff_var = _find_var(ds, (t.m1d_node_dim,), "offset", float_only=True)
        noff = np.asarray(ds.variables[noff_var][:], dtype=float) \
            if noff_var else None
        en_net = np.asarray(edge_nodes, dtype=np.int64) - en_start \
            if edge_nodes is not None else None
        for e in np.where(keep_branch[eb0])[0]:
            kb = int(eb0[e])
            for side in (0, 1):
                m = int(en0[e, side])
                if keep_m1n[m] or m in rescued:
                    continue
                # the network node the point sits on (nearest end of its owner)
                rb, netnode = int(nb[m]), None
                if en_net is not None:
                    at_end = (noff is not None and blen is not None and
                              noff[m] > 0.5 * blen[rb])
                    netnode = int(en_net[rb, 1 if at_end else 0])
                # where that node is on the kept branch
                if en_net is not None and netnode == en_net[kb, 0]:
                    end = 0
                elif en_net is not None and netnode == en_net[kb, 1]:
                    end = 1
                else:
                    end = side      # mesh1d edges run along the branch
                off = 0.0 if end == 0 else (float(blen[kb]) if blen is not None
                                            else None)
                rescued[m] = (kb, off, netnode)
        if rescued:
            keep_m1n[list(rescued)] = True
            node_over[t.m1d_node_branch] = {m: v[0] for m, v in rescued.items()}
            if noff_var:
                node_over[noff_var] = {m: v[1] for m, v in rescued.items()
                                       if v[1] is not None}
            bids = [norm_id(s) for s in
                    _chars_to_str(ds.variables[t.branch_id_var][:])] \
                if t.branch_id_var else None
            if bids:
                names = {m: "%s_%.3f" % (bids[v[0]], v[1] or 0.0)
                         for m, v in rescued.items()}
                for key in ("node_id",):
                    vn = _find_var(ds, (t.m1d_node_dim,), key, chars=True)
                    if vn:
                        node_over[vn] = names

    if keep_m1n is not None:
        # kept points ordered by (new branch, offset), as the FM Suite writes them
        m1n_idx = np.where(keep_m1n)[0]
        if rescued:
            nb_new = nb.copy()
            for m, v in rescued.items():
                nb_new[m] = v[0]
            off_new = noff.copy() if noff is not None else np.zeros(nb.size)
            for m, v in rescued.items():
                if v[1] is not None:
                    off_new[m] = v[1]
            order = np.lexsort((m1n_idx, off_new[m1n_idx],
                                b_map[nb_new[m1n_idx]]))
            m1n_idx = m1n_idx[order]
        m1n_map = np.full(keep_m1n.size, -1, dtype=np.int64)
        m1n_map[m1n_idx] = np.arange(m1n_idx.size)
    if t.m1d_edge_dim:
        n_e = len(ds.dimensions[t.m1d_edge_dim])
        keep_m1e = np.ones(n_e, dtype=bool)
        if t.m1d_edge_branch:
            eb = np.asarray(ds.variables[t.m1d_edge_branch][:], dtype=np.int64)
            eb -= int(getattr(ds.variables[t.m1d_edge_branch], "start_index", 0))
            keep_m1e &= keep_branch[eb]
        if t.m1d_edge_nodes and m1n_map is not None:
            en = np.asarray(ds.variables[t.m1d_edge_nodes][:], dtype=np.int64)
            en -= int(getattr(ds.variables[t.m1d_edge_nodes], "start_index", 0))
            keep_m1e &= keep_m1n[en[:, 0]] & keep_m1n[en[:, 1]]

    # ---- 1D2D contacts ----------------------------------------------------
    keep_ct = None
    if t.contact_var and t.contact_dim and m1n_map is not None:
        cv = ds.variables[t.contact_var]
        if cv.shape[0] > 0:
            cdata = np.asarray(cv[:], dtype=np.int64)
            cdata -= int(getattr(cv, "start_index", 0))
            keep_ct = own_m1n[cdata[:, t.m1d_col]]
        else:
            keep_ct = np.zeros(0, dtype=bool)

    # ---- dimension -> keep-index table -----------------------------------
    sel = {t.branch_dim: b_idx, t.netnode_dim: nn_idx}
    if keep_geom is not None:
        sel[t.geom_dim] = geom_idx
    if keep_m1n is not None:
        sel[t.m1d_node_dim] = m1n_idx
    if keep_m1e is not None:
        sel[t.m1d_edge_dim] = np.where(keep_m1e)[0]
    if keep_ct is not None:
        sel[t.contact_dim] = np.where(keep_ct)[0]

    # variables whose *values* are indices that have to be renumbered
    remap = {}
    if t.net_edge_nodes:
        remap[t.net_edge_nodes] = (nn_map, en_start)
    if t.m1d_edge_nodes and m1n_map is not None:
        remap[t.m1d_edge_nodes] = (m1n_map,
                                   int(getattr(ds.variables[t.m1d_edge_nodes],
                                               "start_index", 0)))
    for v in (t.m1d_node_branch, t.m1d_edge_branch):
        if v:
            remap[v] = (b_map, int(getattr(ds.variables[v], "start_index", 0)))

    # ---- meshes that disappear completely --------------------------------
    # A dimension of length 0 cannot be written to a NETCDF3_CLASSIC file (a
    # fixed dimension must be > 0 there, and size 0 would be taken as
    # UNLIMITED).  When a whole mesh is gone the right answer is to drop its
    # dimensions, its variables and its topology variable, which is also the
    # correct UGRID result: the file simply no longer has that mesh.
    empty_dims = {d for d, idx in sel.items() if len(idx) == 0}
    drop_vars = set()
    if empty_dims:
        for vname, var in ds.variables.items():
            if any(d in empty_dims for d in var.dimensions):
                drop_vars.add(vname)
        if t.mesh1d and (t.m1d_node_dim in empty_dims or t.m1d_edge_dim in empty_dims):
            drop_vars.add(t.mesh1d)
        if t.contact_topo and t.contact_dim in empty_dims:
            drop_vars.add(t.contact_topo)
        if t.network and t.branch_dim in empty_dims:
            drop_vars.add(t.network)
            geom = getattr(ds.variables[t.network], "edge_geometry", None)
            if geom and geom in ds.variables:
                drop_vars.add(geom)
        drop_vars.discard(None)

    # ---- write ------------------------------------------------------------
    out = nc.Dataset(dst_path, "w", format=ds.file_format)
    try:
        out.setncatts({a: ds.getncattr(a) for a in ds.ncattrs()})
        hist = "%s: %s branches removed by %s" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tag, SCRIPT)
        out.history = (getattr(ds, "history", "") + "\n" + hist).strip()

        for dname, dim in ds.dimensions.items():
            if dname in empty_dims:
                continue
            if dname in sel:
                size = len(sel[dname])
            elif dim.isunlimited():
                size = None
            else:
                size = len(dim)
            out.createDimension(dname, size)

        for vname, var in ds.variables.items():
            if vname in drop_vars:
                continue
            fill = var.getncattr("_FillValue") if "_FillValue" in var.ncattrs() else None
            new = out.createVariable(vname, var.dtype, var.dimensions,
                                     fill_value=fill,
                                     zlib=False)
            new.setncatts({a: var.getncattr(a) for a in var.ncattrs()
                           if a != "_FillValue"})
            if var.ndim == 0:
                new[...] = var[...]
                continue
            # untouched variables (the whole 2D grid) are copied in slices so a
            # big 1D2D net file does not have to fit in memory twice
            if not any(dn in sel for dn in var.dimensions) and vname not in remap \
                    and vname != t.contact_var and vname != t.geom_count_var:
                n0 = var.shape[0]
                step = max(1, int(4e7 // max(int(np.prod(var.shape[1:]) or 1), 1)))
                for i in range(0, n0, step):
                    new[i:i + step, ...] = var[i:i + step, ...]
                continue
            data = var[:]
            for ax, dname in enumerate(var.dimensions):
                if dname in sel:
                    data = np.take(data, sel[dname], axis=ax)
            if vname == t.geom_count_var and new_counts is not None:
                data = new_counts
            if vname in node_over and node_over[vname]:
                data = _override_rows(data, var, node_over[vname], m1n_map,
                                      remap.get(vname))
            if vname in remap:
                table, start = remap[vname]
                raw = np.asarray(data, dtype=np.int64) - start
                mapped = np.where(raw >= 0, table[np.clip(raw, 0, table.size - 1)], raw)
                data = mapped + start
            if vname == t.contact_var and data.size:
                table, start = m1n_map, int(getattr(var, "start_index", 0))
                d = np.asarray(data, dtype=np.int64)
                d[:, t.m1d_col] = table[d[:, t.m1d_col] - start] + start
                data = d
            new[...] = data
    finally:
        out.close()

    stats = {
        "dropped_meshes": sorted(drop_vars) if empty_dims else [],
        "branches": (int(keep_branch.sum()), n_branch),
        "network_nodes": (int(keep_netnode.sum()), n_netnode),
        "mesh1d_nodes": (int(keep_m1n.sum()), keep_m1n.size) if keep_m1n is not None else None,
        "mesh1d_edges": (int(keep_m1e.sum()), keep_m1e.size) if keep_m1e is not None else None,
        "geometry_nodes": (int(keep_geom.sum()), keep_geom.size) if keep_geom is not None else None,
        "link1d2d": (int(keep_ct.sum()), keep_ct.size) if keep_ct is not None else None,
        "contact_var": t.contact_var,
        "reassigned_mesh1d_nodes": [
            (_node_name(ds, t, m), _branch_name(ds, t, int(nb[m])),
             _branch_name(ds, t, v[0]), v[1]) for m, v in rescued.items()],
        "mesh2d_faces": len(ds.dimensions[getattr(ds.variables[t.mesh2d], "face_dimension", "")])
        if t.mesh2d and getattr(ds.variables[t.mesh2d], "face_dimension", "") in ds.dimensions else 0,
    }
    kept_netnode_ids, all_netnode_ids = set(), set()
    if t.netnode_id_var:
        allids = [norm_id(s) for s in _chars_to_str(ds.variables[t.netnode_id_var][:])]
        all_netnode_ids = set(allids)
        kept_netnode_ids = {allids[i] for i in nn_idx}
    topo_out["kept_network_node_ids"] = kept_netnode_ids
    topo_out["removed_network_node_ids"] = all_netnode_ids - kept_netnode_ids
    ds.close()
    return stats


# --------------------------------------------------------------------------- #
#  text-file filters
# --------------------------------------------------------------------------- #

def filter_ini(path, section, predicate, dry, label=None):
    """Drop blocks of `section` for which predicate(block) is False.

    Returns (n_removed, n_kept, dropped_blocks)."""
    if not path or not os.path.isfile(path):
        return 0, 0, []
    f = IniFile(path)
    targets = [b for b in f.blocks if b.name.lower() == section.lower()]
    if not targets:
        return 0, 0, []
    dropped = [b for b in targets if not predicate(b)]
    if dropped:
        drop_set = set(id(b) for b in dropped)
        f.blocks = [b for b in f.blocks if id(b) not in drop_set]
        if not dry:
            backup(path)
            f.save()
    log("  %-24s %5d removed, %5d kept" %
        (label or os.path.basename(path), len(dropped), len(targets) - len(dropped)))
    return len(dropped), len(targets) - len(dropped), dropped


def ini_is_empty(path, header_names=("general",)):
    """True when the file has nothing but its [General] header left."""
    if not path or not os.path.isfile(path):
        return False
    try:
        return len(IniFile(path).data_blocks(header_names)) == 0
    except Exception:
        return False


def count_section_on_branches(path, section, keys, branches):
    """How many blocks of `section` point at one of `branches`."""
    if not path or not os.path.isfile(path):
        return 0
    n = 0
    for b in IniFile(path).of(section):
        for k in keys:
            if norm_id(b.get(k)) in branches:
                n += 1
                break
    return n


# --------------------------------------------------------------------------- #
#  outfall manholes: a storage node where a kept sewer branch ran into a
#  branch that is being removed
# --------------------------------------------------------------------------- #
#
# Removing a channel leaves every sewer branch that discharged into it with a
# free end: a pipe that stops in mid-air, with no compartment to hold water and
# nothing for the FM Suite to draw.  Before the branch goes, the network node
# where the two met is recorded, and a manhole (a [StorageNode] in nodeFile.ini)
# is written on it, so the sewer system keeps a proper outfall compartment.
#
# The levels of the new manhole follow the sewer branch it terminates:
#
#   follow (default)  the manhole at the OTHER end of that branch, moved by the
#                     invert difference along the branch
#                     (bed = donor_bed + (invert_here - invert_there)), so the
#                     bed-to-invert relation the model already uses is kept
#   invert            bed = the pipe invert at this end (crsloc `shift`),
#                     street = bed + the manhole depth
#   copy              the neighbouring manhole's levels, unchanged
#
# Whatever the mode, --manhole-bed-level / --manhole-street-level / --manhole-
# depth / --manhole-area override the result, and every added manhole is listed
# in the report and in added_manholes.csv.

MANHOLE_ORDER = ("id", "name", "nodeId", "ManholeId", "bedLevel", "area",
                 "streetLevel", "storageType", "streetStorageArea",
                 "CompartmentShape", "useTable")

DEFAULT_MANHOLE = OrderedDict([
    ("storageType", "Reservoir"),
    ("useTable", "False"),
])

FALLBACK_DEPTH = 2.0          # street - bed when nothing else is known
FALLBACK_AREA = 1.0


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _fmt_like(sample, value, default_dec=3):
    """Format a number with as many decimals as the sample value has."""
    s = str(sample or "").strip()
    dec = default_dec
    if "." in s:
        tail = s.split(".", 1)[1]
        if tail.isdigit():
            dec = len(tail)
    return "%.*f" % (dec, value)


def find_outfall_junctions(net_path, keep_mask, cls):
    """Network nodes where a branch that is being removed meets a KEPT SEWER
    branch - the points where the sewer system was hanging on the other half.

    Must run on the ORIGINAL net file, before the branches are dropped.
    Returns OrderedDict node_id -> dict(index, x, y, sewer_ends, removed).
    `sewer_ends` is [(branch_id, side, other_node_id)], side 0 = branch start."""
    ds = nc.Dataset(net_path)
    try:
        t = NetTopology(ds)
        if not (t.net_edge_nodes and t.netnode_id_var and t.branch_id_var):
            return OrderedDict()
        bids = [norm_id(s) for s in _chars_to_str(ds.variables[t.branch_id_var][:])]
        nids = [norm_id(s) for s in _chars_to_str(ds.variables[t.netnode_id_var][:])]
        en = np.asarray(ds.variables[t.net_edge_nodes][:], dtype=np.int64) - \
            int(getattr(ds.variables[t.net_edge_nodes], "start_index", 0))
        xs = np.asarray(ds.variables[t.netnode_x][:]) if t.netnode_x else None
        ys = np.asarray(ds.variables[t.netnode_y][:]) if t.netnode_y else None
    finally:
        ds.close()

    keep = np.asarray(keep_mask, dtype=bool)
    sewer_ends, removed_at = {}, {}
    for i, b in enumerate(bids):
        for side in (0, 1):
            n = int(en[i, side])
            if keep[i]:
                if cls.get(b) == SEWER:
                    sewer_ends.setdefault(n, []).append(
                        (b, side, nids[int(en[i, 1 - side])]))
            else:
                removed_at.setdefault(n, []).append(b)

    out = OrderedDict()
    for n in sorted(sewer_ends):
        if n not in removed_at:
            continue
        out[nids[n]] = {
            "index": n,
            "x": float(xs[n]) if xs is not None else None,
            "y": float(ys[n]) if ys is not None else None,
            "sewer_ends": sewer_ends[n],
            "removed": removed_at[n],
        }
    return out


def network_node_coords(net_path):
    """nodeId -> (x, y) for every network node, for the nearest-manhole
    fallback when a sewer branch has no manhole at its other end."""
    out = {}
    try:
        ds = nc.Dataset(net_path)
    except Exception:
        return out
    try:
        t = NetTopology(ds)
        if not (t.netnode_id_var and t.netnode_x and t.netnode_y):
            return out
        ids = [norm_id(v) for v in _chars_to_str(ds.variables[t.netnode_id_var][:])]
        xs = np.asarray(ds.variables[t.netnode_x][:])
        ys = np.asarray(ds.variables[t.netnode_y][:])
        for i, nid in enumerate(ids):
            out[nid] = (float(xs[i]), float(ys[i]))
    finally:
        ds.close()
    return out


def read_storage_nodes(path):
    """nodeId -> {'kv': block.kv, 'block': Block} for every [StorageNode]."""
    out = OrderedDict()
    if not path or not os.path.isfile(path):
        return out
    for b in IniFile(path).of("StorageNode"):
        nid = norm_id(b.get("nodeId") or b.get("id"))
        if nid:
            out[nid] = {"kv": b.kv, "block": b}
    return out


def read_branch_end_levels(crsloc_path):
    """branchId -> {0: invert at the branch start, 1: invert at its end}.

    For pipes and sewer connections the crsloc `shift` is the invert level, so
    the entry with the lowest chainage gives the level at the start node and the
    one with the highest chainage the level at the end node."""
    out = {}
    if not crsloc_path or not os.path.isfile(crsloc_path):
        return out
    best = {}
    for b in IniFile(crsloc_path).of("CrossSection"):
        br = norm_id(b.get("branchId"))
        ch = _num(b.get("chainage"))
        sh = _num(b.get("shift"))
        if br is None or ch is None or sh is None:
            continue
        lo, hi = best.get(br, (None, None))
        if lo is None or ch < lo[0]:
            lo = (ch, sh)
        if hi is None or ch > hi[0]:
            hi = (ch, sh)
        best[br] = (lo, hi)
    for br, (lo, hi) in best.items():
        out[br] = {0: lo[1], 1: hi[1]}
    return out


def read_gui_compartments(branch_path):
    """branchId -> {0: sourceCompartmentName, 1: targetCompartmentName}."""
    out = {}
    if not branch_path or not os.path.isfile(branch_path):
        return out
    for b in IniFile(branch_path).of("Branch"):
        nm = norm_id(b.get("name"))
        if nm:
            out[nm] = {0: norm_id(b.get("sourceCompartmentName")),
                       1: norm_id(b.get("targetCompartmentName"))}
    return out


def _nearest_storage_node(x, y, storage, coords):
    """Fallback donor: the existing manhole closest to this junction."""
    if x is None or not coords:
        return None
    best, bestd = None, None
    for nid in storage:
        c = coords.get(nid)
        if not c:
            continue
        d = (c[0] - x) ** 2 + (c[1] - y) ** 2
        if bestd is None or d < bestd:
            best, bestd = nid, d
    return best


def plan_outfall_manholes(junctions, storage, levels, gui_comp, coords, args):
    """One new manhole per junction node that does not have one yet.

    Returns (plan, skipped_existing, warnings).  Each plan entry carries the
    values of the block to write plus where they came from."""
    plan, skipped, warn = [], [], []
    used_ids = {norm_id(v["kv"].get("id")) for v in storage.values()}
    used_ids |= set(storage)

    depths = [d for d in ((_num(v["kv"].get("streetlevel")) or 0) -
                          (_num(v["kv"].get("bedlevel")) or 0)
                          for v in storage.values()) if d > 0]
    areas = [a for a in (_num(v["kv"].get("area")) for v in storage.values())
             if a and a > 0]
    med_depth = sorted(depths)[len(depths) // 2] if depths else FALLBACK_DEPTH
    med_area = sorted(areas)[len(areas) // 2] if areas else FALLBACK_AREA
    template0 = next(iter(storage.values()))["block"] if storage else None

    for node_id, j in junctions.items():
        if node_id in storage:
            skipped.append(node_id)
            continue

        # ---- which sewer branch does this manhole terminate, and on what --
        donor = donor_id = branch = None
        side = inv_here = inv_there = None
        for br, sd, other in j["sewer_ends"]:
            cand = gui_comp.get(br, {}).get(1 - sd) or other
            if cand in storage:
                donor_id, donor = cand, storage[cand]
                branch, side = br, sd
                break
        if branch is None:
            branch, side, other = j["sewer_ends"][0]
            near = _nearest_storage_node(j["x"], j["y"], storage, coords)
            if near:
                donor_id, donor = near, storage[near]
        lv = levels.get(branch, {})
        inv_here, inv_there = lv.get(side), lv.get(1 - side)

        d_bed = _num(donor["kv"].get("bedlevel")) if donor else None
        d_street = _num(donor["kv"].get("streetlevel")) if donor else None
        d_depth = (d_street - d_bed) if (d_bed is not None and d_street is not None
                                         and d_street > d_bed) else None
        depth = args.manhole_depth if args.manhole_depth is not None else \
            (d_depth if d_depth is not None else med_depth)

        mode = args.manhole_levels
        bed = street = None
        how = None
        if mode == "follow" and d_bed is not None and \
                inv_here is not None and inv_there is not None:
            shift = inv_here - inv_there
            bed = d_bed + shift
            street = (d_street + shift) if d_street is not None else bed + depth
            how = "follow %s from %s" % (branch, donor_id)
        elif mode == "invert" and inv_here is not None:
            bed, street = inv_here, inv_here + depth
            how = "invert of %s" % branch
        elif mode != "invert" and d_bed is not None:
            bed = d_bed
            street = d_street if d_street is not None else bed + depth
            how = "copy of %s" % donor_id
        elif inv_here is not None:
            bed, street = inv_here, inv_here + depth
            how = "invert of %s" % branch
        elif d_bed is not None:
            bed = d_bed
            street = d_street if d_street is not None else bed + depth
            how = "copy of %s" % donor_id

        if args.manhole_bed_level is not None:
            bed = args.manhole_bed_level
            how = "fixed"
        if bed is None:
            warn.append("%s: no level could be derived - no manhole written "
                        "(give --manhole-bed-level)" % node_id)
            continue
        if args.manhole_street_level is not None:
            street = args.manhole_street_level
        elif args.manhole_depth is not None or street is None:
            street = bed + depth
        if street <= bed:
            street = bed + max(depth, FALLBACK_DEPTH)
            warn.append("%s: streetLevel was not above bedLevel, set to %.3f"
                        % (node_id, street))

        area = args.manhole_area
        if area is None:
            area = _num(donor["kv"].get("area")) if donor else None
        if area is None or area <= 0:
            area = med_area

        mid = (args.manhole_id_prefix or "") + node_id
        base, n = mid, 2
        while mid in used_ids:
            mid = "%s_%d" % (base, n)
            n += 1
        used_ids.add(mid)

        tmpl = donor["block"] if donor else template0
        tkv = donor["kv"] if donor else (template0.kv if template0 else {})
        values = OrderedDict((
            ("id", mid), ("name", mid), ("nodeId", node_id), ("ManholeId", mid),
            ("bedLevel", _fmt_like(tkv.get("bedlevel"), bed)),
            ("streetLevel", _fmt_like(tkv.get("streetlevel"), street)),
            ("area", _fmt_like(tkv.get("area"), area, 4)),
        ))
        plan.append({
            "node": node_id, "id": mid, "branch": branch, "side": side,
            "donor": donor_id, "how": how, "x": j["x"], "y": j["y"],
            "bed": bed, "street": street, "area": area,
            "removed": j["removed"], "sewer_ends": j["sewer_ends"],
            "template": tmpl, "values": values,
        })
    return plan, skipped, warn


def render_storage_node(template, values):
    """A [StorageNode] block: the template's exact layout, new values."""
    low = {k.lower(): v for k, v in values.items()}
    if template is None:
        lines = ["[StorageNode]\n"]
        for k in MANHOLE_ORDER:
            v = low.get(k.lower(), DEFAULT_MANHOLE.get(k))
            if v is not None:
                lines.append("    %-21s = %s\n" % (k, v))
        return "".join(lines)

    out, seen, last_kv = [], set(), None
    for ln in template.lines:
        m = _KEY_RE.match(ln.split("#", 1)[0])
        if not m:
            if ln.strip() or out:
                out.append(ln)
            continue
        key = m.group(1).lower()
        out.append(set_kv_line(ln, low[key]) if key in low else ln)
        seen.add(key)
        last_kv = len(out) - 1
    if not out or not out[0].lstrip().startswith("["):
        out.insert(0, "[StorageNode]\n")
    for k, v in values.items():
        if k.lower() not in seen:
            line = new_kv_line(out[last_kv] if last_kv is not None else None, k, v)
            out.insert((last_kv + 1) if last_kv is not None else len(out), line)
            last_kv = (last_kv + 1) if last_kv is not None else len(out) - 1
    text = "".join(out)
    if not text.endswith("\n"):
        text += "\n"
    return text


def write_outfall_manholes(node_path, plan, dry, mdu=None, folder=None,
                           mdu_section="geometry", mdu_key="StorageNodeFile"):
    """Append the new [StorageNode] blocks to nodeFile.ini (creating it, and
    the mdu key, when the model had no storage node file at all)."""
    if not plan:
        return node_path, 0
    created = False
    if not node_path:
        node_path = os.path.join(folder, "nodeFile.ini")
        created = True
    if not os.path.isfile(node_path):
        created = True
        if not dry:
            with open(node_path, "w", encoding="utf-8", newline="") as fh:
                fh.write("[General]\n"
                         "    fileVersion           = 2.00\n"
                         "    fileType              = storageNodes\n"
                         "    useStreetStorage      = 1\n\n")
    if dry:
        return node_path, len(plan)

    backup(node_path)
    f = IniFile(node_path)
    last = f.blocks[-1].lines[-1] if (f.blocks and f.blocks[-1].lines) else "\n"
    nl = "\r\n" if last.endswith("\r\n") else "\n"

    def close_block(lines):
        """End a block with a newline and one blank separator line."""
        if not lines:
            return lines
        if not lines[-1].endswith("\n"):
            lines[-1] += nl
        if lines[-1].strip():
            lines.append(nl)
        return lines

    if f.blocks:
        close_block(f.blocks[-1].lines)
    for item in plan:
        text = render_storage_node(item["template"], item["values"])
        f.blocks.append(Block("StorageNode", close_block(text.splitlines(True))))
    f.save()
    if created and mdu is not None:
        mdu.set_value(mdu_section, mdu_key, os.path.basename(node_path))
    return node_path, len(plan)


def set_gui_compartments(branch_path, assigns, dry):
    """Give every sewer branch that now ends in a new manhole its compartment
    name in branches.gui (the end that used to run into the removed branch has
    none, which is how the FM Suite marks 'this end is not a compartment')."""
    if not assigns or not branch_path or not os.path.isfile(branch_path):
        return 0
    keys = {0: ("sourceCompartmentName",
                "Source compartment name this sewer connection is beginning"),
            1: ("targetCompartmentName",
                "Target compartment name this sewer connection is ending")}
    f = IniFile(branch_path)
    by_name = {}
    for b in f.of("Branch"):
        nm = norm_id(b.get("name"))
        if nm:
            by_name.setdefault(nm, b)
    n = 0
    for branch, side, mid in assigns:
        b = by_name.get(branch)
        if b is None:
            continue
        key, comment = keys[side]
        done = False
        for i, ln in enumerate(b.lines):
            m = _KEY_RE.match(ln.split("#", 1)[0])
            if m and m.group(1).lower() == key.lower():
                b.lines[i] = set_kv_line(ln, mid)
                done = True
                break
        if not done:
            at = max((i for i, ln in enumerate(b.lines)
                      if _KEY_RE.match(ln.split("#", 1)[0])), default=0)
            sample = b.lines[at] if at else None
            has_comment = "#" in (sample or "")
            b.lines.insert(at + 1, new_kv_line(sample, key, mid,
                                               comment if has_comment else None))
        b.kv[key.lower()] = mid
        n += 1
    if n and not dry:
        backup(branch_path)
        f.save()
    return n


def write_manhole_csv(path, plan):
    try:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("manholeId,nodeId,x,y,bedLevel,streetLevel,area,"
                     "onBranch,fromManhole,levelsFrom,removedBranches\n")
            for it in plan:
                fh.write("%s,%s,%s,%s,%.3f,%.3f,%.4f,%s,%s,%s,%s\n" % (
                    it["id"], it["node"],
                    "" if it["x"] is None else "%.3f" % it["x"],
                    "" if it["y"] is None else "%.3f" % it["y"],
                    it["bed"], it["street"], it["area"], it["branch"],
                    it["donor"] or "", (it["how"] or "").replace(",", " "),
                    " ".join(it["removed"])))
        return True
    except Exception as exc:
        log("  could not write %s: %s" % (path, exc))
        return False


# --------------------------------------------------------------------------- #
#  pre-flight: is anything holding the files open?
# --------------------------------------------------------------------------- #

def check_writable(paths):
    """Return [(path, reason)] for files that cannot be opened for writing.

    On Windows the FM Suite keeps the project's files open while the project is
    loaded, so this is what catches "I ran the script but nothing changed"."""
    bad = []
    for p in paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "r+b"):
                pass
        except Exception as exc:
            bad.append((p, exc.__class__.__name__ + ": " + str(exc)))
    return bad


# --------------------------------------------------------------------------- #
#  is the FM Suite holding this project?
# --------------------------------------------------------------------------- #

_GEN_RE = re.compile(r"^#\s*Generated on\s+(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})",
                     re.I | re.M)


def mdu_generated_on(mdu_path):
    """The '# Generated on ...' stamp the FM Suite writes at the top of the mdu.
    It is the moment the GUI last exported this model."""
    try:
        with open(mdu_path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except Exception:
        return None
    m = _GEN_RE.search(head)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def project_state(mdu_path, folder, dsproj_path=None, fresh_minutes=60):
    """Report who wrote this model last: the GUI or this script.

    Returns True when the GUI looks like it exported after our last run, which
    is exactly the "I removed the channels but the GUI still shows them" case."""
    gen = mdu_generated_on(mdu_path)
    logf = os.path.join(folder, "%s.log" % SCRIPT)
    last_run = datetime.fromtimestamp(os.path.getmtime(logf)) \
        if os.path.isfile(logf) else None
    now = datetime.now()

    log()
    log("who wrote this model last")
    log("  mdu 'Generated on' (FM Suite export) : %s" %
        (gen.strftime("%Y-%m-%d %H:%M:%S") if gen else "not stamped"))
    log("  last %-32s: %s" % (
        os.path.basename(logf).replace(".log", " run"),
        last_run.strftime("%Y-%m-%d %H:%M:%S") if last_run else "never"))
    if dsproj_path and os.path.isfile(dsproj_path):
        log("  .dsproj last saved                   : %s" %
            datetime.fromtimestamp(os.path.getmtime(dsproj_path))
            .strftime("%Y-%m-%d %H:%M:%S"))

    # Decide from file mtimes, not from the stamp: mtimes come from one clock,
    # while '# Generated on' is local wall-clock text written by the GUI.
    mdu_m = datetime.fromtimestamp(os.path.getmtime(mdu_path)) \
        if os.path.isfile(mdu_path) else None
    overwritten = bool(mdu_m and last_run and
                       (mdu_m - last_run).total_seconds() > 5)
    if overwritten:
        log()
        log("  !! the FM Suite exported this model AFTER the last run.")
        log("     Saving the project re-writes every input file from the")
        log("     network the GUI holds in memory, so the channels came back.")
    elif gen and 0 <= (now - gen).total_seconds() < fresh_minutes * 60:
        log()
        log("  !  the FM Suite exported this model %d minute(s) ago, so the"
            % int((now - gen).total_seconds() // 60))
        log("     project is probably still open.  A loaded project keeps its")
        log("     own copy of the network in memory: editing these files does")
        log("     not change what is on screen, and the next Save overwrites")
        log("     them.  Close the project WITHOUT saving, then reopen it.")
    return overwritten


# --------------------------------------------------------------------------- #
#  audit: how much of the targeted 1D content is still in this model?
# --------------------------------------------------------------------------- #

def audit(folder, paths, remove_branches, label, noun="1D channel"):
    """Count everything that still belongs to a channel.  Used both for
    --check and for the verification pass after writing."""
    net_path = paths.get("net")
    found = OrderedDict()

    n_ch = 0
    no_grid = []        # kept branches that have no mesh1d edge at all
    if net_path and os.path.isfile(net_path):
        ds = nc.Dataset(net_path)
        try:
            t = NetTopology(ds)
            if t.branch_id_var and t.branch_id_var in ds.variables:
                ids = [norm_id(s) for s in
                       _chars_to_str(ds.variables[t.branch_id_var][:])]
                n_ch = sum(1 for b in ids if b in remove_branches)
                found["net file branches"] = (n_ch, len(ids))
                if t.m1d_edge_branch and t.m1d_edge_branch in ds.variables:
                    ev = ds.variables[t.m1d_edge_branch]
                    eb = np.asarray(ev[:], dtype=np.int64) - \
                        int(getattr(ev, "start_index", 0))
                    eb = eb[(eb >= 0) & (eb < len(ids))]
                    cnt = np.bincount(eb, minlength=len(ids))
                    no_grid = [b for i, b in enumerate(ids)
                               if cnt[i] == 0 and b not in remove_branches]
            else:
                found["net file branches"] = (0, 0)
        finally:
            ds.close()

    def add(key, path, section, keys):
        if path and os.path.isfile(path):
            tot = len(IniFile(path).of(section))
            found[key] = (count_section_on_branches(path, section, keys,
                                                    remove_branches), tot)

    add("structures", paths.get("struct"), "Structure", ("branchId",))
    add("cross sections", paths.get("crsloc"), "CrossSection", ("branchId",))
    add("branches.gui", paths.get("branch"), "Branch", ("name",))

    log()
    log("%s" % label)
    dirty = 0
    for k, (bad, tot) in found.items():
        dirty += bad
        log("  %-22s %6d of %6d still on a %s" % (k, bad, tot, noun))
    if found.get("net file branches", (0, 0))[1]:
        log("  %-22s %6d of %6d branches" % ("without grid cells",
                                             len(no_grid),
                                             found["net file branches"][1]))
        for b in no_grid:
            log("      %s  <- the FM Suite will refuse to run" % b)
        dirty += len(no_grid)
    return dirty


# --------------------------------------------------------------------------- #
#  .dsproj inspection
# --------------------------------------------------------------------------- #

NETWORK_TABLES = ["networks", "features", "md_array_branchfeature",
                  "md_values_network_location", "features_links_hydro_node",
                  "ICrossSectionDefinition", "CrossSectionSection",
                  "unstructured_grids"]


def inspect_dsproj(path):
    """The FM Suite .dsproj is a SQLite file that stores the *project shell*;
    the 1D network itself lives in the model input files.  Report what is in
    there so nothing 1D is silently left behind."""
    log()
    log("dsproj: %s" % path)
    if not os.path.isfile(path):
        log("  not found - skipped")
        return
    try:
        con = sqlite3.connect(path)
    except Exception as exc:
        log("  cannot open (%s) - skipped" % exc)
        return
    tables = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    hot = []
    for tb in NETWORK_TABLES:
        if tb in tables:
            try:
                n = con.execute('select count(*) from "%s"' % tb).fetchone()[0]
            except Exception:
                continue
            if n:
                hot.append((tb, n))
    con.close()
    if not hot:
        log("  no 1D network rows stored in the project file - nothing to edit.")
    else:
        for tb, n in hot:
            log("  %-34s %d row(s)" % (tb, n))
        log("  Note: rows here are project/GIS bookkeeping, not branch geometry.")
    log("  The network lives in the input files, so the FM Suite picks the")
    log("  changes up when the model is reopened / reimported.  The .dsproj")
    log("  is left untouched on purpose - editing its SQLite schema by hand")
    log("  is what corrupts projects.")


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #

def main(argv=None):
    # a fresh run, even if main() is called again in the same process
    del LOG_LINES[:]
    BACKED_UP.clear()
    ap = argparse.ArgumentParser(prog=SCRIPT, description=DESCRIPTION)
    ap.add_argument("model", help="model input folder, or the .mdu file itself")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="only report whether this model still contains 1D "
                         "channels (point it at the folder the GUI has open)")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the post-run verification pass")
    ap.add_argument("--force", action="store_true",
                    help="run even when a file looks locked by another program")
    ap.add_argument("--dsproj", help="path to the .dsproj to inspect (not edited)")
    ap.add_argument("--remove-ids", help="text file with extra branch ids to remove")
    ap.add_argument("--keep-ids", help="text file with branch ids to force-keep")
    ap.add_argument("--target", choices=["channel", "sewer", "all"],
                    default=DEFAULT_TARGET,
                    help="which 1D part to REMOVE: 'channel' removes the open "
                         "channels and keeps pipes/sewer connections/manholes; "
                         "'sewer' does the opposite; 'all' removes the entire "
                         "1D network with every 1D structure and manhole and "
                         "leaves a 2D-only model (default: %s)" % DEFAULT_TARGET)
    ap.add_argument("--keep-1d-mdu-keys", action="store_true",
                    help="with --target all: leave FrictFile (the 1D roughness "
                         "files) and the 1dField initial conditions in place "
                         "instead of clearing what only 1D used")
    ap.add_argument("--allow-empty-2d", action="store_true",
                    help="with --target all: continue even when the net file "
                         "has no 2D grid (the result would be an empty model)")
    ap.add_argument("--unknown-as", choices=["keep", "channel", "sewer"],
                    default="keep",
                    help="class for branches that cannot be classified "
                         "(default: keep, i.e. never removed)")
    ap.add_argument("--drop-all-storage-nodes", action="store_true",
                    help="remove every manhole / storage node, not only the "
                         "ones whose network node disappeared (implied by "
                         "--target sewer)")
    ap.add_argument("--keep-crsdef", action="store_true",
                    help="keep cross-section definitions that are no longer used")
    ap.add_argument("--keep-storage-nodes", action="store_true",
                    help="keep manhole/storage nodes even when their network "
                         "node disappeared")
    ap.add_argument("--no-outfall-manholes", action="store_true",
                    help="do not add a manhole where a kept sewer branch ran "
                         "into a branch that is removed (the default is to "
                         "add one, so the sewer keeps an outfall compartment)")
    ap.add_argument("--manhole-levels", choices=["follow", "invert", "copy"],
                    default="follow",
                    help="levels of the added manholes: 'follow' (default) "
                         "takes the manhole at the other end of the sewer "
                         "branch and moves it by the invert difference along "
                         "that branch; 'invert' puts the bed at the pipe "
                         "invert (crsloc shift) and the street a manhole depth "
                         "above it; 'copy' copies the neighbouring manhole")
    ap.add_argument("--manhole-bed-level", type=float,
                    help="force this bedLevel on every added manhole")
    ap.add_argument("--manhole-street-level", type=float,
                    help="force this streetLevel on every added manhole")
    ap.add_argument("--manhole-depth", type=float,
                    help="streetLevel - bedLevel of the added manholes "
                         "(default: the depth of the manhole it was derived "
                         "from, else the median depth in nodeFile.ini)")
    ap.add_argument("--manhole-area", type=float,
                    help="storage area of the added manholes (default: the "
                         "area of the manhole it was derived from)")
    ap.add_argument("--manhole-id-prefix", default="",
                    help="prefix for the added manhole ids "
                         "(default: none, the id is the network node id)")
    ap.add_argument("--no-outfall-compartment-names", action="store_true",
                    help="do not write the new manhole name into branches.gui "
                         "as the sewer branch's source/targetCompartmentName")
    ap.add_argument("--manhole-csv",
                    help="list the added manholes here "
                         "(default: <input>/added_manholes.csv)")
    ap.add_argument("--log", help="write the report to this file "
                                  "(default: <input>/%s.log)" % SCRIPT)
    args = ap.parse_args(argv)

    # ---- locate the mdu ---------------------------------------------------
    model = os.path.abspath(args.model)
    if os.path.isdir(model):
        mdus = [f for f in sorted(os.listdir(model)) if f.lower().endswith(".mdu")]
        if len(mdus) != 1:
            sys.exit("expected exactly one .mdu in %s, found %d" % (model, len(mdus)))
        mdu_path = os.path.join(model, mdus[0])
    else:
        mdu_path = model
    folder = os.path.dirname(mdu_path)

    mode = "CHECK ONLY - nothing is written" if args.check else \
           ("DRY RUN - nothing is written" if args.dry_run else
            "WRITE (originals -> .bak)")
    log("=" * 72)
    log("%s   %s" % (SCRIPT, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log("model : %s" % mdu_path)
    log("folder: %s" % folder)
    log("mode  : %s" % mode)
    log("=" * 72)

    mdu = Mdu(mdu_path)
    p = lambda name: os.path.join(folder, name) if name else None

    net_file = (mdu.files("geometry", "NetFile") or [None])[0]
    struct_file = (mdu.files("geometry", "StructureFile") or [None])[0]
    crsdef_file = (mdu.files("geometry", "CrossDefFile") or [None])[0]
    crsloc_file = (mdu.files("geometry", "CrossLocFile") or [None])[0]
    node_file = (mdu.files("geometry", "StorageNodeFile") or [None])[0]
    branch_file = (mdu.files("geometry", "BranchFile") or [None])[0]
    frict_files = mdu.files("geometry", "FrictFile")
    inifield_file = (mdu.files("geometry", "IniFieldFile") or [None])[0]
    ext_files = mdu.files("external forcing", "ExtForceFileNew") + \
        mdu.files("external forcing", "ExtForceFile")

    if not net_file:
        sys.exit("no NetFile in the mdu")
    net_path = p(net_file)
    if not os.path.isfile(net_path):
        sys.exit("net file not found: %s" % net_path)

    # ---- classify ---------------------------------------------------------
    ds = nc.Dataset(net_path)
    topo = NetTopology(ds)
    if not topo.branch_id_var or topo.branch_id_var not in ds.variables:
        faces = 0
        fd = getattr(ds.variables[topo.mesh2d], "face_dimension", "") \
            if topo.mesh2d else ""
        if fd in ds.dimensions:
            faces = len(ds.dimensions[fd])
        ds.close()
        log()
        log("this net file has no 1D network at all - the model is already 2D")
        log("  (%s, %d 2D faces).  Nothing to remove." %
            (os.path.basename(net_path), faces))
        return 0
    branch_ids = [norm_id(s) for s in _chars_to_str(ds.variables[topo.branch_id_var][:])]
    nc_types = None
    for cand in ("network_branch_type", "network1d_branch_type"):
        if cand in ds.variables:
            nc_types = np.asarray(ds.variables[cand][:], dtype=np.int64)
            break
    ds.close()

    def read_ids(path):
        if not path:
            return set()
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return {norm_id(l) for l in fh if l.strip() and not l.startswith("#")}

    target = args.target
    one, many, kept_desc = WORDS[target]
    force_keep = read_ids(args.keep_ids)
    # For --target all the channel/sewer split is still worked out, purely so
    # the report can say what the 1D network was made of.
    cls, report = classify_branches(branch_ids, nc_types, p(branch_file),
                                    read_ids(args.remove_ids), force_keep,
                                    args.unknown_as,
                                    CHANNEL if target == ALL else target)
    split = Counter(cls[b] for b in branch_ids) if target == ALL else None
    if target == ALL:
        for b in branch_ids:
            if b not in force_keep:
                cls[b] = ALL
                report["source"][b] = "--target all"

    remove_branches = {b for b in branch_ids if cls[b] == target}
    keep_mask = np.array([cls[b] != target for b in branch_ids], dtype=bool)
    drop_all_nodes = args.drop_all_storage_nodes or target in (SEWER, ALL)

    log()
    log("branch classification   (removing: %s)" % many)
    log("  net-file code -> class : %s" %
        (", ".join("%s=%s" % kv for kv in sorted(report["code_map"].items())) or "n/a"))
    log("  listed in branches.gui : %d" % len(report["gui"]))
    log("  %-22s : %d" % ("to remove (%s)" % target, len(remove_branches)))
    log("  %-22s : %d" % ("to keep", int(keep_mask.sum())))
    if split:
        log("      of which channels            %d" % split.get(CHANNEL, 0))
        log("      of which sewer / pipes       %d" % split.get(SEWER, 0))
    by_src = Counter(report["source"][b] for b in remove_branches)
    for k, v in by_src.most_common():
        log("      via %-28s %d" % (k, v))

    if target == ALL:
        faces = 0
        ds2 = nc.Dataset(net_path)
        try:
            t2 = NetTopology(ds2)
            fd = getattr(ds2.variables[t2.mesh2d], "face_dimension", "") \
                if t2.mesh2d else ""
            faces = len(ds2.dimensions[fd]) if fd in ds2.dimensions else 0
        finally:
            ds2.close()
        if not faces:
            log()
            log("!! this net file has no 2D grid (%s): removing the 1D network"
                % ("Mesh2d has 0 faces" if t2.mesh2d else "no Mesh2d at all"))
            log("   would leave an EMPTY model.  Point the script at a copy of")
            log("   the project that still has its 2D mesh - a *_net.nc.bak")
            log("   from an earlier run usually is the full 1D2D file.")
            log("   Use --allow-empty-2d to run anyway.")
            if not (args.allow_empty_2d or args.check or args.dry_run):
                return 2
    paths = {"net": net_path, "struct": p(struct_file), "crsloc": p(crsloc_file),
             "crsdef": p(crsdef_file), "branch": p(branch_file),
             "node": p(node_file)}

    if not remove_branches:
        dirty = audit(folder, paths, remove_branches, "state of this model", one)
        project_state(mdu_path, folder,
                      os.path.abspath(args.dsproj) if args.dsproj else None)
        log()
        if dirty:
            log("no branch is classified as %s, but the net file is NOT" % target)
            log("valid: a kept branch has no computational grid cells (see above).")
            log("Restore the originals (*.bak) and run this script again - the")
            log("junction calculation points are now handed to the kept branch.")
            return 1
        log("no branch is classified as %s - the files in" % target)
        log("  %s" % folder)
        log("are already clean.  If the GUI still draws channels it is showing")
        log("its own in-memory network: close the project WITHOUT saving and")
        log("reopen it.  If it is still there after a clean reopen, the GUI is")
        log("reading a different copy of the project.")
        return 0

    if args.check:
        dirty = audit(folder, paths, remove_branches, "state of this model", one)
        if not args.no_outfall_manholes and not drop_all_nodes:
            j = find_outfall_junctions(net_path, keep_mask, cls)
            have = read_storage_nodes(p(node_file))
            log("  %-22s %6d node(s), %d without a manhole" %
                ("sewer/removed junctions", len(j),
                 sum(1 for n in j if n not in have)))
        project_state(mdu_path, folder,
                      os.path.abspath(args.dsproj) if args.dsproj else None)
        log()
        log("RESULT: %d item(s) still belong to a %s -> run without "
            "--check to remove them." % (dirty, one))
        return 0

    # ---- outfall manholes -------------------------------------------------
    # The junctions have to be found while the removed branches are still in
    # the net file, so this runs before anything is rewritten.
    add_manholes = not args.no_outfall_manholes and not drop_all_nodes
    junctions, manhole_plan, gui_assigns = OrderedDict(), [], []
    mh_skipped, mh_warn, mdu_added_key = [], [], None
    if add_manholes:
        junctions = find_outfall_junctions(net_path, keep_mask, cls)
        if junctions:
            storage = read_storage_nodes(p(node_file))
            levels = read_branch_end_levels(p(crsloc_file))
            gui_comp = read_gui_compartments(p(branch_file))
            coords = network_node_coords(net_path)
            manhole_plan, mh_skipped, mh_warn = plan_outfall_manholes(
                junctions, storage, levels, gui_comp, coords, args)
            if not args.no_outfall_compartment_names:
                named = [(it["id"], it["sewer_ends"]) for it in manhole_plan]
                # junctions that already had a manhole still miss its name on
                # the sewer end that used to run into the removed branch
                named += [(norm_id(storage[n]["kv"].get("id")) or n,
                           junctions[n]["sewer_ends"]) for n in mh_skipped]
                for mid, ends in named:
                    for br, sd, _other in ends:
                        if not gui_comp.get(br, {}).get(sd):
                            gui_assigns.append((br, sd, mid))

    # ---- pre-flight: nothing may hold the files open ----------------------
    locked = check_writable([v for v in paths.values() if v] + [mdu_path])
    if locked:
        log()
        log("!! these files cannot be written:")
        for pth, why in locked:
            log("   %s\n      %s" % (pth, why))
        log()
        log("   The Delft3D FM Suite keeps the project's files open while the")
        log("   project is loaded.  CLOSE the project (or the whole FM Suite)")
        log("   and run again.  Use --force to try anyway.")
        if not (args.force or args.dry_run):
            return 2

    # ---- net.nc -----------------------------------------------------------
    log()
    log("network file: %s" % net_file)
    topo_out = {}
    tmp = net_path + ".tmp_nochannels"
    stats = rebuild_net(net_path, tmp, keep_mask, topo_out, one)
    for key in ("branches", "network_nodes", "mesh1d_nodes", "mesh1d_edges",
                "geometry_nodes", "link1d2d"):
        s = stats.get(key)
        if s:
            log("  %-16s kept %8d of %8d   (removed %d)" %
                (key, s[0], s[1], s[1] - s[0]))
    if stats.get("link1d2d") is None:
        log("  link1d2d         none present in this net file")
    moved = stats.get("reassigned_mesh1d_nodes") or []
    if moved:
        log("  junction calculation points handed to a kept branch : %d" % len(moved))
        for nid, old_b, new_b, off in moved:
            log("      %-24s %s -> %s  (offset %s)" % (
                nid, old_b, new_b, "?" if off is None else "%.3f" % off))
    if stats.get("dropped_meshes"):
        log("  emptied, so removed from the net file entirely:")
        log("      %s" % ", ".join(stats["dropped_meshes"]))
    log("  %-16s %8d faces (untouched)" % ("Mesh2d", stats["mesh2d_faces"]))
    if args.dry_run:
        os.remove(tmp)
    else:
        backup(net_path)
        os.replace(tmp, net_path)

    kept_nodes = topo_out.get("kept_network_node_ids", set())
    gone_nodes = topo_out.get("removed_network_node_ids", set())
    log("  network nodes dropped : %d" % len(gone_nodes))

    def on_1d(b, branch_keys=("branchId",), node_keys=()):
        """Is this block anchored on 1D that is going?  With --target all any
        block that names a branch or a network node is 1D by definition, even
        if that id is not in the net file."""
        for k in branch_keys:
            v = norm_id(b.get(k))
            if v is not None and (target == ALL or v in remove_branches):
                return True
        for k in node_keys:
            v = norm_id(b.get(k))
            if v is not None and (target == ALL or v in gone_nodes):
                return True
        return False
    if manhole_plan and kept_nodes:
        lost = [it for it in manhole_plan if it["node"] not in kept_nodes]
        if lost:
            manhole_plan = [it for it in manhole_plan if it["node"] in kept_nodes]
            gui_assigns = [g for g in gui_assigns
                           if g[2] in {it["id"] for it in manhole_plan}]
            mh_warn.append("%d junction node(s) did not survive the rebuild - "
                           "no manhole written there" % len(lost))

    # ---- structures -------------------------------------------------------
    log()
    log("1D objects on the removed branches")
    removed_struct_ids, kept_csdef = set(), set()
    struct_types = Counter()
    if struct_file and os.path.isfile(p(struct_file)):
        f = IniFile(p(struct_file))
        for b in f.of("Structure"):
            if on_1d(b):
                removed_struct_ids.add(norm_id(b.get("id")))
                struct_types[(b.get("type") or "?").strip().lower()] += 1
            else:
                cd = norm_id(b.get("csDefId"))
                if cd:
                    kept_csdef.add(cd)
        for t, n in sorted(struct_types.items()):
            log("      %-10s %5d" % (t, n))
        filter_ini(p(struct_file), "Structure", lambda b: not on_1d(b),
                   args.dry_run, struct_file)

    # ---- cross sections ---------------------------------------------------
    if crsloc_file and os.path.isfile(p(crsloc_file)):
        f = IniFile(p(crsloc_file))
        for b in f.of("CrossSection"):
            if not on_1d(b):
                cd = norm_id(b.get("definitionId"))
                if cd:
                    kept_csdef.add(cd)
        filter_ini(p(crsloc_file), "CrossSection", lambda b: not on_1d(b),
                   args.dry_run, crsloc_file)

    if crsdef_file and os.path.isfile(p(crsdef_file)) and not args.keep_crsdef:
        filter_ini(p(crsdef_file), "Definition",
                   lambda b: norm_id(b.get("id")) in kept_csdef,
                   args.dry_run, crsdef_file)

    # ---- storage nodes (manholes) ----------------------------------------
    if node_file and os.path.isfile(p(node_file)) and not args.keep_storage_nodes:
        if drop_all_nodes:
            # manholes / compartments ARE the sewer system - remove them all
            filter_ini(p(node_file), "StorageNode", lambda b: False,
                       args.dry_run, node_file)
        else:
            filter_ini(p(node_file), "StorageNode",
                       lambda b: (norm_id(b.get("nodeId")) or norm_id(b.get("id")))
                       in kept_nodes, args.dry_run, node_file)

    # ---- branches.gui / routes.gui ----------------------------------------
    if branch_file and os.path.isfile(p(branch_file)):
        filter_ini(p(branch_file), "Branch",
                   lambda b: not on_1d(b, ("name",)),
                   args.dry_run, branch_file)

    routes = os.path.join(folder, "routes.gui")
    if os.path.isfile(routes) and os.path.getsize(routes) > 0:
        for sec in ("Route", "RouteSegment"):
            filter_ini(routes, sec, lambda b: not on_1d(b),
                       args.dry_run, "routes.gui [%s]" % sec)

    # ---- new manholes on the sewer ends the removed branches left free ----
    if add_manholes:
        log()
        log("outfall manholes (sewer ends left free by the removed %s)" % many)
        log("  junction nodes found      : %5d" % len(junctions))
        log("  already had a manhole     : %5d" % len(mh_skipped))
        log("  manholes to add           : %5d" % len(manhole_plan))
        by_how = Counter((it["how"] or "?").split()[0] for it in manhole_plan)
        for k, v in by_how.most_common():
            log("      levels via %-14s %5d" % (k, v))
        for w in mh_warn[:10]:
            log("  !  %s" % w)
        for it in manhole_plan[:5]:
            log("      %-18s bed %9.3f  street %9.3f  area %8.4f  (%s)" %
                (it["id"], it["bed"], it["street"], it["area"], it["how"]))
        if len(manhole_plan) > 5:
            log("      ... and %d more" % (len(manhole_plan) - 5))

        node_path_used, n_added = write_outfall_manholes(
            p(node_file), manhole_plan, args.dry_run, mdu=mdu, folder=folder)
        if n_added:
            log("  %-24s %5d added into %s" %
                ("nodeFile.ini", n_added, os.path.basename(node_path_used)))
            if not node_file:
                node_file = os.path.basename(node_path_used)
                paths["node"] = node_path_used
                mdu_added_key = node_file
        n_gui = set_gui_compartments(p(branch_file), gui_assigns, args.dry_run)
        if n_gui:
            log("  %-24s %5d compartment name(s) set on the sewer ends" %
                (branch_file or "branches.gui", n_gui))
        if manhole_plan and not args.dry_run:
            csv_path = args.manhole_csv or os.path.join(folder,
                                                        "added_manholes.csv")
            if write_manhole_csv(csv_path, manhole_plan):
                log("  list written to %s" % os.path.basename(csv_path))

    # ---- observation points / observation cross sections ------------------
    obs = mdu.files("output", "ObsFile") + mdu.files("output", "CrsFile") + \
        mdu.files("output", "ObsCrsFile")
    for fn in obs:
        fp = p(fn)
        if fp and os.path.isfile(fp) and fp.lower().endswith(".ini"):
            for sec in ("ObservationPoint", "ObservationCrossSection"):
                filter_ini(fp, sec, lambda b: not on_1d(b),
                           args.dry_run, "%s [%s]" % (fn, sec))

    # ---- roughness and 1dField files (branch-wise sections) ---------------
    field_files = []
    if inifield_file and os.path.isfile(p(inifield_file)):
        for b in IniFile(p(inifield_file)).blocks:
            df = b.get("dataFile")
            if df and (b.get("dataFileType") or "").lower() == "1dfield":
                field_files.append(df)
    for fn in list(frict_files) + field_files:
        fp = p(fn)
        if fp and os.path.isfile(fp):
            filter_ini(fp, "Branch",
                       lambda b: not on_1d(b, ("branchId", "name")),
                       args.dry_run, fn)
    if target == ALL and not args.keep_1d_mdu_keys and inifield_file \
            and os.path.isfile(p(inifield_file)):
        # the 1dField entries describe initial conditions on the 1D network
        for sec in ("Initial", "Parameter"):
            filter_ini(p(inifield_file), sec,
                       lambda b: (b.get("dataFileType") or "").strip().lower()
                       != "1dfield", args.dry_run,
                       "%s [%s]" % (inifield_file, sec))

    # ---- boundary conditions / laterals ----------------------------------
    for fn in ext_files:
        fp = p(fn)
        if not fp or not os.path.isfile(fp):
            continue
        for sec in ("Lateral", "Boundary", "Source", "SourceSink"):
            filter_ini(fp, sec,
                       lambda b: not on_1d(b, ("branchId",),
                                           ("nodeId", "compartmentId")),
                       args.dry_run, "%s [%s]" % (fn, sec))

    # ---- structure forcing .bc -------------------------------------------
    if removed_struct_ids:
        for fn in sorted(os.listdir(folder)):
            if not fn.lower().endswith(".bc"):
                continue
            fp = os.path.join(folder, fn)
            filter_ini(fp, "Forcing",
                       lambda b: not any(
                           (norm_id(b.get("name")) or "").startswith(sid)
                           for sid in removed_struct_ids if sid),
                       args.dry_run, fn)

    # ---- blank mdu keys whose file is now empty --------------------------
    log()
    log("mdu")
    emptied = []
    for key, fn, sect in (("StructureFile", struct_file, "geometry"),
                          ("CrossDefFile", crsdef_file, "geometry"),
                          ("CrossLocFile", crsloc_file, "geometry"),
                          ("StorageNodeFile", node_file, "geometry"),
                          ("BranchFile", branch_file, "geometry"),
                          ("IniFieldFile", inifield_file, "geometry")):
        if fn and ini_is_empty(p(fn)):
            emptied.append((sect, key, fn))
    extra_blank = []
    if target == ALL and not args.keep_1d_mdu_keys:
        for sect, key, why in (("geometry", "FrictFile",
                                "1D roughness files - 2D friction comes from "
                                "UnifFrictCoef / the friction field"),):
            if mdu.value(sect, key):
                extra_blank.append((sect, key, why))
    mdu_dirty = bool(mdu_added_key)
    if emptied:
        for sect, key, fn in emptied:
            log("  %s (%s) has no entries left -> key blanked" % (key, fn))
            if not args.dry_run:
                mdu.blank(sect, key)
        mdu_dirty = True
    else:
        log("  no key needed blanking")
    for sect, key, why in extra_blank:
        log("  %s is 1D only -> key blanked (%s)" % (key, why))
        if not args.dry_run:
            mdu.blank(sect, key)
        mdu_dirty = True
    if mdu_added_key:
        log("  StorageNodeFile -> %s (added for the new manholes)" % mdu_added_key)
    if mdu_dirty and not args.dry_run:
        backup(mdu_path)
        mdu.save()

    # ---- verification -----------------------------------------------------
    if not args.dry_run and not args.no_verify:
        dirty = audit(folder, paths, remove_branches,
                      "verification - re-read from disk", one)
        if manhole_plan:
            on_disk = read_storage_nodes(paths.get("node"))
            missing = [it["node"] for it in manhole_plan if it["node"] not in on_disk]
            ds = nc.Dataset(net_path)
            try:
                tv = NetTopology(ds)
                live = {norm_id(v) for v in
                        _chars_to_str(ds.variables[tv.netnode_id_var][:])}
            finally:
                ds.close()
            orphan = [it["node"] for it in manhole_plan if it["node"] not in live]
            log("  %-22s %6d of %6d written, %d on a node that is gone" %
                ("added manholes", len(manhole_plan) - len(missing),
                 len(manhole_plan), len(orphan)))
            dirty += len(missing) + len(orphan)
        if target == ALL:
            ds = nc.Dataset(net_path)
            try:
                tv = NetTopology(ds)
                faces = 0
                fd = getattr(ds.variables[tv.mesh2d], "face_dimension", "") \
                    if tv.mesh2d else ""
                if fd in ds.dimensions:
                    faces = len(ds.dimensions[fd])
                left = [n for n in (tv.network, tv.mesh1d, tv.contact_topo) if n]
            finally:
                ds.close()
            log("  %-22s %s" % ("net file 1D",
                                "gone" if not left else
                                "STILL PRESENT: " + ", ".join(left)))
            log("  %-22s %d faces" % ("net file 2D grid", faces))
            dirty += len(left)
            if not faces:
                log("  !  the model has no 2D grid either - it is now empty")
        if dirty:
            log()
            log("!! VERIFICATION FAILED - %d item(s) still on a %s." % (dirty, one))
            log("   Restore the *.bak files and report this.")
            return 3
        log("  -> PASS: no %s left in any input file." % one)

    if args.dsproj:
        inspect_dsproj(os.path.abspath(args.dsproj))

    log()
    log("done%s." % (" (dry run)" if args.dry_run else
                     " - originals saved as *.bak"))
    if not args.dry_run:
        project_state(mdu_path, folder,
                      os.path.abspath(args.dsproj) if args.dsproj else None)
        log()
        log("Next, in the Delft3D FM Suite:")
        log("  1. if the project is open, close it WITHOUT saving - a loaded")
        log("     project keeps its own network in memory and Save writes it")
        log("     straight back over the files this script just cleaned")
        log("  2. reopen %s" % (os.path.basename(args.dsproj) if args.dsproj
                                else "the .dsproj"))
        log("  3. the map should now show only %s" % kept_desc)
        log("  Re-run with --check at any time to confirm what is on disk.")

    log_path = args.log or os.path.join(folder, "%s.log" % SCRIPT)
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(LOG_LINES) + "\n")
        print("\nreport appended to %s" % log_path)
    except Exception as exc:
        print("could not write log: %s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
