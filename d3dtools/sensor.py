"""
Sensor data extraction module for Delft3D FM NetCDF files.

This module provides functions for extracting time series data at observation points
from Delft3D FM NetCDF output files.
"""
import matplotlib.pyplot as plt
import numpy as np
import geopandas as gpd
import pandas as pd
from netCDF4 import Dataset
import os


def getdata(nc_file,
            obs_shp,
            output_csv='water_depth.csv',
            output_excel='water_depth.xlsx',
            plot=False):
  """
      Extract time series data at observation points from a Delft3D FM NetCDF file.

      Parameters:
      -----------
      nc_file : str (required)
          Path to the NetCDF file containing model results
      obs_shp : str (required)
          Path to the shapefile containing observation points
      output_csv : str (required, default: 'water_depth.csv')
          Path to save the output CSV file
      output_excel : str (required, default: 'water_depth.xlsx')
          Path to save the output Excel file
      plot : bool (optional, default: False)
          Whether to plot the results

      Returns:
      --------
      pandas.DataFrame
          DataFrame containing time series data at observation points
      """
  # Read observation points from shapefile
  obs = gpd.read_file(obs_shp)

  # Read NetCDF file
  nc = Dataset(nc_file, mode='r')

  # Get the time dimension
  time = nc.variables['time'][:]

  # Get the time unit and convert to datetime
  time_unit = nc.variables['time'].units
  startTime = pd.to_datetime(
      time_unit.split('since')[-1].split('+')[0].strip())

  # Create time series from the start time
  time_series = [startTime + pd.Timedelta(seconds=t) for t in time]

  # Get the water depth data
  Mesh2d_waterdepth = nc.variables['Mesh2d_waterdepth'][:]

  # Get the mesh face boundary coordinates
  Mesh2d_face_x_bnd = nc.variables['Mesh2d_face_x_bnd'][:]
  Mesh2d_face_y_bnd = nc.variables['Mesh2d_face_y_bnd'][:]

  # Get name of observation points
  obs_name = obs['Name']

  # Create an empty dataframe
  df = pd.DataFrame()

  # Loop through all the observation points and get the water depth
  for i in range(len(obs)):
    x1 = obs['geometry'][i].x
    y1 = obs['geometry'][i].y
    name = obs_name[i]

    for j in range(len(Mesh2d_face_x_bnd)):
      if (x1 >= min(Mesh2d_face_x_bnd[j])) and (x1 <= max(Mesh2d_face_x_bnd[j])) and \
         (y1 >= min(Mesh2d_face_y_bnd[j])) and (y1 <= max(Mesh2d_face_y_bnd[j])):
        water_depth = Mesh2d_waterdepth[:, j]
        # Create a dataframe for each observation point
        df1 = pd.DataFrame(water_depth, columns=[name])
        break

    # Concatenate
    df = pd.concat([df, df1], axis=1)

  # Round to 3 decimal places
  df = df.round(3)

  # Add time series
  df['time'] = time_series

  # Move time column to the front
  cols = df.columns.tolist()
  cols = cols[-1:] + cols[:-1]
  df = df[cols]

  # Save the dataframe to csv if specified
  if output_csv:
    df.to_csv(output_csv, index=False)

  # Save the dataframe to excel if specified
  if output_excel:
    df.to_excel(output_excel, index=False)

  # Print the message that the file is saved
  if output_csv:
    print(f"Results saved to CSV: {output_csv}")
  if output_excel:
    print(f"Results saved to Excel: {output_excel}")

  # Plot the water depth if specified
  if plot:
    fig, ax = plt.subplots(figsize=(10, 6))
    for i in range(1, len(df.columns)):
      ax.plot(df['time'], df.iloc[:, i], label=df.columns[i])
    ax.set(xlabel='Time',
           ylabel='Water Depth',
           title='Water Depth at Observation Points')
    plt.legend()
    plt.tight_layout()
    plt.show()

  return df


def main():
  """
    Command line entry point for the sensor data extraction tool.

    Example usage:
        sensor --nc-file path/to/file.nc --obs-shp path/to/observations.shp --output-csv results.csv --plot
    """
  import argparse

  parser = argparse.ArgumentParser(
      description="Extract time series data from Delft3D FM NetCDF files at observation points")

  parser.add_argument('--nc-file', dest='nc_file', required=True,
                      help='Path to the NetCDF file containing model results')
  parser.add_argument('--obs-shp', dest='obs_shp', required=True,
                      help='Path to the shapefile containing observation points')
  parser.add_argument('--output-csv', dest='output_csv', default='water_depth.csv',
                      metavar='water_depth.csv',
                      help='Path to save the output CSV file')
  parser.add_argument('--output-excel', dest='output_excel', default='water_depth.xlsx',
                      metavar='water_depth.xlsx',
                      help='Path to save the output Excel file')
  parser.add_argument('--plot', action='store_true',
                      help='Display a plot of the results (optional)')
  parser.add_argument('--verbose', '-v', action='store_true',
                      help='Display additional information during processing (optional)')

  args = parser.parse_args()

  # Print arguments if verbose
  if args.verbose:
    print("Processing with parameters:")
    for arg, value in vars(args).items():
      print(f"  {arg}: {value}")

  # Call the getdata function with the provided arguments
  print(f"Extracting time series data from {args.nc_file}...")
  df = getdata(nc_file=args.nc_file,
               obs_shp=args.obs_shp,
               output_csv=args.output_csv,
               output_excel=args.output_excel,
               plot=args.plot)

  print(f"Processed {len(df.columns) - 1} observation points.")
  # if args.output_csv:
  #     print(f"Results saved to CSV: {args.output_csv}")
  # if args.output_excel:
  #     print(f"Results saved to Excel: {args.output_excel}")

  return 0


if __name__ == "__main__":
  main()
