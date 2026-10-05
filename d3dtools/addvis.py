#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
addvis - add (or update) the viscosity key in the [physics] section of every
D-Flow FM (Delft3D FM) .mdu under a model folder.

Default: Viscosity = 1.0

If the key already exists in [physics] its line is rewritten; otherwise it is
added after the last line of the section.  The new line is lined up with the
first key line of the section ('=' and trailing comment in the same columns).
Files without a [physics] section are skipped.

The target can be a folder (all .mdu below it, recursively), a single .mdu,
or a .dsproj (its <project>.dsproj_data folder).

Usage
-----
    addvis                              (all .mdu below the current folder)
    addvis F:/path/to/project           (all .mdu below a given folder)
    addvis . --value 0.5                (another value)
    addvis . --check                    (show the changes, write nothing)
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from .mduutils import read_lines, write_lines

SCRIPT = "addvis"
SECTION = "physics"
DEFAULT_KEY = "Viscosity"
DEFAULT_VALUE = "1.0"
COMMENT = "# Uniform horizontal viscosity (m2/s)"

HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def find_mdus(target=None):
    """Return the sorted .mdu paths for a folder (recursive), a .mdu or a .dsproj."""
    target = os.path.abspath(target or ".")
    if target.lower().endswith(".dsproj"):
        target = os.path.splitext(target)[0] + ".dsproj_data"
        if not os.path.isdir(target):
            raise FileNotFoundError("project data folder not found: %s" % target)
    if os.path.isfile(target):
        if not target.lower().endswith(".mdu"):
            raise ValueError("give a folder, a .mdu or a .dsproj")
        return [target]
    if not os.path.isdir(target):
        raise FileNotFoundError("not found: %s" % target)
    return sorted(os.path.join(dirpath, f)
                  for dirpath, _, files in os.walk(target)
                  for f in files if f.lower().endswith(".mdu"))


def format_line(key, value, template=None):
    """Build 'Key = value # comment' lined up like `template` (a key line of the file)."""
    if template:
        m = re.match(r"^(\s*\S+\s*)=(\s*)(\S+)(\s*)(#.*)?$", template.rstrip("\r\n"))
        if m:
            key_w = len(m.group(1))
            if not m.group(5):
                return "%s= %s" % (key.ljust(key_w), value)
            val_w = len(m.group(2)) + len(m.group(3)) + len(m.group(4))
            return "%s=%s%s" % (key.ljust(key_w), (" " + value).ljust(val_w), COMMENT)
    return "%-34s= %-68s%s" % (key, value, COMMENT)


# --------------------------------------------------------------------------- #
# Python API
# --------------------------------------------------------------------------- #
def set_viscosity(mdu, value=DEFAULT_VALUE, key=DEFAULT_KEY, backup=True, write=True):
    """
    Add or update `key = value` in the [physics] section of `mdu`.

    Returns
    -------
    (action, line number, old line or None, new line or None)
        action is 'add', 'update', 'ok' (already set) or 'skip' (no [physics]).
    """
    value = str(value)
    lines, encoding = read_lines(mdu)
    eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"

    start = end = None
    for i, line in enumerate(lines):
        m = HEADER_RE.match(line)
        if not m:
            continue
        if start is not None:
            end = i
            break
        if m.group(1).strip().lower() == SECTION:
            start = i
    if start is None:
        return "skip", None, None, None
    if end is None:
        end = len(lines)

    template = next((ln for ln in lines[start + 1:end]
                     if "=" in ln and not ln.lstrip().startswith("#")), None)
    new_line = format_line(key, value, template)

    key_re = re.compile(r"^\s*%s\s*=" % re.escape(key), re.IGNORECASE)
    for i in range(start + 1, end):
        if key_re.match(lines[i]):
            old = lines[i].rstrip("\r\n")
            if old.rstrip() == new_line.rstrip():
                return "ok", i + 1, old, new_line
            lines[i] = new_line + lines[i][len(old):]
            action, lineno = "update", i + 1
            break
    else:
        # insert after the last non-blank line of the section
        old = None
        pos = end
        while pos > start + 1 and not lines[pos - 1].strip():
            pos -= 1
        if not lines[pos - 1].endswith(("\n", "\r")):
            lines[pos - 1] += eol
        lines.insert(pos, new_line + eol)
        action, lineno = "add", pos + 1

    if write:
        write_lines(mdu, lines, encoding, backup=backup)
    return action, lineno, old, new_line


def add_viscosity(target=None, value=DEFAULT_VALUE, key=DEFAULT_KEY, backup=True, write=True):
    """Run set_viscosity() on every .mdu of `target`; return [(mdu, result), ...]."""
    return [(mdu, set_viscosity(mdu, value, key, backup=backup, write=write))
            for mdu in find_mdus(target)]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Add (or update) the viscosity key in the [physics] section of "
                    "every D-Flow FM .mdu under a folder.",
        epilog="""
examples:
  %(prog)s                                (all .mdu below the current folder)
  %(prog)s F:/path/to/project             (all .mdu below a given folder)
  %(prog)s FlowFM.mdu                     (a single .mdu)
  %(prog)s MyProject.dsproj               (all .mdu in MyProject.dsproj_data)
  %(prog)s . --value 0.5                  (another value)
  %(prog)s . --check                      (show the changes, write nothing)

Files without a [physics] section are skipped.
Each changed .mdu is backed up as <file>.bak unless --no-backup is given.
Close the project in the FM Suite before running.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default=None,
                    help="folder (searched recursively), .mdu or .dsproj "
                         "(default: current folder)")
    ap.add_argument("--value", default=DEFAULT_VALUE,
                    help="viscosity value in m2/s (default: %(default)s)")
    ap.add_argument("--key", default=DEFAULT_KEY,
                    help="key to set in [physics] (default: %(default)s)")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep a <file>.bak copy of each changed .mdu")
    ap.add_argument("--check", "--dry-run", dest="check", action="store_true",
                    help="show the changes, write nothing")
    args = ap.parse_args(argv)

    try:
        mdus = find_mdus(args.target)
    except (ValueError, FileNotFoundError) as exc:
        ap.error(str(exc))
    if not mdus:
        print("No .mdu files found under %s" % os.path.abspath(args.target or "."))
        return 0

    changed = 0
    for mdu in mdus:
        try:
            action, lineno, old, new = set_viscosity(mdu, args.value, args.key,
                                                     backup=not args.no_backup,
                                                     write=not args.check)
        except (ValueError, OSError) as exc:
            print("%s: error: %s: %s" % (SCRIPT, mdu, exc), file=sys.stderr)
            continue
        if action == "skip":
            print("[skip] %s: no [physics] section" % mdu)
        elif action == "ok":
            print("[ok]   %s: already %s = %s" % (mdu, args.key, args.value))
        elif action == "update":
            changed += 1
            print("[upd]  %s: line %d\n       - %s\n       + %s"
                  % (mdu, lineno, old.strip(), new.strip()))
        else:
            changed += 1
            print("[add]  %s: line %d\n       + %s" % (mdu, lineno, new.strip()))

    print("\n%d .mdu file(s) found, %d %schanged."
          % (len(mdus), changed, "would be " if args.check else ""))
    if args.check:
        print("Check only - nothing written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
