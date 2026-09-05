"""
Input validation for incoming coordinates.

Implements the cheap early-reject check from the design document
(Section 5): coordinates outside Kenya's approximate bounding box are
rejected before a single query reaches PostGIS. Also catches the
common client bug of swapped lat/lon parameters.
"""

from locus.config import get_settings, KENYA_BBOX


class InvalidCoordinatesError(ValueError):
    """Raised when coordinates are outside Kenya's bounding box or obviously wrong."""


def validate_coordinates(lat: float, lon: float) -> None:
    """Validate that (lat, lon) falls within Kenya's approximate bounding box.

    Raises InvalidCoordinatesError if:
    - lat or lon is outside the Kenya bounding box.
    - lat and lon appear to be swapped (a common client bug where a
      plausible lat value ends up in the lon slot and vice versa).

    This is a fast filter, not a correctness guarantee — a coordinate
    can pass this check and still yield no match (e.g., a point inside
    Lake Victoria).
    """
    bbox = KENYA_BBOX

    lat_in = bbox.lat_min <= lat <= bbox.lat_max
    lon_in = bbox.lon_min <= lon <= bbox.lon_max

    if lat_in and lon_in:
        return  # fast-path: valid

    # Detect the swapped-parameter case: if swapping would put both
    # values inside the box, the caller almost certainly mixed them up.
    swapped_lat_ok = bbox.lat_min <= lon <= bbox.lat_max
    swapped_lon_ok = bbox.lon_min <= lat <= bbox.lon_max

    if swapped_lat_ok and swapped_lon_ok:
        raise InvalidCoordinatesError(
            f"Coordinates (lat={lat}, lon={lon}) appear to be swapped. "
            f"Did you mean lat={lon}, lon={lat}?"
        )

    raise InvalidCoordinatesError(
        f"Coordinates (lat={lat}, lon={lon}) are outside Kenya's "
        f"bounding box ({bbox.lat_min}–{bbox.lat_max}°N, "
        f"{bbox.lon_min}–{bbox.lon_max}°E)."
    )


def validate_batch_size(points: list) -> None:
    """Reject a batch request whose size exceeds settings.MAX_BATCH_SIZE."""
    settings = get_settings()
    if len(points) > settings.MAX_BATCH_SIZE:
        raise ValueError(
            f"Batch size {len(points)} exceeds the maximum of "
            f"{settings.MAX_BATCH_SIZE}."
        )
