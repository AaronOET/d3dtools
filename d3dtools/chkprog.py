#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chkprog - check the progress of a running D-Flow FM (Delft3D FM, 1D2D)
simulation, serial or MPI.

The target can be a DIMR run folder, a dimr_config.xml, a model folder or a
.mdu.  The simulation period comes from the [time] section of the .mdu
(RefDate, Tunit, TStart, TStop or StartDateTime, StopDateTime); the progress
from the statistics lines that D-Flow FM writes to the .dia file every
StatsInterval:

    Sim. time done   Sim. time left   Real time used   Real time left Steps left Complete% ...
         0d  6:00:00      0d 18:00:00      0d  0:12:31      0d  0:37:33      2160    25.0%   30.0

When the .dia has no statistics line yet (or StatsInterval = 0), the last time
written to the his / map NetCDF output is used instead, and the wall-clock
time / ETA are estimated from the "Modelinit finished at" line.  D-Flow FM
buffers its .dia output, so "Last update" also looks at the NetCDF files.
In MPI mode every rank is listed; the ranks run in lockstep, so the overall
progress is that of the most advanced rank (the others may just lag in
flushing).

Usage
-----
    chkprog                            (run folder = current folder, mode auto-detected)
    chkprog --mode serial              (non-MPI run: reads <model>.dia)
    chkprog --mode mpi -n 6            (MPI run: reads <model>_NNNN.dia of 6 ranks)
    chkprog -w                         (refresh every 60 s until the run ends)
    chkprog -w 10 path/to/run          (refresh every 10 s, another run folder)
    chkprog path/to/FlowFM.mdu         (a model without DIMR config)
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .dimrutils import COMPONENT_RE, DIMR_CONFIG_NAMES, tag_value
from .mduutils import TUNIT_SECONDS

SCRIPT = "chkprog"
DUR = r"(\d+)d\s+(\d+):(\d{2}):(\d{2})"
STATS_RE = re.compile(r"%s\s+%s\s+%s\s+%s\s+(\S+)\s+([\d.]+)\s*%%\s*([-+\d.eE]+)?"
                      % (DUR, DUR, DUR, DUR))
FINISHED_RE = re.compile(r"computation finished|total computation time", re.IGNORECASE)
STARTED_RE = re.compile(r"(?:Computation started|Modelinit finished)\s+at:\s*"
                        r"(\d{1,2}:\d{2}:\d{2}),\s*(\d{2}-\d{2}-\d{4})", re.IGNORECASE)
ERROR_RE = re.compile(r"^\*\*\s*(ERROR|FATAL)", re.IGNORECASE)
FULL_READ = 16 * 1024 * 1024        # .dia files up to this size are read completely
TAIL_READ = 512 * 1024              # otherwise only their last part


# --------------------------------------------------------------------------- #
# Model / DIMR config
# --------------------------------------------------------------------------- #
def find_dimr(folder: Path) -> Path | None:
    for name in DIMR_CONFIG_NAMES:
        if (folder / name).is_file():
            return folder / name
    found = [p for p in sorted(folder.glob("*.xml"))
             if b"<dimrConfig" in p.read_bytes()[:4096]]
    return found[0] if len(found) == 1 else None


def resolve_model(target: str | None) -> tuple[Path, int | None]:
    """Return (.mdu path, number of MPI ranks in the DIMR config or None)."""
    path = Path(target or ".").resolve()
    if path.is_file() and path.suffix.lower() == ".mdu":
        return path, None

    dimr = path if path.is_file() else find_dimr(path) if path.is_dir() else None
    if dimr:
        text = dimr.read_text(encoding="utf-8-sig", errors="replace")
        for block in COMPONENT_RE.findall(text):
            if tag_value(block, "library") != "dflowfm" or not tag_value(block, "inputFile"):
                continue
            workdir = dimr.parent / (tag_value(block, "workingDir") or ".")
            process = tag_value(block, "process")
            ranks = len(process.split()) if process else None
            return (workdir / tag_value(block, "inputFile")).resolve(), ranks
        raise ValueError(f"no dflowfm component in {dimr}")

    if path.is_dir():
        mdus = [p for p in sorted(path.glob("*.mdu")) if not re.search(r"_\d{4}$", p.stem)]
        if len(mdus) == 1:
            return mdus[0], None
        raise ValueError(f"no DIMR config and {len(mdus)} .mdu files in {path}")
    raise FileNotFoundError(f"not found: {path}")


