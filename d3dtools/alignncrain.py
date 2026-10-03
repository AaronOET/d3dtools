#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alignncrain - align the simulation period of a D-Flow FM (Delft3D FM) model
with a NetCDF rainfall file (e.g. one written by ncrain).

In the [time] section of the .mdu it sets
    RefDate = date of the first rainfall time step (yyyymmdd)
    TStart  = first rainfall time w.r.t. RefDate (in Tunit)
    TStop   = last rainfall time w.r.t. RefDate (in Tunit) + pad-end
and, if they are filled in, StartDateTime / StopDateTime (yyyymmddHHMMSS).

By default pad-end is one rainfall time step, so the last rainfall interval
is fully simulated.

It also points the rainfall [Meteo] block of the mdu's ExtForceFileNew to the
NetCDF file (quantity, forcingFile, forcingFileType=netcdf); a new [Meteo]
block is appended if there is none.  The quantity is 'rainfall' for depth
units (mm per time step) and 'rainfall_rate' for rate units (mm/day).

Usage
-----
    alignncrain <input-folder | model.mdu> rain.nc
    alignncrain FlowFM.mdu rain.nc --pad-end 3600
    alignncrain FlowFM.mdu rain.nc --check     (show the changes, write nothing)

Requires: netCDF4      (pip install netCDF4)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

import netCDF4

from .mduutils import (TUNIT_SECONDS, fmt_num, get_key, read_lines, resolve_mdu,
                       set_key, write_lines)
from .ncutils import open_nc

SCRIPT = "alignncrain"


