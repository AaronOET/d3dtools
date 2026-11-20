#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rasmshint - interpolate a raster (GeoTIFF) onto a 2D UGRID mesh and write
the result, as the new 2D grid, into the net file of a D-Flow FM (Delft3D FM
/ D-HYDRO, 1D2D) model.

Steps
  1. Read the 2D mesh from the mesh file (e.g. ras2msh output,
     MESH/dem_150m_net.nc).
  2. Interpolate the raster onto the mesh:
       faces (cells): mean / median / min / max of the raster pixels inside
                      each cell, or bilinear / nearest at the cell centre
       nodes:         bilinear / nearest at the node, or the mean of the
                      adjacent faces
     Cells / nodes without raster data keep the mesh's existing z (if any),
     otherwise get --fill-value (-999 = "no data" for D-Flow FM).
  3. Write into the model's net file (NetFile in the .mdu):
       - the 2D mesh is replaced by the new mesh, with <mesh>_node_z and
         <mesh>_face_z (named after the model's 2D mesh, e.g. mesh2d)
       - everything 1D (network1d, mesh1d, ...) is copied unchanged
       - 1D2D links are re-attached to the new 2D cell holding the old linked
         cell's centre; links that fall outside the new mesh are dropped
         (reported)
  4. Set BedlevType in the .mdu to match --bedlev-location (face -> 1,
     node -> --node-bedlevtype, default 3), unless --keep-mdu.
  The net file and the .mdu are backed up as
  <name>_backup_<yyyymmdd>_<HHMMSS>.<ext> (clrbak removes them).

Usage
-----
    rasmshint <project.dsproj | input-folder | model.mdu> <mesh_net.nc> <raster.tif>
    rasmshint FM_model.dsproj MESH/dem_150m_net.nc RAS/dem.tif
    rasmshint dflowfm/FlowFM.mdu m_net.nc dem.tif --face-method median \\
              --bedlev-location node --node-method bilinear
    rasmshint FM_model.dsproj m_net.nc dem.tif --check   (report, write nothing)

IMPORTANT: close the project in D-HYDRO (without saving) before running, then
reopen it.  Saving an open project overwrites the net file.

Requires: numpy, netCDF4, scipy (optional, faster link re-attachment) and one
of rasterio / GDAL (osgeo) / tifffile.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

import numpy as np

from .intdata import (METHODS, Raster, backup, cell_values, mdu_get, mdu_set,
                      node_values_from_faces, pick_mdu, points_in_polygon,
                      read_mesh2d, read_text)
from .ncutils import open_nc

SCRIPT = "rasmshint"
NODE_METHODS = ("bilinear", "nearest", "faces")


# --------------------------------------------------------------------------- #
# UGRID helpers
# --------------------------------------------------------------------------- #
def find_mesh2d(ds):
    """Name of the 2D mesh_topology variable, or None."""
    for name, v in ds.variables.items():
        if getattr(v, "cf_role", "") == "mesh_topology" and \
                int(getattr(v, "topology_dimension", 0)) == 2:
            return name
    return None


def mesh_vars(ds, name):
    """All variables of a 2D mesh topology (the topology variable and those
    referencing it or using its dimensions), and its dimensions."""
    t = ds[name]
    dims = {getattr(t, a) for a in ("node_dimension", "edge_dimension", "face_dimension",
                                    "max_face_nodes_dimension") if hasattr(t, a)}
    dims |= {d for d in ds.dimensions if d.startswith(name + "_")}
    out = {name}
    for vn, v in ds.variables.items():
        if getattr(v, "mesh", None) == name or vn.startswith(name + "_") or \
                (set(v.dimensions) & dims):
            out.add(vn)
    return out, dims


def contact_vars(ds, mesh2d_name):
    """1D2D contact (link) variables that point to the 2D mesh."""
    return [vn for vn, v in ds.variables.items()
            if getattr(v, "cf_role", "") == "mesh_topology_contact"
            and mesh2d_name in str(getattr(v, "contact", "")).replace(":", " ").split()]


def link_aux_vars(ds, link):
    """Variables (other than `link`) on the link dimension, e.g. ids, types."""
    ldim = ds[link].dimensions[0]
    return [vn for vn, v in ds.variables.items()
            if vn != link and v.dimensions[:1] == (ldim,)]


def locate_faces(mesh, px, py, k=8):
    """Index of the face containing each point (-1 if none). The `k` nearest
    face centres are the candidates, verified with point-in-polygon."""
    res = -np.ones(len(px), dtype=np.int64)
    if not len(px) or not len(mesh["fx"]):
        return res
    centres = np.column_stack([mesh["fx"], mesh["fy"]])
    k = min(k, len(centres))
    try:
        from scipy.spatial import cKDTree
        _, cand = cKDTree(centres).query(np.column_stack([px, py]), k=k)
        cand = np.asarray(cand).reshape(len(px), k)
    except ImportError:
        cand = None
    for i, (x, y) in enumerate(zip(px, py)):
        if cand is not None:
            order = cand[i]
        else:
            d = (centres[:, 0] - x) ** 2 + (centres[:, 1] - y) ** 2
            order = np.argpartition(d, k - 1)[:k]
            order = order[np.argsort(d[order])]
        for f in order:
            if points_in_polygon(np.array([x]), np.array([y]), mesh["polys"][f])[0]:
                res[i] = f
                break
    return res


def reattach_links(psrc, old2d, old_mesh, new_mesh):
    """
    Re-attach every 1D2D link variable of the project to the new 2D mesh.

    Returns {link name: (new link array, keep mask, 2D column, start index)}.
    """
    out = {}
    for ln in contact_vars(psrc, old2d):
        lv = psrc[ln]
        lv.set_auto_maskandscale(False)
        L = np.array(lv[:]).astype(np.int64)
        si = int(getattr(lv, "start_index", 0))
        parts = [p.split(":")[0].strip() for p in str(lv.contact).split() if ":" in p]
        col2d = parts.index(old2d) if old2d in parts else 1
        oldf = L[:, col2d] - si
        ok = (oldf >= 0) & (oldf < len(old_mesh["fx"]))
        newf = -np.ones(len(L), dtype=np.int64)
        newf[ok] = locate_faces(new_mesh, old_mesh["fx"][oldf[ok]], old_mesh["fy"][oldf[ok]])
        keep = newf >= 0
        L[:, col2d] = np.where(keep, newf + si, L[:, col2d])
        out[ln] = (L, keep, col2d, si)
    return out


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def rename_refs(text, rename):
    """Rename the plain words (not 'name:' references) of an attribute."""
    return " ".join(rename(w) if ":" not in w else w for w in text.split(" "))


def copy_var(dst, src_v, name=None, data=None, rename=None):
    """Copy a variable (attributes + data, or `data`) into `dst`."""
    rename = rename or (lambda s: s)
    name = name or src_v.name
    dims = tuple(rename(d) for d in src_v.dimensions)
    att = {k: src_v.getncattr(k) for k in src_v.ncattrs()}
    fill = att.pop("_FillValue", None)
    dt = src_v.datatype
    is_char = dt == "S1" or getattr(dt, "char", "") == "S"
    v = dst.createVariable(name, dt, dims, fill_value=fill)
    v.set_auto_maskandscale(False)
    if is_char:
        v.set_auto_chartostring(False)
    for k, val in att.items():
        if isinstance(val, str):
            val = rename_refs(val, rename)
        v.setncattr(k, val)
    if data is None:
        src_v.set_auto_maskandscale(False)
        if is_char:
            src_v.set_auto_chartostring(False)
        data = src_v[:]
    if np.ndim(data) == 0:
        v.assignValue(data)
    elif np.size(data):
        v[:] = data
    return v


def ensure_dim(dst, name, size):
    if name in dst.dimensions:
        cur = dst.dimensions[name]
        if size is not None and not cur.isunlimited() and len(cur) != size:
            raise RuntimeError("dimension clash: %s (%d vs %d)" % (name, len(cur), size))
    else:
        dst.createDimension(name, size)


def build_net(project_nc, mesh_nc, out_nc, node_z, face_z, fill=-999.0, log=print):
    """
    Write `out_nc`: the content of `project_nc` (may be None) with its 2D mesh
    replaced by the 2D mesh of `mesh_nc` (plus node_z / face_z) and its 1D2D
    links re-attached to the new cells.

    Returns {link name: (n kept, n total)}.
    """
    links_res = {}
    msrc = open_nc(mesh_nc)
    psrc = open_nc(project_nc) if project_nc and os.path.exists(project_nc) else None
    try:
        m2_src = find_mesh2d(msrc)
        if m2_src is None:
            raise ValueError("no 2D mesh topology in %s" % mesh_nc)
        old2d = find_mesh2d(psrc) if psrc else None
        m2_name = old2d or "mesh2d"

        def rn(s):  # source-mesh names -> the project's 2D mesh name
            if s == m2_src or s.startswith(m2_src + "_"):
                return m2_name + s[len(m2_src):]
            return s

        fmt = psrc.data_model if psrc else msrc.data_model
        with open_nc(out_nc, "w", format=fmt) as dst:
            gsrc = psrc or msrc
            dst.setncatts({k: gsrc.getncattr(k) for k in gsrc.ncattrs()})
            dst.history = ("%s; %s: 2D mesh replaced from %s and bed level interpolated "
                           "by d3dtools %s" % (getattr(gsrc, "history", ""),
                                               datetime.datetime.now().isoformat(timespec="seconds"),
                                               os.path.basename(mesh_nc), SCRIPT)).strip("; ")

            # ---- 1) project content except the old 2D mesh and its links
            skip, links = set(), {}
            if psrc:
                if old2d:
                    skip |= mesh_vars(psrc, old2d)[0]
                    old_mesh = read_mesh2d(psrc, old2d)
                    new_mesh = read_mesh2d(msrc, m2_src)
                    links = reattach_links(psrc, old2d, old_mesh, new_mesh)
                    for ln in links:
                        skip.add(ln)
                        skip |= set(link_aux_vars(psrc, ln))
                for d, D in psrc.dimensions.items():
                    if any(d in v.dimensions for vn, v in psrc.variables.items()
                           if vn not in skip):
                        ensure_dim(dst, d, None if D.isunlimited() else len(D))
                for vn, v in psrc.variables.items():
                    if vn not in skip:
                        copy_var(dst, v)
                kept = [vn for vn, v in psrc.variables.items()
                        if vn not in skip and getattr(v, "cf_role", "") == "mesh_topology"]
                if kept:
                    log("  kept from the model net file: %s" % ", ".join(kept))

            # ---- 2) the new 2D mesh
            mv, mdims = mesh_vars(msrc, m2_src)
            for d in sorted(mdims | {d for vn in mv for d in msrc[vn].dimensions}):
                ensure_dim(dst, rn(d), len(msrc.dimensions[d]))
            for c in (vn for vn, v in msrc.variables.items()
                      if hasattr(v, "epsg") and vn not in mv):
                if c not in dst.variables:
                    copy_var(dst, msrc[c])
            grid_mapping = next((c for c in dst.variables if hasattr(dst[c], "epsg")), None)

            t = msrc[m2_src]
            nx, ny = t.node_coordinates.split()[:2]
            for vn in sorted(mv, key=lambda s: (s != m2_src, s)):
                if vn in ("%s_node_z" % m2_src, "%s_face_z" % m2_src):
                    continue
                copy_var(dst, msrc[vn], name=rn(vn), rename=rn)
            zatt = dict(units="m", standard_name="altitude", mesh=m2_name)
            if grid_mapping:
                zatt["grid_mapping"] = grid_mapping
            v = dst.createVariable("%s_node_z" % m2_name, "f8", (rn(t.node_dimension),),
                                   fill_value=fill)
            v.setncatts(dict(zatt, long_name="z-coordinate of mesh nodes", location="node",
                             coordinates="%s %s" % (rn(nx), rn(ny))))
            v[:] = node_z
            fc = getattr(t, "face_coordinates", "%s_face_x %s_face_y" % (m2_src, m2_src))
            v = dst.createVariable("%s_face_z" % m2_name, "f8", (rn(t.face_dimension),),
                                   fill_value=fill)
            v.setncatts(dict(zatt, long_name="z-coordinate of mesh faces", location="face",
                             coordinates=rename_refs(fc, rn)))
            v[:] = face_z

            # ---- 3) the 1D2D links, re-attached to the new 2D faces
            for ln, (L, keep, _, _) in links.items():
                lv = psrc[ln]
                ensure_dim(dst, lv.dimensions[0], int(keep.sum()))
                for d in lv.dimensions[1:]:
                    ensure_dim(dst, d, len(psrc.dimensions[d]))
                copy_var(dst, lv, data=L[keep])
                for vn in link_aux_vars(psrc, ln):
                    v2 = psrc[vn]
                    for d in v2.dimensions[1:]:
                        ensure_dim(dst, d, len(psrc.dimensions[d]))
                    v2.set_auto_maskandscale(False)
                    if v2.dtype == "S1" or str(v2.dtype).startswith("|S"):
                        v2.set_auto_chartostring(False)
                    copy_var(dst, v2, data=np.asarray(v2[:])[keep])
                links_res[ln] = (int(keep.sum()), len(keep))
    finally:
        msrc.close()
        if psrc:
            psrc.close()
    return links_res


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
RASTER_EXT = (".tif", ".tiff", ".gtif", ".gtiff")


def order_inputs(mesh, raster, log=print):
    """Return (mesh, raster), swapped when given the other way round (a
    GeoTIFF as the mesh and a .nc as the raster)."""
    if mesh.lower().endswith(RASTER_EXT) and raster.lower().endswith(".nc"):
        log("NOTE: mesh and raster given the other way round - using %s as the mesh "
            "and %s as the raster" % (raster, mesh))
        return raster, mesh
    if mesh.lower().endswith(RASTER_EXT):
        raise ValueError("the mesh (%s) is a raster - give <model> <mesh_net.nc> "
                         "<raster.tif>" % mesh)
    if raster.lower().endswith(".nc"):
        raise ValueError("the raster (%s) is a NetCDF file - give <model> "
                         "<mesh_net.nc> <raster.tif>" % raster)
    return mesh, raster


def interpolate_mesh(model, mesh, raster, face_method="mean", node_method="bilinear",
                     bedlev_location="face", node_bedlevtype=3, keep_mdu=False,
                     nodata=None, fill_value=-999.0, model_name=None, write=True,
                     log=print):
    """
    Interpolate `raster` onto the 2D mesh of `mesh` and put that mesh, with
    its bed levels, into the net file of a D-Flow FM model.

    Parameters
    ----------
    model : str
        .dsproj, model input folder or .mdu.
    mesh : str
        UGRID net file holding the new 2D mesh (e.g. from ras2msh).
    raster : str
        GeoTIFF with the bed levels, in the mesh's CRS.
    face_method : str
        mean / median / min / max of the pixels in a cell, or bilinear /
        nearest at the cell centre.
    node_method : str
        bilinear / nearest at the node, or faces (mean of adjacent cells).
    bedlev_location : str
        face -> BedlevType 1, node -> `node_bedlevtype` (3, 4, 5 or 6).
    keep_mdu : bool
        Do not change the .mdu.
    nodata : float, optional
        Override the raster nodata value.
    fill_value : float
        Value for cells / nodes without raster data and no existing z.
    model_name : str, optional
        The model to use when a .dsproj has several.
    write : bool
        False interpolates and reports (incl. the link re-attachment),
        writes nothing.

    Returns
    -------
    dict
        mdu, net, face_z, node_z, links ({name: (kept, total)}), backups
        (list), mdu_change (str or None).
    """
    if face_method not in METHODS:
        raise ValueError("face_method must be one of %s" % ", ".join(METHODS))
    if node_method not in NODE_METHODS:
        raise ValueError("node_method must be one of %s" % ", ".join(NODE_METHODS))
    if bedlev_location not in ("face", "node"):
        raise ValueError("bedlev_location must be face or node")
    if int(node_bedlevtype) not in (3, 4, 5, 6):
        raise ValueError("node_bedlevtype must be 3, 4, 5 or 6")
    mesh, raster = order_inputs(mesh, raster, log)
    if not os.path.isfile(mesh):
        raise FileNotFoundError("mesh file not found: %s" % mesh)

    mdu = pick_mdu(model, model_name)
    mdir = os.path.dirname(mdu)
    mdu_text, mdu_enc = read_text(mdu)
    net = os.path.join(mdir, mdu_get(mdu_text, "geometry", "NetFile") or "FlowFM_net.nc")
    if os.path.abspath(net) == os.path.abspath(mesh):
        raise ValueError("the mesh file is the model's own net file")
    log("Model    : %s" % mdu)
    log("Net file : %s%s" % (net, "" if os.path.isfile(net) else " (new)"))

    R = Raster(raster, nodata)
    log("Raster   : %d x %d, %g x %g m, EPSG:%s" % (R.nc, R.nr, R.rx, R.ry, R.epsg))

    with open_nc(mesh) as ds:
        name = find_mesh2d(ds)
        if not name:
            raise ValueError("no 2D mesh in %s" % mesh)
        M = read_mesh2d(ds, name)
        epsg_m = next((int(v.epsg) for v in ds.variables.values()
                       if hasattr(v, "epsg") and str(v.epsg).strip().lstrip("-").isdigit()),
                      None)
        old = {loc: np.ma.filled(ds["%s_%s_z" % (name, loc)][:].astype(float), np.nan)
               if "%s_%s_z" % (name, loc) in ds.variables else None
               for loc in ("node", "face")}
    log("Mesh     : %s, %d nodes, %d faces, EPSG:%s"
        % (os.path.basename(mesh), len(M["x"]), len(M["polys"]), epsg_m))
    if R.epsg and epsg_m and R.epsg != epsg_m:
        log("WARNING: raster EPSG:%s differs from mesh EPSG:%s (no reprojection is done)"
            % (R.epsg, epsg_m))

    # ---- interpolation
    fz = cell_values(M, R, face_method)
    if node_method == "faces":
        nz = node_values_from_faces(M["fn"], len(M["x"]), fz)
    else:
        nz = getattr(R, node_method)(M["x"], M["y"])
    for label, z, prev in (("faces", fz, old["face"]), ("nodes", nz, old["node"])):
        miss = ~np.isfinite(z)
        if miss.any():
            if prev is not None:
                z[miss] = prev[miss]
            z[~np.isfinite(z)] = fill_value
            log("%-9s: %d without raster data -> %s%g"
                % (label, miss.sum(), "existing z / " if prev is not None else "", fill_value))
        good = z != fill_value
        if good.any():
            log("%-9s: z %.3f .. %.3f m (mean %.3f)"
                % (label, z[good].min(), z[good].max(), z[good].mean()))
        else:
            log("WARNING: no %s received a value - check the raster extent / nodata / CRS"
                % label)

    res = dict(mdu=mdu, net=net, face_z=fz, node_z=nz, links={}, backups=[],
               mdu_change=None)
    blt = 1 if bedlev_location == "face" else int(node_bedlevtype)
    old_blt = mdu_get(mdu_text, "geometry", "BedlevType")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ---- write (or, with write=False, only report the links)
    if write:
        b = backup(net, stamp)
        if b:
            res["backups"].append(b)
            log("Backup   : %s" % b)
        tmp = net + ".tmp"
        try:
            res["links"] = build_net(net if os.path.isfile(net) else None, mesh, tmp,
                                     nz, fz, fill_value, log)
            os.replace(tmp, net)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    elif os.path.isfile(net):
        with open_nc(net) as ps, open_nc(mesh) as ms:
            old2d = find_mesh2d(ps)
            if old2d:
                links = reattach_links(ps, old2d, read_mesh2d(ps, old2d), M)
                res["links"] = {ln: (int(k.sum()), len(k)) for ln, (_, k, _, _) in links.items()}
    for ln, (kept, total) in res["links"].items():
        log("1D2D links '%s': %d of %d re-attached%s"
            % (ln, kept, total, ", %d dropped (outside the new 2D mesh)" % (total - kept)
               if kept < total else ""))
    if write:
        log("Written  : %s" % net)

    # ---- .mdu
    if keep_mdu:
        if str(old_blt) != str(blt):
            log("NOTE: --keep-mdu given; set BedlevType = %s yourself (now %s)" % (blt, old_blt))
    elif str(old_blt) == str(blt):
        log("MDU      : BedlevType already %s" % blt)
    else:
        res["mdu_change"] = "BedlevType %s -> %s" % (old_blt, blt)
        if write:
            text, _ = mdu_set(mdu_text, "geometry", "BedlevType", blt)
            b = backup(mdu, stamp)
            res["backups"].append(b)
            with open(mdu, "wb") as f:
                f.write(text.encode(mdu_enc))
            log("MDU      : %s (backup %s)" % (res["mdu_change"], b))
        else:
            log("MDU      : would change %s" % res["mdu_change"])

    for section, key in (("external forcing", "ExtForceFile"),
                         ("external forcing", "ExtForceFileNew"),
                         ("geometry", "IniFieldFile")):
        fn = mdu_get(mdu_text, section, key)
        fp = os.path.join(mdir, fn) if fn else None
        if fp and os.path.isfile(fp):
            text, _ = read_text(fp)
            if re.search(r"quantity\s*=\s*bedlevel", text, re.IGNORECASE):
                log("WARNING: %s also sets 'bedlevel' - it overrides the net-file bed "
                    "level." % fn)
    return res


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Interpolate a raster (GeoTIFF) onto a 2D UGRID mesh and write it, "
                    "with its bed levels, as the 2D grid of a D-Flow FM / D-HYDRO (1D2D) "
                    "model's net file. The 1D network is kept; 1D2D links are "
                    "re-attached to the new cells.",
        epilog="""
examples:
  %(prog)s FM_model.dsproj MESH/dem_150m_net.nc RAS/dem.tif
  %(prog)s dflowfm/FlowFM.mdu m_net.nc dem.tif --face-method median
  %(prog)s FM_model.dsproj m_net.nc dem.tif --bedlev-location node --node-bedlevtype 3
  %(prog)s MyProject.dsproj m_net.nc dem.tif -m TNN_JJ   (project with several models)
  %(prog)s FM_model.dsproj m_net.nc dem.tif --check      (report, write nothing)

The net file and the .mdu are backed up as <name>_backup_<yyyymmdd>_<HHMMSS>.<ext>
(clrbak removes them). Close the project in D-HYDRO (without saving) before
running, then reopen it - saving an open project overwrites the net file.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help=".dsproj, model input folder or .mdu")
    ap.add_argument("mesh", help="UGRID net file with the new 2D mesh (e.g. from ras2msh)")
    ap.add_argument("raster", help="raster (GeoTIFF) with the bed levels")
    ap.add_argument("-m", "--model-name", metavar="NAME",
                    help="the model (.mdu name or its folder) to use when a project "
                         "has several")
    ap.add_argument("--face-method", default="mean", choices=METHODS,
                    help="cell value: statistic of the pixels in the cell, or sampled "
                         "at the cell centre (default mean)")
    ap.add_argument("--node-method", default="bilinear", choices=NODE_METHODS,
                    help="node value: sampled at the node, or the mean of the adjacent "
                         "faces (default bilinear)")
    ap.add_argument("--bedlev-location", default="face", choices=["face", "node"],
                    help="values D-Flow FM uses: face -> BedlevType 1, node -> "
                         "--node-bedlevtype (default face)")
    ap.add_argument("--node-bedlevtype", type=int, default=3, choices=[3, 4, 5, 6],
                    help="BedlevType with --bedlev-location node (3 mean, 4 min, 5 max, "
                         "6 cell mean; default 3)")
    ap.add_argument("--keep-mdu", action="store_true", help="do not change the .mdu")
    ap.add_argument("--nodata", type=float, default=None,
                    help="override the raster nodata value")
    ap.add_argument("--fill-value", type=float, default=-999.0,
                    help="value for cells / nodes without raster data and no existing z "
                         "(default -999)")
    ap.add_argument("--check", action="store_true",
                    help="interpolate and report (incl. the 1D2D links), write nothing")
    args = ap.parse_args(argv)

    try:
        interpolate_mesh(args.model, args.mesh, args.raster, args.face_method,
                         args.node_method, args.bedlev_location, args.node_bedlevtype,
                         args.keep_mdu, args.nodata, args.fill_value, args.model_name,
                         write=not args.check)
    except (ValueError, FileNotFoundError, ImportError, OSError, RuntimeError) as exc:
        print("%s: error: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1
    if args.check:
        print("\nCheck only - nothing written.")
    else:
        print("\nClose the project in D-HYDRO without saving (if open), then reopen it "
              "to load the new mesh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
