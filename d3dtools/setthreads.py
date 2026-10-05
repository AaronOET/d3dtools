#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setthreads - show or change the OpenMP thread count and the MPI settings of
the components in a DIMR configuration file (dimr_config.xml).

With -n, <setting key="threads" value="N" /> is set in every <component>
(inserted after <workingDir> when missing).  <process> and <mpiCommunicator>
are always rewritten after <library>, one line each:

    <process>0 1 ... P-1</process>
    <mpiCommunicator>DFM_COMM_DFMWORLD</mpiCommunicator>

-p P writes them for P processes; without -p they are removed, leaving a
non-MPI (OpenMP-only) run.  The file is edited as text so comments, encoding
(BOM) and layout are kept; it is written with Windows (CRLF) line endings.

With -n, OMP_NUM_THREADS = N is also set as a persistent user environment
variable (setx on Windows).  It takes effect in newly opened command prompts,
not in the current one.

Without -n / -p the current settings are printed.

Usage
-----
    setthreads [run-folder | dimr_config.xml]          (show)
    setthreads -n 8                                    (non-MPI run, 8 threads)
    setthreads -n 2 -p 4                               (MPI run on 4 processes)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

from .dimrutils import (COMPONENT_RE, read_dimr, resolve_dimr, tag_value,
                        write_dimr)

SCRIPT = "setthreads"
DEFAULT_COMMUNICATOR = "DFM_COMM_DFMWORLD"

THREADS_RE = re.compile(r'(<setting\s+key="threads"\s+value=")([^"]*)(")')
NAME_RE = re.compile(r'<component\b[^>]*\bname="([^"]*)"')
WORKDIR_RE = re.compile(r"^([ \t]*)(<workingDir>.*?</workingDir>[ \t]*\n)", re.MULTILINE)
LIBRARY_RE = re.compile(r"^([ \t]*)(<library>.*?</library>[ \t]*\n)", re.MULTILINE)
MPI_TAGS = ("process", "mpiCommunicator")


# --------------------------------------------------------------------------- #
# Component edits
# --------------------------------------------------------------------------- #
def remove_mpi(block):
    """Remove <process> and <mpiCommunicator> so the component runs without MPI."""
    for tag in MPI_TAGS:
        # Element on its own line: drop the whole line; otherwise drop the element inline.
        block = re.sub(r"^[ \t]*<%s\b[^>]*(?:/>|>.*?</%s>)[ \t]*\n" % (tag, tag), "", block,
                       flags=re.MULTILINE | re.DOTALL)
        block = re.sub(r"<%s\b[^>]*(?:/>|>.*?</%s>)" % (tag, tag), "", block, flags=re.DOTALL)
    return block


def add_mpi(block, processes, communicator):
    """Write <process> and <mpiCommunicator> on their own lines after <library>."""
    m = LIBRARY_RE.search(block)
    if not m:
        print("%s: warning: component without <library>; MPI settings not added."
              % SCRIPT, file=sys.stderr)
        return block
    indent = m.group(1)
    ranks = " ".join(str(i) for i in range(processes))
    insert = ("%s<process>%s</process>\n" % (indent, ranks)
              + "%s<mpiCommunicator>%s</mpiCommunicator>\n" % (indent, communicator))
    return block[:m.end()] + insert + block[m.end():]


def set_block_threads(block, n):
    """Set (or insert after <workingDir>) the threads setting of a component."""
    if THREADS_RE.search(block):
        return THREADS_RE.sub(r"\g<1>%d\g<3>" % n, block)
    m = WORKDIR_RE.search(block)
    if not m:
        print("%s: warning: component without <workingDir>; threads setting not added."
              % SCRIPT, file=sys.stderr)
        return block
    insert = '%s<setting key="threads" value="%d" />\n' % (m.group(1), n)
    return block[:m.end()] + insert + block[m.end():]


def update_component(block, threads=None, processes=None,
                     communicator=DEFAULT_COMMUNICATOR):
    block = remove_mpi(block)
    if processes:
        block = add_mpi(block, processes, communicator)
    if threads is not None:
        block = set_block_threads(block, threads)
    return block


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def get_settings(config):
    """
    Return the thread / MPI settings of each <component> in `config`.

    Returns
    -------
    list of dict
        name, library, threads (str or None), process (str or None),
        mpiCommunicator (str or None)
    """
    text, _ = read_dimr(config)
    res = []
    for block in COMPONENT_RE.findall(text):
        name = NAME_RE.search(block)
        threads = THREADS_RE.search(block)
        res.append({"name": name.group(1) if name else None,
                    "library": tag_value(block, "library"),
                    "threads": threads.group(2) if threads else None,
                    "process": tag_value(block, "process"),
                    "mpiCommunicator": tag_value(block, "mpiCommunicator")})
    return res


