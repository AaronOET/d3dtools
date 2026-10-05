#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clrmpi - delete the partitioned (MPI) input and output files of the D-Flow FM
models in a DIMR run folder.

For every dflowfm <component> in the DIMR config (workingDir + inputFile) it
removes, anywhere in the working directory tree:

    input   <model>_NNNN.mdu                       partitioned MDU files
            <net>_NNNN_net.nc                      partitioned grid files
            DFM_interpreted_idomain_<net>_net.nc   partitioning result
    output  <model>_NNNN.dia, <model>_NNNN_map.nc,
            <model>_NNNN_his.nc, ..._rst.nc, ...   any file starting with
                                                   <model>_NNNN

The original MDU, grid and the non-partitioned output (e.g. <model>_map.nc)
are kept.

Usage
-----
    clrmpi --check                     (only list what would be deleted)
    clrmpi                             (list, ask for confirmation, delete)
    clrmpi -y                          (delete without asking)
    clrmpi C:/models/PT01              (another run folder or DIMR config)
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from .dimrutils import dflowfm_models, resolve_dimr
from .mduutils import get_key, read_lines

SCRIPT = "clrmpi"
RANK = r"_\d{4}"


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def mpi_files_for_model(workdir, mdu_name):
    """Return the partitioned input / output files of one D-Flow FM model."""
    mdu = os.path.join(workdir, mdu_name)
    model = os.path.splitext(os.path.basename(mdu_name))[0]
    patterns = [re.compile(r"^%s%s(?:[_.].*)?$" % (re.escape(model), RANK), re.IGNORECASE)]

    net = get_key(read_lines(mdu)[0], "geometry", "NetFile") if os.path.isfile(mdu) else None
    if net:
        net_name = os.path.basename(net.replace("\\", "/"))
        net_stem = re.sub(r"_net\.nc$", "", net_name, flags=re.IGNORECASE)
        patterns.append(re.compile(r"^%s%s_net\.nc$" % (re.escape(net_stem), RANK),
                                   re.IGNORECASE))
        patterns.append(re.compile(r"^DFM_interpreted_idomain_%s$" % re.escape(net_name),
                                   re.IGNORECASE))
    else:
        print("%s: warning: NetFile not found in %s; partitioned grid files not matched."
              % (SCRIPT, mdu), file=sys.stderr)

    files = []
    for dirpath, _, filenames in os.walk(workdir):
        files += [os.path.join(dirpath, f) for f in filenames
                  if any(p.match(f) for p in patterns)]
    return sorted(files)


def find_mpi_files(config):
    """Return the partitioned (MPI) files of all dflowfm components in `config`."""
    files = []
    for workdir, mdu_name in dflowfm_models(config):
        files += mpi_files_for_model(workdir, mdu_name)
    return files


def delete_files(files):
    """Delete `files`; return a list of (path, error) for those that failed."""
    failed = []
    for path in files:
        try:
            os.remove(path)
        except OSError as exc:
            failed.append((path, exc))
    return failed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Delete the partitioned (MPI) input and output files of the "
                    "D-Flow FM models in a DIMR run folder.",
        epilog="""
examples:
  %(prog)s --check                        (only list the files that would be deleted)
  %(prog)s                                (list, ask for confirmation, then delete)
  %(prog)s -y                             (delete without asking)
  %(prog)s C:/models/PT01                 (another run folder)
  %(prog)s -f other/dimr_config.xml       (another DIMR config file)

Kept: the original .mdu and net file, and the non-partitioned output
(e.g. <model>_map.nc). Stop the model run before deleting.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", nargs="?", default=None,
                    help="DIMR run folder or config file (default: current folder)")
    ap.add_argument("-f", "--file", default=None,
                    help="DIMR config file (same as the positional argument)")
    ap.add_argument("-y", "--yes", action="store_true", help="delete without asking")
    ap.add_argument("--check", "--dry-run", dest="check", action="store_true",
                    help="only list the files, delete nothing")
    args = ap.parse_args(argv)

    if args.config and args.file:
        ap.error("give the DIMR config either as argument or with -f, not both")
    try:
        config = resolve_dimr(args.file or args.config)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))

    files = find_mpi_files(config)
    if not files:
        print("No MPI files found.")
        return 0

    root = os.path.dirname(config)
    total = sum(os.path.getsize(p) for p in files)
    for path in files:
        print("  %s" % os.path.relpath(path, root))
    print("%d file(s), %.1f MB" % (len(files), total / 1e6))

    if args.check:
        print("\nCheck only - nothing deleted.")
        return 0
    if not args.yes and input("Delete these files? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return 0

    failed = delete_files(files)
    for path, exc in failed:
        print("Could not delete %s: %s" % (path, exc), file=sys.stderr)
    print("Deleted %d file(s).%s" % (len(files) - len(failed),
                                     " %d failed (in use?)." % len(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
