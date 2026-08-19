"""
Input validation for incoming coordinates.
Implements the cheap early-reject check from the design document
(Section 5): coordinates outside Kenya's approximate bounding box are
rejected before a single query reaches PostGIS. Also catches the
common client bug of swapped lat/lon parameters.
"""
