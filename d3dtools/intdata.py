#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
intdata - interpolate raster data (GeoTIFF) onto the 2D cells of a D-Flow FM
(Delft3D FM / D-HYDRO, 1D2D) model, for the 2D spatial quantities of the
D-HYDRO GUI:

  -q           GUI name             written as
  bedlevel     Bed Level            mesh2d_face_z / mesh2d_node_z in the net
                                    file, BedlevType set in the .mdu
  waterlevel   Initial Water Level  [Initial]   quantity = waterlevel
  roughness    Roughness            [Parameter] quantity = frictioncoefficient
                                    (+ UnifFrictType = --friction-type), or an
                                    old-format .ext block with IFRCTYP
                                    (--target ext)
  viscosity    Viscosity            [Parameter] quantity = horizontaleddyviscositycoefficient
  diffusivity  Diffusivity          [Parameter] quantity = horizontaleddydiffusivitycoefficient
  infiltration Infiltration         [Parameter] quantity = InfiltrationCapacity
                                    (+ [grw] Infiltrationmodel = 2)

How
  1. Read the model's 2D mesh from its net file (NetFile in the .mdu).
  2. Interpolate the raster onto every 2D cell: mean / median / min / max of
     the raster pixels inside the cell, or bilinear / nearest at the cell
     centre (nodes, for bed level: bilinear at the node by default).
  3. Except for bed level: write the cell values as samples at the cell
     centres (<model input>/<Quantity>.xyz) and add a block to the model's
     initial-field file (created and linked in the .mdu if missing), with
     interpolationMethod = averaging and locationType = 2d: every 2D cell gets
     exactly its own value and the 1D network is never touched.  An existing
     block for the same quantity is replaced; other blocks are kept.
  Cells without raster data keep the model's uniform / default value, unless
  --fill-value is given.  Changed files are backed up as
  <name>_backup_<yyyymmdd>_<HHMMSS>.<ext> (clrbak removes them).

Units expected by D-Flow FM: bed level / water level m; roughness per
--friction-type (Manning n s/m^1/3, Chezy C m^1/2/s, White-Colebrook k_s m,
z0 m); viscosity / diffusivity m2/s; infiltration mm/hr.  Use --factor to
convert.  No reprojection is done: the raster must be in the model's CRS.

Usage
-----
    intdata <project.dsproj | input-folder | model.mdu> <raster.tif> -q <quantity>
    intdata FM_model.dsproj RAS/dem.tif -q bedlevel
    intdata FM_model.dsproj RAS/friction.tif -q roughness --friction-type manning
    intdata dflowfm/FlowFM.mdu wl.tif -q waterlevel --method nearest
    intdata FM_model.dsproj RAS/infil.tif -q infiltration --check

Close the project in D-HYDRO (without saving) before running, then reopen it.

Requires: numpy, netCDF4 and one of rasterio / GDAL (osgeo) / tifffile.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import sys

import numpy as np

from .mduutils import read_lines, resolve_mdu
from .ncutils import find_mesh2d, open_nc
from .ras2msh import read_raster

SCRIPT = "intdata"

FRICTION_TYPES = {"chezy": 0, "manning": 1, "whitecolebrook": 2, "z0": 3}
FRICTION_RANGE = {0: (5.0, 150.0), 1: (0.005, 0.5), 2: (1e-5, 5.0), 3: (1e-6, 1.0)}
METHODS = ("mean", "median", "min", "max", "bilinear", "nearest")

# quantity -> (initial-field block, initial-field quantity, old-ext quantity,
#              sample file, units, usual range)
QUANTITIES = {
    "bedlevel":     (None, None, None, None, "m", (-1e4, 1e4)),
    "waterlevel":   ("Initial", "waterlevel", "initialwaterlevel",
                     "InitialWaterLevel.xyz", "m", (-1e4, 1e4)),
    "roughness":    ("Parameter", "frictioncoefficient", "frictioncoefficient",
                     "FrictionCoefficient.xyz", None, None),
    "viscosity":    ("Parameter", "horizontaleddyviscositycoefficient",
                     "horizontaleddyviscositycoefficient",
                     "HorizontalEddyViscosity.xyz", "m2/s", (0.0, 1e3)),
    "diffusivity":  ("Parameter", "horizontaleddydiffusivitycoefficient",
                     "horizontaleddydiffusivitycoefficient",
                     "HorizontalEddyDiffusivity.xyz", "m2/s", (0.0, 1e3)),
    "infiltration": ("Parameter", "InfiltrationCapacity", "infiltrationcapacity",
                     "InfiltrationCapacity.xyz", "mm/hr", (0.0, 1e4)),
}


