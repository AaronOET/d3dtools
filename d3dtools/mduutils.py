"""
Helpers for reading and editing D-Flow FM .mdu (and other INI-style) files,
shared by the D3D tools (alignncrain, otstep, itstep).

Edits keep the file's encoding, line endings, column alignment and trailing
comments.
"""
import os
import re
import shutil

TUNIT_SECONDS = {"D": 86400, "H": 3600, "M": 60, "S": 1}


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
def resolve_mdu(target):
    """Return the .mdu for a model input folder or a .mdu path."""
    target = os.path.abspath(target)
    if os.path.isdir(target):
        mdus = [f for f in sorted(os.listdir(target)) if f.lower().endswith(".mdu")]
        if len(mdus) != 1:
            raise ValueError("expected exactly one .mdu in %s, found %d"
                             % (target, len(mdus)))
        target = os.path.join(target, mdus[0])
    elif not target.lower().endswith(".mdu"):
        raise ValueError("give a model input folder or a .mdu file")
    if not os.path.isfile(target):
        raise FileNotFoundError("mdu not found: %s" % target)
    return target


def read_lines(path):
    """Read a text file as lines (keeping line endings); return (lines, encoding)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    for encoding in ("utf-8", "cp950"):
        try:
            return raw.decode(encoding).splitlines(keepends=True), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("cannot decode %s (tried utf-8, cp950)" % path)


def write_lines(path, lines, encoding, backup=True):
    """Write `lines` back to `path`, keeping a <path>.bak copy if `backup`."""
    if backup:
        shutil.copy2(path, path + ".bak")
    with open(path, "wb") as fh:
        fh.write("".join(lines).encode(encoding))


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #
def get_key(lines, section, key):
    """Return the value of `key` in `section` (comment stripped), or None."""
    current = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip().lower()
            continue
        if current == section.lower() and "=" in line:
            k, v = line.split("=", 1)
            if k.strip().lower() == key.lower():
                return v.split("#", 1)[0].strip()
    return None


def set_key(lines, section, key, value):
    """
    Replace the value of `key` in `section`, preserving the column alignment
    and the trailing comment.  Return the old value, or None if not found.
    """
    current = None
    pattern = re.compile(r"^(\s*%s\s*=\s*)(.*?)(\s*)(#.*)?$" % re.escape(key),
                         re.IGNORECASE)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].strip().lower()
            continue
        if current != section.lower():
            continue
        body = line.rstrip("\r\n")
        m = pattern.match(body)
        if not m:
            continue
        prefix, old, _, comment = m.groups()
        if comment:
            # keep the comment at the same column if possible
            width = len(m.group(0)) - len(comment) - len(prefix)
            new = "%s%s%s" % (prefix, value.ljust(max(width, len(value) + 1)), comment)
        else:
            new = prefix + value
        lines[i] = new + line[len(body):]
        return old.strip()
    return None


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
def fmt_num(value):
    """Write integers without decimals, otherwise keep a compact float."""
    return str(int(round(value))) if abs(value - round(value)) < 1e-9 else "%g" % value


def parse_interval(text):
    """Return seconds for '300', '300s', '5m', '1.5h' or '1d'."""
    original = str(text).strip()
    text = original.lower()
    scale = 1
    if text and text[-1] in "smhd":
        scale = TUNIT_SECONDS[text[-1].upper()]
        text = text[:-1]
    try:
        value = float(text) * scale
    except ValueError:
        raise ValueError("invalid interval '%s' (e.g. 300, 5m, 1h, 1d)" % original)
    if value < 0:
        raise ValueError("interval must be >= 0")
    return value


def fmt_duration(seconds):
    """Human-readable duration, e.g. 3600 -> '1 h', 90 -> '1.5 min'."""
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return "%g %s" % (seconds / size, unit)
    return "%g s" % seconds
