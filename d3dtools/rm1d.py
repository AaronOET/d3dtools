"""
rm1d - turn a Delft3D FM (D-HYDRO / FM Suite) 1D2D model into a 2D-only model.

Removes the entire 1D network - the open channels AND the sewer system -
together with every 1D structure (pump, bridge, weir, orifice, culvert, ...),
every cross section, every manhole, every 1D2D link, and the 1D-only entries
in the .mdu. The 2D grid, fixed weirs, 2D boundaries, laterals, meteo and the
2D initial/parameter fields are left exactly as they are.

The run stops before writing if the net file has no 2D grid, since the
result would be an empty model (see ``--allow-empty-2d``).

Use ``--target`` to remove only the channels or only the sewer system
instead - or use the ``rm1dch`` / ``rm1dsw`` commands, which are the same
engine with a different default.

Examples
--------
    rm1d <input-folder-or-mdu> --check
    rm1d <input-folder-or-mdu> --dry-run
    rm1d <input-folder-or-mdu>
    rm1d <input-folder-or-mdu> --keep-1d-mdu-keys

See ``rm1d --help`` for the full option list (shared with rm1dch/rm1dsw).
"""

import sys

from d3dtools import _split1d_engine as _engine


def main(argv=None):
    _engine.SCRIPT = "rm1d"
    _engine.DEFAULT_TARGET = _engine.ALL
    _engine.DESCRIPTION = (
        "Turn a Delft3D FM 1D2D model into a 2D-only model by removing the "
        "entire 1D network (channels, sewers, manholes and every 1D "
        "structure).  See --target to remove only channels or only the "
        "sewer system instead.")
    _engine.EPILOG = """
examples:
  %(prog)s dflowfm --check                (does the model still contain 1D?)
  %(prog)s dflowfm/FlowFM.mdu --dry-run   (report the plan, write nothing)
  %(prog)s dflowfm                        (remove the entire 1D network)
  %(prog)s dflowfm --keep-1d-mdu-keys     (keep FrictFile / 1dField entries in the .mdu)
  %(prog)s dflowfm --target channel       (same as rm1dch)
  %(prog)s dflowfm --target sewer         (same as rm1dsw)
  %(prog)s dflowfm --log C:/temp/rm1d.log

Every rewritten file is first backed up to <name>.bak.  Close the project in
the FM Suite before running, and reopen it WITHOUT saving.
"""
    return _engine.main(argv)


if __name__ == "__main__":
    sys.exit(main())
