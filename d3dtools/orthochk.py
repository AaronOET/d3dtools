#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orthochk - locate non-orthogonal / problematic 2D cells in a D-Flow FM
(Delft3D FM) UGRID net file (*_net.nc) and export them as a polygon shapefile.

Orthogonality (same definition as RGFGRID / D-Flow FM):
    For every internal net link (edge) shared by two cells,
        ortho = |cos(theta)|
    where theta is the angle between the net link and the flow link that
    connects the circumcentres of the two neighbouring cells.
    0 = perfectly orthogonal, 1 = flow link parallel to the edge.
    Typical guidance: < 0.02 good, 0.02-0.1 acceptable, > 0.1 poor.

Fatal-type defects that typically trigger
"ERROR : network is not orthogonal" in D-Flow FM are also flagged:
    ZERO_LINK: circumcentres of two neighbouring cells coincide (zero-length
               flow link), e.g. a rectangle split along its diagonal into two
               right-angled triangles
    SAMESIDE : both circumcentres lie on the same side of the shared edge
               (flow link does not cross the net link)
    CC_OUT   : cell circumcentre lies outside its own cell
    NONCONVX : cell is non-convex, clockwise, or has (near) zero area
    OVERLAP  : an edge is shared by more than 2 cells (bad topology)

Usage
-----
    orthochk <input-folder | model.mdu | *_net.nc>
    orthochk FlowFM_net.nc -t 0.05 -o bad_cells.shp --edges
    orthochk FlowFM_net.nc --all          (all cells + attributes)
    orthochk FlowFM_net.nc --check        (summary only, write nothing)

Requires: numpy, netCDF4, pyshp      (pip install numpy netCDF4 pyshp)
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

try:
    import netCDF4 as nc
except ImportError:  # pragma: no cover
    sys.exit("Missing package: pip install netCDF4")
try:
    import shapefile  # pyshp
except ImportError:  # pragma: no cover
    sys.exit("Missing package: pip install pyshp")

SCRIPT = "orthochk"


