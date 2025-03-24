"""
Convert dike/bankline shapefile to *.pli and *.pliz files

This module is an alias for shpdike2pliz.py, providing the same functionality
under an alternative command name.
"""
from .shpdike2pliz import convert, main

if __name__ == "__main__":
    main()