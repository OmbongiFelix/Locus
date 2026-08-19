"""
Redis read-through cache for geocode results.
Implements the caching strategy from the design document (Section 7):
cache keyed by coordinates rounded to 6 decimal places, with the
entire cache invalidated on every boundary_version publish rather
than per-key invalidation.
"""
