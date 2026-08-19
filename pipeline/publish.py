"""
Blue-green publish: promotes a validated staging table to production.
Implements the publish sequence from the design document (Section 8):
within a single transaction, set effective_to on the currently active
rows, insert the staged rows as is_current, and flip the version -
never TRUNCATE the live table in place.
"""

