"""Buffer trans_zone_faces inward, select FlowFM faces inside it, dissolve, save."""
import os
import sys
import argparse

import geopandas as gpd

SRC_FACES = os.path.join("SHP_NC", "FlowFM_net_faces.shp")
TRANS_FACES = os.path.join("SHP_TRANS", "trans_zone_faces.shp")
OUT_DIR = "SHP_TRANS"
OUT_SHP_NAME = "trans_zone_core.shp"


def make_trans_zone_core(src_faces=SRC_FACES, trans_faces=TRANS_FACES,
                          out_dir=OUT_DIR, buffer_dist=-1.0):
    """
    Buffer *trans_faces* inward by *buffer_dist*, select FlowFM faces that lie
    within the buffered zone, dissolve them, and save the result.

    Parameters
    ----------
    src_faces : str
        Path to the source faces shapefile (default: 'SHP_NC/FlowFM_net_faces.shp')
    trans_faces : str
        Path to the trans_zone_faces shapefile (default: 'SHP_TRANS/trans_zone_faces.shp')
    out_dir : str
        Directory to write the output shapefile to (default: 'SHP_TRANS')
    buffer_dist : float
        Buffer distance applied to trans_zone_faces (default: -1.0)

    Returns
    -------
    str
        Path to the written shapefile.
    """
    out_shp = os.path.join(out_dir, OUT_SHP_NAME)

    faces = gpd.read_file(src_faces)
    zone = gpd.read_file(trans_faces)
    if zone.crs != faces.crs:
        zone = zone.to_crs(faces.crs)

    zone["geometry"] = zone.geometry.buffer(buffer_dist)
    zone = zone[~zone.geometry.is_empty & zone.geometry.notna()]
    if zone.empty:
        raise RuntimeError("Negative buffer collapsed the trans_zone_faces geometry.")

    selected_idx = gpd.sjoin(
        faces, zone[["geometry"]], how="inner", predicate="within"
    ).index.unique()
    selected = faces.loc[selected_idx].copy()
    if selected.empty:
        raise RuntimeError("No faces lie inside the buffered trans_zone_faces.")

    dissolved = selected.dissolve()
    dissolved = dissolved[["geometry"]]
    dissolved["zone"] = "transition_core"

    os.makedirs(out_dir, exist_ok=True)
    dissolved.to_file(out_shp)

    print(f"Selected faces:    {len(selected)}")
    print(f"Output written to: {out_shp}")

    return out_shp


def main():
    """Main function for the command line interface."""
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description=(
            "Buffer trans_zone_faces inward, select FlowFM faces that lie "
            "within the buffered zone, dissolve them, and save the result."
        ),
        epilog="""
examples:
  %(prog)s
  %(prog)s -s SHP_NC/FlowFM_net_faces.shp -t SHP_TRANS/trans_zone_faces.shp
  %(prog)s -b -2.0
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-s",
        "--src-faces",
        default=SRC_FACES,
        help=f"Path to the source faces shapefile (default: {SRC_FACES})",
    )
    parser.add_argument(
        "-t",
        "--trans-faces",
        default=TRANS_FACES,
        help=f"Path to the trans_zone_faces shapefile (default: {TRANS_FACES})",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        default=OUT_DIR,
        help=f"Directory to write the output shapefile to (default: {OUT_DIR})",
    )
    parser.add_argument(
        "-b",
        "--buffer-dist",
        type=float,
        default=-1.0,
        help="Buffer distance applied to trans_zone_faces (default: -1.0)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.src_faces):
        print(f"Error: Source faces shapefile does not exist: {args.src_faces}")
        sys.exit(1)

    if not os.path.exists(args.trans_faces):
        print(f"Error: trans_zone_faces shapefile does not exist: {args.trans_faces}")
        sys.exit(1)

    try:
        make_trans_zone_core(args.src_faces, args.trans_faces, args.out_dir, args.buffer_dist)
    except Exception as e:
        print(f"Error processing trans_zone_faces shapefile: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
