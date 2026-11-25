"""
netCDF helpers shared by the D3D tools.
"""
import contextlib
import os
import sys
import uuid

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


def _is_ascii(s):
    return all(ord(c) < 128 for c in s)


def _short_path(path):
    """Windows 8.3 short path (ASCII) for a file, or None if unavailable."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, len(buf))
        if 0 < n < len(buf) and _is_ascii(buf.value):
            return buf.value
    except Exception:
        pass
    return None


@contextlib.contextmanager
def open_dataset(path):
    """
    Open a NetCDF file whose path may contain non-ASCII characters (e.g. 繁體中文)
    in the folder or the file name. netCDF-C on Windows often cannot open such
    paths, so fall back to:
      1. the Windows 8.3 short path, then
      2. a temporary ASCII-named hard link (no copy; same disk) opened by a
         relative name from inside its folder, removed afterwards.
    """
    path = os.path.abspath(path)
    link = None
    try:
        ds = nc.Dataset(path, "r")
    except (OSError, UnicodeError) as first_err:
        if _is_ascii(path):
            raise
        ds = None
        short = _short_path(path)
        if short:
            try:
                ds = nc.Dataset(short, "r")
                print(f"(non-ASCII path, opened via short path: {short})")
            except OSError:
                ds = None

        if ds is None:
            folder = os.path.dirname(path)
            link_name = f"_tmp_nc_{uuid.uuid4().hex[:8]}.nc"
            link = os.path.join(folder, link_name)
            try:
                os.link(path, link)
            except OSError as e:
                raise OSError(
                    f"Cannot open non-ASCII path {path!r} ({first_err}); "
                    f"hard link failed ({e}). Rename the file/folder to ASCII."
                ) from e
            cwd = os.getcwd()
            try:
                os.chdir(folder)  # relative ASCII name avoids the non-ASCII folder too
                ds = nc.Dataset(link_name, "r")
                print(f"(non-ASCII path, opened via temporary link: {link_name})")
            except Exception:
                os.remove(link)
                raise
            finally:
                os.chdir(cwd)
    try:
        yield ds
    finally:
        ds.close()
        if link and os.path.exists(link):
            os.remove(link)


def resolve_netfile(target):
    """Return the net file for a model folder, a .mdu or a *_net.nc path."""
    target = os.path.abspath(target)
    mdu = None
    if os.path.isdir(target):
        from .mduutils import model_mdus
        mdus = model_mdus(target)
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
