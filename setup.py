from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
  long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as f:
  requirements = f.read().splitlines()

setup(
    name="d3dtools",
    version="0.22.3",
    author="aaronchh",
    author_email="aaronhsu219@gmail.com",  # Please update this with your email
    description=
    "A collection of tools for working with shapefiles and converting them for Delft3D modeling",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url=
    "https://github.com/AaronOET/d3dtools",  # Update with your GitHub repo URL
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
    install_requires=requirements,    entry_points={
        "console_scripts": [
            "d3dtools=d3dtools.describe:main",  # Display package descriptions (use `d3dtools -h`)
            "d3dtools-info=d3dtools.describe:main",  # Alias for d3dtools
            "ncrain=d3dtools.ncrain:main",
            "snorain=d3dtools.snorain:main",
            "shpbc2pli=d3dtools.shpbc2pli:main",
            "shp2pli=d3dtools.shpbc2pli:main",  # Alias for shpbc2pli
            "shpblock2pol=d3dtools.shpblock2pol:main",
            "shp2pol=d3dtools.shpblock2pol:main",  # Alias for shpblock2pol
            "shpdike2pliz=d3dtools.shpdike2pliz:main",
            "shp2pliz=d3dtools.shpdike2pliz:main",  # Alias for shpdike2pliz
            "shp2ldb=d3dtools.shp2ldb:main",
            "shp2xyz=d3dtools.shp2xyz:main",
            "sensor=d3dtools.sensor:main",  # Tool for extracting time series data from NetCDF files
            "evaluate=d3dtools.evaluate:main",  # Tool for flood accuracy metrics
            "evaluate_sensor=d3dtools.evaluate_sensor:main",  # Tool for evaluating sensor data
            "evaluate_sensor2=d3dtools.evaluate_sensor2:main",  # Tool for evaluating sensor data with dual-threshold shapefiles
            "eval_iot=d3dtools.evaluate_sensor2:main",  # Alias for evaluate_sensor2
            "getfacez=d3dtools.getfacez:main",  # Tool for extracting Mesh2d_face_z values from NetCDF files
            "getfacez2=d3dtools.getfacez2:main",  # Faster, spatial-index accelerated version of getfacez
            "fou2shp=d3dtools.fou2shp:main",  # Tool for converting Delft3D FOU output to shapefiles
            "pliz2shp=d3dtools.pliz2shp:main",  # Tool for converting Delft3D PLIZ files to shapefiles
            "rmgrid=d3dtools.rmgrid:main",  # Tool for removing the 2D mesh from a D-Flow FM .dsproj project
            "transzone1=d3dtools.transzone1:main",  # Tool for building a transition zone from triangle mesh faces
            "transzone2=d3dtools.transzone2:main",  # Tool for building the transition zone core from trans_zone_faces
        ],
    },
)
