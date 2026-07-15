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
import sys
from shapely.geometry import Point, Polygon
import argparse
import time


def _drop_non_utf8_fields(gdf, verbose=False):
    """Drop attribute columns whose original field name is not valid UTF-8.

    Assumes gdf was read with encoding='latin-1', which maps each raw byte to a single
    character 1:1 (lossless), so re-encoding a column name back to latin-1 recovers the
    original bytes exactly, which can then be checked against UTF-8.
    """
    bad_fields = [
        col for col in gdf.columns
        if col != gdf.geometry.name and not _is_valid_utf8(col)
    ]

    if bad_fields:
        _print_safe(f"Warning: skipping {len(bad_fields)} field(s) with non-UTF-8 names: {bad_fields}")
        gdf = gdf.drop(columns=bad_fields)
    elif verbose:
        print("All field names are valid UTF-8; none skipped")

    return gdf


def _is_valid_utf8(name):
    try:
        name.encode('latin-1').decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


def _print_safe(message):
    """Print a message, replacing characters the console encoding can't display.

    Field names recovered via the latin-1 fallback may contain characters outside
    the terminal's codepage (e.g. Windows cp950), which would otherwise crash print()
    with a UnicodeEncodeError.
    """
    encoding = sys.stdout.encoding or 'utf-8'
    print(message.encode(encoding, errors='replace').decode(encoding))


def extract_mesh2d_face_z(nc_file,
                         obs_shp,
                         output_csv='mesh2d_face_z.csv',
                         output_excel='mesh2d_face_z.xlsx',
                         id_field=None,
                         encoding=None,
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
    id_field : str (optional, default: None)
        Shapefile field to use for point names. If not given, tries common field names
        ('Name', 'name', 'NAME', 'id', 'ID', 'Id') before falling back to default names.
    encoding : str (optional, default: None)
        Text encoding of the shapefile's attribute table (e.g. 'cp950', 'big5'). If not
        given, geopandas auto-detects the encoding (via the .cpg file or a UTF-8 default).
    verbose : bool (optional, default: False)
        Whether to print detailed information during processing

    Returns:
    --------
    pandas.DataFrame
        DataFrame containing Mesh2d_face_z values at observation points
    """

    if verbose:
        print(f"Reading observation points from: {obs_shp}")

    # Read observation points from shapefile. If no encoding is specified and the default
    # (UTF-8) decoding fails, re-read losslessly as 'latin-1' (which maps every byte 1:1 to
    # a character and never raises a decode error) and drop any field whose name isn't
    # valid UTF-8, instead of guessing at the "real" encoding.
    try:
        if encoding:
            obs = gpd.read_file(obs_shp, encoding=encoding)
        else:
            try:
                obs = gpd.read_file(obs_shp)
            except UnicodeDecodeError:
                # GDAL's Shapefile driver only recognizes its own encoding names (e.g.
                # 'LATIN1'), not Python's ('latin-1'); passing the latter is silently
                # ignored and it falls back to the .cpg-declared encoding, reproducing
                # the same failure. 'LATIN1' is honored and never raises a decode error.
                obs = gpd.read_file(obs_shp, encoding='LATIN1')
                obs = _drop_non_utf8_fields(obs, verbose=verbose)
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
    
    # Get observation point names (user-specified id_field takes priority over auto-detection)
    if id_field is not None:
        if id_field not in obs.columns:
            nc.close()
            raise ValueError(f"ID field '{id_field}' not found in shapefile. Available fields: {list(obs.columns)}")
        obs_name_field = id_field
    else:
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
            (obs_name_field or 'Point_ID'): name,
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
  %(prog)s --nc-file results.nc --obs-shp points.shp -if StationName
  %(prog)s --nc-file results.nc --obs-shp points.shp -e cp950
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
    parser.add_argument('-if', '--id-field', dest='id_field', default=None,
                        help="Shapefile field to use for point names (default: auto-detect from 'Name', 'name', 'NAME', 'id', 'ID', 'Id')")
    parser.add_argument('-e', '--encoding', dest='encoding', default=None,
                        help="Text encoding of the shapefile's attribute table, e.g. 'cp950', 'big5' (default: auto-detect)")
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
        start_time = time.perf_counter()
        df = extract_mesh2d_face_z(nc_file=args.nc_file,
                                  obs_shp=args.obs_shp,
                                  output_csv=args.output_csv,
                                  output_excel=args.output_excel,
                                  id_field=args.id_field,
                                  encoding=args.encoding,
                                  verbose=args.verbose)
        elapsed_time = time.perf_counter() - start_time

        print(f"\nExtraction completed successfully!")
        print(f"Processed {len(df)} observation points.")
        print(f"Processing time: {elapsed_time:.2f} seconds")

        return 0
        
    except Exception as e:
        print(f"Error during processing: {e}")
        return 1


if __name__ == "__main__":
    exit(main())