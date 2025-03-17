from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as f:
    requirements = f.read().splitlines()

setup(
    name="d3dtools",
    version="0.4.0",
    author="aaronchh",
    author_email="aaronhsu219@gmail.com",  # Please update this with your email
    description="A collection of tools for working with shapefiles and converting them for Delft3D modeling",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/AaronOET/d3dtools",  # Update with your GitHub repo URL
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "ncrain=d3dtools.ncrain:generate",
            "shpbc2pli=d3dtools.shpbc2pli:convert",
            "shpblock2pol=d3dtools.shpblock2pol:convert",
            "shpdike2pliz=d3dtools.shpdike2pliz:convert",
        ],
    },
)
