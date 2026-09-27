"""
rm1dch - remove the 1D channels from a Delft3D FM (D-HYDRO / FM Suite) model.

Removes the open 1D channels and everything anchored on them (structures,
cross sections, 1D2D links, boundaries/laterals, ...), keeping the sewer
system (pipes, sewer connections, manholes) and the 2D grid intact. Where a
kept sewer branch ran into a removed channel, a manhole is added so the sewer
keeps a proper outfall (see ``--no-outfall-manholes``).

Use ``--target`` to remove something else instead (``sewer`` or ``all``) -
or use the ``rm1dsw`` / ``mk2d`` commands, which are the same engine with a
different default.

This is the command-line counterpart of ``rm1dsw`` (removes the sewer system
instead) and ``mk2d`` (removes the entire 1D network for a 2D-only model).

Examples
--------
    rm1dch <input-folder-or-mdu> --check
    rm1dch <input-folder-or-mdu> --dry-run
    rm1dch <input-folder-or-mdu>
    rm1dch <input-folder-or-mdu> --no-outfall-manholes

See ``rm1dch --help`` for the full option list (shared with rm1dsw/mk2d).
"""

import sys

from d3dtools import _split1d_engine as _engine


def main(argv=None):
    _engine.SCRIPT = "rm1dch"
    _engine.DEFAULT_TARGET = _engine.CHANNEL
    _engine.DESCRIPTION = (
        "Remove the open 1D channels from a Delft3D FM model, keeping "
        "pipes, sewer connections, manholes and the 2D grid intact.  "
        "See --target to remove something else instead.")
    _engine.EPILOG = """
examples:
  %(prog)s dflowfm --check                (does the model still contain 1D channels?)
  %(prog)s dflowfm/FlowFM.mdu --dry-run   (report the plan, write nothing)
  %(prog)s dflowfm                        (remove the channels, add outfall manholes)
  %(prog)s dflowfm --no-outfall-manholes
  %(prog)s dflowfm --manhole-levels invert --manhole-depth 1.5
  %(prog)s dflowfm --manhole-id-prefix OUT_ --manhole-csv C:/temp/added.csv
  %(prog)s dflowfm --keep-ids keep_branches.txt
  %(prog)s dflowfm --unknown-as channel   (also remove unclassified branches)

Every rewritten file is first backed up to <name>.bak.  Close the project in
the FM Suite before running, and reopen it WITHOUT saving.
"""
    return _engine.main(argv)


if __name__ == "__main__":
    sys.exit(main())
