#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
otstep - show or change the output time step of the his file (HisInterval)
and the map file (MapInterval) of a D-Flow FM (Delft3D FM) model.

Both keys live in the [output] section of the .mdu and are given in seconds,
optionally followed by an output start and stop time
(e.g. "HisInterval = 300 0 86400").  When a new interval is set, only the
first value is replaced; an existing start / stop is kept.

Without --his / --map the current settings are printed, together with the
simulation period and the number of output steps it gives.

Usage
-----
    otstep <input-folder | model.mdu>                  (show)
    otstep FlowFM.mdu --his 60 --map 3600              (set, in seconds)
    otstep FlowFM.mdu --his 1m --map 1h                (with a unit: s, m, h, d)
    otstep FlowFM.mdu --map 0                          (0 = no map output)
"""

from __future__ import annotations

import argparse
import sys

from .mduutils import (TUNIT_SECONDS, fmt_duration, fmt_num, get_key,
                       parse_interval, read_lines, resolve_mdu, set_key,
                       write_lines)

SCRIPT = "otstep"
KEYS = {"his": "HisInterval", "map": "MapInterval"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def simulation_seconds(lines):
    """Return the simulation length in seconds, or None if it cannot be read."""
    try:
        scale = TUNIT_SECONDS[(get_key(lines, "time", "Tunit") or "S").upper()]
        return (float(get_key(lines, "time", "TStop")) -
                float(get_key(lines, "time", "TStart"))) * scale
    except (KeyError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def get_steps(mdu):
    """
    Return the output settings of `mdu`.

    Returns
    -------
    dict
        his, map : (interval in s or None, raw value string or None)
        duration : simulation length in s (None if unknown)
    """
    lines, _ = read_lines(mdu)
    res = dict(duration=simulation_seconds(lines))
    for name, key in KEYS.items():
        raw = get_key(lines, "output", key)
        try:
            interval = float(raw.split()[0]) if raw else None
        except ValueError:
            interval = None
        res[name] = (interval, raw)
    return res


def set_steps(mdu, his=None, map=None, backup=True, write=True):
    """
    Set the his and / or map output interval (seconds) of `mdu`.

    Only the first value of HisInterval / MapInterval is replaced; an output
    start / stop time after it is kept.  Return a list of (key, old, new).
    """
    lines, encoding = read_lines(mdu)
    changes = []
    for name, seconds in (("his", his), ("map", map)):
        if seconds is None:
            continue
        key = KEYS[name]
        raw = get_key(lines, "output", key)
        if raw is None:
            raise ValueError("[output] %s not found in %s" % (key, mdu))
        rest = raw.split()[1:]
        new = " ".join([fmt_num(seconds)] + rest)
        set_key(lines, "output", key, new)
        changes.append((key, raw, new))
    if write and changes:
        write_lines(mdu, lines, encoding, backup=backup)
    return changes


def print_steps(mdu, info):
    print(mdu)
    duration = info["duration"]
    if duration is not None:
        print("  %-12s %s" % ("Simulation", fmt_duration(duration)))
    for name, key in KEYS.items():
        interval, raw = info[name]
        if raw is None:
            print("  %-12s (not set)" % key)
            continue
        text = "%-14s" % raw
        if interval is None:
            pass
        elif interval == 0:
            text += " (no %s output)" % name
        else:
            text += " (every %s" % fmt_duration(interval)
            # step count only without an output start / stop window
            if duration and len(raw.split()) == 1:
                text += ", %d output steps" % (int(duration // interval) + 1)
            text += ")"
        print("  %-12s %s" % (key, text))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Show or change the output time step of the his file "
                    "(HisInterval) and the map file (MapInterval) in the [output] "
                    "section of a D-Flow FM .mdu.",
        epilog="""
examples:
  %(prog)s dflowfm                          (show; input folder with one .mdu)
  %(prog)s FlowFM.mdu                       (show)
  %(prog)s FlowFM.mdu --his 60 --map 3600   (set, in seconds)
  %(prog)s FlowFM.mdu --his 1m --map 1h     (with a unit: s, m, h, d)
  %(prog)s FlowFM.mdu --map 0               (0 = no map output)
  %(prog)s FlowFM.mdu --map 30m --check     (show the change, write nothing)

Only the interval is replaced; an output start / stop after it is kept.
The .mdu is backed up as <file>.bak unless --no-backup is given.
Close the project in the FM Suite before running.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="model input folder or the .mdu itself")
    ap.add_argument("--his", default=None, metavar="STEP",
                    help="new his output interval, seconds or with a unit "
                         "(e.g. 300, 5m, 1h)")
    ap.add_argument("--map", default=None, metavar="STEP",
                    help="new map output interval, seconds or with a unit "
                         "(e.g. 3600, 1h, 1d)")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep a <file>.bak copy of the .mdu")
    ap.add_argument("--check", action="store_true",
                    help="show the changes, write nothing")
    args = ap.parse_args(argv)

    try:
        mdu = resolve_mdu(args.model)
        his = None if args.his is None else parse_interval(args.his)
        map_ = None if args.map is None else parse_interval(args.map)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))

    if his is None and map_ is None:
        print_steps(mdu, get_steps(mdu))
        return 0

    try:
        changes = set_steps(mdu, his, map_, backup=not args.no_backup,
                            write=not args.check)
    except ValueError as exc:
        print("%s: error: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1
    for key, old, new in changes:
        print("  %-12s '%s' -> '%s'" % (key, old, new))
    if args.check:
        print("\nCheck only - nothing written.")
    else:
        print()
        print_steps(mdu, get_steps(mdu))
    return 0


if __name__ == "__main__":
    sys.exit(main())
