#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
simtime - show or change the simulation period of a D-Flow FM (Delft3D FM)
model: the reference date (RefDate), the start time (TStart) and the stop
time (TStop) in the [time] section of the .mdu.

TStart / TStop are offsets from RefDate in Tunit (S, M, H or D).  New start /
stop times can be given as a date-time (20240725 1200, 2024-07-25 12:00) or as
an offset from RefDate (43200, 12h).  StartDateTime / StopDateTime (newer
.mdu versions; they override TStart / TStop when filled in) are kept in step.

The target can be a .mdu, a model input folder or a .dsproj (every .mdu in
its <project>.dsproj_data folder; use -m to pick one when there are several).

Usage
-----
    simtime <project.dsproj | input-folder | model.mdu>        (show)
    simtime MyProject.dsproj --start "2024-07-25 12:00" --stop "2024-07-26 02:00"
    simtime FlowFM.mdu --refdate 20240801                      (shift the period)
    simtime FlowFM.mdu --refdate 20240801 --keep-times         (same period)
    simtime FlowFM.mdu --start 12h --duration 14h
    simtime MyProject.dsproj -i                                (prompt for values)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

from .mduutils import (TUNIT_SECONDS, fmt_duration, fmt_num, get_key,
                       parse_interval, read_lines, resolve_mdu, set_key,
                       write_lines)

SCRIPT = "simtime"
DATE_FMT = "%Y-%m-%d %H:%M:%S"
DT_FORMATS = ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d%H", "%Y%m%d",
              "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H", "%Y-%m-%d",
              "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d %H", "%Y/%m/%d")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def find_mdus(target):
    """Return the .mdu files for a .mdu, a model input folder or a .dsproj."""
    target = os.path.abspath(target)
    if target.lower().endswith(".dsproj"):
        folder = os.path.splitext(target)[0] + ".dsproj_data"
        if not os.path.isdir(folder):
            raise FileNotFoundError("project data folder not found: %s" % folder)
        mdus = sorted(os.path.join(root, f) for root, dirs, files in os.walk(folder)
                      for f in files if f.lower().endswith(".mdu")
                      and not re.search(r"_\d{4}\.mdu$", f, re.IGNORECASE))
        if not mdus:
            raise FileNotFoundError("no .mdu in %s" % folder)
        return mdus
    return [resolve_mdu(target)]


def parse_refdate(text):
    """Return a datetime (midnight) for '20240725' or '2024-07-25'."""
    text = str(text).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("invalid reference date '%s' (e.g. 20240725, 2024-07-25)" % text)


def parse_time(text, refdate):
    """
    Return a datetime for a date-time ('20240725 1200', '2024-07-25 12:00')
    or an offset from `refdate` ('43200' seconds, '12h', '1.5d').
    """
    text = " ".join(str(text).split())
    compact = text.replace(" ", "") if re.fullmatch(r"\d{8}( \d{2,6})?", text) else text
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(compact, fmt)
        except ValueError:
            continue
    try:
        return refdate + timedelta(seconds=parse_interval(text))
    except ValueError:
        raise ValueError("invalid time '%s' (e.g. '2024-07-25 12:00', 20240725120000, "
                         "43200 or 12h after RefDate)" % text)


def _float(raw):
    try:
        return float(raw.split()[0]) if raw else None
    except ValueError:
        return None


def _datetime14(raw):
    try:
        return datetime.strptime(raw.split()[0], "%Y%m%d%H%M%S") if raw else None
    except ValueError:
        return None


