#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpproj - copy a Delft3D FM Suite project (.dsproj) without its model output.

Copies <project>.dsproj and its <project>.dsproj_data folder, leaving out the
output of every model: folders named "output" (left in the copy as empty
folders, as the FM Suite expects them) and DFM_OUTPUT_<model> folders.  The
project file stores its paths relative to the .dsproj_data folder, so the copy
can be given a new name; the data folder is renamed to match.

Close the project in the FM Suite first, or the copied .dsproj may miss the
last changes.

Usage
-----
    cpproj <project.dsproj> [<new.dsproj | folder>]
    cpproj MyProject.dsproj                       (-> MyProject_copy.dsproj)
    cpproj MyProject.dsproj D:/runs/Scenario2.dsproj
    cpproj MyProject.dsproj D:/runs               (-> D:/runs/MyProject.dsproj)
    cpproj MyProject.dsproj --check               (list what is copied / skipped)
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import sys

SCRIPT = "cpproj"
OUTPUT_DIRS = ("output", "DFM_OUTPUT_*")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def data_folder(dsproj):
    """Return the <project>.dsproj_data folder of a .dsproj."""
    return os.path.splitext(dsproj)[0] + ".dsproj_data"


def resolve_paths(source, dest=None):
    """
    Return (source .dsproj, destination .dsproj) as absolute paths.

    `dest` may be a .dsproj path, an existing folder (the project keeps its
    name) or None (<project>_copy.dsproj next to the source).
    """
    source = os.path.abspath(source)
    if os.path.isdir(source):
        found = [f for f in os.listdir(source) if f.lower().endswith(".dsproj")]
        if len(found) != 1:
            raise ValueError("%d .dsproj files in %s; give the project file"
                             % (len(found), source))
        source = os.path.join(source, found[0])
    if not source.lower().endswith(".dsproj"):
        raise ValueError("not a .dsproj: %s" % source)
    if not os.path.isfile(source):
        raise FileNotFoundError("project not found: %s" % source)

    if dest is None:
        dest = os.path.splitext(source)[0] + "_copy.dsproj"
    else:
        dest = os.path.abspath(dest)
        if os.path.isdir(dest):
            dest = os.path.join(dest, os.path.basename(source))
        elif not dest.lower().endswith(".dsproj"):
            dest += ".dsproj"
    if os.path.normcase(dest) == os.path.normcase(source):
        raise ValueError("destination is the source project")
    return source, dest


def is_output_dir(name, patterns=OUTPUT_DIRS):
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns)


def folder_size(folder):
    return sum(os.path.getsize(os.path.join(root, f))
               for root, _, files in os.walk(folder) for f in files)


def fmt_size(nbytes):
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (nbytes, unit)
        nbytes /= 1024.0


