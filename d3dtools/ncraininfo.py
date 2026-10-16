#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ncraininfo - show the contents of a NetCDF rainfall file (e.g. from ncrain):
the start time, end time and time step, the grid, and how the rainfall is
distributed in time and space.

Printed:
  * time      start, end, time step (and irregular steps, if any), duration
  * grid      size, cell size, extent, CRS
  * per step  domain mean / max rainfall and wet-cell share, with a bar chart
              of the mean (hyetograph)
  * total     domain mean, min and max of the accumulated rainfall, and where
              the maximum falls (or that it is the same in every cell, as for a
              design storm)

Rainfall in depth units (mm per time step) is summed as is; rate units
(mm/hr, mm/day, mm/s) are multiplied by the time step to get the totals.

With --plot a figure is shown (or saved with -o): the hyetograph (domain mean
and max per step) and a map of the total rainfall, or of the peak time step
when the total is the same in every cell.

Usage
-----
    ncraininfo rain.nc
    ncraininfo rain.nc --plot                  (show the figure)
    ncraininfo rain.nc --plot -o rain.png      (save the figure)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta

import numpy as np

from .ncutils import nc, open_nc

SCRIPT = "ncraininfo"
BAR_WIDTH = 40
# seconds per unit of time in a rate unit (mm/hr, mm day-1, ...)
RATE_SECONDS = (("/s", 1), (" s-1", 1), ("/sec", 1), ("/min", 60), ("min-1", 60),
                ("/h", 3600), ("hr", 3600), ("hour", 3600), ("h-1", 3600),
                ("/d", 86400), ("day", 86400), ("d-1", 86400))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def rate_seconds(units):
    """Seconds per time unit for a rate unit (mm/hr -> 3600), None for a depth unit."""
    u = " " + (units or "").strip().lower()
    for token, seconds in RATE_SECONDS:
        if token in u:
            return seconds
    return None


