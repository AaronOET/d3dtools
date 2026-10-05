"""
Helpers for reading and editing DIMR configuration files (dimr_config.xml),
shared by the D3D tools (setthreads, clrmpi).

The file is handled as text, not parsed as XML, so edits keep comments,
encoding (BOM) and layout.
"""
import os
import re

DIMR_CONFIG_NAMES = ("dimr_config.xml", "dimr.xml")
COMPONENT_RE = re.compile(r"<component\b.*?</component>", re.DOTALL)


def resolve_dimr(target=None):
    """
    Return the DIMR config for a run folder or an .xml path.

    A folder is searched for dimr_config.xml, then dimr.xml, then a single
    other .xml file with a <dimrConfig> root.  `target` defaults to the
    current directory.
    """
    target = os.path.abspath(target or ".")
    if os.path.isdir(target):
        for name in DIMR_CONFIG_NAMES:
            path = os.path.join(target, name)
            if os.path.isfile(path):
                return path
        found = [os.path.join(target, f) for f in sorted(os.listdir(target))
                 if f.lower().endswith(".xml") and _is_dimr(os.path.join(target, f))]
        if len(found) != 1:
            raise ValueError("expected one DIMR config (%s) in %s, found %d"
                             % (" / ".join(DIMR_CONFIG_NAMES), target, len(found)))
        return found[0]
    if not os.path.isfile(target):
        raise FileNotFoundError("DIMR config not found: %s" % target)
    return target


def _is_dimr(path):
    try:
        with open(path, "rb") as fh:
            return b"<dimrConfig" in fh.read(4096)
    except OSError:
        return False


def read_dimr(path):
    """Read a DIMR config; return (text with '\\n' line endings, has_bom)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    return raw.decode("utf-8-sig").replace("\r\n", "\n"), bom


def write_dimr(path, text, bom):
    """Write a DIMR config with Windows (CRLF) line endings, keeping its BOM."""
    with open(path, "wb") as fh:
        fh.write((b"\xef\xbb\xbf" if bom else b"")
                 + text.replace("\n", "\r\n").encode("utf-8"))


def tag_value(block, tag):
    """Return the stripped text of the first <tag>...</tag> in `block`, or None."""
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), block, re.DOTALL)
    return m.group(1).strip() if m else None


def dflowfm_models(path):
    """
    Return (workingDir, inputFile) for each dflowfm <component> in the DIMR
    config `path`; workingDir is absolute.
    """
    text, _ = read_dimr(path)
    root = os.path.dirname(os.path.abspath(path))
    models = []
    for block in COMPONENT_RE.findall(text):
        if tag_value(block, "library") != "dflowfm":
            continue
        mdu = tag_value(block, "inputFile")
        if mdu:
            workdir = os.path.normpath(os.path.join(root, tag_value(block, "workingDir") or "."))
            models.append((workdir, mdu))
    return models
