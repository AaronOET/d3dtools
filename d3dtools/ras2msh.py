#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ras2msh - create a standalone regular 2D D-Flow FM (Delft3D FM) mesh, as a
UGRID net file (*_net.nc), from a GeoTIFF.

Nothing is written into a D-HYDRO project: import the file later via
D-HYDRO (FM model > Grid > Import, UGRID / *_net.nc) or set NetFile in a .mdu.

- Cells of size dx x dy, aligned to the raster's top-left corner, covering the
  raster's valid (non-nodata) area.
- A cell is kept if at least --min-valid-fraction of it lies on valid data.
- Bed level: cell = mean of the valid raster values inside it; node = mean of
  its adjacent cells (Mesh2d_node_z). --no-z leaves it out.
- --xyz also writes the cell-centre bed levels as an "x y z" samples file
  (to import as bathymetry samples later).

Usage
-----
    ras2msh dem.tif --dx 150            (-> ./MESH/dem_150m_net.nc)
    ras2msh dem.tif --dx 200 --dy 100 --xyz
                                        (-> ./MESH/dem_200x100m_net.nc
                                            + ./MESH/dem_200x100m_net_cellz.xyz)
    ras2msh dem.tif --dx 100 -of D:/meshes
    ras2msh dem.tif --dx 100 -o grid_100m_net.nc
    ras2msh dem.tif --dx 100 --check    (raster + mesh summary, write nothing)

Requires: numpy, netCDF4 and one of rasterio / GDAL (osgeo) / tifffile;
pyproj (optional) for the coordinate-system attributes.
"""

from __future__ import annotations

import argparse
import datetime
import math
import os
import sys
import warnings

import numpy as np

SCRIPT = "ras2msh"

TWD97_TM2_WKT = (
    'PROJCS["TWD97 / TM2 zone 121",GEOGCS["TWD97",DATUM["Taiwan_Datum_1997",'
    'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],'
    'AUTHORITY["EPSG","1026"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","3824"]],'
    'PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],'
    'PARAMETER["central_meridian",121],PARAMETER["scale_factor",0.9999],'
    'PARAMETER["false_easting",250000],PARAMETER["false_northing",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH],'
    'AUTHORITY["EPSG","3826"]]'
)


# --------------------------------------------------------------------------- #
# Raster reading
# --------------------------------------------------------------------------- #
def read_raster(path):
    """
    Read band 1 of a north-up GeoTIFF.

    Returns a dict: data (float array, nrows x ncols), x0, y0 (top-left
    corner), resx, resy (> 0), nodata (or None), epsg (or None).
    Uses rasterio, else GDAL (osgeo), else tifffile.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError("raster not found: %s" % path)

    def out(a, x0, y0, rx, ry, nodata, epsg):
        return dict(data=a, x0=float(x0), y0=float(y0), resx=float(rx),
                    resy=float(ry), nodata=nodata, epsg=epsg)

    try:
        import rasterio
    except ImportError:
        rasterio = None
    if rasterio is not None:
        with warnings.catch_warnings():   # NotGeoreferencedWarning: checked below
            warnings.simplefilter("ignore")
            src = rasterio.open(path)
        with src:
            if src.count < 1:
                raise ValueError("%s has no raster band - is it a raster (GeoTIFF)?"
                                 % path)
            t = src.transform
            if t.is_identity and not src.crs:
                raise ValueError("%s is not a georeferenced raster (no geotransform / "
                                 "CRS)" % path)
            a = src.read(1).astype(float)
            if t.b != 0 or t.d != 0 or t.e >= 0:
                raise ValueError("only north-up, non-rotated rasters are supported")
            epsg = src.crs.to_epsg() if src.crs else None
            return out(a, t.c, t.f, t.a, -t.e, src.nodata, epsg)

    try:
        from osgeo import gdal, osr
    except ImportError:
        gdal = None
    if gdal is not None:
        ds = gdal.Open(path)
        if ds is None:
            raise OSError("cannot open raster: %s" % path)
        b = ds.GetRasterBand(1)
        a = b.ReadAsArray().astype(float)
        g = ds.GetGeoTransform()
        if g[2] != 0 or g[4] != 0 or g[5] >= 0:
            raise ValueError("only north-up, non-rotated rasters are supported")
        epsg = None
        if ds.GetProjection():
            sr = osr.SpatialReference(wkt=ds.GetProjection())
            sr.AutoIdentifyEPSG()
            code = sr.GetAuthorityCode(None)
            epsg = int(code) if code else None
        return out(a, g[0], g[3], g[1], -g[5], b.GetNoDataValue(), epsg)

    try:
        import tifffile
    except ImportError:
        raise ImportError("install one of: rasterio, GDAL (osgeo) or tifffile")
    with tifffile.TiffFile(path) as tf:
        p = tf.pages[0]
        a = p.asarray().astype(float)
        if a.ndim > 2:
            a = a[..., 0] if a.shape[-1] < a.shape[0] else a[0]
        tags = {t.name: t.value for t in p.tags.values()}
        if "ModelPixelScaleTag" not in tags or "ModelTiepointTag" not in tags:
            raise ValueError("not a georeferenced GeoTIFF: %s" % path)
        sx, sy = tags["ModelPixelScaleTag"][:2]
        tp = tags["ModelTiepointTag"]
        i, j, x, y = tp[0], tp[1], tp[3], tp[4]
        x0, y0 = x - i * sx, y + j * sy
        nodata = tags.get("GDAL_NODATA")
        nodata = float(str(nodata).strip("\x00 ")) if nodata is not None else None
        epsg = None
        gk = tags.get("GeoKeyDirectoryTag")
        if gk:
            for k in range(4, len(gk), 4):
                # ProjectedCSTypeGeoKey (3072) / GeographicTypeGeoKey (2048)
                if gk[k] in (3072, 2048) and gk[k + 1] == 0:
                    epsg = int(gk[k + 3])
                    if gk[k] == 3072:
                        break
        return out(a, x0, y0, sx, sy, nodata, epsg)