# --------------------------------------------------------------------------- #
# Raster sampling
# --------------------------------------------------------------------------- #
class Raster:
    """A north-up raster with nodata as NaN, sampled in model coordinates."""

    def __init__(self, path, nodata=None):
        r = read_raster(path)
        a = r["data"]
        self.x0, self.y0, self.rx, self.ry = r["x0"], r["y0"], r["resx"], r["resy"]
        self.epsg = r["epsg"]
        self.nodata = r["nodata"] if nodata is None else nodata
        valid = np.isfinite(a)
        if self.nodata is not None:
            valid &= a != self.nodata
        self.a = np.where(valid, a, np.nan)
        self.nr, self.nc = a.shape

    def nearest(self, x, y):
        """Value of the pixel holding each point (NaN outside / nodata)."""
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        c = np.floor((x - self.x0) / self.rx).astype(int)
        r = np.floor((self.y0 - y) / self.ry).astype(int)
        ok = (r >= 0) & (r < self.nr) & (c >= 0) & (c < self.nc)
        out = np.full(x.shape, np.nan)
        out[ok] = self.a[r[ok], c[ok]]
        return out

    def bilinear(self, x, y):
        """Bilinear between pixel centres; missing neighbours are ignored
        (weights renormalised). Falls back to nearest at the raster edge."""
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        fc = (x - self.x0) / self.rx - 0.5
        fr = (self.y0 - y) / self.ry - 0.5
        c0 = np.floor(fc).astype(int)
        r0 = np.floor(fr).astype(int)
        tc = fc - c0
        tr = fr - r0
        num = np.zeros(x.shape)
        den = np.zeros(x.shape)
        for dr, dc, w in ((0, 0, (1 - tr) * (1 - tc)), (0, 1, (1 - tr) * tc),
                          (1, 0, tr * (1 - tc)), (1, 1, tr * tc)):
            r = r0 + dr
            c = c0 + dc
            ok = (r >= 0) & (r < self.nr) & (c >= 0) & (c < self.nc)
            v = np.full(x.shape, np.nan)
            v[ok] = self.a[r[ok], c[ok]]
            g = ok & np.isfinite(v) & (w > 0)
            num[g] += w[g] * v[g]
            den[g] += w[g]
        out = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
        miss = ~np.isfinite(out)
        if miss.any():
            out[miss] = self.nearest(x[miss], y[miss])
        return out

    def cell_stat(self, polys, stat):
        """Statistic of the pixels whose centre lies inside each polygon
        (list of N x 2 arrays); NaN for a polygon without valid pixels."""
        f = {"mean": np.mean, "median": np.median, "min": np.min, "max": np.max}[stat]
        out = np.full(len(polys), np.nan)
        for k, P in enumerate(polys):
            if len(P) < 3:
                continue
            xmin, ymin = P.min(0)
            xmax, ymax = P.max(0)
            c0 = max(int(np.floor((xmin - self.x0) / self.rx - 0.5)), 0)
            c1 = min(int(np.ceil((xmax - self.x0) / self.rx - 0.5)), self.nc - 1)
            r0 = max(int(np.floor((self.y0 - ymax) / self.ry - 0.5)), 0)
            r1 = min(int(np.ceil((self.y0 - ymin) / self.ry - 0.5)), self.nr - 1)
            if c1 < c0 or r1 < r0:
                continue
            cc, rr = np.meshgrid(np.arange(c0, c1 + 1), np.arange(r0, r1 + 1))
            px = self.x0 + (cc + 0.5) * self.rx
            py = self.y0 - (rr + 0.5) * self.ry
            inside = points_in_polygon(px.ravel(), py.ravel(), P).reshape(px.shape)
            v = self.a[rr[inside], cc[inside]]
            v = v[np.isfinite(v)]
            if v.size:
                out[k] = f(v)
        return out


