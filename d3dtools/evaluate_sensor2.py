"""
Module for calculating flood simulation accuracy and recall by comparing
simulated flood extents with observed sensor data.
"""

import os
import sys
import argparse
import geopandas as gpd
import pandas as pd


SOURCE_FIELD_CANDIDATES = ("來源說", "通報類型")

DEPTH_FIELD_PREFIX = "最大深"

OBS_SOURCE_EPSG = 4326
OBS_TARGET_EPSG = 3826


def _resolve_radius(source_value, buffer_radius):
    """Return the buffer radius for a given sensor source value."""
    if isinstance(buffer_radius, dict):
        return float(buffer_radius.get(source_value, 0))
    return float(buffer_radius)


def _resolve_source_field(columns):
    """Return the first matching source-field name found in columns, or None."""
    for name in SOURCE_FIELD_CANDIDATES:
        if name in columns:
            return name
    return None


def _resolve_depth_field(columns):
    """Return the first column whose name starts with 最大深, or None."""
    for name in columns:
        if str(name)[:len(DEPTH_FIELD_PREFIX)] == DEPTH_FIELD_PREFIX:
            return name
    return None


def confusion_matrix(
    low_threshold_sim_path="SHP/SIM_thrd125.shp",
    high_threshold_sim_path="SHP/SIM_thrd475.shp",
    obs_path="SHP/IOT_SENSOR.shp",
    buffer_radius=20,
    depth_threshold=10,
    output_csv=None,
    output_buffer_shp=None,
):
    """
    Calculate the accuracy and recall of a flood simulation using sensor data.

    Parameters
    ----------
    low_threshold_sim_path : str
        Path to the simulated flood extent shapefile with low threshold
    high_threshold_sim_path : str
        Path to the simulated flood extent shapefile with high threshold
    obs_path : str
        Path to the observed sensor point file (shapefile or GeoPackage
        ``*.gpkg``). If its CRS is EPSG:4326, it is reprojected to
        EPSG:3826 before use.
    buffer_radius : float or dict, optional
        Buffer radius around sensor points in meters. Set to 0 to use point
        data directly. Pass a dict mapping source-field values (``來源說``
        or ``通報類型``, whichever is present) to radii to use different
        radii per source, e.g. ``{"EMIC": 30, "淹水感測": 20}``
        (sources missing from the dict get a radius of 0). Default: 20.
    depth_threshold : float, optional
        Water depth threshold in centimeters (default: 10)
    output_csv : str, optional
        Path to the output CSV file
    output_buffer_shp : str, optional
        Path to a shapefile or GeoPackage (``*.gpkg``) where the buffered
        sensor geometries (with the applied radius and computed area) will
        be written. The format is chosen from the file extension.

    Returns
    -------
    dict
        Dictionary containing the accuracy, recall, and confusion matrix values
    """
    # Load the data
    low_sim_gdf = gpd.read_file(low_threshold_sim_path)
    high_sim_gdf = gpd.read_file(high_threshold_sim_path)
    obs_gdf = gpd.read_file(obs_path)

    # Reproject observation data from WGS84 to TWD97 / TM2 zone 121 if needed
    if obs_gdf.crs is not None and obs_gdf.crs.to_epsg() == OBS_SOURCE_EPSG:
        obs_gdf = obs_gdf.to_crs(epsg=OBS_TARGET_EPSG)

    # Create a buffer around observation points (per-source radius supported).
    # Rows whose resolved radius is 0 keep their original point geometry.
    obs_buffer = obs_gdf.copy()
    if isinstance(buffer_radius, dict):
        source_field = _resolve_source_field(obs_buffer.columns)
        if source_field is None:
            raise ValueError(
                "Per-source buffer requires one of the fields "
                f"{SOURCE_FIELD_CANDIDATES} in {obs_path}"
            )
        radii = obs_buffer[source_field].map(
            lambda s: _resolve_radius(s, buffer_radius)
        )
        obs_buffer["geometry"] = [
            geom.buffer(r) if r > 0 else geom
            for geom, r in zip(obs_buffer.geometry, radii)
        ]
    elif buffer_radius > 0:
        obs_buffer["geometry"] = obs_buffer.geometry.buffer(buffer_radius)
        radii = pd.Series([float(buffer_radius)] * len(obs_buffer), index=obs_buffer.index)
    else:
        radii = pd.Series([0.0] * len(obs_buffer), index=obs_buffer.index)

    # Record applied radius and resulting buffer area for each sensor
    obs_buffer["buf_r"] = radii.astype(float).values
    obs_buffer["buf_area"] = obs_buffer.geometry.area

    # Export buffered geometries if requested
    if output_buffer_shp:
        out_dir = os.path.dirname(output_buffer_shp)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        if os.path.splitext(output_buffer_shp)[1].lower() == ".gpkg":
            obs_buffer.to_file(output_buffer_shp, driver="GPKG")
        else:
            obs_buffer.to_file(output_buffer_shp, encoding="utf-8")
        print(f"Buffer geometries saved to {output_buffer_shp}")
        total_area = float(obs_buffer.geometry.area.sum())
        print(f"Total buffer area: {total_area:.2f} (CRS units squared)")

    # Resolve the depth field: the column whose name's first three
    # characters are 最大深 (e.g. 最大深度cm, 最大深度(公分), ...)
    depth_field = _resolve_depth_field(obs_buffer.columns)
    if depth_field is None:
        raise ValueError(
            f"No column starting with '{DEPTH_FIELD_PREFIX}' found in {obs_path}"
        )

    # Calculate True Positive (TP) and False Positive (FP)
    # Sensors with depth >= threshold that intersect with simulated flood
    True_Positive = 0
    False_Negative = 0

    for _, obs in obs_buffer.iterrows():
        if obs[depth_field] >= depth_threshold:
            # For both point and buffered data, intersects works appropriately
            if low_sim_gdf.intersects(obs.geometry).any():
                True_Positive += 1
            else:
                False_Negative += 1

    # Calculate False Positive (FP)
    # Sensors with depth < threshold that intersect with simulated flood
    False_Positive = 0
    True_Negative = 0

    for _, obs in obs_buffer.iterrows():
        if obs[depth_field] < depth_threshold:
            # For both point and buffered data, intersects works appropriately
            if high_sim_gdf.intersects(obs.geometry).any():
                False_Positive += 1
            else:
                True_Negative += 1

    # Calculate accuracy and recall
    accuracy = (True_Positive + True_Negative) / len(obs_buffer) * 100
    recall = (
        True_Positive / (True_Positive + False_Negative) * 100
        if (True_Positive + False_Negative) > 0
        else 0
    )
    # Comment out precision and f1_score as they're not used in the current output
    # precision = True_Positive / \
    #     (True_Positive + False_Positive) * \
    #     100 if (True_Positive + False_Positive) > 0 else 0
    # f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

    # Print the results
    print(f"True Positive: {True_Positive}")
    print(f"True Negative: {True_Negative}")
    print(f"False Positive: {False_Positive}")
    print(f"False Negative: {False_Negative}")
    print(f"Accuracy: {accuracy:.2f}%")
    print(f"Recall: {recall:.2f}%")
    # print(f"Precision: {precision:.2f}%")
    # print(f"F1 Score: {f1_score:.2f}%")

    # Save results to CSV if requested
    if output_csv:
        # Create a DataFrame to store the results
        results_df = pd.DataFrame(
            {
                "Simulation low threshold": [os.path.basename(low_threshold_sim_path)],
                "Simulation high threshold": [os.path.basename(high_threshold_sim_path)],
                "Observation": [os.path.basename(obs_path)],
                "True Positive": [True_Positive],
                "True Negative": [True_Negative],
                "False Positive": [False_Positive],
                "False Negative": [False_Negative],
                "Accuracy (%)": [round(accuracy, 2)],
                "Recall (%)": [round(recall, 2)],
                # 'Precision (%)': [round(precision, 2)],
                # 'F1 Score (%)': [round(f1_score, 2)]
            }
        )

        results_df.to_csv(output_csv, index=False)
        print(f"Results saved to {output_csv}")  # Return the results as a dictionary
    return {
        "accuracy": accuracy,
        "recall": recall,
        "true_positive": True_Positive,
        "true_negative": True_Negative,
        "false_positive": False_Positive,
        "false_negative": False_Negative,
    }