def mdu_values(mdu: Path) -> dict[str, str]:
    """Return {section.key (lower case): value} of an MDU file."""
    values, section = {}, ""
    for line in mdu.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
        elif "=" in line:
            key, value = line.split("=", 1)
            values[f"{section}.{key.strip().lower()}"] = value.strip()
    return values


def sim_period(values: dict[str, str]) -> tuple[datetime, datetime]:
    """Return (start, stop) of the simulation from the [time] section."""
    def stamp(text: str) -> datetime:
        return datetime.strptime(text.ljust(14, "0")[:14], "%Y%m%d%H%M%S")

    start, stop = values.get("time.startdatetime"), values.get("time.stopdatetime")
    if start and stop:
        return stamp(start), stamp(stop)
    ref = stamp(values.get("time.refdate", "").strip() or "20000101")
    scale = TUNIT_SECONDS.get((values.get("time.tunit") or "S").strip().upper()[:1], 1)
    tstart = float((values.get("time.tstart") or "0").split()[0]) * scale
    tstop = float((values.get("time.tstop") or "0").split()[0]) * scale
    return ref + timedelta(seconds=tstart), ref + timedelta(seconds=tstop)


def output_dir(mdu: Path, values: dict[str, str]) -> Path:
    out = (values.get("output.outputdir") or "").strip()
    return (mdu.parent / out) if out else mdu.parent / f"DFM_OUTPUT_{mdu.stem}"


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #
def seconds(groups) -> int:
    d, h, m, s = (int(g) for g in groups)
    return ((d * 24 + h) * 60 + m) * 60 + s