def points_in_polygon(px, py, P):
    """Even-odd rule; points exactly on the left / bottom edge count as inside."""
    inside = np.zeros(px.shape, bool)
    n = len(P)
    for i in range(n):
        x1, y1 = P[i]
        x2, y2 = P[(i + 1) % n]
        cond = (y1 > py) != (y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (px < xint)
    return inside


def cell_values(mesh, raster, method="mean"):
    """Raster value per 2D cell: a pixel statistic (mean / median / min /
    max; cells smaller than a pixel fall back to bilinear at the centre) or
    bilinear / nearest at the cell centre. NaN where there is no data."""
    if method not in METHODS:
        raise ValueError("method must be one of %s" % ", ".join(METHODS))
    if method in ("bilinear", "nearest"):
        return getattr(raster, method)(mesh["fx"], mesh["fy"])
    val = raster.cell_stat(mesh["polys"], method)
    miss = ~np.isfinite(val)
    if miss.any():
        val[miss] = raster.bilinear(mesh["fx"][miss], mesh["fy"][miss])
    return val


# --------------------------------------------------------------------------- #
# Model / mesh
# --------------------------------------------------------------------------- #
SKIP_MDU = re.compile(r"(_\d{4}|_backup_\d{8}_\d{6}(_\d+)?)\.mdu$", re.IGNORECASE)


def find_mdus(target):
    """Return the .mdu files for a .mdu, a model input folder or a .dsproj
    (every model .mdu in its <project>.dsproj_data folder, without output,
    partition and backup copies)."""
    target = os.path.abspath(target)
    if os.path.isdir(target):
        mdus = [os.path.join(target, f) for f in sorted(os.listdir(target))
                if f.lower().endswith(".mdu") and not SKIP_MDU.search(f)]
        if len(mdus) != 1:
            raise ValueError("expected exactly one .mdu in %s, found %d"
                             % (target, len(mdus)))
        return mdus
    if target.lower().endswith(".dsproj"):
        folder = os.path.splitext(target)[0] + ".dsproj_data"
        if not os.path.isdir(folder):
            raise FileNotFoundError("project data folder not found: %s" % folder)
        mdus = []
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs
                       if not re.match(r"(output|DFM_OUTPUT.*)$", d, re.IGNORECASE)]
            mdus += [os.path.join(root, f) for f in files
                     if f.lower().endswith(".mdu") and not SKIP_MDU.search(f)]
        if not mdus:
            raise FileNotFoundError("no .mdu in %s" % folder)
        return sorted(mdus)
    return [resolve_mdu(target)]


def pick_mdu(target, name=None):
    """Return the one .mdu of `target`, picked by `name` (.mdu name or
    folder) when the project has several."""
    mdus = find_mdus(target)
    if name:
        n = name.lower()
        mdus = [m for m in mdus if n in (
            os.path.splitext(os.path.basename(m))[0].lower(),
            os.path.basename(os.path.dirname(m)).lower(),
            os.path.basename(os.path.dirname(os.path.dirname(m))).lower())]
        if not mdus:
            raise ValueError("no model '%s' in %s" % (name, target))
    if len(mdus) > 1:
        raise ValueError("%d models in the project; pick one with -m:\n  %s"
                         % (len(mdus), "\n  ".join(mdus)))
    return mdus[0]


def read_mesh2d(ds, name):
    """Return the 2D mesh: node x / y, face_nodes (0-based, -1 padded),
    face polygons and face centres."""
    t = ds[name]
    nx, ny = t.node_coordinates.split()[:2]
    x = np.asarray(ds[nx][:], float)
    y = np.asarray(ds[ny][:], float)
    fv = ds[t.face_node_connectivity]
    fn = np.ma.filled(fv[:], -1).astype(np.int64)
    si = int(getattr(fv, "start_index", 0))
    fn = np.where(fn >= si, fn - si, -1)
    polys = [np.column_stack([x[r[r >= 0]], y[r[r >= 0]]]) for r in fn]
    fc = getattr(t, "face_coordinates", "").split()
    if len(fc) == 2 and fc[0] in ds.variables and fc[1] in ds.variables:
        fx = np.asarray(ds[fc[0]][:], float)
        fy = np.asarray(ds[fc[1]][:], float)
    else:
        fx = np.array([p[:, 0].mean() for p in polys])
        fy = np.array([p[:, 1].mean() for p in polys])
    return dict(name=name, x=x, y=y, fn=fn, polys=polys, fx=fx, fy=fy)


def model_epsg(ds):
    for v in ds.variables.values():
        if hasattr(v, "epsg"):
            try:
                return int(v.epsg)
            except (TypeError, ValueError):
                pass
    return None


def mesh1d_names(ds):
    return [n for n, v in ds.variables.items()
            if getattr(v, "cf_role", "") == "mesh_topology"
            and int(getattr(v, "topology_dimension", 0)) == 1]


