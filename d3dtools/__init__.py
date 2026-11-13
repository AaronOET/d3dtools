"""
D3DTOOLS - A collection of tools for working with shapefiles and converting them for Delft3D modeling.
"""

__version__ = '0.40.3'

# Define all modules that should be exposed when using "from d3dtools import *"
__all__ = [
    'ncrain',
    'snorain',
    'shpbc2pli',
    'shpdike2pliz',
    'shpblock2pol',
    'shp2pli',
    'shp2pliz',
    'shp2pol',
    'shp2ldb',
    'shp2xyz',
    'describe',
    'evaluate',
    'evaluate_sensor',
    'evaluate_sensor2',
    'eval_plot',
    'sensor',
    'getfacez',
    'getfacez2',
    'fou2shp',
    'fou2mfd',
    'foustrip1d',
    'ncts2shp',
    'mrgshp',
    'pliz2shp',
    'pli2shp',
    'pol2shp',
    'xyz2shp',
    'rmgrid',
    'rsgrid',
    'rmgriddimr',
    'rsgriddimr',
    'mkdimr',
    'rm1dch',
    'rm1dsw',
    'mk2d',
    'rmlinks',
    'orthochk',
    'expgrid',
    'alignncrain',
    'ncraininfo',
    'otstep',
    'itstep',
    'clrbak',
    'setthreads',
    'clrmpi',
    'addvis',
    'chkprog',
    'cpproj',
    'simtime',
]

# Submodules are imported lazily (PEP 562), on first access such as
# `d3dtools.fou2shp` or `from d3dtools import fou2shp`. Importing them all up
# front pulls in pandas, geopandas, rasterio, matplotlib, scipy, ... and makes
# every command (even `d3dtools -v`) take seconds to start.
import importlib as _importlib
import sys as _sys


def __getattr__(name):
    if name in __all__:
        module = _importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))


if _sys.version_info < (3, 7):  # no module __getattr__: import eagerly
    for _name in __all__:
        globals()[_name] = _importlib.import_module(f".{_name}", __name__)
