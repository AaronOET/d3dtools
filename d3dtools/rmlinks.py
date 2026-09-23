#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rmlinks - remove ONLY the 1D2D links from a Delft3D FM (D-HYDRO / FM Suite)
1D2D model.

The 1D network (branches, network nodes, geometry), mesh1d, Mesh2d and every
other input file (structures, cross sections, manholes, boundaries, ...) are
copied through untouched.  Only the mesh-contact block of the net file goes:

    links                     (cf_role = mesh_topology_contact)
    links_contact_id
    links_contact_long_name
    links_contact_type
    links_nContacts           (dimension)

--type lets you remove only some kinds of link and keep the rest:

    lateral        (3)   lateral_1d2d_link
    longitudinal   (4)   longitudinal_1d2d_link
    street_inlet   (5)   street_inlet_1d2d_link
    roof_gutter    (7)   roof_gutter_1d2d_link
    embedded       (1)   embedded 1D2D link (older files)
    all                  every link (default)

When every link is removed, the contact dimension and all variables on it are
dropped from the file (a NETCDF3 file cannot hold a zero-length fixed
dimension, and UGRID-wise the file then simply has no contact set).  If the
.mdu has a non-empty 1D2DLinkFile key (links defined in a separate ini file),
that key is blanked as well, unless --keep-linkfile is given.

The net file is rewritten through a temporary file and the original is first
copied to <name>.bak (or .bak2, .bak3 ... so an existing backup is never lost).

IMPORTANT - close the project in the FM Suite before running.  A loaded
DeltaShell project holds the links in memory; these files are only its last
export, so the next Save writes the links straight back.  After running,
reopen the project WITHOUT saving first.

Usage
-----
    rmlinks <input-folder | model.mdu | *_net.nc> --check
    rmlinks <input-folder | model.mdu | *_net.nc> --dry-run
    rmlinks <input-folder | model.mdu | *_net.nc>
    rmlinks <input-folder> --type street_inlet roof_gutter

Requires: numpy, netCDF4      (pip install numpy netCDF4)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from collections import Counter, OrderedDict
from datetime import datetime

import numpy as np

try:
    import netCDF4 as nc
except ImportError:  # pragma: no cover
    sys.exit("netCDF4 is required:  pip install netCDF4")

SCRIPT = "rmlinks"

# UGRID / Deltares contact_type codes
TYPE_CODES = OrderedDict([
    ("embedded", 1),
    ("lateral", 3),
    ("longitudinal", 4),
    ("street_inlet", 5),
    ("roof_gutter", 7),
])
CODE_NAMES = {v: k for k, v in TYPE_CODES.items()}


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #

LOG_LINES = []


def log(msg=""):
    print(msg)
    LOG_LINES.append(str(msg))


def backup(path, dry=False):
    """Copy path -> path.bak, never overwriting an existing backup."""
    if not os.path.isfile(path):
        return None
    cand = path + ".bak"
    n = 2
    while os.path.exists(cand):
        cand = "%s.bak%d" % (path, n)
        n += 1
    if not dry:
        shutil.copy2(path, cand)
    return cand


def is_writable(path):
    try:
        with open(path, "r+b"):
            pass
        return None
    except Exception as exc:  # file locked by the FM Suite on Windows
        return "%s: %s" % (exc.__class__.__name__, exc)


_KEY_RE = re.compile(r"^\s*([A-Za-z0-9_][\w\-.]*)\s*=\s*(.*?)\s*$")
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_GEN_RE = re.compile(r"^#\s*Generated on\s+(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})",
                     re.I | re.M)


def mdu_read(path):
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        return fh.readlines()


def mdu_value(lines, key):
    """(line index, value) of `key` anywhere in the mdu, or (None, None)."""
    low = key.lower()
    for i, ln in enumerate(lines):
        m = _KEY_RE.match(ln.split("#", 1)[0])
        if m and m.group(1).lower() == low:
            return i, m.group(2).strip()
    return None, None


def blank_line(line):
    """`key = value   # comment`  ->  `key =           # comment`."""
    nl = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
    body = line[:len(line) - len(nl)]
    head, sep, comment = body.partition("#")
    eq = head.index("=")
    return head[:eq + 1] + " " * max(len(head) - eq - 1, 1) + \
        (sep + comment if sep else "") + nl


