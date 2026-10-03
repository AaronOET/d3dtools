#!/usr/bin/env python
"""
Command-line utility to display descriptions of d3dtools functionality.
"""

import argparse
import sys
import webbrowser
from textwrap import dedent

TOOL_DESCRIPTIONS = {
    'evaluate':
    """
        Calculate flood simulation accuracy and catch rate metrics.

        This tool compares simulated flood extent shapefiles with observed flood extent
        shapefiles to calculate accuracy and catch rate metrics, which are important for
        validating flood model performance.

        Examples:
            evaluate --sim SHP/SIM.shp --obs SHP/OBS.shp
            evaluate --sim SHP/SIM.shp --obs SHP/OBS.shp --output results.csv
    """,
    'evaluate_sensor':
    """
        Evaluate flood simulation accuracy and recall using sensor data.

        This tool compares simulated flood extent shapefiles with observed flood extent
        shapefiles to calculate accuracy and recall metrics, which are important for
        validating flood model performance.

        Examples:
            evaluate_sensor --sim SHP/SIM.shp --obs SHP/OBS.shp
            evaluate_sensor --sim SHP/SIM.shp --obs SHP/OBS.shp --buffer 50 --thresh-iot 20
            evaluate_sensor --sim SHP/SIM.shp --obs SHP/OBS.shp --output results.csv
    """,
    'evaluate_sensor2':
    """
        Evaluate flood simulation accuracy using sensor data with dual-threshold shapefiles.

        This tool compares two simulated flood extent shapefiles (low and high depth threshold)
        with observed sensor point data to calculate accuracy and recall metrics. TP/FN are
        determined against the low-threshold simulation; FP/TN against the high-threshold one.

        Examples:
            evaluate_sensor2 --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp
            evaluate_sensor2 --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp --buffer 50 --thresh-iot 20
            evaluate_sensor2 --sim-low SHP/SIM_thrd125.shp --sim-high SHP/SIM_thrd475.shp --obs SHP/OBS_SENSOR.shp --output results.csv
    """,
    'sensor':
    """
        Extract time series data at observation points from Delft3D FM NetCDF files.

        This tool extracts water depth or other parameters at specified observation points
        from Delft3D FM NetCDF output files and exports the results to CSV and/or Excel.

        Examples:
            sensor --nc-file results.nc --obs-shp observation_points.shp
            sensor --nc-file results.nc --obs-shp points.shp --output-csv depth.csv
            sensor --nc-file results.nc --obs-shp points.shp --plot --verbose
    """,
    'ncrain':
    """
        Generate a NetCDF file from rainfall data and thiessen polygon shapefiles.

        This tool processes rainfall data in CSV format along with thiessen polygon
        shapefiles to create NetCDF files for use in Delft3D modeling.

        Note: Currently only works for Taiwan data in EPSG:3826 projection.

        Examples:
            ncrain                      # Process all CSV files in the input folder
            ncrain --single rainfall.csv  # Process only a specific CSV file
            ncrain --verbose            # Display additional processing information
            ncrain --no-clean           # Keep intermediate files
    """,
    'snorain':
    """
        Process rainfall scenario data and generate time series CSV files.

        This tool processes rainfall scenario data from CSV files and generates
        time series files for use in modeling.

        Examples:
            snorain -i rainfall_scenarios.csv -of custom/TAB
            snorain --input rainfall_scenarios.csv --output-folder custom/TAB --verbose
    """,
    'shp2ldb':
    """
        Convert boundary line shapefiles to LDB files for Delft3D.

        This tool converts boundary line shapefiles to the LDB format used in
        Delft3D modeling.

        Examples:
            shp2ldb
            shp2ldb -i custom/SHP_LDB -of custom/LDB
            shp2ldb --id_field BoundaryName
    """,
    'shpbc2pli':
    """
        Convert boundary line shapefiles to PLI files for Delft3D.

        This tool converts boundary line shapefiles to the PLI format used in
        Delft3D modeling.

        Examples:
            shpbc2pli
            shpbc2pli -i custom/SHP_BC -of custom/PLI_BC
            shpbc2pli --id_field BoundaryName
    """,
    'shp2pli':
    """
        Alias for shpbc2pli. Convert boundary line shapefiles to PLI files.

        Examples:
            shp2pli
            shp2pli -i custom/SHP_BC -of custom/PLI_BC
    """,
    'shpblock2pol':
    """
        Convert shapefile blocks to POL files for Delft3D.

        This tool converts polygon shapefiles to the POL format used in
        Delft3D modeling.

        Examples:
            shpblock2pol
            shpblock2pol -i custom/SHP_BLOCK -of custom/POL_BLOCK
    """,
    'shp2pol':
    """
        Alias for shpblock2pol. Convert shapefile blocks to POL files.

        Examples:
            shp2pol
            shp2pol -i custom/SHP_BLOCK -of custom/POL_BLOCK
    """,
    'shpdike2pliz':
    """
        Convert bankline shapefiles to PLIZ files for Delft3D.

        This tool converts bankline/dike shapefiles to the PLIZ format used in
        Delft3D modeling.

        Examples:
            shpdike2pliz
            shpdike2pliz -i custom/SHP_DIKE -of custom/PLIZ_DIKE
            shpdike2pliz --id_field DikeName
    """,
    'shp2pliz':
    """
        Alias for shpdike2pliz. Convert bankline shapefiles to PLIZ files.

        Examples:
            shp2pliz
            shp2pliz -i custom/SHP_DIKE -of custom/PLIZ_DIKE
    """,
    'shp2xyz':
    """
        Convert point shapefiles to XYZ files.

        This tool converts point shapefiles to XYZ files for use in Delft3D modeling.

        Examples:
            shp2xyz
            shp2xyz -i custom/SHP_SAMPLE -of custom/XYZ_SAMPLE
            shp2xyz --z_field ELEVATION
    """,
    'getfacez':
    """
        Extract Mesh2d_face_z values from Delft3D FM NetCDF files at observation points (faster).

        Drop-in, spatial-index accelerated replacement for getfacez. Uses an STRtree for
        point-in-polygon matching and a cKDTree for nearest-neighbor matching instead of
        scanning every mesh face for every observation point, which speeds up processing
        on large meshes. Same output format as getfacez2.

        The NetCDF file can be given directly with --nc-file, or resolved from a D-Flow FM
        project with -p/--project (the NetFile entry of the project's MDU is used). If
        neither is given, a single .dsproj in the current directory is used.

        Examples:
            getfacez --obs-shp points.shp
            getfacez -p MyProject.dsproj --obs-shp points.shp
            getfacez --nc-file results.nc --obs-shp observation_points.shp
            getfacez --nc-file results.nc --obs-shp points.shp --output-csv bathymetry.csv
            getfacez --nc-file results.nc --obs-shp points.shp --output-excel bathymetry.xlsx --verbose
    """,
    'getfacez2':
    """
        Extract Mesh2d_face_z values from Delft3D FM NetCDF files at observation points.

        This tool extracts bed level/bathymetry values (Mesh2d_face_z) at specified observation
        points from Delft3D FM NetCDF output files and exports the results to CSV and/or Excel.

        Examples:
            getfacez2 --nc-file results.nc --obs-shp observation_points.shp
            getfacez2 --nc-file results.nc --obs-shp points.shp --output-csv bathymetry.csv
            getfacez2 --nc-file results.nc --obs-shp points.shp --output-excel bathymetry.xlsx --verbose
    """,
    'fou2shp':
    """
        Reconstruct FlowFM 2D mesh faces as polygons in threshold shapefiles from FOU NetCDF output.

        This tool reads a Delft3D FM fourier/statistics NetCDF file, extracts 2D mesh face
        polygons with depth values, and writes multiple threshold-based shapefiles (e.g.,
        SIM_thrd125, SIM_thrd300, SIM_thrd475) to an output directory.

        Examples:
            fou2shp
            fou2shp --input NC/FlowFM_fou.nc -of SHP
            fou2shp --input NC/FlowFM_fou.nc --var Mesh2d_fourier002_max_depth --output-folder output
    """,
    'pliz2shp':
    """
        Convert a Delft3D/D-Flow FM .pliz file (weir/dike polyline with Z) to a 3D ESRI Shapefile.

        Reads one .pliz file or a folder of them and writes a PolylineZ shapefile per
        input file, with length, Z range, and any extra attribute columns summarized.

        Examples:
            pliz2shp -i Dike001.pliz
            pliz2shp -i Dike001.pliz -of output --crs EPSG:4326
            pliz2shp -if PLIZ_DIR -of SHP_DIR
    """,
    'pli2shp':
    """
        Convert a Delft3D polyline file (.pli or .ldb) to an ESRI Shapefile.

        Reads one .pli/.ldb file or a folder of them and writes a line shapefile per
        input file.

        Examples:
            pli2shp -i boundary.pli
            pli2shp -i LDB_001.ldb -of output --crs EPSG:4326
            pli2shp -if PLI_DIR -of SHP_DIR
    """,
    'pol2shp':
    """
        Convert a Delft3D/D-Flow FM .pol file to a polygon ESRI Shapefile.

        Reads one .pol file or a folder of them and writes a polygon shapefile per
        input file, closing unclosed rings and summarizing any extra attribute columns.

        Examples:
            pol2shp -i POL_001.pol
            pol2shp -i POL_001.pol -of output --crs EPSG:4326
            pol2shp -if POL_DIR -of SHP_DIR
    """,
    'xyz2shp':
    """
        Convert an XYZ point file (.xyz or .csv) to an ESRI Shapefile.

        Reads one .xyz/.csv file or a folder of them and writes a point shapefile per
        input file (3D x,y,z points by default; use -d 2 for 2D points).

        Examples:
            xyz2shp -i XYZ_001.xyz
            xyz2shp -i XYZ_001.csv -of output --crs EPSG:4326
            xyz2shp -i XYZ_001.xyz -d 2
            xyz2shp -if XYZ_DIR -of SHP_DIR
    """,
    'rmgrid':
    """
        Remove (clear) the 2D computational mesh and 1D2D links from a D-Flow FM .dsproj project.

        This tool empties the 2D mesh in the project's NetCDF net file while preserving the
        1D network (pipes/branches). It also strips any 2D-specific sections from the
        IniFieldFile. The original net file is backed up as <name>.nc.bak so the change can
        be reverted with --restore.

        Examples:
            rmgrid
            rmgrid -i MyProject.dsproj
            rmgrid -i MyProject.dsproj --restore
            rmgrid -i MyProject.dsproj --force-backup
    """,
    'rsgrid':
    """
        Restore the 2D computational mesh and/or 2D spatial fields into a D-Flow FM
        .dsproj project.

        -s restores the 2D mesh (including Mesh2d_face_z bed levels) into a target
        project's NetCDF net file by cloning it from a source project's net file,
        while preserving the target's existing 1D network.

        -f restores the 2D spatial fields (infiltration capacity, roughness) that
        are lost along with the mesh: it copies the *.xyz sample files (and any
        initialFields.ini / roughness *.ini) from a fields directory into the
        model's input folder and re-registers them in the MDU (IniFieldFile,
        FrictFile, Infiltrationmodel). The iniField file is created if the project
        has none, or updated in place (only the dataFile entries) if it has one.

        Examples:
            rsgrid -s Intact.dsproj
            rsgrid -i Stripped.dsproj -s Intact.dsproj
            rsgrid -s source_net.nc
            rsgrid -i target_net.nc -s source_net.nc
            rsgrid -f                               # restore fields from cwd
            rsgrid -i Target.dsproj -f -d fields/    # take the .xyz files from fields/
            rsgrid -i Target.dsproj -s Intact.dsproj -f   # mesh first, then fields
            rsgrid -f -q frictioncoefficient=rough2024.xyz
    """,
    'rmgriddimr':
    """
        Remove (clear) the 2D mesh and 1D2D links from a DIMR run folder (dimr.xml + dflowfm/).

        The DIMR-folder counterpart of rmgrid: identical processing, but the model is
        located through the DIMR export layout instead of a .dsproj project. The target
        may be a run folder, a dimr.xml, a dflowfm folder or an .mdu file; the current
        directory is used when -i is omitted.

        The net file is backed up as <name>.nc.bak and the IniFieldFile as
        <name>.ini.bak, so --restore brings back both the 2D mesh and the 2D
        roughness / infiltration blocks.

        Examples:
            rmgriddimr                              # run folder = current directory
            rmgriddimr -i C:/models/PT01
            rmgriddimr -i C:/models/PT01/dimr.xml
            rmgriddimr -i C:/models/PT01/dflowfm
            rmgriddimr -i C:/models/PT01 --restore
            rmgriddimr -i C:/models/PT01 --force-backup
    """,
    'rsgriddimr':
    """
        Restore the 2D mesh and/or 2D spatial fields into a DIMR run folder (dimr.xml + dflowfm/).

        The DIMR-folder counterpart of rsgrid, and the inverse of rmgriddimr.

        -s clones the 2D mesh (including Mesh2d_face_z bed levels) from a source model
        into the target's net file, preserving the target's own 1D network. The source
        may be a run folder, a dimr.xml, a dflowfm folder, an .mdu or a .nc net file.

        -f restores the 2D spatial fields that are lost with the mesh: it copies the
        coverage files (*.xyz samples, *.tif GeoTIFFs) and any roughness *.ini from a
        fields directory into the model's dflowfm folder and re-registers them in the
        MDU (IniFieldFile, FrictFile, Infiltrationmodel). Each coverage is checked
        against the restored mesh extent. An existing iniField file is updated in place
        (only the dataFile entries) so the D-HYDRO interpolation settings survive; one
        is created when the model has none.

        Examples:
            rsgriddimr -s C:/models/Intact          # restore mesh into cwd's model
            rsgriddimr -i C:/models/PT01 -s C:/models/Intact
            rsgriddimr -i C:/models/PT01 -s source_net.nc
            rsgriddimr -i C:/models/PT01 -f -d fields/
            rsgriddimr -i C:/models/PT01 -s Intact -f    # mesh first, then the fields
            rsgriddimr -f -q frictioncoefficient=RHI.tif
    """,
    'makedimr':
    """
        Build a DIMR run folder (dimr_config.xml + dflowfm/) from a Delft3D FM Suite project (.dsproj).

        Reads the FM model name and its data folder straight from the .dsproj file (a SQLite
        database), copies <project>.dsproj_data/<FM model>/input into <out>/dflowfm, and writes
        a dimr_config.xml pointing at it. The counterpart of rmgriddimr/rsgriddimr, which operate
        on a DIMR run folder once it exists -- makedimr is what creates it in the first place.

        Examples:
            makedimr 2DOF_KS.dsproj
            makedimr 2DOF_KS.dsproj --out DIMR --threads 1 --force
            makedimr 2DOF_KS.dsproj --model FlowFM1
    """,
    'rm1dch':
    """
        Remove the open 1D channels from a Delft3D FM (D-HYDRO / FM Suite) model.

        Removes the 1D channels and everything anchored on them (structures, cross
        sections, 1D2D links, boundary/lateral blocks, forcing records), keeping the
        sewer system (pipes, sewer connections, manholes) and the 2D grid intact. Where
        a kept sewer branch ran into a removed channel, a manhole is added automatically
        so the sewer keeps a proper outfall (--no-outfall-manholes to disable). Every
        rewritten file is first backed up to <name>.bak. Same engine as rm1dsw/mk2d;
        --target switches what is removed.

        Examples:
            rm1dch <input-folder-or-mdu> --check
            rm1dch <input-folder-or-mdu> --dry-run
            rm1dch <input-folder-or-mdu>
            rm1dch <input-folder-or-mdu> --no-outfall-manholes
    """,
    'rm1dsw':
    """
        Remove the 1D sewer system from a Delft3D FM (D-HYDRO / FM Suite) model.

        Removes the sewer system (pipes, sewer connections, manholes) and everything
        anchored on it, keeping the 1D channels and the 2D grid intact. Same engine as
        rm1dch, opposite default direction; --target switches what is removed.

        Examples:
            rm1dsw <input-folder-or-mdu> --check
            rm1dsw <input-folder-or-mdu> --dry-run
            rm1dsw <input-folder-or-mdu>
            rm1dsw <input-folder-or-mdu> --target channel
    """,
    'mk2d':
    """
        Turn a Delft3D FM (D-HYDRO / FM Suite) 1D2D model into a 2D-only model.

        Removes the entire 1D network - channels, sewers, manholes and every 1D
        structure - together with the 1D-only .mdu entries, leaving the 2D grid, fixed
        weirs, 2D boundaries, laterals, meteo and 2D fields untouched. Refuses to run
        when the net file has no 2D grid (--allow-empty-2d to continue anyway). Same
        engine as rm1dch/rm1dsw, with --target all.

        Examples:
            mk2d <input-folder-or-mdu> --check
            mk2d <input-folder-or-mdu> --dry-run
            mk2d <input-folder-or-mdu>
            mk2d <input-folder-or-mdu> --keep-1d-mdu-keys
    """,
    'rmlinks':
    """
        Remove only the 1D2D links from a Delft3D FM (D-HYDRO / FM Suite) net file.

        The 1D network, mesh1d, Mesh2d and every other input file are left untouched;
        only the mesh-contact block of the net file is removed. --type restricts removal
        to specific link kinds (lateral, longitudinal, street_inlet, roof_gutter,
        embedded); default is every link. If the .mdu has a non-empty 1D2DLinkFile key
        and every link is removed, that key is blanked too (--keep-linkfile to disable).

        Examples:
            rmlinks <input-folder | model.mdu | *_net.nc> --check
            rmlinks <input-folder | model.mdu | *_net.nc> --dry-run
            rmlinks <input-folder | model.mdu | *_net.nc>
            rmlinks <input-folder> --type street_inlet roof_gutter
    """,
    'orthochk':
    """
        Locate non-orthogonal / problematic 2D cells in a D-Flow FM net file and export
        them as a polygon shapefile.

        Orthogonality per internal edge is |cos| of the angle between the net link and
        the flow link joining the two cell circumcentres (as in RGFGRID / D-Flow FM):
        < 0.02 good, 0.02-0.1 acceptable, > 0.1 poor. Also flags the defects behind
        "network is not orthogonal": coincident circumcentres (ZERO_LINK), flow link
        missing the edge (SAMESIDE), circumcentre outside the cell (CC_OUT), non-convex
        or zero-area cells (NONCONVX), and edges shared by more than 2 cells (OVERLAP).
        Accepts a model input folder, a .mdu or the *_net.nc itself. Output defaults to
        ./<netfile>_orthochk/<netfile>_nonortho_cells.shp (a folder in the
        current directory); --edges adds a polyline shapefile of bad edges.

        Examples:
            orthochk <input-folder | model.mdu | *_net.nc>
            orthochk FlowFM_net.nc -t 0.05 -o bad_cells.shp --edges
            orthochk FlowFM_net.nc --check
            orthochk FlowFM_net.nc --all
    """,
    'expgrid':
    """
        Export the 2D grid (Mesh2d) of a D-Flow FM net file to a new 2D-only net file.

        Copies the Mesh2d variables (nodes, edges, faces, ...), the coordinate-system
        variable and the global attributes; drops the 1D network, mesh1d, the 1D2D
        links and the composite mesh. The cell bed levels (Mesh2d_face_z) are cleared
        unless -z/--face-z is given. The input file is never
        modified. Accepts a model input folder, a .mdu or the *_net.nc itself. Output
        defaults to ./<netfile>_2d.nc in the current directory
        (FlowFM_net.nc -> FlowFM_net_2d.nc); an existing output needs -f.

        Examples:
            expgrid <input-folder | model.mdu | *_net.nc>
            expgrid FlowFM_net.nc -o grid.nc
            expgrid FlowFM_net.nc --face-z
            expgrid FlowFM_net.nc --check
    """,
    'alignncrain':
    """
        Align the simulation period of a D-Flow FM model with a NetCDF rainfall file.

        Sets RefDate, TStart and TStop in the [time] section of the .mdu (and
        StartDateTime / StopDateTime when they are filled in) so the run covers the
        rainfall from its first time stamp to its last one plus --pad-end seconds
        (default: one rainfall time step). TStart/TStop are written in the mdu's Tunit.
        Also points the rainfall [Meteo] block of ExtForceFileNew to the NetCDF file
        (quantity, forcingFile, forcingFileType=netcdf), appending one if missing; the
        quantity is 'rainfall' for depth units (mm) and 'rainfall_rate' for rate units
        (mm/day). Changed files are backed up as <file>.bak.

        Examples:
            alignncrain <input-folder | model.mdu> rain.nc
            alignncrain FlowFM.mdu rain.nc --pad-end 3600
            alignncrain FlowFM.mdu rain.nc --no-ext
            alignncrain FlowFM.mdu rain.nc --check
    """,
    'otstep':
    """
        Show or change the output time step of the his file and the map file.

        Reads / writes HisInterval and MapInterval in the [output] section of the
        .mdu (seconds). Without --his / --map it prints the current intervals, the
        simulation length and the number of output steps. Steps can be given in
        seconds or with a unit (s, m, h, d); 0 switches the output off. Only the
        interval is replaced - an output start / stop after it is kept. The .mdu
        is backed up as <file>.bak.

        Examples:
            otstep <input-folder | model.mdu>
            otstep FlowFM.mdu --his 60 --map 3600
            otstep FlowFM.mdu --his 1m --map 1h
            otstep FlowFM.mdu --map 30m --check
    """,
}


