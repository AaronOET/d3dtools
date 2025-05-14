from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
  long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as f:
  requirements = f.read().splitlines()

setup(
    name="d3dtools",
    version="0.12.1",
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
            "ncrain=d3dtools.ncrain:main",
            "shpbc2pli=d3dtools.shpbc2pli:main",
            "shp2pli=d3dtools.shpbc2pli:main",  # Alias for shpbc2pli
            "shpblock2pol=d3dtools.shpblock2pol:main",
            "shp2pol=d3dtools.shpblock2pol:main",  # Alias for shpblock2pol
            "shpdike2pliz=d3dtools.shpdike2pliz:main",
            "shp2pliz=d3dtools.shpdike2pliz:main",  # Alias for shpdike2pliz
            "shp2ldb=d3dtools.shp2ldb:main",
            "shp2xyz=d3dtools.shp2xyz:main",
            "snorain=d3dtools.snorain:main",
            "evaluate=d3dtools.evaluate:main",  # Tool for flood accuracy metrics
            "d3dtools-info=d3dtools.describe:main",  # New tool to display package descriptions
            "sensor=d3dtools.sensor:main",  # Tool for extracting time series data from NetCDF files
        ],
    },
)