def fmt_duration(seconds):
    seconds = int(round(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = (["%dd" % d] if d else []) + (["%dh" % h] if h else []) + \
            (["%dm" % m] if m else []) + (["%ds" % s] if s else [])
    return " ".join(parts) or "0s"


def find_var(ds, name, candidates):
    """Return `name` if given (must exist), else the first of `candidates` in `ds`."""
    if name:
        if name not in ds.variables:
            raise KeyError("variable '%s' not in the file (variables: %s)"
                           % (name, ", ".join(ds.variables)))
        return name
    for cand in candidates:
        if cand in ds.variables:
            return cand
    raise KeyError("none of %s in the file (variables: %s); give it with --var / --time-var"
                   % (", ".join(candidates), ", ".join(ds.variables)))


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def get_info(path, var=None, time_var=None):
    """
    Read a NetCDF rainfall file and return a dict with
    path, var, units, rate (seconds per rate unit or None),
    times (list of datetime), time_units, dt (median step, s), steps (unique steps, s),
    x, y (1D coordinates or None), crs (str or None),
    rain (masked array time x y x x), depth (rain as depth per step),
    mean, max, wet (per step: domain mean, max, wet-cell fraction),
    total (2D accumulated depth), total_mean, total_min, total_max,
    total_uniform (same in every cell),
    total_max_xy (None when uniform or without 1D x / y).
    """
    with open_nc(path) as ds:
        var = find_var(ds, var, ("rainfall", "rain", "precipitation", "precip", "pr"))
        time_var = find_var(ds, time_var, ("time", "t"))
        t = ds.variables[time_var]
        times = list(nc.num2date(t[:], t.units, getattr(t, "calendar", "standard"),
                                 only_use_cftime_datetimes=False,
                                 only_use_python_datetimes=True))
        rv = ds.variables[var]
        if rv.dimensions[0] != t.dimensions[0]:
            raise ValueError("'%s' does not have time (%s) as its first dimension"
                             % (var, t.dimensions[0]))
        rain = np.ma.masked_invalid(np.ma.asarray(rv[:], dtype=float))
        units = getattr(rv, "units", "")
        x = ds.variables["x"][:] if "x" in ds.variables and ds.variables["x"].ndim == 1 else None
        y = ds.variables["y"][:] if "y" in ds.variables and ds.variables["y"].ndim == 1 else None
        crs = None
        gm = getattr(rv, "grid_mapping", None)
        if gm and gm in ds.variables:
            gv = ds.variables[gm]
            crs = getattr(gv, "epsg_code", None) or getattr(gv, "long_name", None)
        time_units = t.units

    deltas = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    dt = float(np.median(deltas)) if deltas else 0.0
    rate = rate_seconds(units)
    depth = rain * (dt / rate) if rate and dt else rain

    n = rain.shape[0]
    flat = rain.reshape(n, -1)
    mean = flat.mean(axis=1).filled(np.nan)
    mx = flat.max(axis=1).filled(np.nan)
    valid = flat.count(axis=1)
    wet = np.where(valid > 0, (flat > 0).sum(axis=1).filled(0) / np.maximum(valid, 1), np.nan)

    total = depth.sum(axis=0)
    total_max_xy = None
    tmin = float(total.min()) if total.count() else float("nan")
    tmax = float(total.max()) if total.count() else float("nan")
    uniform = bool(total.count()) and tmax - tmin <= 1e-4 * max(abs(tmax), 1.0)
    if total.count() and not uniform:
        iy_ix = np.unravel_index(int(total.argmax()), total.shape)
        if x is not None and y is not None and total.ndim == 2:
            total_max_xy = (float(x[iy_ix[1]]), float(y[iy_ix[0]]))
    return {
        "path": os.path.abspath(path), "var": var, "units": units, "rate": rate,
        "times": times, "time_units": time_units, "dt": dt,
        "steps": sorted(set(round(d, 3) for d in deltas)),
        "x": x, "y": y, "crs": crs, "rain": rain, "depth": depth,
        "mean": mean, "max": mx, "wet": wet, "total": total,
        "total_mean": float(total.mean()) if total.count() else float("nan"),
        "total_min": tmin, "total_max": tmax, "total_uniform": uniform,
        "total_max_xy": total_max_xy,
    }


def format_info(info, bars=True):
    """Return the text report for an info dict from get_info()."""
    times, dt, units = info["times"], info["dt"], info["units"] or "?"
    depth_units = units.split("/")[0].split()[0] if info["rate"] else units
    lines = [
        "File       : %s" % info["path"],
        "Variable   : %s  [%s]%s" % (info["var"], units,
                                     "  (rate; totals = rate x time step)" if info["rate"] else ""),
        "Time units : %s" % info["time_units"],
        "Start      : %s" % times[0],
        "End        : %s" % times[-1],
    ]
    if len(times) > 1:
        lines.append("Time step  : %s (%g s)" % (fmt_duration(dt), dt))
        if len(info["steps"]) > 1:
            lines.append("             WARNING: irregular time steps: %s"
                         % ", ".join(fmt_duration(s) for s in info["steps"]))
    lines.append("Steps      : %d  (duration %s)"
                 % (len(times), fmt_duration((times[-1] - times[0]).total_seconds())))

    rain = info["rain"]
    grid = " x ".join(str(s) for s in rain.shape[1:])
    if info["x"] is not None and info["y"] is not None and len(info["x"]) > 1 and len(info["y"]) > 1:
        x, y = info["x"], info["y"]
        dx, dy = abs(float(x[1] - x[0])), abs(float(y[1] - y[0]))
        lines.append("Grid       : %s cells (y x x), cell %g x %g" % (grid, dx, dy))
        lines.append("Extent     : x %.1f .. %.1f, y %.1f .. %.1f"
                     % (float(x.min()), float(x.max()), float(y.min()), float(y.max())))
    else:
        lines.append("Grid       : %s cells" % grid)
    if info["crs"]:
        lines.append("CRS        : %s" % info["crs"])
    masked = int(np.ma.count_masked(rain))
    if masked:
        lines.append("No data    : %d value(s) masked" % masked)

    lines += ["", "Rainfall per time step [%s]:" % units,
              "  %-19s  %9s  %9s  %6s  %s" % ("Time", "Mean", "Max", "Wet %", "Mean" if bars else "")]
    peak = np.nanmax(info["mean"]) if np.isfinite(info["mean"]).any() else 0
    for i, t in enumerate(times):
        m, mx, w = info["mean"][i], info["max"][i], info["wet"][i]
        bar = ""
        if bars and peak > 0 and np.isfinite(m):
            bar = "#" * int(round(BAR_WIDTH * m / peak))
        lines.append("  %-19s  %9.2f  %9.2f  %6.1f  %s"
                     % (t.strftime("%Y-%m-%d %H:%M:%S"), m, mx, 100 * w if np.isfinite(w) else np.nan, bar))

    imax = int(np.nanargmax(info["mean"])) if np.isfinite(info["mean"]).any() else None
    lines += ["", "Total rainfall [%s]:" % depth_units,
              "  Domain mean : %.2f" % info["total_mean"]]
    if info["total_uniform"]:
        lines.append("  Every cell  : %.2f  (same total everywhere)" % info["total_max"])
    else:
        lines += ["  Min (cell)  : %.2f" % info["total_min"],
                  "  Max (cell)  : %.2f%s" % (info["total_max"],
                                             "  at x %.1f, y %.1f" % info["total_max_xy"]
                                             if info["total_max_xy"] else "")]
    if imax is not None:
        lines.append("  Peak step   : %s  (domain mean %.2f %s)"
                     % (times[imax], info["mean"][imax], units))
    return "\n".join(lines)


def plot_info(info, out=None, show=True):
    """
    Plot the hyetograph (domain mean / max per step) and the total-rainfall map.
    Save to `out` if given, otherwise show the figure.  Return the figure.
    """
    import matplotlib
    if out and not show:
        matplotlib.use("Agg")
    elif not os.environ.get("MPLBACKEND"):
        # The default Qt backend aborts the whole process (not catchable) when its
        # platform plugin cannot load, as in some conda envs; Tk ships with Python.
        try:
            import tkinter  # noqa: F401
            matplotlib.use("TkAgg")
        except ImportError:
            pass
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    times, units = info["times"], info["units"] or ""
    has_map = info["total"].ndim == 2
    fig, axes = plt.subplots(1, 2 if has_map else 1, figsize=(13 if has_map else 8, 5),
                             squeeze=False)
    ax = axes[0][0]
    # The value at a time stamp is the rainfall of the step that follows it: draw each
    # bar between its time stamp and the next one, and the max-cell point at the end
    # of that step.
    step = timedelta(seconds=info["dt"] or 3600)
    ends = times[1:] + [times[-1] + step]
    widths = [(e - t).total_seconds() / 86400 for t, e in zip(times, ends)]
    ax.bar(times, info["mean"], width=widths, align="edge", edgecolor="white", linewidth=0.8,
           color="#3b7dd8", label="domain mean")
    ax.plot(ends, info["max"], "o-", color="#d8453b", label="max cell")
    ax.set_xticks(times + [ends[-1]])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.set_xlim(times[0] - step * 0.2, ends[-1] + step * 0.2)
    ax.set_ylabel("rainfall [%s]" % units)
    ax.set_title("Rainfall per time step")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.autofmt_xdate()

    if has_map:
        ax = axes[0][1]
        depth_units = units.split("/")[0].split()[0] if info["rate"] else units
        if info["total_uniform"] and np.isfinite(info["mean"]).any():
            # a uniform total says nothing spatially: map the peak step instead
            ipeak = int(np.nanargmax(info["mean"]))
            field, label = info["rain"][ipeak], "rainfall [%s]" % units
            peak_end = times[ipeak + 1] if ipeak + 1 < len(times) else times[ipeak] + step
            title = "Peak step %s - %s  (total %.1f %s in every cell)" % (
                times[ipeak].strftime("%m-%d %H:%M"), peak_end.strftime("%H:%M"),
                info["total_max"], depth_units)
        else:
            field, label = info["total"], "total rainfall [%s]" % depth_units
            title = "Total rainfall  (%s - %s)" % (times[0].strftime("%m-%d %H:%M"),
                                                   times[-1].strftime("%m-%d %H:%M"))
        if info["x"] is not None and info["y"] is not None:
            mesh = ax.pcolormesh(info["x"], info["y"], field, shading="nearest", cmap="Blues")
            ax.set_aspect("equal")
            ax.set_xlabel("x")
            ax.set_ylabel("y")
            ax.ticklabel_format(useOffset=False, style="plain")
            if info["total_max_xy"]:
                ax.plot(*info["total_max_xy"], "r+", markersize=12, label="max total")
                ax.legend(loc="upper right")
        else:
            mesh = ax.imshow(field, origin="lower", cmap="Blues")
        fig.colorbar(mesh, ax=ax, label=label)
        ax.set_title(title)
    fig.suptitle(os.path.basename(info["path"]))
    fig.tight_layout()
    if out:
        fig.savefig(out, dpi=150)
    elif show:
        plt.show()
    return fig


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Show the start time, end time, time step, grid and rainfall "
                    "distribution of a NetCDF rainfall file.",
        epilog="""
examples:
  %(prog)s rain.nc                         (time info + rainfall per step + totals)
  %(prog)s rain.nc --plot                  (also show a hyetograph and a total-rainfall map)
  %(prog)s rain.nc --plot -o rain.png      (save the figure instead of showing it)
  %(prog)s rain.nc --var precip --time-var t
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ncfile", help="NetCDF rainfall file")
    ap.add_argument("--var", default=None,
                    help="rainfall variable (default: rainfall, rain, precipitation, ...)")
    ap.add_argument("--time-var", default=None, help="time variable (default: time)")
    ap.add_argument("--plot", action="store_true",
                    help="plot the hyetograph and the total-rainfall map")
    ap.add_argument("-o", "--output", default=None, metavar="PNG",
                    help="save the figure to this file (implies --plot)")
    ap.add_argument("--no-bars", action="store_true",
                    help="leave out the bar chart in the per-step table")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.ncfile):
        ap.error("file not found: %s" % args.ncfile)
    try:
        info = get_info(args.ncfile, args.var, args.time_var)
    except (KeyError, ValueError) as exc:
        print("%s: error: %s" % (SCRIPT, exc.args[0] if exc.args else exc), file=sys.stderr)
        return 1
    print(format_info(info, bars=not args.no_bars))

    if args.plot or args.output:
        plot_info(info, out=args.output, show=not args.output)
        if args.output:
            print("\nFigure saved: %s" % args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