def main():
    """
    Main function for the command line interface.
    """
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(os.path.basename(sys.argv[0]))[0],
        description="Calculate flood simulation accuracy and recall using sensor data.",
        epilog="""
examples:
  %(prog)s              # Use default shapefiles and parameters (input SHP/SIM_thrd125.shp, SHP/SIM_thrd475.shp, SHP/IOT_SENSOR.shp; buffer 20m; threshold 10cm)
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp (default arguments)
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp --buffer 50 --thresh-iot 20
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp --buffer 0 --thresh-iot 30
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/IOT_SENSOR.shp --output results.csv
  %(prog)s --obs SHP/IOT_SENSOR.gpkg # --obs also accepts a GeoPackage (*.gpkg) file
  %(prog)s --buffer-by-source 淹水感測=20 EMIC=30 # Use different buffer radii per "來源說"/"通報類型" source
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sim-low",
        default=os.path.join("SHP", "SIM_thrd125.shp"),
        help="Path to low-threshold simulated flood extent shapefile (default: SHP/SIM_thrd125.shp)",
    )
    parser.add_argument(
        "--sim-high",
        default=os.path.join("SHP", "SIM_thrd475.shp"),
        help="Path to high-threshold simulated flood extent shapefile (default: SHP/SIM_thrd475.shp)",
    )
    parser.add_argument(
        "--obs",
        default=os.path.join("SHP", "IOT_SENSOR.shp"),
        help=(
            "Path to observed sensor point file: shapefile or GeoPackage "
            "(*.gpkg) (default: SHP/IOT_SENSOR.shp). If its CRS is "
            "EPSG:4326, it is reprojected to EPSG:3826 automatically."
        ),
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=20,
        help="Buffer radius around sensor points in meters. Set to 0 to use point data directly (default: 20). Ignored when --buffer-by-source is given.",
    )
    parser.add_argument(
        "--buffer-by-source",
        nargs="+",
        metavar="SOURCE=RADIUS",
        help=(
            "Per-source buffer radii using the '來源說' or '通報類型' field "
            "(whichever is present), e.g. "
            "--buffer-by-source EMIC=30 淹水感測=20. Sources not listed get radius 0."
        ),
    )
    parser.add_argument(
        "--thresh-iot",
        type=float,
        default=10,
        help="Water depth threshold in cm (default: 10)",
    )
    parser.add_argument("--output", help="Path to output CSV file (optional)")
    parser.add_argument(
        "--output-buffer",
        default=os.path.join("SHP", "IOT_BUFFER.gpkg"),
        help="Path to output shapefile or GeoPackage (*.gpkg) of the buffered sensor geometries (default: SHP/IOT_BUFFER.gpkg). Use '' to disable.",
    )

    args = parser.parse_args()

    # Check if the input files exist
    if not os.path.exists(args.sim_low):
        print(
            "Error: Low-threshold simulated flood extent file does not exist: "
            f"{args.sim_low}"
        )
        sys.exit(1)
    if not os.path.exists(args.sim_high):
        print(
            "Error: High-threshold simulated flood extent file does not exist: "
            f"{args.sim_high}"
        )
        sys.exit(1)
    if not os.path.exists(args.obs):
        print(f"Error: Observed sensor point file does not exist: {args.obs}")
        sys.exit(1)

    try:
        # Build buffer parameter: dict for per-source radii, else scalar
        if args.buffer_by_source:
            buffer_param = {}
            for item in args.buffer_by_source:
                if "=" not in item:
                    print(
                        f"Error: --buffer-by-source expects SOURCE=RADIUS, got: {item}"
                    )
                    sys.exit(1)
                key, value = item.split("=", 1)
                buffer_param[key.strip()] = float(value)
        else:
            buffer_param = args.buffer

        # Run the confusion matrix calculation (output is already handled in the function)
        confusion_matrix(
            low_threshold_sim_path=args.sim_low,
            high_threshold_sim_path=args.sim_high,
            obs_path=args.obs,
            buffer_radius=buffer_param,
            depth_threshold=args.thresh_iot,
            output_csv=args.output,
            output_buffer_shp=args.output_buffer,
        )
    except Exception as e:
        print(f"Error processing sensor evaluation: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