# --------------------------------------------------------------------------- #
# Input resolution
# --------------------------------------------------------------------------- #
def resolve_netfile(target):
    """Return the net file for a model folder, a .mdu or a *_net.nc path."""
    target = os.path.abspath(target)
    mdu = None
    if os.path.isdir(target):
        mdus = [f for f in sorted(os.listdir(target))
                if f.lower().endswith(".mdu")]
        if len(mdus) != 1:
            raise ValueError("expected exactly one .mdu in %s, found %d"
                             % (target, len(mdus)))
        mdu = os.path.join(target, mdus[0])
    elif target.lower().endswith(".mdu"):
        mdu = target
    elif target.lower().endswith(".nc"):
        net = target
    else:
        raise ValueError("give a model input folder, a .mdu or a *_net.nc file")

    if mdu:
        if not os.path.isfile(mdu):
            raise FileNotFoundError("mdu not found: %s" % mdu)
        netfile = None
        with open(mdu, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, sep, val = line.partition("=")
                if sep and key.strip().lower() == "netfile":
                    netfile = val.split("#")[0].strip()
                    break
        if not netfile:
            raise ValueError("NetFile is empty in %s" % mdu)
        net = os.path.join(os.path.dirname(mdu), netfile)
    if not os.path.isfile(net):
        raise FileNotFoundError("net file not found: %s" % net)
    return net


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def find_mesh2d(ds):
    """Return the name of the 2D mesh_topology variable."""
    for name, var in ds.variables.items():
        if getattr(var, "cf_role", "") == "mesh_topology" and \
                int(getattr(var, "topology_dimension", 2)) == 2:
            return name
    raise RuntimeError("No 2D mesh_topology variable found in file.")


def read_mesh(path):
    """Return node x, y, 0-based face_node table (-1 padded), WKT, mesh name."""
    with nc.Dataset(path) as ds:
        mesh = find_mesh2d(ds)
        mvar = ds[mesh]
        xname, yname = mvar.node_coordinates.split()[:2]
        fvar = ds[mvar.face_node_connectivity]

        x = np.asarray(ds[xname][:], dtype=np.float64)
        y = np.asarray(ds[yname][:], dtype=np.float64)

        fn = fvar[:]
        fill = getattr(fvar, "_FillValue", -999)
        if np.ma.isMaskedArray(fn):
            fn = fn.filled(-1)
        fn = np.asarray(fn, dtype=np.int64)
        fn[fn == fill] = -1
        start = int(getattr(fvar, "start_index", 0))
        fn[fn >= 0] -= start

        # projection WKT (for the .prj file)
        wkt = None
        for name, var in ds.variables.items():
            if name in ("projected_coordinate_system", "wgs84") or \
                    hasattr(var, "grid_mapping_name"):
                wkt = getattr(var, "wkt", None) or getattr(var, "crs_wkt", None)
                if wkt:
                    break
    return x, y, fn, wkt, mesh


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def circumcentres(x, y, fn, nv):
    """
    Circumcentre per cell.
      triangles : exact circumcentre
      polygons  : least-squares point closest to all perpendicular
                  bisectors of the cell edges (exact for rectangles and
                  cyclic quads, a good approximation otherwise).
    """
    nf, nmax = fn.shape
    cx = np.zeros(nf)
    cy = np.zeros(nf)
    # accumulate normal equations  A x = b  with A = sum t t^T, b = sum t t^T m
    a11 = np.zeros(nf); a12 = np.zeros(nf); a22 = np.zeros(nf)
    b1 = np.zeros(nf); b2 = np.zeros(nf)
    for j in range(nmax):
        valid = j < nv
        jn = np.where(j + 1 < nv, j + 1, 0)
        ia = np.where(valid, fn[:, j], 0)
        ib = np.where(valid, fn[np.arange(nf), jn], 0)
        tx = x[ib] - x[ia]
        ty = y[ib] - y[ia]
        L = np.hypot(tx, ty)
        L[L == 0] = 1.0
        tx /= L; ty /= L
        mx = 0.5 * (x[ia] + x[ib])
        my = 0.5 * (y[ia] + y[ib])
        w = valid.astype(float)
        a11 += w * tx * tx; a12 += w * tx * ty; a22 += w * ty * ty
        d = tx * mx + ty * my
        b1 += w * tx * d; b2 += w * ty * d
    det = a11 * a22 - a12 * a12
    ok = np.abs(det) > 1e-12
    cx[ok] = (a22[ok] * b1[ok] - a12[ok] * b2[ok]) / det[ok]
    cy[ok] = (a11[ok] * b2[ok] - a12[ok] * b1[ok]) / det[ok]

    # exact formula for triangles
    tri = nv == 3
    if tri.any():
        i0, i1, i2 = fn[tri, 0], fn[tri, 1], fn[tri, 2]
        ax, ay = x[i0], y[i0]
        bx, by = x[i1] - ax, y[i1] - ay
        qx, qy = x[i2] - ax, y[i2] - ay
        D = 2.0 * (bx * qy - by * qx)
        D[D == 0] = np.nan
        ux = (qy * (bx**2 + by**2) - by * (qx**2 + qy**2)) / D
        uy = (bx * (qx**2 + qy**2) - qx * (bx**2 + by**2)) / D
        cx[tri] = ax + ux
        cy[tri] = ay + uy

    # fall back to the mass centre where the system is singular
    bad = (~ok & ~tri) | ~np.isfinite(cx) | ~np.isfinite(cy)
    if bad.any():
        xs = np.where(fn >= 0, x[np.maximum(fn, 0)], 0.0)
        ys = np.where(fn >= 0, y[np.maximum(fn, 0)], 0.0)
        cx[bad] = xs[bad].sum(1) / nv[bad]
        cy[bad] = ys[bad].sum(1) / nv[bad]
    return cx, cy


def cell_shape_checks(x, y, fn, nv, cx, cy):
    """Signed area, convexity and 'circumcentre inside cell' test."""
    nf, nmax = fn.shape
    area2 = np.zeros(nf)
    all_pos = np.ones(nf, bool)       # all turn cross-products > 0 (CCW convex)
    cc_in = np.ones(nf, bool)
    rows = np.arange(nf)
    for j in range(nmax):
        valid = j < nv
        j1 = np.where(j + 1 < nv, j + 1, 0)
        j2 = np.where(j1 + 1 < nv, j1 + 1, 0)
        p0 = np.where(valid, fn[:, j], 0)
        p1 = np.where(valid, fn[rows, j1], 0)
        p2 = np.where(valid, fn[rows, j2], 0)
        area2 += np.where(valid, x[p0] * y[p1] - x[p1] * y[p0], 0.0)
        ex, ey = x[p1] - x[p0], y[p1] - y[p0]
        fx, fy = x[p2] - x[p1], y[p2] - y[p1]
        turn = ex * fy - ey * fx
        all_pos &= ~valid | (turn > 0)
        # circumcentre left of every (CCW) edge -> inside
        side = ex * (cy - y[p0]) - ey * (cx - x[p0])
        cc_in &= ~valid | (side >= -1e-9 * (ex**2 + ey**2))
    area = 0.5 * area2
    nonconvex = (~all_pos) | (area <= 1e-6)
    return area, nonconvex, ~cc_in


def edge_orthogonality(x, y, fn, nv, cx, cy):
    """Build edge->cell adjacency and compute per-edge orthogonality."""
    nf, nmax = fn.shape
    nn = len(x)
    rows = np.arange(nf)
    fa, fb, ff = [], [], []
    for j in range(nmax):
        valid = j < nv
        jn = np.where(j + 1 < nv, j + 1, 0)
        fa.append(fn[valid, j])
        fb.append(fn[rows[valid], jn[valid]])
        ff.append(rows[valid])
    fa = np.concatenate(fa); fb = np.concatenate(fb); ff = np.concatenate(ff)
    lo = np.minimum(fa, fb); hi = np.maximum(fa, fb)
    key = lo * nn + hi
    order = np.argsort(key, kind="stable")
    key = key[order]; lo = lo[order]; hi = hi[order]; ff = ff[order]

    same = key[1:] == key[:-1]
    # edges used by > 2 cells
    triple = np.zeros(len(key), bool)
    if len(key) > 2:
        t = same[1:] & same[:-1]
        triple[1:-1] |= t; triple[:-2] |= t; triple[2:] |= t
    overlap_faces = np.unique(ff[triple])

    idx = np.nonzero(same)[0]
    f1 = ff[idx]; f2 = ff[idx + 1]
    n1 = lo[idx]; n2 = hi[idx]

    ex, ey = x[n2] - x[n1], y[n2] - y[n1]
    lx, ly = cx[f2] - cx[f1], cy[f2] - cy[f1]
    le = np.hypot(ex, ey); ll = np.hypot(lx, ly)
    with np.errstate(invalid="ignore", divide="ignore"):
        ortho = np.abs(ex * lx + ey * ly) / (le * ll)
    # (near) coincident circumcentres -> zero-length flow link
    zerolink = ll < 1e-3 * le
    ortho[zerolink | ~np.isfinite(ortho)] = 1.0

    # do both circumcentres lie strictly on the same side of the edge?
    # (a circumcentre lying ON the edge, e.g. right-angled triangle, is not
    #  counted here - it is orthogonal, only the half-link is zero)
    tol = 1e-6 * le
    with np.errstate(invalid="ignore", divide="ignore"):
        d1 = (ex * (cy[f1] - y[n1]) - ey * (cx[f1] - x[n1])) / le
        d2 = (ex * (cy[f2] - y[n1]) - ey * (cx[f2] - x[n1])) / le
    sameside = (np.abs(d1) > tol) & (np.abs(d2) > tol) & \
        (np.sign(d1) == np.sign(d2))
    return f1, f2, n1, n2, ortho, sameside, zerolink, overlap_faces


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def check_orthogonality(netfile, threshold=0.1):
    """
    Run all orthogonality / cell-shape checks on the 2D mesh of a net file.

    Parameters
    ----------
    netfile : str
        D-Flow FM UGRID net file (*_net.nc).
    threshold : float
        Orthogonality threshold |cos|; edges above it are flagged (default 0.1).

    Returns
    -------
    dict
        Mesh arrays (x, y, fn, nv, wkt, mesh), per-cell results (cx, cy, area,
        max_ortho, n_bad, zero_link, sameside, cc_out, nonconvex, overlap,
        flagged) and per-edge results (f1, f2, n1, n2, ortho, edge_sameside,
        edge_zerolink, bad_edge).  Face ids are 0-based.
    """
    x, y, fn, wkt, mesh = read_mesh(netfile)
    nv = (fn >= 0).sum(1)
    cx, cy = circumcentres(x, y, fn, nv)
    area, nonconvex, cc_out = cell_shape_checks(x, y, fn, nv, cx, cy)
    f1, f2, n1, n2, ortho, sameside, zerolink, overlap_faces = \
        edge_orthogonality(x, y, fn, nv, cx, cy)

    nf = len(fn)
    max_ortho = np.zeros(nf)
    np.maximum.at(max_ortho, f1, ortho)
    np.maximum.at(max_ortho, f2, ortho)
    bad_edge = (ortho > threshold) | sameside
    n_bad = np.zeros(nf, int)
    np.add.at(n_bad, f1[bad_edge], 1)
    np.add.at(n_bad, f2[bad_edge], 1)
    ss_cell = np.zeros(nf, bool)
    ss_cell[f1[sameside]] = True
    ss_cell[f2[sameside]] = True
    zl_cell = np.zeros(nf, bool)
    zl_cell[f1[zerolink]] = True
    zl_cell[f2[zerolink]] = True
    overlap = np.zeros(nf, bool)
    overlap[overlap_faces] = True

    flagged = (max_ortho > threshold) | ss_cell | zl_cell | cc_out | \
        nonconvex | overlap

    return dict(
        x=x, y=y, fn=fn, nv=nv, wkt=wkt, mesh=mesh, threshold=threshold,
        cx=cx, cy=cy, area=area, max_ortho=max_ortho, n_bad=n_bad,
        zero_link=zl_cell, sameside=ss_cell, cc_out=cc_out,
        nonconvex=nonconvex, overlap=overlap, flagged=flagged,
        f1=f1, f2=f2, n1=n1, n2=n2, ortho=ortho, edge_sameside=sameside,
        edge_zerolink=zerolink, bad_edge=bad_edge,
    )


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def write_prj(shp_path, wkt):
    if wkt:
        with open(os.path.splitext(shp_path)[0] + ".prj", "w") as f:
            f.write(wkt.replace("\n", "").replace("    ", ""))


def write_cells(path, res, sel):
    """Write the cells `sel` (0-based face ids) of a check result as polygons."""
    x, y, fn, nv = res["x"], res["y"], res["fn"], res["nv"]
    fields = [res["max_ortho"], res["n_bad"], res["zero_link"].astype(int),
              res["sameside"].astype(int), res["cc_out"].astype(int),
              res["nonconvex"].astype(int), res["overlap"].astype(int),
              res["area"], res["cx"], res["cy"]]
    w = shapefile.Writer(path, shapeType=shapefile.POLYGON, encoding="utf-8")
    w.field("FACE_ID", "N", 10, 0)        # 0-based index in the net file
    w.field("MAX_ORTHO", "N", 12, 6)
    w.field("N_BAD_EDG", "N", 3, 0)
    w.field("ZERO_LINK", "N", 1, 0)
    w.field("SAMESIDE", "N", 1, 0)
    w.field("CC_OUT", "N", 1, 0)
    w.field("NONCONVX", "N", 1, 0)
    w.field("OVERLAP", "N", 1, 0)
    w.field("AREA_M2", "N", 16, 3)
    w.field("CX", "N", 16, 3)
    w.field("CY", "N", 16, 3)
    for k in sel:
        ids = fn[k, :nv[k]]
        ring = [(float(x[i]), float(y[i])) for i in ids]
        ring = ring[::-1]                  # shapefile outer ring = clockwise
        ring.append(ring[0])
        w.poly([ring])
        w.record(int(k), *[f[k].item() for f in fields])
    w.close()
    write_prj(path, res["wkt"])


def write_edges(path, res, ei):
    """Write the edges `ei` (indices into the internal-edge arrays) as lines."""
    x, y, n1, n2 = res["x"], res["y"], res["n1"], res["n2"]
    w = shapefile.Writer(path, shapeType=shapefile.POLYLINE, encoding="utf-8")
    w.field("FACE1", "N", 10, 0)
    w.field("FACE2", "N", 10, 0)
    w.field("ORTHO", "N", 12, 6)
    w.field("SAMESIDE", "N", 1, 0)
    for e in ei:
        w.line([[(float(x[n1[e]]), float(y[n1[e]])),
                 (float(x[n2[e]]), float(y[n2[e]]))]])
        w.record(int(res["f1"][e]), int(res["f2"][e]),
                 float(res["ortho"][e]), int(res["edge_sameside"][e]))
    w.close()
    write_prj(path, res["wkt"])


def print_summary(res):
    ortho = res["ortho"]
    th = res["threshold"]
    print("\nSummary (%d internal edges)" % len(ortho))
    print("  max orthogonality              : %.4f"
          % (ortho.max() if len(ortho) else 0.0))
    for t in (0.02, 0.05, 0.1, 0.2, 0.5):
        print("  edges with ortho > %-5g        : %d" % (t, (ortho > t).sum()))
    print("  edges, coincident circumcentres: %d   <-- zero-length flow link"
          % res["edge_zerolink"].sum())
    print("  edges, circumcentres same side : %d   <-- flow link misses edge"
          % res["edge_sameside"].sum())
    print("  cells, circumcentre outside    : %d" % res["cc_out"].sum())
    print("  cells non-convex / zero area   : %d" % res["nonconvex"].sum())
    print("  cells on edges shared by >2    : %d" % res["overlap"].sum())
    print("  cells flagged (ortho > %g or any defect): %d"
          % (th, res["flagged"].sum()))

    mo = res["max_ortho"]
    worst = np.argsort(mo)[::-1][:10]
    print("\n  10 worst cells (FACE_ID, max_ortho, x, y):")
    for k in worst:
        print("   %9d  %.4f  %.2f  %.2f" % (k, mo[k], res["cx"][k], res["cy"][k]))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Locate non-orthogonal / problematic 2D cells in a D-Flow FM "
                    "net file and export them as a polygon shapefile.",
        epilog="Flags per cell: ZERO_LINK (coincident circumcentres), SAMESIDE "
               "(flow link misses the edge), CC_OUT (circumcentre outside cell), "
               "NONCONVX (non-convex / clockwise / zero area), OVERLAP (edge "
               "shared by >2 cells). FACE_ID is 0-based.")
    ap.add_argument("model",
                    help="model input folder, the .mdu, or the *_net.nc itself")
    ap.add_argument("-t", "--threshold", type=float, default=0.1,
                    help="orthogonality threshold |cos| (default 0.1)")
    ap.add_argument("-o", "--output", default=None,
                    help="output polygon shapefile "
                         "(default <netfile>_nonortho_cells.shp)")
    ap.add_argument("--edges", action="store_true",
                    help="also write a polyline shapefile of the offending edges")
    ap.add_argument("--all", action="store_true",
                    help="export ALL cells with attributes (large file!)")
    ap.add_argument("--check", action="store_true",
                    help="print the summary only, write nothing")
    args = ap.parse_args(argv)

    t0 = time.time()
    try:
        netfile = resolve_netfile(args.model)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))
    out = args.output or os.path.splitext(netfile)[0] + "_nonortho_cells.shp"

    print("Reading", netfile)
    res = check_orthogonality(netfile, args.threshold)
    print("  mesh '%s': %d nodes, %d cells"
          % (res["mesh"], len(res["x"]), len(res["fn"])))
    print_summary(res)

    if args.check:
        print("\nCheck only - nothing written.")
        return 1 if res["flagged"].any() else 0

    sel = np.arange(len(res["fn"])) if args.all else np.nonzero(res["flagged"])[0]
    if len(sel) == 0:
        print("\nNo cells flagged - nothing written.")
        return 0

    print("\nWriting %d cells -> %s" % (len(sel), out))
    write_cells(out, res, sel)

    if args.edges:
        eout = os.path.splitext(out)[0] + "_edges.shp"
        ei = np.nonzero(res["bad_edge"] | res["edge_zerolink"])[0]
        print("Writing %d edges -> %s" % (len(ei), eout))
        write_edges(eout, res, ei)

    print("Done in %.1f s" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