def _build_tool_listing():
    """Build the formatted listing of all available tools."""
    lines = [
        "D3DTOOLS - A collection of tools for working with shapefiles and converting them for Delft3D modeling.",
        "",
        "Available tools:",
        "",
    ]
    for tool, description in TOOL_DESCRIPTIONS.items():
        short_desc = description.strip().split('\n')[0]
        lines.append(f"  {tool:<18} - {short_desc}")
    lines.append("")
    lines.append("Use 'd3dtools <tool_name>' to get detailed information about a specific tool.")
    lines.append("Use '<tool_name> --help' to see command-line options for each tool.")
    return "\n".join(lines)


def main():
    """Main function to display tool descriptions."""
    parser = argparse.ArgumentParser(
        prog='d3dtools',
        description='Display descriptions of d3dtools functionality.',
        epilog=_build_tool_listing(),
        formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument('tool',
                        nargs='?',
                        choices=[*TOOL_DESCRIPTIONS.keys(), 'all'],
                        default='all',
                        help='The specific tool to describe (default: all)')

    parser.add_argument('-v', '--version',
                        action='store_true',
                        help='Show the d3dtools version')

    parser.add_argument('--pypi',
                        action='store_true',
                        help='Open the d3dtools PyPI project page in a web browser')

    args = parser.parse_args()

    if args.version:
        from . import __version__
        print(f"d3dtools version {__version__}")
        return

    if args.pypi:
        print("Opening d3dtools PyPI project page in your web browser...")
        webbrowser.open("https://pypi.org/project/d3dtools/")
        return

    if args.tool == 'all':
        print(_build_tool_listing())
    else:
        print(dedent(TOOL_DESCRIPTIONS[args.tool]).strip())
        print(f"\nUse '{args.tool} --help' to see all command-line options.\n")


if __name__ == "__main__":
    main()
