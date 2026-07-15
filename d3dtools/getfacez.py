"""
Mesh2d_face_z extraction module for Delft3D FM NetCDF files (spatial-index accelerated).

This is a drop-in, faster replacement for getfacez.py. The original module matches every
observation point against every mesh face (O(N_points x N_faces)), which becomes slow on
large meshes. This version builds a spatial index once and queries it per point instead:

- Boundary mode (point-in-polygon): builds a shapely STRtree over all face polygons once,
  then does a single vectorized batch query for all observation points.
- Center mode (nearest neighbor): builds a scipy cKDTree over all face centers once, then
  does a single vectorized nearest-neighbor query for all observation points.

Both approaches turn the search into roughly O(N_faces log N_faces) to build the index plus
O(N_points log N_faces) to query it, instead of O(N_points x N_faces).

Note: Mesh2d_face_z has no time dimension as it represents static bed levels.
"""
import numpy as np
import geopandas as gpd
import pandas as pd
from netCDF4 import Dataset
import os
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree
from scipy.spatial import cKDTree
import argparse


def _build_face_polygons(x_bnd, y_bnd, verbose=False):
    """Build valid face polygons once, along with the original face index each one maps to."""
    polygons = []
    face_indices = []

    for j in range(len(x_bnd)):
        face_x_coords = x_bnd[j]
        face_y_coords = y_bnd[j]

        valid_mask = ~np.ma.getmaskarray(face_x_coords) & ~np.ma.getmaskarray(face_y_coords)
        if not np.any(valid_mask):
            continue

        face_x_valid = face_x_coords[valid_mask]
        face_y_valid = face_y_coords[valid_mask]

        if len(face_x_valid) < 3:
            continue

        try:
            polygon_coords = list(zip(face_x_valid, face_y_valid))
            if polygon_coords[0] != polygon_coords[-1]:
                polygon_coords.append(polygon_coords[0])

            polygon = Polygon(polygon_coords)
            if not polygon.is_valid:
                continue

            polygons.append(polygon)
            face_indices.append(j)
        except Exception:
            continue

    if verbose:
        print(f"Built {len(polygons)}/{len(x_bnd)} valid face polygons for spatial index")

    return polygons, np.array(face_indices)


def _match_points_to_faces_by_polygon(obs_x, obs_y, x_bnd, y_bnd, verbose=False):
    """Match observation points to mesh faces via an STRtree point-in-polygon query."""
    polygons, face_indices = _build_face_polygons(x_bnd, y_bnd, verbose=verbose)

    n_points = len(obs_x)
    matched_face_idx = np.full(n_points, -1, dtype=int)

    if not polygons:
        return matched_face_idx

    tree = STRtree(polygons)
    points = np.array([Point(x, y) for x, y in zip(obs_x, obs_y)])

    # 'within' evaluates point.within(polygon), i.e. point-in-polygon, for every point/polygon
    # pair in a single vectorized call instead of a nested Python loop.
    input_idx, tree_idx = tree.query(points, predicate='within')

    # A well-formed mesh has no overlapping faces, but guard against duplicates by keeping
    # the first match found for each point.
    seen = set()
    for ii, tt in zip(input_idx, tree_idx):
        if ii in seen:
            continue
        seen.add(ii)
        matched_face_idx[ii] = face_indices[tt]

    return matched_face_idx


def _match_points_to_faces_by_nearest(obs_x, obs_y, face_x, face_y, verbose=False):
    """Match observation points to mesh faces via a cKDTree nearest-neighbor query."""
    face_centers = np.column_stack((np.asarray(face_x), np.asarray(face_y)))
    tree = cKDTree(face_centers)

    obs_coords = np.column_stack((obs_x, obs_y))
    distances, nearest_idx = tree.query(obs_coords)

    if verbose:
        for i in range(min(5, len(obs_x))):
            print(f"Point {i}: nearest face {nearest_idx[i]}, distance: {distances[i]:.3f}")

    return nearest_idx


def extract_mesh2d_face_z(nc_file,
                         obs_shp,
                         output_csv='mesh2d_face_z.csv',
                         output_excel='mesh2d_face_z.xlsx',
                         id_field=None,
                         verbose=False):
    """
    Extract Mesh2d_face_z values at observation points from a Delft3D FM NetCDF file.

    Uses a spatial index (STRtree for point-in-polygon, cKDTree for nearest-neighbor) so
    the search is not O(N_points x N_faces) like the original getfacez.py implementation.

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
            print("Using face boundary coordinates with STRtree point-in-polygon search")
    elif 'Mesh2d_face_x' in nc.variables and 'Mesh2d_face_y' in nc.variables:
        mesh2d_face_x = nc.variables['Mesh2d_face_x'][:]
        mesh2d_face_y = nc.variables['Mesh2d_face_y'][:]
        use_boundaries = False
        if verbose:
            print("Using face center coordinates with cKDTree nearest neighbor search")
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

    obs_x = obs.geometry.x.to_numpy()
    obs_y = obs.geometry.y.to_numpy()

    # Match all observation points to mesh faces in one batched spatial-index query,
    # instead of looping over every face for every point.
    if use_boundaries:
        matched_face_idx = _match_points_to_faces_by_polygon(
            obs_x, obs_y, mesh2d_face_x_bnd, mesh2d_face_y_bnd, verbose=verbose)
    else:
        matched_face_idx = _match_points_to_faces_by_nearest(
            obs_x, obs_y, mesh2d_face_x, mesh2d_face_y, verbose=verbose)

    # Prepare results list
    results = []

    for i in range(len(obs)):
        face_index = int(matched_face_idx[i])
        face_z_value = mesh2d_face_z[face_index] if face_index >= 0 else np.nan

        results.append({
            'Point_ID': obs_names[i],
            'X_Coordinate': obs_x[i],
            'Y_Coordinate': obs_y[i],
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
    Command line entry point for the Mesh2d_face_z extraction tool (spatial-index accelerated).

    Example usage:
        python getfacez2.py --nc-file path/to/file.nc --obs-shp path/to/observations.shp
        python getfacez2.py --nc-file results.nc --obs-shp points.shp --output-csv bathymetry.csv --verbose
    """
    parser = argparse.ArgumentParser(
        description="Extract Mesh2d_face_z values from Delft3D FM NetCDF files at observation points (spatial-index accelerated)",
        epilog='''
examples:
  %(prog)s --nc-file results.nc --obs-shp observation_points.shp
  %(prog)s --nc-file results.nc --obs-shp points.shp --output-csv bathymetry.csv
  %(prog)s --nc-file results.nc --obs-shp points.shp --output-excel bathymetry.xlsx --verbose
  %(prog)s --nc-file results.nc --obs-shp points.shp -if StationName
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
                                  id_field=args.id_field,
                                  verbose=args.verbose)

        print(f"\nExtraction completed successfully!")
        print(f"Processed {len(df)} observation points.")

        return 0

    except Exception as e:
        print(f"Error during processing: {e}")
        return 1


if __name__ == "__main__":
    exit(main())