"""
Convert block shapefile to *.pol file

This module is an alias for shpblock2pol.py, providing the same functionality
under an alternative command name.
"""
from .shpblock2pol import convert, main

if __name__ == "__main__":
    main()