def mdu_generated_on(lines):
    m = _GEN_RE.search("".join(lines[:10]))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
#  net file
# --------------------------------------------------------------------------- #

def find_contacts(ds):
    """Return a list of contact sets:
    {topo, conn, dim, type_var, id_var, vars_on_dim}."""
    sets = []
    for name, var in ds.variables.items():
        if getattr(var, "cf_role", "") != "mesh_topology_contact":
            continue
        # D-HYDRO stores the connectivity in the topology variable itself
        # (links(nContacts, 2)); UGRID 1.0 allows a separate variable.
        conn = name if var.ndim == 2 else None
        for a in ("contact_connectivity", "contact"):
            v = getattr(var, a, None)
            if isinstance(v, str) and v in ds.variables \
                    and ds.variables[v].ndim == 2:
                conn = v
                break
        if conn is None:
            continue
        dim = ds.variables[conn].dimensions[0]
        tvar = getattr(var, "contact_type", None)
        tvar = tvar if tvar in ds.variables else None
        ivar = getattr(var, "contact_id", None)
        ivar = ivar if ivar in ds.variables else None
        on_dim = [n for n, v in ds.variables.items() if dim in v.dimensions]
        sets.append(dict(topo=name, conn=conn, dim=dim, type_var=tvar,
                         id_var=ivar, vars_on_dim=on_dim))
    # fallback for old files without cf_role: link1d2d(nLink1D2D_edge, 2)
    if not sets:
        for name, var in ds.variables.items():
            if var.ndim == 2 and var.shape[1] == 2 \
                    and re.search(r"link1d2d|contact", name, re.I) \
                    and np.issubdtype(var.dtype, np.integer):
                dim = var.dimensions[0]
                on_dim = [n for n, v in ds.variables.items() if dim in v.dimensions]
                tvar = next((n for n in on_dim if "type" in n.lower()
                             and ds.variables[n].ndim == 1), None)
                ivar = next((n for n in on_dim if n.lower().endswith("id")), None)
                sets.append(dict(topo=name, conn=name, dim=dim, type_var=tvar,
                                 id_var=ivar, vars_on_dim=on_dim))
                break
    return sets


def contact_types(ds, cs):
    n = len(ds.dimensions[cs["dim"]])
    if cs["type_var"]:
        t = ds.variables[cs["type_var"]]
        t.set_auto_mask(False)
        return np.asarray(t[:], dtype=np.int64).reshape(n)
    return np.full(n, -1, dtype=np.int64)       # unknown type


def describe(ds, sets):
    """Log the link count per set and type; return total."""
    total = 0
    if not sets:
        log("  no 1D2D link (mesh contact) variables in this net file")
        return 0
    for cs in sets:
        types = contact_types(ds, cs)
        total += types.size
        log("  %-26s %6d link(s)   [dimension %s]" %
            (cs["topo"], types.size, cs["dim"]))
        for code, cnt in sorted(Counter(types.tolist()).items()):
            log("      type %2d  %-14s %6d" %
                (code, CODE_NAMES.get(code, "unknown"), cnt))
    return total


