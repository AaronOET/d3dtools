"""
rm1dsw - remove the 1D sewer system from a Delft3D FM (D-HYDRO / FM Suite) model.

Removes the sewer system - pipes, sewer connections and all manholes /
storage nodes - and everything anchored on it (structures, cross sections,
1D2D links, boundaries/laterals, ...), keeping the 1D channels and the 2D
grid intact.

Use ``--target`` to remove something else instead (``channel`` or ``all``) -
or use the ``rm1dch`` / ``mk2d`` commands, which are the same engine with a
different default.

This is the command-line counterpart of ``rm1dch`` (removes the channels
instead) and ``mk2d`` (removes the entire 1D network for a 2D-only model).

Examples
--------
    rm1dsw <input-folder-or-mdu> --check
    rm1dsw <input-folder-or-mdu> --dry-run
    rm1dsw <input-folder-or-mdu>
    rm1dsw <input-folder-or-mdu> --target channel

See ``rm1dsw --help`` for the full option list (shared with rm1dch/mk2d).
"""

import sys

from d3dtools import _split1d_engine as _engine


def main(argv=None):
    _engine.SCRIPT = "rm1dsw"
    _engine.DEFAULT_TARGET = _engine.SEWER
    _engine.DESCRIPTION = (
        "Remove the 1D sewer system (pipes, sewer connections and manholes) "
        "from a Delft3D FM model, keeping the 1D channels and the 2D grid "
        "intact.  See --target to remove something else instead.")
    return _engine.main(argv)


if __name__ == "__main__":
    sys.exit(main())
