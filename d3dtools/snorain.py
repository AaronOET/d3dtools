"""
Process rainfall scenario data from a CSV file and generate separate time series CSV files for each scenario.

This module can be executed using the 'snorain' command.
"""
import numpy as np
import pandas as pd
import os
import argparse


def generate(input_file, output_folder='TAB', verbose=False):
    """
          Process a rainfall CSV file and generate separate time series CSV files for each scenario.

          Parameters:
          -----------
          input_file : str
              Path to the input CSV file containing rainfall scenario data
          output_folder : str
              Directory where output CSV files will be saved (default: 'TAB')
          verbose : bool
              Whether to display detailed processing information (default: False)

          Returns:
          --------
          list
              List of paths to the created CSV files
          """    # Read data
    Scenario = pd.read_csv(input_file, encoding='utf-8')

    # Get base filename for output files
    base_filename = os.path.splitext(os.path.basename(input_file))[0]

    # Create output directory if it doesn't exist
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Get column names, index names, and unique scenario values
    scenario_index = Scenario.columns.get_loc('情境')
    values_cols = Scenario.columns[scenario_index + 1:-1].tolist()

    column_names = Scenario['ID'].unique()
    index_values = Scenario['情境'].unique()

    if verbose:
        print(f"Processing file: {input_file}")
        print(f"Found {len(index_values)} scenarios: {index_values}")
        print("Values columns:")
        print(values_cols)
        print("\nColumns (station IDs):")
        print(column_names)    # Create pivot table structure
    table = pd.pivot_table(Scenario,
                           values=values_cols,
                           columns='ID',
                           index='情境',
                           aggfunc='sum')

    # Process each scenario
    output_files = []
    for scenario_value in index_values:
        if verbose:
            print(f"\nProcessing scenario {scenario_value}...")

        # Get table for the current scenario
        scenario_table = table.query(f'情境 == {scenario_value}')

        # Use information from the pivot table structure to concatenate the results
        result_table = pd.concat(
            [scenario_table[col] for col in scenario_table.columns.levels[0]],
            axis=0)

        # Replace the index with time stamps starting from 1/1/2000 0:00
        result_table.index = pd.date_range('2000-01-01 00:00',
                                           periods=len(result_table),
                                           freq='h')

        # Replace the index name with 'time'
        result_table.index.name = 'time'

        # Create output filename
        output_file = os.path.join(output_folder,
                                   f"{base_filename}_{scenario_value}.csv")
        output_files.append(output_file)

        # Save the result to a CSV file
        result_table.to_csv(output_file, encoding='utf-8')
        if verbose:
            print(f"Saved scenario {scenario_value} to {output_file}")

    return output_files


def main():
    """
          Command line entry point
          """
    parser = argparse.ArgumentParser(
        description='Process rainfall scenario data and generate time series CSV files')

    parser.add_argument('-i',
                        '--input',
                        required=True,
                        help='Input CSV file with rainfall scenario data')

    parser.add_argument(
        '-o',
        '--output',
        default='TAB',
        help='Output folder for generated CSV files (default: "TAB")')

    parser.add_argument('-v',
                        '--verbose',
                        action='store_true',
                        help='Display detailed processing information')

    args = parser.parse_args()

    # Check if output folder exists, if not create it
    if not os.path.exists(args.output):
        os.makedirs(args.output)
        if args.verbose:
            print(f"Output folder '{args.output}' created.")

    # Check if file exists
    if not os.path.exists(args.input):
        print(f"Error: Input file {args.input} not found.")
        return

    # Process the file
    try:
        result_files = generate(input_file=args.input,
                               output_folder=args.output,
                               verbose=args.verbose)

        if args.verbose:
            print(
                f"\nCompleted processing. Created {len(result_files)} output files:")
            for file in result_files:
                print(f"  {file}")
    except Exception as e:
        print(f"Error processing file: {e}")


if __name__ == "__main__":
    main()