# --------------------------------------------------------------------------- #
# .mdu / .ini / .ext text helpers
# --------------------------------------------------------------------------- #
def read_text(path):
    lines, encoding = read_lines(path)
    return "".join(lines), encoding


def mdu_get(text, section, key):
    """Value of `key` in [section] (comment stripped), or None."""
    current = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            current = s[1:-1].strip().lower()
        elif current == section.lower() and "=" in line:
            k, v = line.split("=", 1)
            if k.strip().lower() == key.lower():
                v = v.split("#", 1)[0].strip()
                return v or None
    return None


def mdu_set(text, section, key, value):
    """Set `key` in [section], keeping the alignment and comment; add the key
    (and the section) when missing. Return (new text, old value or None)."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    sec = sec_start = sec_end = None
    pat = re.compile(r"(\s*%s\s*= ?)([^#]*?)(\s*#.*)?$" % re.escape(key), re.IGNORECASE)
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if sec is not None and sec.lower() == section.lower() and sec_end is None:
                sec_end = i
            sec = s[1:-1].strip()
            if sec.lower() == section.lower():
                sec_start = i
            continue
        if sec and sec.lower() == section.lower():
            m = pat.match(ln)
            if m:
                prefix, old = m.group(1), m.group(2)
                if not prefix.endswith(" "):
                    prefix += " "
                lines[i] = prefix + str(value).ljust(len(old)) + (m.group(3) or "")
                return nl.join(lines), old.strip()
    if sec_start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "[%s]" % section, "%-34s= %s" % (key, value), ""]
    else:
        end = sec_end if sec_end is not None else len(lines)
        while end - 1 > sec_start and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, "%-34s= %s" % (key, value))
    return nl.join(lines), None


def remove_ext_quantity(text, quantity):
    """Remove QUANTITY=<quantity> blocks from an old-format .ext file."""
    out, removed, skipping = [], 0, False
    for ln in text.splitlines():
        m = re.match(r"\s*QUANTITY\s*=\s*(\S+)", ln, re.IGNORECASE)
        if m:
            skipping = m.group(1).lower() == quantity.lower()
            removed += skipping
        if not skipping:
            out.append(ln)
        elif not ln.strip():
            skipping = False
    txt = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).rstrip()
    return (txt + "\n") if txt else "", removed


def remove_ini_quantity(text, quantity):
    """Remove the [Initial] / [Parameter] blocks of `quantity` from an
    initial-field file."""
    parts = re.split(r"(?m)^(?=\s*\[)", text)
    keep, removed = [], 0
    for p in parts:
        if re.match(r"\s*\[(initial|parameter)\]", p, re.IGNORECASE) and \
                re.search(r"^\s*quantity\s*=\s*%s\s*$" % re.escape(quantity), p,
                          re.IGNORECASE | re.MULTILINE):
            removed += 1
            continue
        keep.append(p)
    return "".join(keep), removed


def has_quantity(path, quantity):
    if path and os.path.isfile(path):
        text, _ = read_text(path)
        return re.search(r"quantity\s*=\s*%s\b" % re.escape(quantity), text,
                         re.IGNORECASE) is not None
    return False


def backup(path, stamp):
    """Copy `path` to <name>_backup_<stamp>.<ext>; return the copy or None."""
    if not os.path.exists(path):
        return None
    root, ext = os.path.splitext(path)
    b = "%s_backup_%s%s" % (root, stamp, ext)
    n = 1
    while os.path.exists(b):
        b = "%s_backup_%s_%d%s" % (root, stamp, n, ext)
        n += 1
    shutil.copy2(path, b)
    return b


# --------------------------------------------------------------------------- #
# Bed level into the net file
# --------------------------------------------------------------------------- #
def write_bedlevel(net, mesh_name, face_z, node_z, fill=-999.0, log=print):
    """Write <mesh>_face_z / <mesh>_node_z into the net file (created when
    missing). NaN keeps the existing value, else `fill`."""
    with open_nc(net, "a") as ds:
        t = ds[mesh_name]
        gm = next((vn for vn, v in ds.variables.items() if hasattr(v, "epsg")), None)
        for loc, z, dim, coords in (
                ("node", node_z, t.node_dimension, t.node_coordinates),
                ("face", face_z, t.face_dimension,
                 getattr(t, "face_coordinates",
                         "%s_face_x %s_face_y" % (mesh_name, mesh_name)))):
            vn = "%s_%s_z" % (mesh_name, loc)
            z = np.asarray(z, float)
            if vn in ds.variables:
                v = ds[vn]
                old = np.ma.filled(v[:].astype(float), np.nan)
                z = np.where(np.isfinite(z), z, old)
            else:
                v = ds.createVariable(vn, "f8", (dim,), fill_value=fill)
                att = dict(units="m", standard_name="altitude",
                           long_name="z-coordinate of mesh %ss" % loc,
                           mesh=mesh_name, location=loc, coordinates=coords)
                if gm:
                    att["grid_mapping"] = gm
                v.setncatts(att)
            z = np.where(np.isfinite(z), z, fill)
            v[:] = z
            good = z != fill
            rng = ("%.3f .. %.3f m" % (z[good].min(), z[good].max())) if good.any() else "-"
            log("  %s: %d values, %s%s" % (vn, good.sum(), rng,
                                            ", %d = %g (no data)" % ((~good).sum(), fill)
                                            if (~good).any() else ""))


def node_values_from_faces(fn, n_nodes, face_val):
    """Mean of the adjacent cells' values per node (NaN without any)."""
    s = np.zeros(n_nodes)
    c = np.zeros(n_nodes)
    for k in range(fn.shape[1]):
        col = fn[:, k]
        g = (col >= 0) & np.isfinite(face_val)
        np.add.at(s, col[g], face_val[g])
        np.add.at(c, col[g], 1)
    return np.where(c > 0, s / np.where(c > 0, c, 1), np.nan)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def interpolate(model, raster, quantity, method="mean", factor=1.0, fill_value=None,
                nodata=None, samples_name=None, keep_mdu=False,
                friction_type="manning", target="inifield", node_method="bilinear",
                bedlev_location="face", node_bedlevtype=3, model_name=None,
                write=True, log=print):
    """
    Interpolate `raster` onto the 2D cells of a D-Flow FM model.

    Parameters
    ----------
    model : str
        .dsproj, model input folder or .mdu.
    raster : str
        GeoTIFF with the data, in the model's CRS.
    quantity : str
        One of QUANTITIES (bedlevel, waterlevel, roughness, viscosity,
        diffusivity, infiltration).
    method : str
        mean / median / min / max of the pixels in a cell, or bilinear /
        nearest at the cell centre.
    factor : float
        Multiply the raster values (unit conversion).
    fill_value : float, optional
        Value for cells without raster data (default: keep the model's
        default / existing value).
    nodata : float, optional
        Override the raster nodata value.
    samples_name : str, optional
        Sample file name, written next to the .mdu.
    keep_mdu : bool
        Do not change the .mdu (the needed changes are reported).
    friction_type, target : str
        Roughness only: chezy / manning / whitecolebrook / z0, and inifield
        (2D only) or ext (old-format .ext with IFRCTYP).
    node_method, bedlev_location, node_bedlevtype :
        Bed level only: node values bilinear / nearest at the node or the
        mean of the adjacent cells ("faces"); which values D-Flow FM uses
        (face -> BedlevType 1, node -> node_bedlevtype 3..6).
    model_name : str, optional
        The model to use when a .dsproj has several.
    write : bool
        False computes and reports, writes nothing.

    Returns
    -------
    dict
        mdu, net, mesh (name), values (per cell, NaN = no data), n_cells,
        n_valid, written (list of files), backups (list), mdu_changes (list).
    """
    if quantity not in QUANTITIES:
        raise ValueError("quantity must be one of %s" % ", ".join(QUANTITIES))
    block, iniq, extq, sname, units, rng = QUANTITIES[quantity]
    ftype = None
    if quantity == "roughness":
        if friction_type not in FRICTION_TYPES:
            raise ValueError("friction_type must be one of %s" % ", ".join(FRICTION_TYPES))
        ftype = FRICTION_TYPES[friction_type]
        units, rng = friction_type, FRICTION_RANGE[ftype]
    if target not in ("inifield", "ext"):
        raise ValueError("target must be inifield or ext")
    if node_method not in ("bilinear", "nearest", "faces"):
        raise ValueError("node_method must be bilinear, nearest or faces")
    if bedlev_location not in ("face", "node"):
        raise ValueError("bedlev_location must be face or node")
    if int(node_bedlevtype) not in (3, 4, 5, 6):
        raise ValueError("node_bedlevtype must be 3, 4, 5 or 6")
    sname = samples_name or sname

    mdu = pick_mdu(model, model_name)
    mdir = os.path.dirname(mdu)
    mdu_text, mdu_enc = read_text(mdu)
    net = os.path.join(mdir, mdu_get(mdu_text, "geometry", "NetFile") or "FlowFM_net.nc")
    log("Model    : %s" % mdu)
    log("Net file : %s" % net)
    log("Quantity : %s" % quantity)
    if not os.path.isfile(net):
        raise FileNotFoundError("net file not found: %s - create the mesh first" % net)

    with open_nc(net) as ds:
        name = find_mesh2d(ds)
        M = read_mesh2d(ds, name)
        epsg_m = model_epsg(ds)
        n1d = mesh1d_names(ds)
    log("2D mesh  : '%s', %d cells, %d nodes, EPSG:%s%s"
        % (name, len(M["polys"]), len(M["x"]), epsg_m,
           "  (1D: %s - not changed)" % ", ".join(n1d) if n1d else ""))

    R = Raster(raster, nodata)
    log("Raster   : %d x %d, %g x %g m, EPSG:%s" % (R.nc, R.nr, R.rx, R.ry, R.epsg))
    if R.epsg and epsg_m and R.epsg != epsg_m:
        log("WARNING: raster EPSG:%s differs from model EPSG:%s (no reprojection "
            "is done)" % (R.epsg, epsg_m))

    # ---- interpolate onto the cells
    val = cell_values(M, R, method) * factor
    miss = ~np.isfinite(val)
    if miss.any():
        if fill_value is not None:
            val[miss] = fill_value
            log("%d cells without raster data -> %g" % (miss.sum(), fill_value))
        else:
            log("%d cells without raster data -> keep the model default / "
                "existing value" % miss.sum())
    ok = np.isfinite(val)
    if not ok.any():
        raise ValueError("no cell received a value - check the raster extent / "
                         "nodata / CRS")
    lo, hi = rng
    bad = ok & ((val < lo) | (val > hi))
    if bad.any():
        log("WARNING: %d values outside the usual range %g..%g %s - check --factor%s"
            % (bad.sum(), lo, hi, units,
               " / --friction-type" if quantity == "roughness" else ""))
    if quantity == "roughness" and (val[ok] <= 0).any():
        raise ValueError("roughness values must be > 0 (%d are not)"
                         % (val[ok] <= 0).sum())
    if quantity in ("viscosity", "diffusivity", "infiltration") and (val[ok] < 0).any():
        log("WARNING: %d negative values set to 0" % (val[ok] < 0).sum())
        val[ok & (val < 0)] = 0.0
    log("Values   : %.4g .. %.4g (mean %.4g) %s on %d cells"
        % (val[ok].min(), val[ok].max(), val[ok].mean(), units, ok.sum()))

    res = dict(mdu=mdu, net=net, mesh=name, values=val, n_cells=len(val),
               n_valid=int(ok.sum()), written=[], backups=[], mdu_changes=[])
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    txt = mdu_text
    changes = res["mdu_changes"]
    ini_name = mdu_get(mdu_text, "geometry", "IniFieldFile")
    ext_name = mdu_get(mdu_text, "external forcing", "ExtForceFile")

    def do_backup(path):
        b = backup(path, stamp)
        if b:
            res["backups"].append(b)
        return b

    # ---- bed level -> net file
    if quantity == "bedlevel":
        if node_method == "faces":
            nz = node_values_from_faces(M["fn"], len(M["x"]), val)
        else:
            nz = getattr(R, node_method)(M["x"], M["y"]) * factor
            if fill_value is not None:
                nz[~np.isfinite(nz)] = fill_value
        blt = 1 if bedlev_location == "face" else int(node_bedlevtype)
        txt, old = mdu_set(txt, "geometry", "BedlevType", blt)
        if str(old) != str(blt):
            changes.append("BedlevType %s -> %s" % (old, blt))
        for fn in (ini_name, ext_name):
            if fn and has_quantity(os.path.join(mdir, fn), "bedlevel"):
                log("WARNING: %s also sets bedlevel - it overrides the net-file "
                    "bed level." % fn)
        if write:
            log("Backup   : %s" % do_backup(net))
            write_bedlevel(net, name, val, nz, -999.0, log)
            res["written"].append(net)
            log("Written  : %s" % net)

    # ---- other quantities -> samples + initial-field file (or old .ext)
    else:
        xyz = os.path.join(mdir, sname)
        use_ext = quantity == "roughness" and target == "ext"
        if write:
            bk = do_backup(xyz)
            np.savetxt(xyz, np.column_stack([M["fx"][ok], M["fy"][ok], val[ok]]),
                       fmt="%.3f %.3f %.6g")
            res["written"].append(xyz)
            log("Written  : %s%s" % (xyz, " (backup %s)" % bk if bk else ""))

        if use_ext:
            new = not ext_name
            ext_name = ext_name or os.path.splitext(os.path.basename(mdu))[0] + ".ext"
            ext_path = os.path.join(mdir, ext_name)
            ext_text, nrem = "", 0
            if os.path.isfile(ext_path):
                ext_text, _ = read_text(ext_path)
                ext_text, nrem = remove_ext_quantity(ext_text, extq)
            if nrem:
                log("Replace  : %d existing %s block(s) in %s" % (nrem, extq, ext_name))
            blk = ("QUANTITY=%s\nFILENAME=%s\nFILETYPE=7\nMETHOD=6\nOPERAND=O\n"
                   "AVERAGINGTYPE=1\nRELATIVESEARCHCELLSIZE=1.01\nIFRCTYP=%d\n"
                   % (extq, sname, ftype))
            if write:
                do_backup(ext_path)
                with open(ext_path, "w", encoding="utf-8") as f:
                    f.write((ext_text.rstrip() + "\n\n" if ext_text.strip() else "") + blk)
                res["written"].append(ext_path)
                log("Written  : %s" % ext_path)
            if new:
                txt, _ = mdu_set(txt, "external forcing", "ExtForceFile", ext_name)
                changes.append("ExtForceFile -> %s" % ext_name)
            if n1d:
                log("NOTE: 1D2D model - with --target ext check that the 1D friction "
                    "is not affected; --target inifield restricts the field to 2D.")
            other, other_q = ini_name, iniq
        else:
            new = not ini_name
            ini_name = ini_name or "initialFields.ini"
            ini_path = os.path.join(mdir, ini_name)
            if os.path.isfile(ini_path):
                ini_text, _ = read_text(ini_path)
                ini_text, nrem = remove_ini_quantity(ini_text, iniq)
                if nrem:
                    log("Replace  : %d existing %s block(s) in %s" % (nrem, iniq, ini_name))
            else:
                ini_text = ("[General]\n    fileVersion           = 2.00\n"
                            "    fileType              = iniField\n")
            blk = ("\n[%s]\n"
                   "    quantity              = %s\n"
                   "    dataFile              = %s\n"
                   "    dataFileType          = sample\n"
                   "    interpolationMethod   = averaging\n"
                   "    averagingType         = mean\n"
                   "    averagingRelSize      = 1.01\n"
                   "    locationType          = 2d\n" % (block, iniq, sname))
            if write:
                do_backup(ini_path)
                with open(ini_path, "w", encoding="utf-8") as f:
                    f.write(ini_text.rstrip() + "\n" + blk)
                res["written"].append(ini_path)
                log("Written  : %s" % ini_path)
            if new:
                txt, _ = mdu_set(txt, "geometry", "IniFieldFile", ini_name)
                changes.append("IniFieldFile -> %s" % ini_name)
            if quantity == "roughness":
                txt, old = mdu_set(txt, "physics", "UnifFrictType", ftype)
                if str(old) != str(ftype):
                    changes.append("UnifFrictType %s -> %s (also applies to UnifFrictCoef "
                                   "%s - check it is a %s value)"
                                   % (old, ftype, mdu_get(mdu_text, "physics", "UnifFrictCoef"),
                                      friction_type))
            other, other_q = ext_name, extq

        if other and has_quantity(os.path.join(mdir, other), other_q):
            log("WARNING: %s also sets %s - remove it there to avoid conflicts."
                % (other, quantity))
        if quantity == "infiltration":
            txt, old = mdu_set(txt, "grw", "Infiltrationmodel", 2)
            if str(old) != "2":
                changes.append("Infiltrationmodel %s -> 2" % old)
        if quantity == "diffusivity":
            log("NOTE: the horizontal eddy diffusivity only matters with salinity / "
                "temperature / tracers.")

    if not changes:
        log("MDU      : no changes needed")
    elif keep_mdu:
        log("NOTE: --keep-mdu given; make these .mdu changes yourself: "
            + "; ".join(changes))
    elif write:
        bk = do_backup(mdu)
        with open(mdu, "wb") as f:
            f.write(txt.encode(mdu_enc))
        res["written"].append(mdu)
        log("MDU      : %s (backup %s)" % ("; ".join(changes), bk))
    else:
        log("MDU      : would change %s" % "; ".join(changes))
    return res


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Interpolate raster data (GeoTIFF) onto the 2D cells of a D-Flow FM "
                    "/ D-HYDRO (1D2D) model: bed level into the net file, the other "
                    "quantities as cell-centre samples + a locationType = 2d block in "
                    "the initial-field file (the 1D network is never touched).",
        epilog="""
quantities (-q):
  bedlevel      mesh2d_face_z / mesh2d_node_z in the net file + BedlevType
  waterlevel    [Initial]   waterlevel                          (m)
  roughness     [Parameter] frictioncoefficient + UnifFrictType (--friction-type)
  viscosity     [Parameter] horizontaleddyviscositycoefficient  (m2/s)
  diffusivity   [Parameter] horizontaleddydiffusivitycoefficient (m2/s)
  infiltration  [Parameter] InfiltrationCapacity + Infiltrationmodel = 2 (mm/hr)

examples:
  %(prog)s FM_model.dsproj RAS/dem.tif -q bedlevel
  %(prog)s FM_model.dsproj RAS/friction.tif -q roughness --friction-type manning
  %(prog)s FM_model.dsproj RAS/friction.tif -q roughness --target ext
  %(prog)s dflowfm/FlowFM.mdu wl.tif -q waterlevel --method nearest
  %(prog)s FM_model.dsproj RAS/infil.tif -q infiltration --factor 10 --check

Changed files are backed up as <name>_backup_<yyyymmdd>_<HHMMSS>.<ext>
(clrbak removes them). Close the project in D-HYDRO (without saving)
before running, then reopen it.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help=".dsproj, model input folder or .mdu")
    ap.add_argument("raster", help="raster (GeoTIFF) with the data, in the model's CRS")
    ap.add_argument("-q", "--quantity", required=True, choices=list(QUANTITIES),
                    help="quantity to set")
    ap.add_argument("-m", "--model-name", metavar="NAME",
                    help="the model (.mdu name or its folder) to use when a project "
                         "has several")
    ap.add_argument("--method", default="mean", choices=METHODS,
                    help="cell value: statistic of the pixels in the cell, or sampled "
                         "at the cell centre (default mean)")
    ap.add_argument("--factor", type=float, default=1.0,
                    help="multiply the raster values (unit conversion)")
    ap.add_argument("--fill-value", type=float, default=None,
                    help="value for cells without raster data (default: keep the "
                         "model default / existing value)")
    ap.add_argument("--nodata", type=float, default=None,
                    help="override the raster nodata value")
    ap.add_argument("--samples-name", default=None,
                    help="sample file name, written next to the .mdu")
    ap.add_argument("--keep-mdu", action="store_true", help="do not change the .mdu")
    ap.add_argument("--check", action="store_true",
                    help="interpolate and report, write nothing")
    r = ap.add_argument_group("roughness")
    r.add_argument("--friction-type", default="manning", choices=list(FRICTION_TYPES),
                   help="type of the roughness values (default manning)")
    r.add_argument("--target", default="inifield", choices=["inifield", "ext"],
                   help="inifield (default, 2D only) or old-format .ext with IFRCTYP")
    b = ap.add_argument_group("bed level")
    b.add_argument("--node-method", default="bilinear",
                   choices=["bilinear", "nearest", "faces"],
                   help="node values: sampled at the node, or the mean of the "
                        "adjacent cells (default bilinear)")
    b.add_argument("--bedlev-location", default="face", choices=["face", "node"],
                   help="values D-Flow FM uses: face -> BedlevType 1, node -> "
                        "--node-bedlevtype (default face)")
    b.add_argument("--node-bedlevtype", type=int, default=3, choices=[3, 4, 5, 6],
                   help="BedlevType with --bedlev-location node (default 3)")
    args = ap.parse_args(argv)

    try:
        interpolate(args.model, args.raster, args.quantity, method=args.method,
                    factor=args.factor, fill_value=args.fill_value, nodata=args.nodata,
                    samples_name=args.samples_name, keep_mdu=args.keep_mdu,
                    friction_type=args.friction_type, target=args.target,
                    node_method=args.node_method, bedlev_location=args.bedlev_location,
                    node_bedlevtype=args.node_bedlevtype, model_name=args.model_name,
                    write=not args.check)
    except (ValueError, FileNotFoundError, ImportError, OSError, RuntimeError) as exc:
        print("%s: error: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1
    if args.check:
        print("\nCheck only - nothing written.")
    else:
        print("\nReopen the project in D-HYDRO to see the result.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
