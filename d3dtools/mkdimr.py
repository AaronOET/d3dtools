"""
Build a DIMR run folder from a Deltares D-Flow FM Suite project (.dsproj).

Creates:
    <out>/
        dimr_config.xml        (creationDate = time this tool is executed)
        dflowfm/                (copy of <project>.dsproj_data/<FM model>/input)

The FM model name and its data folder are read from the .dsproj file itself
(it is a SQLite database), so this works for any project as long as it
contains one D-Flow FM model (use ``--model`` to pick one when it has several).

This is the counterpart of ``rmgriddimr``/``rsgriddimr``: those tools operate
on a DIMR run folder once it exists; ``mkdimr`` is what creates it in the
first place from a .dsproj project.

Examples
--------
    mkdimr 2DOF_KS.dsproj
    mkdimr 2DOF_KS.dsproj --out DIMR --threads 1 --force
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DIMR_XML_TEMPLATE = """﻿<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<dimrConfig xmlns="http://schemas.deltares.nl/dimr" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://schemas.deltares.nl/dimr https://content.oss.deltares.nl/schemas/dimr-1.2.xsd">
  <documentation>
    <fileVersion>1.2</fileVersion>
    <createdBy>Deltares, Coupling Team</createdBy>
    <creationDate>{creation_date}</creationDate>
  </documentation>
  <control>
    <start name="{model_name}" />
  </control>
  <component name="{model_name}">
    <library>dflowfm</library>
    <workingDir>dflowfm</workingDir>
    <setting key="threads" value="{threads}" />
    <inputFile>{mdu_name}</inputFile>
  </component>
</dimrConfig>
"""


def read_fm_models(dsproj):
    """Return a list of (model_name, data_path) for every D-Flow FM model in the project."""
    dsproj = Path(dsproj)
    con = sqlite3.connect(f"file:{dsproj.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT a.name, f.Path FROM fm_model f "
            "JOIN activities a ON a.project_item_id = f.project_item_id"
        ).fetchall()
    finally:
        con.close()
    return rows


def resolve_input_dir(dsproj, model_name):
    """Locate <project>.dsproj_data/<model_name>/input."""
    dsproj = Path(dsproj)
    data_root = dsproj.with_name(dsproj.name + "_data")
    input_dir = data_root / model_name / "input"
    if not input_dir.is_dir():
        sys.exit(f"Error: input folder not found: {input_dir}")
    return input_dir


def resolve_fm_model(dsproj, model=None):
    """Return the (model_name, input_dir, mdu_name) to export from *dsproj*.

    Exits with an error if the project has no D-Flow FM model, or several and
    none/an unknown one was picked with *model*.
    """
    models = read_fm_models(dsproj)
    if not models:
        sys.exit("Error: no D-Flow FM model found in the project")
    if model:
        models = [m for m in models if m[0] == model]
        if not models:
            sys.exit(f"Error: model '{model}' not found in the project")
    if len(models) > 1:
        names = ", ".join(m[0] for m in models)
        sys.exit(f"Error: project contains several FM models ({names}); "
                 "choose one with --model")
    model_name = models[0][0]

    input_dir = resolve_input_dir(dsproj, model_name)
    mdu_files = sorted(input_dir.glob("*.mdu"))
    if len(mdu_files) != 1:
        sys.exit(f"Error: expected exactly one .mdu in {input_dir}, "
                 f"found {len(mdu_files)}")
    return model_name, input_dir, mdu_files[0].name


def create_dimr_folder(dsproj, out=None, threads=1, model=None, force=False):
    """Build a DIMR run folder from *dsproj*. Returns a summary dict.

    *out* defaults to a ``DIMR`` folder next to *dsproj*. Raises via
    ``sys.exit`` (like the rest of this package) when the output folder
    already exists and *force* is not set, or the model input cannot be
    located.
    """
    dsproj = Path(dsproj).resolve()
    if not dsproj.is_file():
        sys.exit(f"Error: {dsproj} not found")

    model_name, input_dir, mdu_name = resolve_fm_model(dsproj, model)

    out_dir = Path(out).resolve() if out else dsproj.with_name("DIMR")
    dflowfm_dir = out_dir / "dflowfm"
    if out_dir.exists() and not force:
        sys.exit(f"Error: {out_dir} already exists (use --force to overwrite)")

    # 1. copy model input -> <out>/dflowfm (files are overwritten in place with --force)
    shutil.copytree(input_dir, dflowfm_dir, dirs_exist_ok=True)

    # 2. write dimr_config.xml with the current execution time (UTC, same format the GUI writes)
    now_utc = datetime.now(timezone.utc)
    creation_date = now_utc.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    xml_text = DIMR_XML_TEMPLATE.format(
        creation_date=creation_date,
        model_name=model_name,
        mdu_name=mdu_name,
        threads=threads,
    )
    config_path = out_dir / "dimr_config.xml"
    config_path.write_text(xml_text, encoding="utf-8", newline="\r\n")

    n_files = sum(1 for p in dflowfm_dir.rglob("*") if p.is_file())
    return {
        "dsproj": dsproj,
        "model_name": model_name,
        "mdu_name": mdu_name,
        "out_dir": out_dir,
        "dflowfm_dir": dflowfm_dir,
        "n_files": n_files,
        "config_path": config_path,
        "creation_date": creation_date,
    }


def main():
    """Main function for the command line interface."""
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Build a DIMR run folder (dimr_config.xml + dflowfm/) from "
                    "a Delft3D FM Suite project (.dsproj).",
        epilog="""
examples:
  %(prog)s 2DOF_KS.dsproj
  %(prog)s 2DOF_KS.dsproj --out DIMR --threads 1 --force
  %(prog)s 2DOF_KS.dsproj --model FlowFM1
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("dsproj", type=Path, help="path to the .dsproj file")
    parser.add_argument("--out", type=Path, default=None,
                        help="output folder (default: DIMR next to the .dsproj)")
    parser.add_argument("--threads", type=int, default=1,
                        help="threads setting in dimr_config.xml")
    parser.add_argument("--model", default=None,
                        help="FM model name to export (only needed if the "
                             "project has several)")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing output folder")
    args = parser.parse_args()

    try:
        result = create_dimr_folder(
            args.dsproj, out=args.out, threads=args.threads,
            model=args.model, force=args.force,
        )
    except SystemExit:
        raise
    except Exception as e:
        print(f"Error processing project: {str(e)}")
        sys.exit(1)

    print(f"Project     : {result['dsproj']}")
    print(f"FM model    : {result['model_name']}  ({result['mdu_name']})")
    print(f"Copied      : {result['n_files']} file(s) -> {result['dflowfm_dir']}")
    print(f"Config      : {result['config_path']}")
    local = datetime.strptime(result['creation_date'], "%Y-%m-%dT%H:%M:%S.%fZ")
    local = local.replace(tzinfo=timezone.utc).astimezone()
    print(f"creationDate: {result['creation_date']}  (local {local:%Y-%m-%d %H:%M:%S %z})")


if __name__ == "__main__":
    main()