def plan_copy(src_data, exclude=()):
    """
    Walk the source data folder.

    Returns (files, output_dirs, excluded): files to copy as relative paths,
    output folders as (relative path, size) and files / folders skipped by an
    `exclude` pattern as relative paths.
    """
    files, output_dirs, excluded = [], [], []
    for root, dirs, names in os.walk(src_data):
        rel_root = os.path.relpath(root, src_data)
        keep = []
        for d in sorted(dirs):
            rel = os.path.normpath(os.path.join(rel_root, d))
            if is_output_dir(d):
                output_dirs.append((rel, folder_size(os.path.join(root, d))))
            elif is_output_dir(d, exclude):
                excluded.append(rel)
            else:
                keep.append(d)
        dirs[:] = keep
        for f in sorted(names):
            rel = os.path.normpath(os.path.join(rel_root, f))
            (excluded if is_output_dir(f, exclude) else files).append(rel)
    return files, output_dirs, excluded


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def copy_project(source, dest=None, exclude=(), write=True):
    """
    Copy a .dsproj project without its model output.

    Parameters
    ----------
    source : str
        The .dsproj, or a folder holding exactly one .dsproj.
    dest : str, optional
        New .dsproj path or an existing folder; default
        <project>_copy.dsproj next to the source.
    exclude : sequence of str
        Extra file / folder name patterns to leave out (e.g. "*.bak*").
    write : bool
        Copy the files; False only works out what would be copied.

    Returns
    -------
    dict
        source, dest (the .dsproj paths), files (relative paths copied from
        the data folder), size (bytes copied, .dsproj included), output_dirs
        (list of (relative path, size) left out) and excluded (relative paths).
    """
    source, dest = resolve_paths(source, dest)
    src_data, dst_data = data_folder(source), data_folder(dest)
    for path in (dest, dst_data):
        if os.path.exists(path):
            raise FileExistsError("already exists: %s" % path)
    if not os.path.isdir(src_data):
        raise FileNotFoundError("project data folder not found: %s" % src_data)

    files, output_dirs, excluded = plan_copy(src_data, exclude)
    size = os.path.getsize(source) + sum(
        os.path.getsize(os.path.join(src_data, f)) for f in files)

    if write:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(source, dest)
        os.makedirs(dst_data)
        for rel, _ in output_dirs:
            if os.path.basename(rel).lower() == "output":
                os.makedirs(os.path.join(dst_data, rel), exist_ok=True)
        for rel in files:
            target = os.path.join(dst_data, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(src_data, rel), target)
        # Keep empty input folders too
        for root, dirs, _ in os.walk(src_data):
            dirs[:] = [d for d in dirs
                       if not is_output_dir(d) and not is_output_dir(d, exclude)]
            for d in dirs:
                rel = os.path.relpath(os.path.join(root, d), src_data)
                os.makedirs(os.path.join(dst_data, rel), exist_ok=True)

    return dict(source=source, dest=dest, files=files, size=size,
                output_dirs=output_dirs, excluded=excluded)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Copy a Delft3D FM Suite project (.dsproj + .dsproj_data) "
                    "without the model output.",
        epilog="""
examples:
  %(prog)s MyProject.dsproj                       (-> MyProject_copy.dsproj)
  %(prog)s MyProject.dsproj D:/runs/Scenario2.dsproj
  %(prog)s MyProject.dsproj D:/runs               (-> D:/runs/MyProject.dsproj)
  %(prog)s MyProject.dsproj New.dsproj -x "*.bak*"   (also leave out backups)
  %(prog)s MyProject.dsproj --check               (list, copy nothing)

Left out: every folder named "output" (created empty in the copy) and
DFM_OUTPUT_<model> folders. Close the project in the FM Suite first.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help=".dsproj, or a folder holding one")
    ap.add_argument("dest", nargs="?",
                    help="new .dsproj or an existing folder "
                         "(default: <project>_copy.dsproj next to the source)")
    ap.add_argument("-x", "--exclude", action="append", default=[],
                    metavar="PATTERN",
                    help="also leave out files / folders matching this name "
                         "pattern (repeatable)")
    ap.add_argument("--check", action="store_true",
                    help="show what would be copied and skipped, copy nothing")
    args = ap.parse_args(argv)

    try:
        res = copy_project(args.source, args.dest, exclude=args.exclude,
                           write=not args.check)
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        ap.error(str(exc))
    except OSError as exc:
        print("%s: copy failed: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1

    print("Source : %s" % res["source"])
    print("Copy   : %s" % res["dest"])
    if res["output_dirs"]:
        print("\nOutput left out:")
        for rel, size in res["output_dirs"]:
            print("  %-50s %10s" % (rel, fmt_size(size)))
    if res["excluded"]:
        print("\nExcluded:")
        for rel in res["excluded"]:
            print("  %s" % rel)
    skipped = fmt_size(sum(size for _, size in res["output_dirs"]))
    msg = "%d file(s) + .dsproj, %s (output skipped: %s)" % (
        len(res["files"]), fmt_size(res["size"]), skipped)
    if args.check:
        print("\nWould copy %s. Check only - nothing copied." % msg)
    else:
        print("\nCopied %s." % msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