def check_period(info):
    """Return a list of warnings for the period in `info`."""
    warnings = []
    start, stop = info["start"], info["stop"]
    if start is None or stop is None:
        return ["cannot work out the simulation period"]
    if stop <= start:
        warnings.append("stop time (%s) is not after start time (%s)"
                        % (stop.strftime(DATE_FMT), start.strftime(DATE_FMT)))
    if info["tstart"] is not None and info["tstart"] < 0:
        warnings.append("TStart is before RefDate")
    dtuser = info["dtuser"]
    span = (stop - start).total_seconds()
    if dtuser and span > 0 and abs(span / dtuser - round(span / dtuser)) > 1e-9:
        warnings.append("the period (%g s) is not a multiple of DtUser (%g s)"
                        % (span, dtuser))
    for key in ("startdatetime", "stopdatetime"):
        if info[key] is not None:
            warnings.append("%s is set and overrides %s"
                            % ("StartDateTime" if key == "startdatetime" else "StopDateTime",
                               "TStart" if key == "startdatetime" else "TStop"))
    return warnings


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def get_period(mdu):
    """
    Return the simulation period of `mdu`.

    Returns
    -------
    dict
        refdate (datetime or None), tunit (str), tstart / tstop (offsets in
        Tunit or None), start / stop (datetime or None; StartDateTime /
        StopDateTime win when set), startdatetime / stopdatetime (datetime or
        None), dtuser (s or None), tzone (raw str or None) and raw (dict of
        the raw .mdu values).
    """
    lines, _ = read_lines(mdu)
    raw = {k: get_key(lines, "time", k) for k in
           ("RefDate", "Tunit", "TStart", "TStop", "StartDateTime",
            "StopDateTime", "DtUser", "Tzone")}
    try:
        refdate = parse_refdate(raw["RefDate"]) if raw["RefDate"] else None
    except ValueError:
        refdate = None
    tunit = (raw["Tunit"] or "S").upper()
    scale = TUNIT_SECONDS.get(tunit)
    tstart, tstop = _float(raw["TStart"]), _float(raw["TStop"])
    sdt, edt = _datetime14(raw["StartDateTime"]), _datetime14(raw["StopDateTime"])

    def absolute(offset, override):
        if override is not None:
            return override
        if refdate is None or offset is None or scale is None:
            return None
        return refdate + timedelta(seconds=offset * scale)

    return dict(refdate=refdate, tunit=tunit, tstart=tstart, tstop=tstop,
                start=absolute(tstart, sdt), stop=absolute(tstop, edt),
                startdatetime=sdt, stopdatetime=edt,
                dtuser=_float(raw["DtUser"]), tzone=raw["Tzone"], raw=raw)


def set_period(mdu, refdate=None, start=None, stop=None, duration=None,
               keep_times=False, backup=True, write=True):
    """
    Change RefDate / TStart / TStop in the [time] section of `mdu`.

    Parameters
    ----------
    refdate : datetime or str, optional
        New reference date ('20240725'); the time of day is dropped.
    start, stop : datetime or str, optional
        New start / stop time: a datetime, a date-time string or an offset
        from the (new) RefDate such as '43200' or '12h'.
    duration : float or str, optional
        Simulation length (seconds or '14h'); sets stop = start + duration.
    keep_times : bool
        With a new RefDate and no new start / stop, keep the absolute start
        and stop times (the offsets change).  By default the offsets are kept,
        so the period moves with RefDate.
    backup, write : bool
        Keep a <file>.bak copy; False for `write` only works out the changes.

    Returns
    -------
    (list of (key, old value, new value), new period dict)
    """
    if stop is not None and duration is not None:
        raise ValueError("give a stop time or a duration, not both")
    lines, encoding = read_lines(mdu)
    cur = get_period(mdu)
    scale = TUNIT_SECONDS.get(cur["tunit"])
    if scale is None:
        raise ValueError("unknown Tunit '%s' in %s" % (cur["tunit"], mdu))
    if cur["refdate"] is None and refdate is None:
        raise ValueError("RefDate missing or invalid in %s; give --refdate" % mdu)

    old_ref = cur["refdate"]
    if isinstance(refdate, str):
        refdate = parse_refdate(refdate)
    new_ref = refdate.replace(hour=0, minute=0, second=0, microsecond=0) \
        if refdate is not None else old_ref

    # start / stop as datetimes
    start_given = start is not None
    if isinstance(start, str):
        start = parse_time(start, new_ref)
    if isinstance(stop, str):
        stop = parse_time(stop, new_ref)
    if start is None:
        start = cur["start"] if keep_times or old_ref is None or cur["tstart"] is None \
            else new_ref + timedelta(seconds=cur["tstart"] * scale)
    if duration is not None:
        seconds = parse_interval(duration) if isinstance(duration, str) else float(duration)
        stop = start + timedelta(seconds=seconds)
    if stop is None:
        if start_given and cur["start"] and cur["stop"]:
            stop = start + (cur["stop"] - cur["start"])   # new start: keep the length
        else:
            stop = cur["stop"] if keep_times or old_ref is None or cur["tstop"] is None \
                else new_ref + timedelta(seconds=cur["tstop"] * scale)
    if start is None or stop is None:
        raise ValueError("cannot work out the start / stop time of %s" % mdu)
    if stop <= start:
        raise ValueError("stop time %s is not after start time %s"
                         % (stop.strftime(DATE_FMT), start.strftime(DATE_FMT)))

    new = [("RefDate", new_ref.strftime("%Y%m%d")),
           ("TStart", fmt_num((start - new_ref).total_seconds() / scale)),
           ("TStop", fmt_num((stop - new_ref).total_seconds() / scale))]
    if cur["raw"]["StartDateTime"]:
        new.append(("StartDateTime", start.strftime("%Y%m%d%H%M%S")))
    if cur["raw"]["StopDateTime"]:
        new.append(("StopDateTime", stop.strftime("%Y%m%d%H%M%S")))

    changes = []
    for key, value in new:
        old = get_key(lines, "time", key)
        if old is None:
            raise ValueError("[time] %s not found in %s" % (key, mdu))
        if old != value:
            set_key(lines, "time", key, value)
            changes.append((key, old, value))
    if write and changes:
        write_lines(mdu, lines, encoding, backup=backup)

    res = dict(cur, refdate=new_ref, start=start, stop=stop,
               tstart=(start - new_ref).total_seconds() / scale,
               tstop=(stop - new_ref).total_seconds() / scale,
               startdatetime=start if cur["raw"]["StartDateTime"] else None,
               stopdatetime=stop if cur["raw"]["StopDateTime"] else None)
    return changes, res