def rebuild_net(src, dst, remove_codes):
    """Write src -> dst without the selected 1D2D links.  Returns stats."""
    ds = nc.Dataset(src, "r")
    ds.set_auto_maskandscale(False)
    ds.set_auto_chartostring(False)
    stats = []
    try:
        sets = find_contacts(ds)
        sel, drop_vars, drop_dims = {}, set(), set()
        for cs in sets:
            types = contact_types(ds, cs)
            if remove_codes is None:
                rm = np.ones(types.size, dtype=bool)
            else:
                rm = np.isin(types, list(remove_codes))
            keep = np.where(~rm)[0]
            stats.append((cs["topo"], int(rm.sum()), int(types.size)))
            if keep.size == 0:
                drop_dims.add(cs["dim"])
                drop_vars.update(cs["vars_on_dim"])
                drop_vars.add(cs["topo"])
            elif keep.size < types.size:
                sel[cs["dim"]] = keep

        out = nc.Dataset(dst, "w", format=ds.file_format)
        out.set_auto_maskandscale(False)
        out.set_auto_chartostring(False)
        try:
            out.setncatts({a: ds.getncattr(a) for a in ds.ncattrs()})
            hist = "%s: 1D2D links removed by %s" % (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"), SCRIPT)
            out.history = (str(getattr(ds, "history", "")) + "\n" + hist).strip()

            for dname, dim in ds.dimensions.items():
                if dname in drop_dims:
                    continue
                if dim.isunlimited():
                    size = None
                elif dname in sel:
                    size = len(sel[dname])
                else:
                    size = len(dim)
                out.createDimension(dname, size)

            for vname, var in ds.variables.items():
                if vname in drop_vars:
                    continue
                attrs = {a: var.getncattr(a) for a in var.ncattrs()}
                fill = attrs.pop("_FillValue", None)
                new = out.createVariable(vname, var.dtype, var.dimensions,
                                         fill_value=fill)
                new.setncatts(attrs)
                if var.ndim == 0:
                    new.assignValue(var.getValue())
                    continue
                if not any(d in sel for d in var.dimensions):
                    # untouched (network, mesh1d, Mesh2d ...): copy in slices
                    n0 = var.shape[0]
                    if n0 == 0:
                        continue
                    row = int(np.prod(var.shape[1:])) if var.ndim > 1 else 1
                    step = max(1, int(4e7 // max(row, 1)))
                    for i in range(0, n0, step):
                        new[i:i + step, ...] = var[i:i + step, ...]
                    continue
                data = var[:]
                for ax, d in enumerate(var.dimensions):
                    if d in sel:
                        data = np.take(data, sel[d], axis=ax)
                new[...] = data
        finally:
            out.close()
    finally:
        ds.close()
    return stats


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Remove only the 1D2D links from a Delft3D FM net file; "
                    "the 1D network, mesh1d, Mesh2d and all other input files "
                    "are left untouched.")
    ap.add_argument("model",
                    help="model input folder, the .mdu, or the *_net.nc itself")
    ap.add_argument("--type", nargs="+", default=["all"],
                    choices=["all"] + list(TYPE_CODES),
                    help="which link types to remove (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="only list the 1D2D links in the net file")
    ap.add_argument("--keep-linkfile", action="store_true",
                    help="do not blank a 1D2DLinkFile key in the .mdu")
    ap.add_argument("--force", action="store_true",
                    help="run even if a file looks locked by another program")
    ap.add_argument("--log", help="report file "
                                  "(default: <input>/rmlinks.log)")
    args = ap.parse_args(argv)

    # ---- locate mdu / net file -------------------------------------------
    target = os.path.abspath(args.model)
    mdu_path = net_path = None
    if os.path.isdir(target):
        mdus = [f for f in sorted(os.listdir(target)) if f.lower().endswith(".mdu")]
        if len(mdus) != 1:
            sys.exit("expected exactly one .mdu in %s, found %d"
                     % (target, len(mdus)))
        mdu_path = os.path.join(target, mdus[0])
    elif target.lower().endswith(".mdu"):
        mdu_path = target
    elif target.lower().endswith(".nc"):
        net_path = target
    else:
        sys.exit("give a model input folder, a .mdu or a *_net.nc file")

    mdu_lines = None
    if mdu_path:
        if not os.path.isfile(mdu_path):
            sys.exit("mdu not found: %s" % mdu_path)
        mdu_lines = mdu_read(mdu_path)
        _, netfile = mdu_value(mdu_lines, "NetFile")
        if not netfile:
            sys.exit("NetFile is empty in %s" % mdu_path)
        net_path = os.path.join(os.path.dirname(mdu_path), netfile)
    if not os.path.isfile(net_path):
        sys.exit("net file not found: %s" % net_path)
    folder = os.path.dirname(mdu_path or net_path)

    remove_codes = None if "all" in args.type else \
        {TYPE_CODES[t] for t in args.type}
    mode = "CHECK ONLY - nothing is written" if args.check else \
        ("DRY RUN - nothing is written" if args.dry_run else
         "WRITE (original -> .bak)")

    log("=" * 72)
    log("%s   %s" % (SCRIPT, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log("mdu   : %s" % (mdu_path or "-"))
    log("net   : %s" % net_path)
    log("remove: %s" % ("all 1D2D links" if remove_codes is None else
                        ", ".join("%s (%d)" % (CODE_NAMES[c], c)
                                  for c in sorted(remove_codes))))
    log("mode  : %s" % mode)
    log("=" * 72)

    # ---- is the FM Suite still holding the project? ----------------------
    if mdu_lines:
        gen = mdu_generated_on(mdu_lines)
        if gen and 0 <= (datetime.now() - gen).total_seconds() < 3600:
            log()
            log("!  the FM Suite exported this model %d minute(s) ago - the"
                % int((datetime.now() - gen).total_seconds() // 60))
            log("   project may still be open.  Close it WITHOUT saving before")
            log("   running, or the next Save writes the links back.")

    # ---- current state ---------------------------------------------------
    log()
    log("1D2D links in the net file")
    with nc.Dataset(net_path) as ds:
        sets = find_contacts(ds)
        total = describe(ds, sets)
        to_remove = 0
        for cs in sets:
            t = contact_types(ds, cs)
            to_remove += t.size if remove_codes is None else \
                int(np.isin(t, list(remove_codes)).sum())

    linkfile_idx = linkfile = None
    if mdu_lines:
        linkfile_idx, linkfile = mdu_value(mdu_lines, "1D2DLinkFile")
        if linkfile:
            log("  .mdu 1D2DLinkFile        = %s" % linkfile)
    blank_linkfile = bool(linkfile) and remove_codes is None \
        and not args.keep_linkfile

    if args.check:
        return finish(args, folder, 0)

    if to_remove == 0 and not blank_linkfile:
        log()
        log("nothing to remove.")
        return finish(args, folder, 0)

    log()
    log("plan")
    log("  remove %d of %d link(s) from %s" %
        (to_remove, total, os.path.basename(net_path)))
    if blank_linkfile:
        log("  blank 1D2DLinkFile in %s" % os.path.basename(mdu_path))
    log("  everything else (1D network, mesh1d, Mesh2d, other files) untouched")

    # ---- locks -----------------------------------------------------------
    files = [net_path] + ([mdu_path] if blank_linkfile else [])
    locked = [(p, r) for p in files for r in [is_writable(p)] if r]
    if locked:
        log()
        for p, r in locked:
            log("!! cannot write %s  (%s)" % (p, r))
        if not args.force:
            log("   Close the project in the FM Suite and run again "
                "(or use --force).")
            return finish(args, folder, 2)

    if args.dry_run:
        return finish(args, folder, 0)

    # ---- write -----------------------------------------------------------
    log()
    if to_remove:
        tmp = net_path + ".tmp_%s.nc" % os.getpid()
        try:
            stats = rebuild_net(net_path, tmp, remove_codes)
            bak = backup(net_path)
            os.replace(tmp, net_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        log("backup: %s" % bak)
        for topo, rm, tot in stats:
            log("  %-26s removed %d of %d%s" %
                (topo, rm, tot, "  (contact set dropped)" if rm == tot else ""))

    if blank_linkfile:
        bak = backup(mdu_path)
        mdu_lines[linkfile_idx] = blank_line(mdu_lines[linkfile_idx])
        with open(mdu_path, "w", encoding="utf-8", newline="") as fh:
            fh.writelines(mdu_lines)
        log("backup: %s" % bak)
        log("  1D2DLinkFile blanked (the file %s itself is kept)" % linkfile)

    # ---- verify ----------------------------------------------------------
    log()
    log("verification")
    with nc.Dataset(net_path) as ds:
        left = 0
        for cs in find_contacts(ds):
            t = contact_types(ds, cs)
            left += t.size if remove_codes is None else \
                int(np.isin(t, list(remove_codes)).sum())
        describe(ds, find_contacts(ds))
        topo = [n for n, v in ds.variables.items()
                if getattr(v, "cf_role", "") == "mesh_topology"]
        log("  mesh topologies kept: %s" % ", ".join(topo))
    if left:
        log("!! %d targeted link(s) still present" % left)
        return finish(args, folder, 1)
    log("  OK - targeted 1D2D links removed.")
    log()
    log("Reopen the project in the FM Suite WITHOUT saving it first.")
    with nc.Dataset(net_path) as ds:
        if not find_contacts(ds):
            log("Note: with no 1D2D links the 1D and 2D parts no longer "
                "exchange water.")
    return finish(args, folder, 0)


def finish(args, folder, code):
    path = args.log or os.path.join(folder, "rmlinks.log")
    if not args.check:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(LOG_LINES) + "\n\n")
        except Exception as exc:
            print("(could not write log %s: %s)" % (path, exc))
    return code


if __name__ == "__main__":
    sys.exit(main())
