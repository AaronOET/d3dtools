"""
Convert boundary line shapefile to *.pli file

This module is an alias for shpbc2pli.py, providing the same functionality
under an alternative command name.
"""
from .shpbc2pli import convert, main

if __name__ == "__main__":
    main()