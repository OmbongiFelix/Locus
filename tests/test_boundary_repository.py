"""
Integration tests for BoundaryRepository against a real PostGIS
instance.
Uses a small, fixed set of seeded test polygons (not the full
national dataset) so tests are fast and deterministic. Covers the
edge cases from the design document (Section 6): islands, water
bodies, and boundary-line points.
"""

