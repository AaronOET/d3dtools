#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clrbak - remove the backup files that the D3D tools leave in a D-Flow FM
(Delft3D FM) model input folder.

Backups are <file>.bak (alignncrain, otstep, itstep, rmgrid, ...) and
<file>.bak2, <file>.bak3, ... (rm1dch, rm1dsw, mk2d keep older backups that
way).  Only files whose name ends in .bak or .bak<number> are removed.

The target can be a model input folder, the .mdu in it (its folder is used)
or a .dsproj (its <project>.dsproj_data folder is searched recursively).

Usage
-----
    clrbak <input-folder | model.mdu | project.dsproj>
    clrbak dflowfm --check              (list the backups, remove nothing)
    clrbak models -r                    (include subfolders)
"""

from __future__ import annotations

import argparse
import os
import re
import sys

SCRIPT = "clrbak"
BACKUP_RE = re.compile(r"\.bak\d*$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def resolve_folder(target):
    """
    Return (folder, recursive) for a folder, a .mdu or a .dsproj.

    A .dsproj maps to its <project>.dsproj_data folder, which is always
    searched recursively (the model inputs live in subfolders).
    """
    target = os.path.abspath(target)
    if os.path.isdir(target):
        return target, False
    if target.lower().endswith(".dsproj"):
        folder = os.path.splitext(target)[0] + ".dsproj_data"
        if not os.path.isdir(folder):
            raise FileNotFoundError("project data folder not found: %s" % folder)
        return folder, True
    if target.lower().endswith(".mdu"):
        if not os.path.isfile(target):
            raise FileNotFoundError("mdu not found: %s" % target)
        return os.path.dirname(target), False
    if not os.path.exists(target):
        raise FileNotFoundError("not found: %s" % target)
    raise ValueError("give a model input folder, a .mdu or a .dsproj")


def find_backups(folder, recursive=False):
    """Return the sorted paths of the backup files in `folder`."""
    if recursive:
        found = [os.path.join(root, f) for root, _, files in os.walk(folder)
                 for f in files if BACKUP_RE.search(f)]
    else:
        found = [os.path.join(folder, f) for f in os.listdir(folder)
                 if BACKUP_RE.search(f) and os.path.isfile(os.path.join(folder, f))]
    return sorted(found)


def fmt_size(nbytes):
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (nbytes, unit)
        nbytes /= 1024.0


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def clear_backups(target, recursive=False, write=True):
    """
    Remove the backup files (*.bak, *.bak2, ...) of a model.

    Parameters
    ----------
    target : str
        Model input folder, .mdu or .dsproj.
    recursive : bool
        Also search subfolders (always on for a .dsproj).
    write : bool
        Remove the files; False only lists them.

    Returns
    -------
    dict
        folder (str), files (list of (path, size in bytes)), removed (list of
        paths), failed (list of (path, error message)).
    """
    folder, always_recursive = resolve_folder(target)
    files = [(p, os.path.getsize(p))
             for p in find_backups(folder, recursive or always_recursive)]
    removed, failed = [], []
    if write:
        for path, _ in files:
            try:
                os.remove(path)
                removed.append(path)
            except OSError as exc:  # e.g. file open in the FM Suite
                failed.append((path, exc.strerror or str(exc)))
    return dict(folder=folder, files=files, removed=removed, failed=failed)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Remove the backup files (*.bak, *.bak2, ...) from a D-Flow FM "
                    "model input folder.",
        epilog="""
examples:
  %(prog)s dflowfm                          (input folder)
  %(prog)s dflowfm/FlowFM.mdu               (the folder of the .mdu)
  %(prog)s MyProject.dsproj                 (all models of the project)
  %(prog)s models -r                        (include subfolders)
  %(prog)s dflowfm --check                  (list the backups, remove nothing)

Removed files cannot be recovered; rmgrid / rmgriddimr --restore need the
*_net.nc.bak, so restore first or run with --check to see what goes.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="model input folder, .mdu or .dsproj")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="also search subfolders (always on for a .dsproj)")
    ap.add_argument("--check", action="store_true",
                    help="list the backup files, remove nothing")
    args = ap.parse_args(argv)

    try:
        res = clear_backups(args.target, recursive=args.recursive,
                            write=not args.check)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))

    files = res["files"]
    if not files:
        print("No backup files in %s" % res["folder"])
        return 0
    print("%s" % res["folder"])
    for path, size in files:
        print("  %-50s %10s" % (os.path.relpath(path, res["folder"]), fmt_size(size)))
    total = fmt_size(sum(size for _, size in files))
    if args.check:
        print("\n%d backup file(s), %s. Check only - nothing removed." % (len(files), total))
        return 0
    for path, msg in res["failed"]:
        print("%s: cannot remove %s: %s" % (SCRIPT, path, msg), file=sys.stderr)
    print("\nRemoved %d of %d backup file(s), %s." % (len(res["removed"]), len(files), total))
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
