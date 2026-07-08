"""
Extract triangle mesh cells from a faces shapefile, buffer, dissolve, save.

Then select all faces that intersect the resulting trans_zone and dissolve them.
"""
import os
import sys
import argparse

import geopandas as gpd

SRC_SHP = os.path.join("SHP_NC", "FlowFM_net_faces.shp")
OUT_DIR = "SHP_TRANS"
OUT_SHP_NAME = "trans_zone.shp"
OUT_FACES_SHP_NAME = "trans_zone_faces.shp"


def make_trans_zone(src_shp=SRC_SHP, out_dir=OUT_DIR, buffer_dist=1.0):
    """
    Extract triangle mesh cells from *src_shp*, buffer and dissolve them into
    a transition zone, then select and dissolve all faces intersecting that
    zone.

    Parameters
    ----------
    src_shp : str
        Path to the source faces shapefile (default: 'SHP_NC/FlowFM_net_faces.shp')
    out_dir : str
        Directory to write output shapefiles to (default: 'SHP_TRANS')
    buffer_dist : float
        Buffer distance applied to triangle faces (default: 1.0)

    Returns
    -------
    dict
        Paths to the written 'zone' and 'faces' shapefiles.
    """
    out_shp = os.path.join(out_dir, OUT_SHP_NAME)
    out_faces_shp = os.path.join(out_dir, OUT_FACES_SHP_NAME)

    gdf = gpd.read_file(src_shp)

    triangles = gdf[gdf["type"] == "triangle"].copy()
    if triangles.empty:
        raise RuntimeError("No triangle faces found in attribute table.")

    triangles["geometry"] = triangles.geometry.buffer(buffer_dist)

    dissolved = triangles.dissolve()
    dissolved = dissolved[["geometry"]]
    dissolved["zone"] = "transition"

    os.makedirs(out_dir, exist_ok=True)
    dissolved.to_file(out_shp)

    print(f"Triangles extracted: {len(triangles)}")
    print(f"Output written to:   {out_shp}")

    # Select faces that intersect the trans_zone, then dissolve.
    faces = gpd.read_file(src_shp)
    zone = gpd.read_file(out_shp)
    if faces.crs != zone.crs:
        zone = zone.to_crs(faces.crs)

    selected_idx = gpd.sjoin(
        faces, zone[["geometry"]], how="inner", predicate="intersects"
    ).index.unique()
    selected = faces.loc[selected_idx].copy()

    selected_dissolved = selected.dissolve()
    selected_dissolved = selected_dissolved[["geometry"]]
    selected_dissolved["zone"] = "transition_faces"

    selected_dissolved.to_file(out_faces_shp)

    print(f"Intersecting faces:  {len(selected)}")
    print(f"Output written to:   {out_faces_shp}")

    return {"zone": out_shp, "faces": out_faces_shp}


def main():
    """Main function for the command line interface."""
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description=(
            "Extract triangle mesh cells from a faces shapefile, buffer and "
            "dissolve them into a transition zone, then select and dissolve "
            "all faces intersecting that zone."
        ),
        epilog="""
examples:
  %(prog)s
  %(prog)s -s SHP_NC/FlowFM_net_faces.shp -o SHP_TRANS
  %(prog)s -b 2.0
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-s",
        "--src",
        default=SRC_SHP,
        help=f"Path to the source faces shapefile (default: {SRC_SHP})",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        default=OUT_DIR,
        help=f"Directory to write output shapefiles to (default: {OUT_DIR})",
    )
    parser.add_argument(
        "-b",
        "--buffer-dist",
        type=float,
        default=1.0,
        help="Buffer distance applied to triangle faces (default: 1.0)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.src):
        print(f"Error: Source faces shapefile does not exist: {args.src}")
        sys.exit(1)

    try:
        make_trans_zone(args.src, args.out_dir, args.buffer_dist)
    except Exception as e:
        print(f"Error processing faces shapefile: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