def print_period(mdu, info):
    print(mdu)
    raw = info["raw"]
    ref = info["refdate"].strftime("%Y-%m-%d") if info["refdate"] else "?"
    print("  RefDate    %-16s %s" % (raw["RefDate"], ref))
    print("  Tunit      %s" % info["tunit"])
    for key, when in (("TStart", info["start"]), ("TStop", info["stop"])):
        print("  %-10s %-16s %s" % (key, raw[key],
                                    when.strftime(DATE_FMT) if when else "?"))
    for key in ("StartDateTime", "StopDateTime"):
        if raw[key]:
            print("  %-13s %s" % (key, raw[key]))
    if info["start"] and info["stop"]:
        print("  Duration   %s" % fmt_duration((info["stop"] - info["start"]).total_seconds()))
    if raw["Tzone"] and _float(raw["Tzone"]):
        print("  Tzone      %s (times are in UTC%+g)" % (raw["Tzone"], _float(raw["Tzone"])))
    for warning in check_period(info):
        print("  warning: " + warning)


def _ask(label, current):
    answer = input("  %-28s [%s]: " % (label, current)).strip()
    return answer or None


def ask_period(info):
    """Prompt for a new RefDate / start / stop; return the set_period kwargs."""
    print("\nNew values (Enter keeps the current one; a time may also be an "
          "offset from RefDate, e.g. 12h):")
    ref = _ask("RefDate (yyyymmdd)",
               info["refdate"].strftime("%Y%m%d") if info["refdate"] else "")
    keep_times = False
    if ref:
        keep_times = (input("  Keep the start / stop date-times? [y/N]: ")
                      .strip().lower() in ("y", "yes"))
    start = _ask("Start (yyyy-mm-dd HH:MM:SS)",
                 info["start"].strftime(DATE_FMT) if info["start"] else "")
    stop = _ask("Stop  (or +duration, e.g. +14h)",
                info["stop"].strftime(DATE_FMT) if info["stop"] else "")
    kwargs = dict(refdate=ref, start=start, stop=stop, keep_times=keep_times)
    if stop and stop.startswith("+"):
        kwargs.update(stop=None, duration=stop[1:])
    return kwargs


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Show or change the reference date (RefDate), start time (TStart) "
                    "and stop time (TStop) of a D-Flow FM model.",
        epilog="""
examples:
  %(prog)s MyProject.dsproj                     (show every model of the project)
  %(prog)s FlowFM.mdu --start "2024-07-25 12:00" --stop "2024-07-26 02:00"
  %(prog)s FlowFM.mdu --start 12h --duration 14h   (offset from RefDate + length)
  %(prog)s FlowFM.mdu --refdate 20240801        (move the period with RefDate)
  %(prog)s FlowFM.mdu --refdate 20240801 --keep-times   (same period, new offsets)
  %(prog)s MyProject.dsproj -i                  (prompt for the new values)
  %(prog)s MyProject.dsproj -m TNN_JJ --stop 26h --check

Times: 20240725120000, "20240725 1200", "2024-07-25 12:00[:00]", or an offset
from RefDate in seconds or with a unit (43200, 720m, 12h, 0.5d).
A new start time alone keeps the simulation length.
The .mdu is backed up as <file>.bak unless --no-backup is given.
Close the project in the FM Suite before running.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help=".dsproj, model input folder or .mdu")
    ap.add_argument("-m", "--model-name", metavar="NAME",
                    help="the model (.mdu name or its folder) to use when a project "
                         "has several")
    ap.add_argument("-r", "--refdate", metavar="DATE",
                    help="new reference date, yyyymmdd or yyyy-mm-dd")
    ap.add_argument("-s", "--start", metavar="TIME", help="new start time")
    ap.add_argument("-e", "--stop", metavar="TIME", help="new stop time")
    ap.add_argument("-d", "--duration", metavar="LENGTH",
                    help="simulation length instead of --stop (e.g. 14h, 2d)")
    ap.add_argument("--keep-times", action="store_true",
                    help="with --refdate: keep the start / stop date-times instead "
                         "of the offsets")
    ap.add_argument("-i", "--interactive", action="store_true",
                    help="show the period and prompt for new values")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep a <file>.bak copy of the .mdu")
    ap.add_argument("--check", action="store_true",
                    help="show the changes, write nothing")
    args = ap.parse_args(argv)

    try:
        mdus = find_mdus(args.model)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))
    if args.model_name:
        name = args.model_name.lower()
        mdus = [m for m in mdus if name in (
            os.path.splitext(os.path.basename(m))[0].lower(),
            os.path.basename(os.path.dirname(m)).lower(),
            os.path.basename(os.path.dirname(os.path.dirname(m))).lower())]
        if not mdus:
            ap.error("no model '%s' in %s" % (args.model_name, args.model))

    editing = args.interactive or any(
        v is not None for v in (args.refdate, args.start, args.stop, args.duration))
    if not editing:
        for i, mdu in enumerate(mdus):
            if i:
                print()
            print_period(mdu, get_period(mdu))
        return 0
    if len(mdus) > 1:
        ap.error("%d models in the project; pick one with -m:\n  %s"
                 % (len(mdus), "\n  ".join(mdus)))
    mdu = mdus[0]

    if args.interactive:
        print_period(mdu, get_period(mdu))
        try:
            kwargs = ask_period(get_period(mdu))
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled - nothing written.")
            return 1
    else:
        kwargs = dict(refdate=args.refdate, start=args.start, stop=args.stop,
                      duration=args.duration, keep_times=args.keep_times)

    try:
        changes, res = set_period(mdu, backup=not args.no_backup, write=False, **kwargs)
    except ValueError as exc:
        print("%s: error: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1
    if not changes:
        print("\nNo change.")
        return 0
    print()
    for key, old, value in changes:
        print("  %-13s '%s' -> '%s'" % (key, old, value))
    print("  -> %s  to  %s  (%s)" % (res["start"].strftime(DATE_FMT),
                                     res["stop"].strftime(DATE_FMT),
                                     fmt_duration((res["stop"] - res["start"]).total_seconds())))
    if args.check:
        print("\nCheck only - nothing written.")
        return 0
    if args.interactive and input("\nWrite these changes? [Y/n]: ").strip().lower() \
            in ("n", "no"):
        print("Nothing written.")
        return 0
    set_period(mdu, backup=not args.no_backup, write=True, **kwargs)
    print()
    print_period(mdu, get_period(mdu))
    return 0


if __name__ == "__main__":
    sys.exit(main())
