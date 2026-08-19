"""Centralized runtime configuration for the Locus service.
Reads environment variables once into a cached Settings object so the
rest of the codebase never calls os.environ directly. Includes the
Kenya bounding box constants used by domain/validation.py for the
cheap early-reject check described in the design document (Section 5).
"""