def set_threads(config, threads=None, processes=None,
                communicator=DEFAULT_COMMUNICATOR, backup=True, write=True):
    """
    Set the OpenMP threads and MPI processes of every <component> in `config`.

    threads None leaves the threads setting unchanged; processes None (or 0)
    removes <process> / <mpiCommunicator> for a non-MPI run.  Return the
    number of components updated.  The environment is not touched; see
    set_omp_num_threads().
    """
    if threads is not None and threads < 1:
        raise ValueError("threads must be >= 1")
    if processes is not None and processes < 0:
        raise ValueError("processes must be >= 0")
    text, bom = read_dimr(config)
    new_text, count = COMPONENT_RE.subn(
        lambda m: update_component(m.group(0), threads, processes, communicator), text)
    if count == 0:
        raise ValueError("no <component> blocks found in %s" % config)
    if write:
        if backup:
            shutil.copy2(config, config + ".bak")
        write_dimr(config, new_text, bom)
    return count


def set_omp_num_threads(n):
    """
    Set OMP_NUM_THREADS for this process and, on Windows, persistently for the
    user (setx).  A child process cannot change its parent shell's
    environment, so the current command prompt is not affected.
    """
    os.environ["OMP_NUM_THREADS"] = str(n)
    if os.name != "nt":
        print("Run in your shell: export OMP_NUM_THREADS=%d" % n)
        return
    result = subprocess.run(["setx", "OMP_NUM_THREADS", str(n)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print("%s: warning: setx failed: %s" % (SCRIPT, result.stderr.strip()),
              file=sys.stderr)
        return
    print("Set user environment variable OMP_NUM_THREADS = %d (setx)" % n)
    print("Note: applies to NEW command prompts. In this one, run: SET OMP_NUM_THREADS=%d" % n)


def print_settings(config, settings):
    print(config)
    for s in settings:
        nproc = len(s["process"].split()) if s["process"] else 0
        mpi = ("MPI on %d process(es), %s" % (nproc, s["mpiCommunicator"] or "no communicator")
               if nproc else "non-MPI")
        print("  %-16s %-10s threads = %-6s %s"
              % (s["name"] or "?", s["library"] or "?", s["threads"] or "(not set)", mpi))
    omp = os.environ.get("OMP_NUM_THREADS")
    print("  OMP_NUM_THREADS = %s" % (omp if omp is not None else "(not set)"))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Show or change the OpenMP threads and MPI processes of the "
                    "components in a DIMR configuration file (dimr_config.xml).",
        epilog="""
examples:
  %(prog)s                                (show; dimr_config.xml in the current folder)
  %(prog)s -n 8                           (non-MPI run, 8 threads)
  %(prog)s -n 2 -p 4                      (MPI run on 4 processes, 2 threads each)
  %(prog)s -n 2 -p 4 -c MY_COMM           (MPI run with a custom communicator)
  %(prog)s -p 6                           (MPI run on 6 processes, threads unchanged)
  %(prog)s C:/models/PT01 -n 8            (another run folder)
  %(prog)s -n 8 -f other/dimr_config.xml  (another DIMR config file)

Without -p, <process> and <mpiCommunicator> are removed (non-MPI run).
-n also sets OMP_NUM_THREADS (setx) unless --no-env is given; open a new
command prompt for it to take effect.
The config is backed up as <file>.bak unless --no-backup is given.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", nargs="?", default=None,
                    help="DIMR run folder or config file (default: current folder)")
    ap.add_argument("-f", "--file", default=None,
                    help="DIMR config file (same as the positional argument)")
    ap.add_argument("-n", "--threads", type=int, help="number of OpenMP threads")
    ap.add_argument("-p", "--processes", type=int,
                    help="number of MPI processes (omit for a non-MPI run)")
    ap.add_argument("-c", "--communicator", default=DEFAULT_COMMUNICATOR,
                    help="MPI communicator, used with -p (default: %(default)s)")
    ap.add_argument("--no-env", action="store_true",
                    help="do not set the OMP_NUM_THREADS environment variable")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep a <file>.bak copy of the config")
    ap.add_argument("--check", action="store_true",
                    help="show the changes, write nothing")
    args = ap.parse_args(argv)

    if args.config and args.file:
        ap.error("give the DIMR config either as argument or with -f, not both")
    if args.threads is not None and args.threads < 1:
        ap.error("threads must be >= 1")
    if args.processes is not None and args.processes < 1:
        ap.error("processes must be >= 1")
    try:
        config = resolve_dimr(args.file or args.config)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))

    if args.threads is None and args.processes is None:
        print_settings(config, get_settings(config))
        return 0

    try:
        count = set_threads(config, args.threads, args.processes, args.communicator,
                            backup=not args.no_backup, write=not args.check)
    except ValueError as exc:
        print("%s: error: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1

    mode = "MPI on %d process(es)" % args.processes if args.processes else "non-MPI"
    threads = ("threads = %d" % args.threads if args.threads is not None
               else "threads unchanged")
    print("%s, %s, in %d component(s) of %s" % (threads, mode, count, config))
    if args.check:
        print("\nCheck only - nothing written.")
        return 0
    if args.threads is not None and not args.no_env:
        set_omp_num_threads(args.threads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
