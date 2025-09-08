"""
Mesh2d_face_z extraction module for Delft3D FM NetCDF files.

This module provides functions for extracting Mesh2d_face_z values (bed level/bathymetry)
at observation points from Delft3D FM NetCDF output files.
Note: Mesh2d_face_z has no time dimension as it represents static bed levels.
"""
import numpy as np
import geopandas as gpd
import pandas as pd
from netCDF4 import Dataset
import os
from shapely.geometry import Point, Polygon
import argparse


def extract_mesh2d_face_z(nc_file,
                         obs_shp,
                         output_csv='mesh2d_face_z.csv',
                         output_excel='mesh2d_face_z.xlsx',
                         verbose=False):
    """
    Extract Mesh2d_face_z values at observation points from a Delft3D FM NetCDF file.

    Parameters:
    -----------
    nc_file : str (required)
        Path to the NetCDF file containing model results
    obs_shp : str (required)
        Path to the shapefile containing observation points
    output_csv : str (required, default: 'mesh2d_face_z.csv')
        Path to save the output CSV file
    output_excel : str (required, default: 'mesh2d_face_z.xlsx')
        Path to save the output Excel file
    verbose : bool (optional, default: False)
        Whether to print detailed information during processing

    Returns:
    --------
    pandas.DataFrame
        DataFrame containing Mesh2d_face_z values at observation points
    """
    
    if verbose:
        print(f"Reading observation points from: {obs_shp}")
    
    # Read observation points from shapefile
    try:
        obs = gpd.read_file(obs_shp)
    except Exception as e:
        raise ValueError(f"Error reading shapefile: {e}")
    
    if verbose:
        print(f"Found {len(obs)} observation points")
        print(f"Reading NetCDF file: {nc_file}")
    
    # Read NetCDF file
    try:
        nc = Dataset(nc_file, mode='r')
    except Exception as e:
        raise ValueError(f"Error reading NetCDF file: {e}")
    
    # Check if Mesh2d_face_z variable exists
    if 'Mesh2d_face_z' not in nc.variables:
        available_vars = list(nc.variables.keys())
        nc.close()
        raise ValueError(f"Mesh2d_face_z variable not found in NetCDF file. Available variables: {available_vars}")
    
    # Get the Mesh2d_face_z data (bed level/bathymetry - no time dimension)
    mesh2d_face_z = nc.variables['Mesh2d_face_z'][:]
    
    if verbose:
        print(f"Mesh2d_face_z shape: {mesh2d_face_z.shape}")
        print(f"Mesh2d_face_z range: {np.nanmin(mesh2d_face_z):.3f} to {np.nanmax(mesh2d_face_z):.3f}")
    
    # Get the mesh face boundary coordinates
    if 'Mesh2d_face_x_bnd' in nc.variables and 'Mesh2d_face_y_bnd' in nc.variables:
        mesh2d_face_x_bnd = nc.variables['Mesh2d_face_x_bnd'][:]
        mesh2d_face_y_bnd = nc.variables['Mesh2d_face_y_bnd'][:]
        use_boundaries = True
        if verbose:
            print("Using face boundary coordinates for point-in-polygon search")
    elif 'Mesh2d_face_x' in nc.variables and 'Mesh2d_face_y' in nc.variables:
        mesh2d_face_x = nc.variables['Mesh2d_face_x'][:]
        mesh2d_face_y = nc.variables['Mesh2d_face_y'][:]
        use_boundaries = False
        if verbose:
            print("Using face center coordinates for nearest neighbor search")
    else:
        nc.close()
        raise ValueError("Neither face boundary coordinates nor face center coordinates found in NetCDF file")
    
    # Get observation point names (try common field names)
    obs_name_field = None
    for field in ['Name', 'name', 'NAME', 'id', 'ID', 'Id']:
        if field in obs.columns:
            obs_name_field = field
            break
    
    if obs_name_field is None:
        # Create default names if no name field found
        obs_names = [f"Point_{i+1}" for i in range(len(obs))]
        if verbose:
            print("No name field found in shapefile, using default names")
    else:
        obs_names = obs[obs_name_field].tolist()
        if verbose:
            print(f"Using '{obs_name_field}' field for point names")
    
    # Prepare results list
    results = []
    
    # Loop through all observation points
    for i in range(len(obs)):
        x1 = obs.geometry.iloc[i].x
        y1 = obs.geometry.iloc[i].y
        name = obs_names[i]
        
        face_z_value = np.nan
        face_index = -1
        
        if use_boundaries:
            # Use face boundary coordinates for point-in-polygon check
            for j in range(len(mesh2d_face_x_bnd)):
                # Skip faces with invalid coordinates
                face_x_coords = mesh2d_face_x_bnd[j]
                face_y_coords = mesh2d_face_y_bnd[j]
                
                # Remove masked/invalid coordinates
                valid_mask = ~np.ma.getmaskarray(face_x_coords) & ~np.ma.getmaskarray(face_y_coords)
                if not np.any(valid_mask):
                    continue
                
                face_x_valid = face_x_coords[valid_mask]
                face_y_valid = face_y_coords[valid_mask]
                
                # Create polygon from face boundary
                if len(face_x_valid) >= 3:  # Need at least 3 points for a polygon
                    try:
                        polygon_coords = list(zip(face_x_valid, face_y_valid))
                        # Close the polygon if not already closed
                        if polygon_coords[0] != polygon_coords[-1]:
                            polygon_coords.append(polygon_coords[0])
                        
                        polygon = Polygon(polygon_coords)
                        point = Point(x1, y1)
                        
                        if polygon.is_valid and polygon.contains(point):
                            face_z_value = mesh2d_face_z[j]
                            face_index = j
                            break
                    except Exception:
                        continue
        else:
            # Use face center coordinates for nearest neighbor search
            distances = np.sqrt((mesh2d_face_x - x1)**2 + (mesh2d_face_y - y1)**2)
            nearest_face_idx = np.argmin(distances)
            face_z_value = mesh2d_face_z[nearest_face_idx]
            face_index = nearest_face_idx
            
            if verbose and i < 5:  # Print details for first few points
                print(f"Point {name}: nearest face {nearest_face_idx}, distance: {distances[nearest_face_idx]:.3f}")
        
        # Store results
        results.append({
            'Point_Name': name,
            'X_Coordinate': x1,
            'Y_Coordinate': y1,
            'Mesh2d_face_z': face_z_value,
            'Face_Index': face_index
        })
        
        if verbose and (i + 1) % 10 == 0:
            print(f"Processed {i + 1}/{len(obs)} points")
    
    # Close NetCDF file
    nc.close()
    
    # Create DataFrame
    df = pd.DataFrame(results)
    
    # Round coordinates and face_z to reasonable precision
    df['X_Coordinate'] = df['X_Coordinate'].round(6)
    df['Y_Coordinate'] = df['Y_Coordinate'].round(6)
    df['Mesh2d_face_z'] = df['Mesh2d_face_z'].round(3)
    
    # Check for points without valid face_z values
    invalid_points = df['Mesh2d_face_z'].isna().sum()
    if invalid_points > 0:
        print(f"Warning: {invalid_points} points could not be matched to valid mesh faces")
    
    # Save results
    if output_csv:
        try:
            df.to_csv(output_csv, index=False)
            print(f"Results saved to CSV: {output_csv}")
        except Exception as e:
            print(f"Error saving CSV file: {e}")
    
    if output_excel:
        try:
            df.to_excel(output_excel, index=False)
            print(f"Results saved to Excel: {output_excel}")
        except Exception as e:
            print(f"Error saving Excel file: {e}")
    
    if verbose:
        print(f"\nSummary:")
        print(f"Total points processed: {len(df)}")
        print(f"Points with valid Mesh2d_face_z: {len(df) - invalid_points}")
        print(f"Mesh2d_face_z range: {df['Mesh2d_face_z'].min():.3f} to {df['Mesh2d_face_z'].max():.3f}")
    
    return df