# --------------------------------------------------------------------------- #
# NetCDF
# --------------------------------------------------------------------------- #
def read_nc_times(nc_path, time_var="time"):
    """
    Return (first time, last time, number of steps, time step in s).

    Times are naive datetimes in the file's time zone; the time step is the
    median interval between consecutive time stamps (0 if only one step).
    """
    with open_nc(nc_path) as ds:
        t = ds.variables[time_var]
        times = sorted(netCDF4.num2date(t[:], t.units,
                                        getattr(t, "calendar", "standard"),
                                        only_use_cftime_datetimes=False,
                                        only_use_python_datetimes=True))
    steps = sorted((b - a).total_seconds() for a, b in zip(times, times[1:]))
    dt = steps[len(steps) // 2] if steps else 0.0
    return times[0], times[-1], len(times), dt


def rainfall_quantity(nc_path, rain_var="rainfall"):
    """
    Return (quantity, units): 'rainfall' for depth units (mm per time step),
    'rainfall_rate' for rate units (mm/day, mm/hr, mm s-1, ...).
    """
    with open_nc(nc_path) as ds:
        units = getattr(ds.variables[rain_var], "units", "").strip().lower()
    is_rate = any(s in units for s in ("/", "-1", "day", "hr", "hour", " s"))
    return ("rainfall_rate" if is_rate else "rainfall"), units


# --------------------------------------------------------------------------- #
# External forcing file (.ext)
# --------------------------------------------------------------------------- #
def update_ext_lines(lines, forcing_file, quantity):
    """
    Point the rainfall [Meteo] block of new-format *.ext lines to `forcing_file`.

    The first [Meteo] block with quantity rainfall / rainfall_rate is updated
    (other keys such as interpolationMethod and operand are kept); if there
    is none, a new [Meteo] block is appended.  Return a list of
    (key, old value or None, new value) changes.
    """
    values = {"quantity": quantity, "forcingFile": forcing_file,
              "forcingFileType": "netcdf"}
    eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"

    def key_of(line):
        return line.split("=", 1)[0].strip() if "=" in line else ""

    # locate [Meteo] blocks as (start, end) line ranges
    headers = [i for i, ln in enumerate(lines) if ln.strip().startswith("[")]
    blocks = [(s, e) for s, e in zip(headers, headers[1:] + [len(lines)])
              if lines[s].strip().lower() == "[meteo]"]
    target = None
    for s, e in blocks:
        q = next((ln.split("=", 1)[1].split("#", 1)[0].strip().lower()
                  for ln in lines[s + 1:e] if key_of(ln).lower() == "quantity"), "")
        if q in ("rainfall", "rainfall_rate"):
            target = (s, e)
            break

    changes = []
    if target is None:
        if lines and lines[-1].strip():
            lines.append(eol)
        lines.append("[Meteo]" + eol)
        lines += ["%s=%s%s" % (k, v, eol) for k, v in values.items()]
        lines += ["interpolationMethod=linearSpaceTime" + eol, "operand=O" + eol]
        changes = [("[Meteo]", None, None)] + \
                  [(k, None, v) for k, v in values.items()]
        return changes

    s, e = target
    missing = dict(values)
    for i in range(s + 1, e):
        key = key_of(lines[i])
        for k, v in values.items():
            if key and key.lower() == k.lower():
                old = lines[i].split("=", 1)[1].strip()
                lines[i] = "%s=%s%s" % (key, v, eol)
                missing.pop(k)
                changes.append((key, old, v))
    # add keys that were absent right after the quantity line
    insert_at = next(i for i in range(s + 1, e)
                     if key_of(lines[i]).lower() == "quantity") + 1
    for k, v in missing.items():
        lines.insert(insert_at, "%s=%s%s" % (k, v, eol))
        insert_at += 1
        changes.append((k, None, v))
    return changes


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def align(mdu, nc_path, pad_end=None, time_var="time", rain_var="rainfall",
          quantity=None, update_ext=True, backup=True, write=True):
    """
    Align the [time] section of `mdu` with the NetCDF rainfall file `nc_path`
    and point the rainfall [Meteo] block of its ExtForceFileNew to it.

    Parameters
    ----------
    mdu : str
        D-Flow FM .mdu file.
    nc_path : str
        NetCDF rainfall file.
    pad_end : float, optional
        Seconds to simulate after the last rainfall time stamp
        (default: one rainfall time step).
    time_var, rain_var : str
        Names of the time and rainfall variables in the NetCDF file.
    quantity : {'rainfall', 'rainfall_rate'}, optional
        Ext quantity (default: from the units of `rain_var`).
    update_ext : bool
        Also update the external forcing file (ExtForceFileNew).
    backup : bool
        Keep a copy of each changed file as <file>.bak.
    write : bool
        Write the changes; False only computes them.

    Returns
    -------
    dict
        start, stop, last_rain (datetime), nsteps (int), dt (s), pad_end (s),
        tunit (str), mdu_changes and ext_changes (lists of (key, old, new)),
        ext (path or None), quantity (str or None).
    """
    t0, t_last, n, dt = read_nc_times(nc_path, time_var)
    pad_end = dt if pad_end is None else float(pad_end)
    t1 = t_last + timedelta(seconds=pad_end)

    lines, encoding = read_lines(mdu)
    tunit = (get_key(lines, "time", "Tunit") or "S").upper()
    if tunit not in TUNIT_SECONDS:
        raise ValueError("unknown Tunit '%s' in %s" % (tunit, mdu))
    scale = TUNIT_SECONDS[tunit]
    refdate = datetime(t0.year, t0.month, t0.day)

    new = [("RefDate", refdate.strftime("%Y%m%d")),
           ("TStart", fmt_num((t0 - refdate).total_seconds() / scale)),
           ("TStop", fmt_num((t1 - refdate).total_seconds() / scale))]
    # newer MDU versions: these override TStart/TStop when filled in
    if get_key(lines, "time", "StartDateTime"):
        new.append(("StartDateTime", t0.strftime("%Y%m%d%H%M%S")))
    if get_key(lines, "time", "StopDateTime"):
        new.append(("StopDateTime", t1.strftime("%Y%m%d%H%M%S")))

    mdu_changes = []
    for key, value in new:
        old = set_key(lines, "time", key, value)
        if old is None:
            raise ValueError("[time] %s not found in %s" % (key, mdu))
        mdu_changes.append((key, old, value))
    if write:
        write_lines(mdu, lines, encoding, backup=backup)

    res = dict(start=t0, stop=t1, last_rain=t_last, nsteps=n, dt=dt,
               pad_end=pad_end, tunit=tunit, mdu_changes=mdu_changes,
               ext=None, quantity=None, units=None, ext_changes=[])
    if not update_ext:
        return res
    ext_name = get_key(lines, "external forcing", "ExtForceFileNew")
    if not ext_name:
        return res

    ext = os.path.join(os.path.dirname(os.path.abspath(mdu)), ext_name)
    if not os.path.isfile(ext):
        raise FileNotFoundError("ExtForceFileNew not found: %s" % ext)
    units = None
    if quantity is None:
        quantity, units = rainfall_quantity(nc_path, rain_var)
    try:
        forcing_file = os.path.relpath(os.path.abspath(nc_path),
                                       os.path.dirname(os.path.abspath(ext)))
    except ValueError:  # different drive
        forcing_file = os.path.abspath(nc_path)
    forcing_file = forcing_file.replace("\\", "/")

    ext_lines, ext_encoding = read_lines(ext)
    ext_changes = update_ext_lines(ext_lines, forcing_file, quantity)
    if write:
        write_lines(ext, ext_lines, ext_encoding, backup=backup)
    res.update(ext=ext, quantity=quantity, units=units, ext_changes=ext_changes)
    return res


def print_changes(title, changes):
    print(title)
    for key, old, new in changes:
        if new is None:
            print("  %-15s block appended" % key)
        elif old is None:
            print("  %-15s added '%s'" % (key, new))
        else:
            print("  %-15s '%s' -> '%s'" % (key, old, new))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Align the simulation period (RefDate, TStart, TStop and, if "
                    "filled in, StartDateTime / StopDateTime) of a D-Flow FM .mdu "
                    "with a NetCDF rainfall file, and point the rainfall [Meteo] "
                    "block of its ExtForceFileNew to that file.",
        epilog="""
examples:
  %(prog)s dflowfm rain.nc                  (input folder with one .mdu)
  %(prog)s dflowfm/FlowFM.mdu rain.nc
  %(prog)s FlowFM.mdu rain.nc --pad-end 3600  (simulate 1 h after the last rain stamp)
  %(prog)s FlowFM.mdu rain.nc --quantity rainfall_rate
  %(prog)s FlowFM.mdu rain.nc --no-ext      (only change the [time] section)
  %(prog)s FlowFM.mdu rain.nc --check       (show the changes, write nothing)

Changed files are backed up as <file>.bak unless --no-backup is given.
Close the project in the FM Suite before running.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="model input folder or the .mdu itself")
    ap.add_argument("nc", help="NetCDF rainfall file")
    ap.add_argument("--time-var", default="time",
                    help="time variable in the NetCDF file (default: time)")
    ap.add_argument("--rain-var", default="rainfall",
                    help="rainfall variable in the NetCDF file; its units decide "
                         "the quantity (default: rainfall)")
    ap.add_argument("--pad-end", type=float, default=None,
                    help="extra seconds to simulate after the last rainfall time "
                         "stamp (default: one rainfall time step)")
    ap.add_argument("--quantity", choices=["rainfall", "rainfall_rate"], default=None,
                    help="ext quantity (default: 'rainfall' for depth units such "
                         "as mm, 'rainfall_rate' for rate units such as mm/day)")
    ap.add_argument("--no-ext", action="store_true",
                    help="do not update the external forcing file")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep <file>.bak copies of the changed files")
    ap.add_argument("--check", action="store_true",
                    help="show the changes, write nothing")
    args = ap.parse_args(argv)

    try:
        mdu = resolve_mdu(args.model)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))
    if not os.path.isfile(args.nc):
        ap.error("NetCDF file not found: %s" % args.nc)

    try:
        res = align(mdu, args.nc, pad_end=args.pad_end, time_var=args.time_var,
                    rain_var=args.rain_var, quantity=args.quantity,
                    update_ext=not args.no_ext, backup=not args.no_backup,
                    write=not args.check)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print("%s: error: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1

    fmt = "%Y-%m-%d %H:%M:%S"
    print("Rainfall  : %d steps of %g s, %s -> %s"
          % (res["nsteps"], res["dt"], res["start"].strftime(fmt),
             res["last_rain"].strftime(fmt)))
    print("Simulation: %s -> %s (pad-end %g s)"
          % (res["start"].strftime(fmt), res["stop"].strftime(fmt), res["pad_end"]))
    print_changes("\n%s (Tunit = %s)" % (mdu, res["tunit"]), res["mdu_changes"])
    if args.no_ext:
        pass
    elif res["ext"] is None:
        print("\nNo ExtForceFileNew in the mdu; external forcing file not updated.")
    else:
        if res["units"] is not None:
            print("\nRainfall units '%s' -> quantity = %s" % (res["units"], res["quantity"]))
        print_changes("\n%s" % res["ext"], res["ext_changes"])

    print("\nCheck only - nothing written." if args.check else "\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
