"""
Spatial query layer over the kenya_admin_boundaries table.
Implements the two queries defined in the design document (Section 4):
find_exact(lat, lon) - ST_Contains against is_current boundaries
find_nearest(lat, lon, radius_m) - ST_DWithin fallback, ordered by
ST_Distance, used only when
find_exact() returns nothing
This is the only file in the codebase that contains raw spatial SQL.
"""

