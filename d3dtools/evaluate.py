"""
Module for calculating flood simulation accuracy and catch rate by comparing
simulated flood extents with observed flood extents.
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd
import geopandas as gpd
import matplotlib.pyplot as plt


def confusion_matrix(sim_path, obs_path, output_csv=None):
    """
        Calculate the accuracy and recall of a flood simulation.

        Parameters
        ----------
        sim_path : str
            Path to the simulated flood extent shapefile
        obs_path : str
            Path to the observed flood extent shapefile
        output_csv : str (optional)
            Path to the output CSV file

        Returns
        -------
        dict
            Dictionary containing the accuracy and catch rate values
        """
    # Load the data
    flood_sim = gpd.read_file(sim_path)
    flood_obs = gpd.read_file(obs_path)

    # Calculate the intersection between the two datasets
    intersection = gpd.overlay(flood_sim, flood_obs, how='intersection')

    # Calculate the accuracy
    accuracy = intersection.area.sum() / (flood_obs.area.sum() +
                                          flood_sim.area.sum() -
                                          intersection.area.sum()) * 100

    # Calculate the catch rate
    recall = intersection.area.sum() / flood_obs.area.sum() * 100

    # Print the results
    print(f"Simulated flood area: {flood_sim.area.sum() / 1e6:.2f}")
    print(f"Observed flood area: {flood_obs.area.sum() / 1e6:.2f}")
    print(f"Intersection area: {intersection.area.sum() / 1e6:.2f}")
    print(f"Accuracy: {accuracy:.2f}%")
    print(f"Recall: {recall:.2f}%")

    # Output
    if output_csv:
        # Create a DataFrame to store the results
        results_df = pd.DataFrame({
            'Simulation': [os.path.basename(sim_path)],
            'Observation': [os.path.basename(obs_path)],
            'Accuracy (%)': [round(accuracy, 2)],
            'Recall (%)': [round(recall, 2)]
        })

        # Save the results to a CSV file
        results_df.to_csv(output_csv, index=False)
        print(f"Results saved to {output_csv}")
    # Return the results as a dictionary
    return {'accuracy': accuracy, 'recall': recall}


def main():
    """
        Main function for the command line interface.
        """
    parser = argparse.ArgumentParser(
        description='Calculate flood simulation accuracy and catch rate.')
    parser.add_argument('--sim',
                        required=True,
                        help='Path to simulated flood extent shapefile')
    parser.add_argument('--obs',
                        required=True,
                        help='Path to observed flood extent shapefile')
    parser.add_argument('--output', help='Path to output CSV file (optional)')

    args = parser.parse_args()

    # Check if the input files exist
    if not os.path.exists(args.sim):
        print(f"Error: Simulated flood extent file does not exist: {args.sim}")
        sys.exit(1)

    if not os.path.exists(args.obs):
        print(f"Error: Observed flood extent file does not exist: {args.obs}")
        sys.exit(1)

    try:
        matrix = confusion_matrix(args.sim, args.obs)

        # Save the results to a CSV file if requested
        if args.output:
            results_df = pd.DataFrame([{
                'Simulation': os.path.basename(args.sim),
                'Observation': os.path.basename(args.obs),
                'Accuracy (%)': round(matrix['accuracy'], 2),
                'Recall (%)': round(matrix['recall'], 2)
            }])
            results_df.to_csv(args.output, index=False)
            print(f"Results saved to {args.output}")

    except Exception as e:
        print(f"Error processing flood matrix: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
