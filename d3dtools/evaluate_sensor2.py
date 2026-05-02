"""
Module for calculating flood simulation accuracy and recall by comparing
simulated flood extents with observed sensor data.
"""

import os
import sys
import argparse
import geopandas as gpd
import pandas as pd


def confusion_matrix(
    low_threshold_sim_path,
    high_threshold_sim_path,
    obs_path,
    buffer_radius=30,
    depth_threshold=30,
    output_csv=None,
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
        Path to the observed sensor point shapefile
    buffer_radius : float, optional
        Buffer radius around sensor points in meters. Set to 0 to use point data directly (default: 30)
    depth_threshold : float, optional
        Water depth threshold in centimeters (default: 30)
    output_csv : str, optional
        Path to the output CSV file

    Returns
    -------
    dict
        Dictionary containing the accuracy, recall, and confusion matrix values
    """
    # Load the data
    low_sim_gdf = gpd.read_file(low_threshold_sim_path)
    high_sim_gdf = gpd.read_file(high_threshold_sim_path)
    obs_gdf = gpd.read_file(obs_path)

    # Create a buffer around observation points or use original points if buffer_radius is 0
    obs_buffer = obs_gdf.copy()
    if buffer_radius > 0:
        obs_buffer["geometry"] = obs_buffer.geometry.buffer(buffer_radius)
    # If buffer_radius is 0, keep the original point geometry

    # Calculate True Positive (TP) and False Positive (FP)
    # Sensors with depth >= threshold that intersect with simulated flood
    True_Positive = 0
    False_Negative = 0

    for _, obs in obs_buffer.iterrows():
        if obs["最大深"] >= depth_threshold:
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
        if obs["最大深"] < depth_threshold:
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
        description="Calculate flood simulation accuracy and recall using sensor data.",
        epilog="""
examples:
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp --buffer 50 --threshold 20
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp --buffer 0 --threshold 30
  %(prog)s --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp --output results.csv
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sim-low",
        required=True,
        help="Path to low-threshold simulated flood extent shapefile",
    )
    parser.add_argument(
        "--sim-high",
        required=True,
        help="Path to high-threshold simulated flood extent shapefile",
    )
    parser.add_argument(
        "--obs", required=True, help="Path to observed sensor point shapefile"
    )
    parser.add_argument(
        "--buffer",
        type=float,
        default=30,
        help="Buffer radius around sensor points in meters. Set to 0 to use point data directly (default: 30)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=30,
        help="Water depth threshold in cm (default: 30)",
    )
    parser.add_argument("--output", help="Path to output CSV file (optional)")

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
        # Run the confusion matrix calculation (output is already handled in the function)
        confusion_matrix(
            low_threshold_sim_path=args.sim_low,
            high_threshold_sim_path=args.sim_high,
            obs_path=args.obs,
            buffer_radius=args.buffer,
            depth_threshold=args.threshold,
            output_csv=args.output,
        )
    except Exception as e:
        print(f"Error processing sensor evaluation: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