def read_text(path: Path) -> str:
    with path.open("rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - TAIL_READ) if size > FULL_READ else 0)
        return fh.read().decode("latin-1")


def nc_last_time(paths: list[Path], ref: datetime) -> tuple[float, Path] | None:
    """Return (seconds since `ref`, file) of the last time in the first readable NetCDF file."""
    os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
    try:
        import netCDF4
        from .ncutils import open_nc
    except ImportError:
        return None
    for path in paths:
        if not path.is_file():
            continue
        try:
            with open_nc(str(path)) as ds:
                var = ds.variables["time"]
                if var.shape[0] == 0:
                    continue
                last = netCDF4.num2date(var[-1], var.units, only_use_cftime_datetimes=False,
                                        only_use_python_datetimes=True)
                return (last - ref).total_seconds(), path
        except Exception:
            continue
    return None


def dia_status(dia: Path, start: datetime, stop: datetime, nc_files: list[Path]) -> dict:
    """Progress of one D-Flow FM process from its .dia (or NetCDF output)."""
    total = (stop - start).total_seconds()
    info = {"dia": dia, "state": "missing", "percent": None, "done": None, "left": None,
            "used": None, "remaining": None, "dt": None, "errors": [], "mtime": None,
            "source": None, "estimated": False, "observed": None}
    if not dia.is_file():
        return info

    text = read_text(dia)
    mtimes = [p.stat().st_mtime for p in [dia] + nc_files if p.is_file()]
    info["mtime"] = datetime.fromtimestamp(max(mtimes))
    info["errors"] = [ln.strip() for ln in text.splitlines() if ERROR_RE.match(ln)]
    stats = STATS_RE.findall(text)
    if stats:
        g = stats[-1]
        info.update(done=seconds(g[0:4]), left=seconds(g[4:8]), used=seconds(g[8:12]),
                    remaining=seconds(g[12:16]), percent=float(g[17]), source="dia",
                    observed=datetime.fromtimestamp(dia.stat().st_mtime))
        info["dt"] = float(g[18]) if g[18] else None
    else:
        last = nc_last_time(nc_files, start)
        if last is not None:
            done, path = last
            info.update(done=done, left=max(total - done, 0), source=path.name,
                        percent=100.0 * done / total if total > 0 else None)
            started = STARTED_RE.findall(text)
            if started and done > 0:
                t0 = datetime.strptime(" ".join(started[-1]), "%H:%M:%S %d-%m-%Y")
                observed = datetime.fromtimestamp(path.stat().st_mtime)
                used = (observed - t0).total_seconds()
                if used > 0:
                    info.update(used=used, remaining=info["left"] * used / done,
                                estimated=True, observed=observed)

    if FINISHED_RE.search(text):
        info.update(state="finished", percent=100.0, done=total, left=0, remaining=0)
    elif info["errors"] and re.search(r"stop|abort|fatal|exit", info["errors"][-1], re.I):
        info["state"] = "error"
    elif info["percent"] is None:
        info["state"] = "initialising"
    else:
        info["state"] = "running"
    return info


def running_processes() -> dict[str, int]:
    """Count the dimr / dflowfm / mpiexec processes on this machine (best effort)."""
    names = ("dimr", "dflowfm", "mpiexec", "hydra_pmi_proxy")
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True,
                                 text=True, timeout=15).stdout
            images = [ln.split('","')[0].strip('"').lower() for ln in out.splitlines()]
        else:
            out = subprocess.run(["ps", "-eo", "comm"], capture_output=True,
                                 text=True, timeout=15).stdout
            images = [Path(ln.strip()).name.lower() for ln in out.splitlines()]
    except (OSError, subprocess.SubprocessError):
        return {}
    counts = {}
    for image in images:
        stem = image.rsplit(".", 1)[0] if image.endswith(".exe") else image
        if stem in names:
            counts[stem] = counts.get(stem, 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def hms(sec: float | None) -> str:
    if sec is None:
        return "-"
    sec = int(round(sec))
    d, rem = divmod(sec, 86400)
    return f"{d}d {rem // 3600:02d}:{rem % 3600 // 60:02d}:{rem % 60:02d}"


def wall_clock(p: dict) -> tuple[float | None, float | None, datetime | None]:
    """
    Return (used, left, ETA) of the wall-clock time, brought forward to now.

    The .dia statistics line / NetCDF output time are only as recent as the
    last flush, so the time since then is added to 'used' and taken off 'left'.
    """
    used, left, observed = p["used"], p["remaining"], p["observed"]
    if used is None or observed is None:
        return used, left, None
    eta = observed + timedelta(seconds=left or 0)
    if p["state"] == "finished":
        return used, 0, None
    lag = max((datetime.now() - observed).total_seconds(), 0)
    return used + lag, max((left or 0) - lag, 0), eta


def ago(stamp: datetime | None) -> str:
    return "-" if stamp is None else hms((datetime.now() - stamp).total_seconds()) + " ago"


def bar(percent: float | None, width: int = 40) -> str:
    p = min(max(percent or 0.0, 0.0), 100.0)
    filled = int(round(width * p / 100))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def detect_mode(out: Path, model: str, ranks: int | None) -> str:
    if ranks and ranks > 1:
        return "mpi"
    serial = out / f"{model}.dia"
    rank_dias = sorted(out.glob(f"{model}_[0-9][0-9][0-9][0-9].dia"))
    if rank_dias and (not serial.is_file() or
                      max(p.stat().st_mtime for p in rank_dias) >= serial.stat().st_mtime):
        return "mpi"
    return "serial"


def collect(mdu: Path, mode: str, nprocs: int | None, dimr_ranks: int | None) -> dict:
    values = mdu_values(mdu)
    start, stop = sim_period(values)
    out = output_dir(mdu, values)
    model = mdu.stem
    if mode == "auto":
        mode = detect_mode(out, model, dimr_ranks)

    procs = []
    if mode == "serial":
        # recent versions write the .dia to the output folder, older ones next to the .mdu
        cands = [p for p in (out / f"{model}.dia", mdu.parent / f"{model}.dia") if p.is_file()]
        dia = max(cands, key=lambda p: p.stat().st_mtime) if cands else out / f"{model}.dia"
        nc = [out / f"{model}_his.nc", out / f"{model}_map.nc"]
        procs.append(("-", dia_status(dia, start, stop, nc)))
    else:
        n = nprocs or dimr_ranks or len(list(out.glob(f"{model}_[0-9][0-9][0-9][0-9].dia"))) \
            or len(list(mdu.parent.glob(f"{model}_[0-9][0-9][0-9][0-9].mdu")))
        if not n:
            raise ValueError("cannot tell the number of MPI ranks; give it with -n")
        for rank in range(n):
            tag = f"{model}_{rank:04d}"
            dia = out / f"{tag}.dia"
            if not dia.is_file() and (mdu.parent / f"{tag}.dia").is_file():
                dia = mdu.parent / f"{tag}.dia"
            nc = [out / f"{tag}_his.nc", out / f"{tag}_map.nc"]
            procs.append((f"{rank:04d}", dia_status(dia, start, stop, nc)))
    return {"mdu": mdu, "mode": mode, "start": start, "stop": stop, "out": out, "procs": procs}


def overall(procs: list[dict]) -> dict:
    """The run as a whole: errors first, then the most advanced rank."""
    states = [p["state"] for p in procs]
    with_progress = [p for p in procs if p["percent"] is not None]
    ref = max(with_progress, key=lambda p: p["percent"]) if with_progress else procs[0]
    if "error" in states:
        state = "error"
    elif all(s == "finished" for s in states):
        state = "finished"
    elif all(s == "missing" for s in states):
        state = "not started"
    elif with_progress:
        state = "running"
    else:
        state = "initialising"
    mtimes = [p["mtime"] for p in procs if p["mtime"]]
    return dict(ref, state=state, mtime=max(mtimes) if mtimes else None)


def report(run: dict, stale_min: float, check_procs: bool) -> str:
    procs = [p for _, p in run["procs"]]
    tot = overall(procs)
    start, stop = run["start"], run["stop"]
    n = len(procs)
    lines = [
        f"Checked    : {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Model      : {run['mdu']}",
        f"Mode       : {'MPI, %d rank(s)' % n if run['mode'] == 'mpi' else 'serial (non-MPI)'}",
        f"Period     : {start:%Y-%m-%d %H:%M:%S} -> {stop:%Y-%m-%d %H:%M:%S}"
        f"  ({hms((stop - start).total_seconds())})",
    ]

    state = tot["state"].upper()
    if tot["state"] in ("running", "initialising") and tot["mtime"] and \
            (datetime.now() - tot["mtime"]).total_seconds() > stale_min * 60:
        state += f"  (WARNING: no output update for over {stale_min:g} min - stalled or crashed?)"
    lines.append(f"Status     : {state}")

    if tot["percent"] is not None:
        lines.append(f"Progress   : {bar(tot['percent'])} {tot['percent']:5.1f} %")
        lines.append(f"Sim time   : {start + timedelta(seconds=tot['done']):%Y-%m-%d %H:%M:%S}"
                     f"   (done {hms(tot['done'])}, left {hms(tot['left'])})")
    if tot["used"] is not None:
        used, left, eta = wall_clock(tot)
        lines.append(f"Wall clock : used {hms(used)}, left {hms(left)}"
                     + (f", ETA {eta:%Y-%m-%d %H:%M}" if eta else "")
                     + ("  (estimated)" if tot["estimated"] else ""))
        # speed from the values as observed, so it matches the sim time shown
        if tot["used"] and tot["done"]:
            lines.append(f"Speed      : {tot['done'] / tot['used']:.1f}x real time")
    if tot["dt"] is not None:
        lines.append(f"Time step  : {tot['dt']:g} s (interval-averaged)")
    if tot["source"] and tot["source"] != "dia":
        lines.append(f"Source     : last output time in {tot['source']} "
                     "(no statistics line in the .dia yet)")
    lines.append(f"Last update: {ago(tot['mtime'])}")

    if check_procs:
        counts = running_processes()
        if counts:
            lines.append("Processes  : " + ", ".join(f"{k} x{v}" for k, v in sorted(counts.items())))
        elif tot["state"] not in ("finished",):
            lines.append("Processes  : no dimr / dflowfm process found on this machine")

    if run["mode"] == "mpi":
        lines += ["", f"{'Rank':<6}{'Status':<14}{'Complete':>9}  {'Sim done':>12}  "
                      f"{'Real left':>12}  {'dt (s)':>8}  {'Errors':>6}  Last update"]
        for rank, p in run["procs"]:
            pct = f"{p['percent']:.1f} %" if p["percent"] is not None else "-"
            dt = f"{p['dt']:g}" if p["dt"] is not None else "-"
            lines.append(f"{rank:<6}{p['state']:<14}{pct:>9}  {hms(p['done']):>12}  "
                         f"{hms(wall_clock(p)[1]):>12}  {dt:>8}  {len(p['errors']):>6}  "
                         f"{ago(p['mtime'])}")

    for rank, p in run["procs"]:
        if p["errors"]:
            where = p["dia"].name
            lines.append(f"\n{len(p['errors'])} error line(s) in {where}, last: {p['errors'][-1]}")
        if p["state"] == "missing":
            lines.append(f"\nNot found: {p['dia']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def check(target: str | None = None, mode: str = "auto", nprocs: int | None = None) -> dict:
    """
    Return the progress of the run at `target` (DIMR run folder, dimr_config.xml,
    model folder or .mdu; default: current folder).

    The dict has mdu, mode ('serial' / 'mpi'), start, stop, out (output folder),
    procs [(rank, info), ...] and overall (the run as a whole: state, percent,
    done, left, used, remaining, ...).  Format it with report().
    """
    mdu, dimr_ranks = resolve_model(target)
    if not mdu.is_file():
        raise FileNotFoundError(f"mdu not found: {mdu}")
    if nprocs and mode == "auto":
        mode = "mpi"
    run = collect(mdu, mode, nprocs, dimr_ranks)
    run["overall"] = overall([p for _, p in run["procs"]])
    return run


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Check the progress of a D-Flow FM (1D2D) run, serial or MPI.",
        epilog="""
examples:
  %(prog)s                             (run folder = current folder, mode auto-detected)
  %(prog)s --mode serial               (non-MPI run: reads <model>.dia)
  %(prog)s --mode mpi                  (MPI run: reads <model>_NNNN.dia of every rank)
  %(prog)s --mode mpi -n 6             (MPI run with 6 ranks; default: from dimr_config.xml)
  %(prog)s -w                          (refresh every 60 s until the run ends)
  %(prog)s -w 10 C:/models/PT01        (refresh every 10 s, another run folder)
  %(prog)s path/to/FlowFM.mdu          (a model without DIMR config)

Exit code 1 when the run stopped with an error.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default=None,
                    help="DIMR run folder, dimr_config.xml, model folder or .mdu "
                         "(default: current folder)")
    ap.add_argument("-m", "--mode", choices=("auto", "serial", "mpi"), default="auto",
                    help="serial (non-MPI) or mpi run; auto: MPI when dimr_config.xml has "
                         "more than one <process>, or the <model>_NNNN.dia files are newest")
    ap.add_argument("-n", "--nprocs", type=int, default=None,
                    help="number of MPI ranks (default: from dimr_config.xml or the "
                         "<model>_NNNN.dia files)")
    ap.add_argument("-w", "--watch", type=float, nargs="?", const=60.0, default=None,
                    metavar="SEC", help="refresh every SEC seconds (default 60) until "
                                        "the run finishes or fails; Ctrl+C to stop")
    ap.add_argument("--stale", type=float, default=15.0, metavar="MIN",
                    help="warn when the .dia and his/map output have not changed for "
                         "MIN minutes (default 15)")
    ap.add_argument("--no-procs", action="store_true",
                    help="do not look for running dimr / dflowfm processes")
    args = ap.parse_args(argv)
    if args.nprocs is not None and args.nprocs < 1:
        ap.error("--nprocs must be >= 1")
    if args.nprocs and args.mode == "auto":
        args.mode = "mpi"

    try:
        mdu, dimr_ranks = resolve_model(args.target)
        if not mdu.is_file():
            raise FileNotFoundError(f"mdu not found: {mdu}")
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))
    if args.mode == "mpi" and dimr_ranks is None and args.nprocs is None:
        print("note: no <process> in the DIMR config; ranks taken from the "
              "<model>_NNNN files", file=sys.stderr)

    try:
        while True:
            try:
                run = collect(mdu, args.mode, args.nprocs, dimr_ranks)
            except ValueError as exc:
                print(f"{SCRIPT}: error: {exc}", file=sys.stderr)
                return 1
            text = report(run, args.stale, not args.no_procs)
            if args.mode == "serial" and detect_mode(run["out"], mdu.stem, None) == "mpi":
                text += ("\n\nnote: newer <model>_NNNN.dia files found - is this an MPI run? "
                         "Try --mode mpi.")
            if args.watch:
                os.system("cls" if os.name == "nt" else "clear")
            print(text)
            state = overall([p for _, p in run["procs"]])["state"]
            if not args.watch or state in ("finished", "error"):
                return 1 if state == "error" else 0
            print(f"\nRefreshing every {args.watch:g} s - Ctrl+C to stop.")
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