# --------------------------------------------------------------------------- #
# Mesh building
# --------------------------------------------------------------------------- #
def build_mesh(a, x0, y0, rx, ry, nodata, dx, dy, min_frac=0.5, sub=None):
    """
    Build a regular dx x dy mesh over the valid area of raster `a`.

    Each mesh cell is sampled on a sub-grid of points (about one per raster
    cell, or `sub` x `sub`); a cell is kept when at least `min_frac` of the
    points lie on valid data. Returns a dict with node_x/y/z, faces (0-based,
    counter-clockwise), face_x/y/z, edges and edge_faces (-1 = boundary).
    """
    nr, nc = a.shape
    valid = np.isfinite(a)
    if nodata is not None:
        valid &= a != nodata

    width, height = nc * rx, nr * ry
    ncx, ncy = math.ceil(width / dx - 1e-9), math.ceil(height / dy - 1e-9)

    # sub-sample each mesh cell on an (sx x sy) grid of points
    sx = sub or max(1, math.ceil(dx / rx))
    sy = sub or max(1, math.ceil(dy / ry))
    ox = (np.arange(sx) + 0.5) / sx * dx           # offsets within a cell
    oy = (np.arange(sy) + 0.5) / sy * dy
    px = (np.arange(ncx)[:, None] * dx + ox[None, :]).ravel()     # distance from x0
    py = (np.arange(ncy)[:, None] * dy + oy[None, :]).ravel()     # distance below y0
    col = np.floor(px / rx).astype(int)
    row = np.floor(py / ry).astype(int)
    incol, inrow = (col >= 0) & (col < nc), (row >= 0) & (row < nr)
    colc, rowc = np.clip(col, 0, nc - 1), np.clip(row, 0, nr - 1)

    v = valid[np.ix_(rowc, colc)] & inrow[:, None] & incol[None, :]
    z = np.where(v, a[np.ix_(rowc, colc)], 0.0)
    v = v.reshape(ncy, sy, ncx, sx)
    z = z.reshape(ncy, sy, ncx, sx)
    nvalid = v.sum(axis=(1, 3))
    frac = nvalid / (sx * sy)
    keep = (frac >= min_frac) & (nvalid > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        cellz = z.sum(axis=(1, 3)) / nvalid

    # nodes / faces
    nid = -np.ones((ncy + 1, ncx + 1), dtype=np.int64)
    rows_k, cols_k = np.nonzero(keep)                        # row 0 = north
    corners = [(1, 0), (1, 1), (0, 1), (0, 0)]               # SW, SE, NE, NW -> CCW
    for di, dj in corners:
        nid[rows_k + di, cols_k + dj] = 0
    ni, nj = np.nonzero(nid >= 0)
    nid[ni, nj] = np.arange(len(ni))
    faces = np.stack([nid[rows_k + di, cols_k + dj] for di, dj in corners], axis=1) \
        if len(rows_k) else np.zeros((0, 4), dtype=np.int64)
    node_x = x0 + nj * dx
    node_y = y0 - ni * dy
    face_x = x0 + (cols_k + 0.5) * dx
    face_y = y0 - (rows_k + 0.5) * dy
    face_z = cellz[rows_k, cols_k]

    # node z = mean of the adjacent kept cells
    zs = np.zeros(len(ni))
    cnt = np.zeros(len(ni))
    for k in range(4):
        np.add.at(zs, faces[:, k], face_z)
        np.add.at(cnt, faces[:, k], 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        node_z = zs / cnt

    # edges + edge-face connectivity
    e = np.concatenate([np.stack([faces[:, k], faces[:, (k + 1) % 4]], 1) for k in range(4)])
    ef = np.concatenate([np.arange(len(faces))] * 4)
    e.sort(axis=1)
    uniq, inv = np.unique(e, axis=0, return_inverse=True)
    inv = inv.ravel()
    edge_faces = -np.ones((len(uniq), 2), dtype=np.int64)
    order = np.argsort(inv, kind="stable")
    first = np.ones(len(order), bool)
    first[1:] = inv[order][1:] != inv[order][:-1]
    edge_faces[inv[order][first], 0] = ef[order][first]
    edge_faces[inv[order][~first], 1] = ef[order][~first]

    return dict(node_x=node_x, node_y=node_y, node_z=node_z, faces=faces,
                face_x=face_x, face_y=face_y, face_z=face_z, edges=uniq,
                edge_faces=edge_faces, ncx=ncx, ncy=ncy)


# --------------------------------------------------------------------------- #
# Writing the UGRID net file
# --------------------------------------------------------------------------- #
def crs_attrs(epsg):
    """Return (variable name, attributes) of the coordinate-system variable."""
    epsg = epsg or 3826
    att = dict(epsg=epsg, EPSG_code="EPSG:%d" % epsg,
               grid_mapping_name="Unknown projected",
               longitude_of_prime_meridian=0.0, projection_name="unknown")
    try:
        from pyproj import CRS
        c = CRS.from_epsg(epsg)
        with warnings.catch_warnings():     # "lose projection information"
            warnings.simplefilter("ignore", UserWarning)
            proj4 = c.to_proj4()
        att.update(name=c.name, wkt=c.to_wkt("WKT1_GDAL"), proj4_params=proj4,
                   semi_major_axis=c.ellipsoid.semi_major_metre,
                   semi_minor_axis=c.ellipsoid.semi_minor_metre,
                   inverse_flattening=c.ellipsoid.inverse_flattening)
        if c.is_geographic:
            return "wgs84", att
    except Exception:
        if epsg == 3826:
            att.update(name="TWD97 / TM2 zone 121", wkt=TWD97_TM2_WKT,
                       proj4_params="+proj=tmerc +lat_0=0 +lon_0=121 +k=0.9999 +x_0=250000 "
                                    "+y_0=0 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs",
                       semi_major_axis=6378137.0, semi_minor_axis=6356752.314140356,
                       inverse_flattening=298.257222101)
    return "projected_coordinate_system", att


def write_net(path, m, crs_name, crs_att, write_z=True, history=""):
    """Write mesh `m` (from build_mesh) as a UGRID-1.0 D-Flow FM net file."""
    import netCDF4

    d = netCDF4.Dataset(path, "w", format="NETCDF3_CLASSIC")
    try:
        d.setncatts({"institution": "Deltares",
                     "references": "https://github.com/ugrid-conventions/ugrid-conventions",
                     "source": "d3dtools %s" % SCRIPT,
                     "history": history,
                     "Conventions": "CF-1.8 UGRID-1.0 Deltares-0.10"})
        pc = d.createVariable(crs_name, "i4")
        pc.setncatts(crs_att)
        pc.assignValue(int(crs_att.get("epsg", 0)))

        d.createDimension("mesh2d_nNodes", len(m["node_x"]))
        d.createDimension("mesh2d_nEdges", len(m["edges"]))
        d.createDimension("mesh2d_nFaces", len(m["faces"]))
        d.createDimension("mesh2d_nMax_face_nodes", 4)
        d.createDimension("Two", 2)

        t = d.createVariable("mesh2d", "i4")
        t.setncatts(dict(
            cf_role="mesh_topology", long_name="Topology data of 2D mesh", topology_dimension=2,
            node_coordinates="mesh2d_node_x mesh2d_node_y", node_dimension="mesh2d_nNodes",
            max_face_nodes_dimension="mesh2d_nMax_face_nodes",
            edge_node_connectivity="mesh2d_edge_nodes", edge_dimension="mesh2d_nEdges",
            edge_coordinates="mesh2d_edge_x mesh2d_edge_y",
            face_node_connectivity="mesh2d_face_nodes", face_dimension="mesh2d_nFaces",
            edge_face_connectivity="mesh2d_edge_faces",
            face_coordinates="mesh2d_face_x mesh2d_face_y"))
        t.assignValue(0)

        def var(name, dims, data, dtype="f8", **att):
            v = d.createVariable(name, dtype, dims,
                                 fill_value=-999.0 if dtype == "f8" else -999)
            v.setncatts(att)
            v[:] = data

        xs = dict(units="m", standard_name="projection_x_coordinate", mesh="mesh2d")
        ys = dict(units="m", standard_name="projection_y_coordinate", mesh="mesh2d")
        var("mesh2d_node_x", ("mesh2d_nNodes",), m["node_x"],
            long_name="x-coordinate of mesh nodes", location="node", **xs)
        var("mesh2d_node_y", ("mesh2d_nNodes",), m["node_y"],
            long_name="y-coordinate of mesh nodes", location="node", **ys)
        if write_z:
            var("mesh2d_node_z", ("mesh2d_nNodes",), m["node_z"], units="m",
                standard_name="altitude", long_name="z-coordinate of mesh nodes",
                mesh="mesh2d", location="node",
                coordinates="mesh2d_node_x mesh2d_node_y", grid_mapping=crs_name)
        e = m["edges"]
        var("mesh2d_edge_x", ("mesh2d_nEdges",), m["node_x"][e].mean(1), location="edge",
            long_name="Characteristic x-coordinate of the mesh edge (e.g. midpoint)", **xs)
        var("mesh2d_edge_y", ("mesh2d_nEdges",), m["node_y"][e].mean(1), location="edge",
            long_name="Characteristic y-coordinate of the mesh edge (e.g. midpoint)", **ys)
        var("mesh2d_edge_nodes", ("mesh2d_nEdges", "Two"), e + 1, "i4",
            cf_role="edge_node_connectivity", mesh="mesh2d", location="edge",
            long_name="Start and end nodes of mesh edges", start_index=1)
        ef = m["edge_faces"]
        var("mesh2d_edge_faces", ("mesh2d_nEdges", "Two"), np.where(ef < 0, -999, ef + 1),
            "i4", cf_role="edge_face_connectivity",
            long_name="Neighboring faces of mesh edges", start_index=1)
        var("mesh2d_face_nodes", ("mesh2d_nFaces", "mesh2d_nMax_face_nodes"),
            m["faces"] + 1, "i4", cf_role="face_node_connectivity", mesh="mesh2d",
            location="face", long_name="Vertex nodes of mesh faces (counterclockwise)",
            start_index=1)
        var("mesh2d_face_x", ("mesh2d_nFaces",), m["face_x"], location="face",
            long_name="Characteristic x-coordinate of mesh face", **xs)
        var("mesh2d_face_y", ("mesh2d_nFaces",), m["face_y"], location="face",
            long_name="Characteristic y-coordinate of mesh face", **ys)
    finally:
        d.close()


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def size_label(dx, dy=None):
    """'150m' for a square cell, '200x100m' otherwise."""
    dy = dy or dx
    return "%gm" % dx if dx == dy else "%gx%gm" % (dx, dy)


def default_output(raster, dx, dy=None, folder="MESH"):
    """<folder>/<raster stem>_<size>_net.nc"""
    stem = os.path.splitext(os.path.basename(raster))[0]
    return os.path.join(folder, "%s_%s_net.nc" % (stem, size_label(dx, dy)))


def raster_to_mesh(raster, output=None, dx=100.0, dy=None, min_valid_fraction=0.5,
                   nodata=None, epsg=None, write_z=True, xyz=False, write=True):
    """
    Create a regular D-Flow FM net file from a GeoTIFF.

    Parameters
    ----------
    raster : str
        Input GeoTIFF; defines the extent, the active area and the bed level.
    output : str, optional
        Output net file (default ./MESH/<raster>_<size>_net.nc). A name not
        ending in .nc gets _net.nc appended.
    dx, dy : float
        Cell size in x / y [m]; dy defaults to dx.
    min_valid_fraction : float
        Keep a cell if at least this fraction (0-1) lies on valid data.
    nodata, epsg : optional
        Override the raster's nodata value / EPSG code (EPSG:3826 if none).
    write_z : bool
        Write the node bed levels (mesh2d_node_z).
    xyz : bool
        Also write the cell-centre bed levels to <output stem>_cellz.xyz.
    write : bool
        False builds the mesh but writes nothing (check).

    Returns
    -------
    dict
        output, xyz (path or None), raster (info without data), mesh (the
        build_mesh dict), n_faces, n_nodes, n_edges, dx, dy, epsg.
    """
    dy = dy or dx
    if dx <= 0 or dy <= 0:
        raise ValueError("mesh size must be > 0")
    if not 0 <= min_valid_fraction <= 1:
        raise ValueError("min_valid_fraction must be between 0 and 1")

    r = read_raster(raster)
    if nodata is not None:
        r["nodata"] = nodata
    if epsg:
        r["epsg"] = epsg

    out = output or default_output(raster, dx, dy)
    if not out.lower().endswith(".nc"):
        out += "_net.nc"
    out = os.path.abspath(out)

    m = build_mesh(r["data"], r["x0"], r["y0"], r["resx"], r["resy"], r["nodata"],
                   dx, dy, min_valid_fraction)
    if len(m["faces"]) == 0:
        raise ValueError("no mesh cells created - check the nodata value / "
                         "min_valid_fraction")

    info = {k: v for k, v in r.items() if k != "data"}
    info["shape"] = r["data"].shape
    res = dict(output=out, xyz=None, raster=info, mesh=m, n_faces=len(m["faces"]),
               n_nodes=len(m["node_x"]), n_edges=len(m["edges"]), dx=dx, dy=dy,
               epsg=r["epsg"] or 3826)
    if not write:
        return res

    os.makedirs(os.path.dirname(out), exist_ok=True)
    crs_name, crs_att = crs_attrs(r["epsg"])
    hist = ("Created on %s by d3dtools %s from %s (dx=%g m, dy=%g m)"
            % (datetime.datetime.now().isoformat(timespec="seconds"), SCRIPT,
               os.path.basename(raster), dx, dy))
    tmp = out + ".tmp"
    try:
        write_net(tmp, m, crs_name, crs_att, write_z, hist)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    if xyz:
        res["xyz"] = os.path.splitext(out)[0] + "_cellz.xyz"
        np.savetxt(res["xyz"], np.column_stack([m["face_x"], m["face_y"], m["face_z"]]),
                   fmt="%.3f")
    return res


def print_summary(res, write_z=True):
    r, m = res["raster"], res["mesh"]
    print("  raster        : %d x %d cells, %g x %g m, top-left (%.3f, %.3f)"
          % (r["shape"][1], r["shape"][0], r["resx"], r["resy"], r["x0"], r["y0"]))
    print("  EPSG / nodata : %s / %s" % (r["epsg"] or "none (3826 used)", r["nodata"]))
    print("  mesh          : %g x %g m, %d cells kept of %d x %d"
          % (res["dx"], res["dy"], res["n_faces"], m["ncx"], m["ncy"]))
    print("  nodes / edges : %d / %d" % (res["n_nodes"], res["n_edges"]))
    print("  extent        : x %.1f - %.1f, y %.1f - %.1f"
          % (m["node_x"].min(), m["node_x"].max(), m["node_y"].min(), m["node_y"].max()))
    if write_z:
        print("  node bed level: %.3f - %.3f m" % (np.nanmin(m["node_z"]),
                                                   np.nanmax(m["node_z"])))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Create a standalone regular 2D D-Flow FM mesh (UGRID *_net.nc) "
                    "from a GeoTIFF. Cells of dx x dy are aligned to the raster's "
                    "top-left corner and kept where enough of them lies on valid "
                    "data; the node bed levels (mesh2d_node_z) are the mean of the "
                    "adjacent cells' raster values.",
        epilog="""
examples:
  %(prog)s dem.tif --dx 150                 (-> ./MESH/dem_150m_net.nc)
  %(prog)s dem.tif --dx 200 --dy 100 --xyz  (+ ./MESH/dem_200x100m_net_cellz.xyz)
  %(prog)s dem.tif --dx 100 -of D:/meshes
  %(prog)s dem.tif --dx 100 -o grid_100m_net.nc -f
  %(prog)s dem.tif --dx 100 --check         (summary only, write nothing)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raster",
                    help="input GeoTIFF (defines extent / active area / bed level)")
    ap.add_argument("--dx", type=float, required=True, help="mesh cell size in x [m]")
    ap.add_argument("--dy", type=float, default=None,
                    help="mesh cell size in y [m] (default = dx)")
    ap.add_argument("-o", "--output", default=None,
                    help="output net file (overrides -of; default "
                         "<output-folder>/<raster>_<dx>m_net.nc)")
    ap.add_argument("-of", "--output-folder", default="MESH",
                    help="folder for the output when -o is not given (default ./MESH)")
    ap.add_argument("--min-valid-fraction", type=float, default=0.5,
                    help="keep a cell if at least this fraction lies on valid data "
                         "(0-1, default 0.5)")
    ap.add_argument("--nodata", type=float, default=None,
                    help="override the raster nodata value")
    ap.add_argument("--epsg", type=int, default=None,
                    help="override / set the EPSG code (default: from the raster, "
                         "else 3826)")
    ap.add_argument("--no-z", action="store_true",
                    help="do not write the bed level (mesh2d_node_z)")
    ap.add_argument("--xyz", action="store_true",
                    help="also write the cell-centre bed levels to <output>_cellz.xyz")
    ap.add_argument("-f", "--force", action="store_true",
                    help="overwrite the output file if it exists")
    ap.add_argument("--check", action="store_true",
                    help="show the raster / mesh summary, write nothing")
    args = ap.parse_args(argv)

    dy = args.dy or args.dx
    out = args.output or default_output(args.raster, args.dx, dy, args.output_folder)
    if not out.lower().endswith(".nc"):
        out += "_net.nc"
    out = os.path.abspath(out)
    if not args.check and os.path.exists(out) and not args.force:
        ap.error("output exists: %s (use -f to overwrite)" % out)

    print("Reading", os.path.abspath(args.raster))
    try:
        res = raster_to_mesh(args.raster, out, args.dx, dy, args.min_valid_fraction,
                             args.nodata, args.epsg, not args.no_z, args.xyz,
                             write=not args.check)
    except (ValueError, FileNotFoundError, ImportError, OSError) as exc:
        print("%s: error: %s" % (SCRIPT, exc), file=sys.stderr)
        return 1

    print_summary(res, not args.no_z)
    if args.check:
        print("\nCheck only - nothing written (would write %s)." % res["output"])
        return 0
    print("\nWrote", res["output"])
    if res["xyz"]:
        print("Wrote", res["xyz"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
