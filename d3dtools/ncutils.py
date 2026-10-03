"""
netCDF helpers shared by the D3D tools.
"""
import os
import sys

try:
    import netCDF4 as nc
except ImportError:  # pragma: no cover
    sys.exit("netCDF4 is required:  pip install netCDF4")


def open_nc(path, mode="r", **kw):
    """nc.Dataset() that also works for non-ASCII paths on Windows.

    The netCDF-C library cannot open paths containing e.g. Chinese
    characters (OSError Errno 22), so open such a file by its bare name
    from inside its folder.  The handle stays valid after chdir back.
    """
    path = os.path.abspath(path)
    folder, name = os.path.split(path)
    if path.isascii() or not name.isascii():
        return nc.Dataset(path, mode, **kw)
    cwd = os.getcwd()
    os.chdir(folder)
    try:
        return nc.Dataset(name, mode, **kw)
    finally:
        os.chdir(cwd)


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


def find_mesh2d(ds):
    """Return the name of the 2D mesh_topology variable."""
    for name, var in ds.variables.items():
        if getattr(var, "cf_role", "") == "mesh_topology" and \
                int(getattr(var, "topology_dimension", 2)) == 2:
            return name
    raise RuntimeError("No 2D mesh_topology variable found in file.")
