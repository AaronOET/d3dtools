#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
itstep - show or change the computational time step settings of a D-Flow FM
(Delft3D FM) model: the user time step (DtUser), the initial time step
(DtInit) and the maximum time step (DtMax).

All three keys live in the [time] section of the .mdu and are given in
seconds.  A key that is missing from the .mdu is added to the [time] section
when it is set.

Without --user / --init / --max the current settings are printed, with a
warning for combinations D-Flow FM will not use as written (e.g. DtMax larger
than DtUser, or his / map output intervals that are not a multiple of DtUser).

Usage
-----
    itstep <input-folder | model.mdu>                      (show)
    itstep FlowFM.mdu --user 60 --max 30 --init 1          (set, in seconds)
    itstep FlowFM.mdu --user 1m --max 30s                  (with a unit: s, m, h, d)
"""

from __future__ import annotations

import argparse
import sys

from .mduutils import (fmt_duration, fmt_num, get_key, insert_key,
                       parse_interval, read_lines, resolve_mdu, set_key,
                       write_lines)

SCRIPT = "itstep"
KEYS = {"user": "DtUser", "init": "DtInit", "max": "DtMax"}
LABELS = {"user": "user time step", "init": "initial time step",
          "max": "max time step"}
# D-Flow FM defaults when a key is absent from the .mdu
DEFAULTS = {"user": 300.0, "init": 1.0, "max": 30.0}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _first_float(raw):
    try:
        return float(raw.split()[0]) if raw else None
    except ValueError:
        return None


def check_steps(info):
    """Return a list of warnings for the time step settings in `info`."""
    user, init, mx = (info[k][0] if info[k][0] is not None else DEFAULTS[k]
                      for k in ("user", "init", "max"))
    warnings = []
    if mx > user:
        warnings.append("DtMax (%g s) > DtUser (%g s): the time step is also "
                        "limited by DtUser" % (mx, user))
    if init > mx:
        warnings.append("DtInit (%g s) > DtMax (%g s)" % (init, mx))
    for key in ("HisInterval", "MapInterval"):
        interval = info["output"].get(key)
        if interval and user and abs(interval / user - round(interval / user)) > 1e-9:
            warnings.append("%s (%g s) is not a multiple of DtUser (%g s)"
                            % (key, interval, user))
    return warnings


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def get_steps(mdu):
    """
    Return the time step settings of `mdu`.

    Returns
    -------
    dict
        user, init, max : (value in s or None, raw value string or None)
        output : {'HisInterval': s, 'MapInterval': s} (None if not set)
    """
    lines, _ = read_lines(mdu)
    res = {name: (_first_float(raw), raw)
           for name, raw in ((n, get_key(lines, "time", k)) for n, k in KEYS.items())}
    res["output"] = {k: _first_float(get_key(lines, "output", k))
                     for k in ("HisInterval", "MapInterval")}
    return res


def set_steps(mdu, user=None, init=None, max=None, backup=True, write=True):
    """
    Set DtUser / DtInit / DtMax (seconds) in the [time] section of `mdu`.

    A key missing from the .mdu is added after DtUser (or after the [time]
    header).  Return a list of (key, old value or None, new value).
    """
    lines, encoding = read_lines(mdu)
    changes = []
    for name, seconds in (("user", user), ("init", init), ("max", max)):
        if seconds is None:
            continue
        if seconds <= 0:
            raise ValueError("%s must be > 0" % KEYS[name])
        key, value = KEYS[name], fmt_num(seconds)
        old = set_key(lines, "time", key, value)
        if old is None and not insert_key(lines, "time", key, value, after="DtUser"):
            raise ValueError("no [time] section in %s" % mdu)
        changes.append((key, old, value))
    if write and changes:
        write_lines(mdu, lines, encoding, backup=backup)
    return changes


def print_steps(mdu, info):
    print(mdu)
    for name, key in KEYS.items():
        value, raw = info[name]
        if raw is None:
            text = "(not set, default %g s)" % DEFAULTS[name]
        elif value is None:
            text = raw
        else:
            text = "%-10s (%s)" % (raw, fmt_duration(value))
        print("  %-8s %-18s %s" % (key, LABELS[name], text))
    for warning in check_steps(info):
        print("  warning: " + warning)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Show or change the user time step (DtUser), the initial time "
                    "step (DtInit) and the maximum time step (DtMax) in the [time] "
                    "section of a D-Flow FM .mdu.",
        epilog="""
examples:
  %(prog)s dflowfm                              (show; input folder with one .mdu)
  %(prog)s FlowFM.mdu                           (show)
  %(prog)s FlowFM.mdu --user 60 --max 30 --init 1  (set, in seconds)
  %(prog)s FlowFM.mdu --user 1m --max 30s       (with a unit: s, m, h, d)
  %(prog)s FlowFM.mdu --max 10 --check          (show the change, write nothing)

A key missing from the .mdu is added to the [time] section.
The .mdu is backed up as <file>.bak unless --no-backup is given.
Close the project in the FM Suite before running.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="model input folder or the .mdu itself")
    ap.add_argument("--user", default=None, metavar="STEP",
                    help="new user time step DtUser, seconds or with a unit "
                         "(e.g. 60, 1m)")
    ap.add_argument("--init", default=None, metavar="STEP",
                    help="new initial time step DtInit, seconds or with a unit "
                         "(e.g. 1, 1s)")
    ap.add_argument("--max", default=None, metavar="STEP",
                    help="new maximum time step DtMax, seconds or with a unit "
                         "(e.g. 30, 30s)")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep a <file>.bak copy of the .mdu")
    ap.add_argument("--check", action="store_true",
                    help="show the changes, write nothing")
    args = ap.parse_args(argv)

    try:
        mdu = resolve_mdu(args.model)
        new = {name: None if getattr(args, name) is None
               else parse_interval(getattr(args, name)) for name in KEYS}
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))

    if all(v is None for v in new.values()):
        print_steps(mdu, get_steps(mdu))
        return 0

    try:
        changes = set_steps(mdu, new["user"], new["init"], new["max"],
                            backup=not args.no_backup, write=not args.check)
    except ValueError as exc:
        print("%s: error: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1
    for key, old, value in changes:
        if old is None:
            print("  %-8s added '%s'" % (key, value))
        else:
            print("  %-8s '%s' -> '%s'" % (key, old, value))
    if args.check:
        print("\nCheck only - nothing written.")
    else:
        print()
        print_steps(mdu, get_steps(mdu))
    return 0


if __name__ == "__main__":
    sys.exit(main())