def main():
    """
    Command line entry point for the Mesh2d_face_z extraction tool.

    Example usage:
        python extract_mesh2d_face_z.py --nc-file path/to/file.nc --obs-shp path/to/observations.shp
        python extract_mesh2d_face_z.py --nc-file results.nc --obs-shp points.shp --output-csv bathymetry.csv --verbose
    """
    parser = argparse.ArgumentParser(
        description="Extract Mesh2d_face_z values from Delft3D FM NetCDF files at observation points",
        epilog='''
examples:
  %(prog)s --nc-file results.nc --obs-shp observation_points.shp
  %(prog)s --nc-file results.nc --obs-shp points.shp --output-csv bathymetry.csv
  %(prog)s --nc-file results.nc --obs-shp points.shp --output-excel bathymetry.xlsx --verbose
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('--nc-file', dest='nc_file', required=True,
                        help='Path to the NetCDF file containing model results')
    parser.add_argument('--obs-shp', dest='obs_shp', required=True,
                        help='Path to the shapefile containing observation points')
    parser.add_argument('--output-csv', dest='output_csv', default='mesh2d_face_z.csv',
                        metavar='mesh2d_face_z.csv',
                        help='Path to save the output CSV file (default: mesh2d_face_z.csv)')
    parser.add_argument('--output-excel', dest='output_excel', default='mesh2d_face_z.xlsx',
                        metavar='mesh2d_face_z.xlsx',
                        help='Path to save the output Excel file (default: mesh2d_face_z.xlsx)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Display additional information during processing')

    args = parser.parse_args()

    # Validate input files
    if not os.path.exists(args.nc_file):
        print(f"Error: NetCDF file not found: {args.nc_file}")
        return 1
    
    if not os.path.exists(args.obs_shp):
        print(f"Error: Shapefile not found: {args.obs_shp}")
        return 1

    # Print arguments if verbose
    if args.verbose:
        print("Processing with parameters:")
        for arg, value in vars(args).items():
            print(f"  {arg}: {value}")
        print()

    try:
        # Call the extraction function
        print(f"Extracting Mesh2d_face_z values from {args.nc_file}...")
        df = extract_mesh2d_face_z(nc_file=args.nc_file,
                                  obs_shp=args.obs_shp,
                                  output_csv=args.output_csv,
                                  output_excel=args.output_excel,
                                  verbose=args.verbose)

        print(f"\nExtraction completed successfully!")
        print(f"Processed {len(df)} observation points.")
        
        return 0
        
    except Exception as e:
        print(f"Error during processing: {e}")
        return 1


if __name__ == "__main__":
    exit(main())