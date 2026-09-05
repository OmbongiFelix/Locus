"""
Orchestrates a single reverse-geocode request end to end.

Sequence (mirrors the request-flow diagram at the top of this
document):

1. validate_coordinates() - domain/validation.py
2. cache.get()            - infrastructure/cache.py
3. repository.find_exact()   - ST_Contains lookup
4. repository.find_nearest() if (3) misses - ST_DWithin fallback,
   tolerance from settings.FALLBACK_RADIUS_M
5. assemble a GeocodeResult, tagging match_type
6. cache.set()
7. return to the caller (api/routes.py)

This is the only place that sequence is allowed to live - routes.py
calls this and nothing else for the actual geocoding logic.
"""

from __future__ import annotations

from locus.config import get_settings
from locus.domain.validation import validate_coordinates, InvalidCoordinatesError
from locus.domain.schemas import GeocodeResult, MatchType


class NoMatchError(Exception):
    """Raised when neither exact nor fallback query finds a boundary."""


class GeocodeService:
    """Orchestrates the full reverse-geocode sequence.

    Takes its repository and cache as constructor arguments
    (dependency injection via api/dependencies.py) specifically so
    tests can pass in mocks.  Never import a concrete repository
    class directly inside this file.
    """

    def __init__(self, repository, cache) -> None:
        self._repository = repository
        self._cache = cache

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _assemble(row: dict, match_type: MatchType) -> GeocodeResult:
        """Build a GeocodeResult from a repository row dict."""
        return GeocodeResult(
            county_name=row["county_name"],
            constituency=row["constituency"],
            ward_name=row["ward_name"],
            location_name=row.get("location_name"),
            sub_location=row.get("sub_location"),
            match_type=match_type,
            distance_m=row.get("distance_m", 0.0),
            boundary_version=row["boundary_version"],
        )

    # ── public API ───────────────────────────────────────────────

    async def reverse_geocode(self, lat: float, lon: float) -> GeocodeResult:
        """Execute the 7-step reverse-geocode sequence for a single point."""
        # Step 1 — validate
        validate_coordinates(lat, lon)

        # Step 2 — cache lookup
        cached = await self._cache.get(lat, lon)
        if cached is not None:
            return cached

        settings = get_settings()

        # Step 3 — exact query (ST_Contains)
        row = await self._repository.find_exact(lat, lon)

        if row is not None:
            result = self._assemble(row, MatchType.EXACT)
        else:
            # Step 4 — fallback query (ST_DWithin)
            row = await self._repository.find_nearest(
                lat, lon, settings.FALLBACK_RADIUS_M
            )
            if row is None:
                raise NoMatchError(
                    f"No boundary found for ({lat}, {lon}) within "
                    f"{settings.FALLBACK_RADIUS_M} m fallback radius."
                )
            result = self._assemble(row, MatchType.NEAREST)

        # Step 6 — cache write
        await self._cache.set(lat, lon, result)

        # Step 7 — return
        return result

    async def reverse_geocode_batch(
        self, points: list[tuple[float, float]]
    ) -> list[GeocodeResult]:
        """Batch reverse-geocode — one batched repository call, not N
        sequential calls.

        Falls back to ``find_nearest`` individually only for points
        that had no exact match.
        """
        # Validate all coordinates up-front
        for lat, lon in points:
            validate_coordinates(lat, lon)

        settings = get_settings()

        # Check cache for each point
        results: list[GeocodeResult | None] = [None] * len(points)
        uncached_indices: list[int] = []
        uncached_points: list[tuple[float, float]] = []

        for i, (lat, lon) in enumerate(points):
            cached = await self._cache.get(lat, lon)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_points.append((lat, lon))

        if not uncached_points:
            return results  # type: ignore[return-value]

        # Single batched repository call for all uncached points
        batch_rows = await self._repository.find_exact_batch(uncached_points)

        for j, idx in enumerate(uncached_indices):
            lat, lon = uncached_points[j]
            row = batch_rows[j]

            if row is not None:
                result = self._assemble(row, MatchType.EXACT)
            else:
                # Individual fallback for misses
                fallback = await self._repository.find_nearest(
                    lat, lon, settings.FALLBACK_RADIUS_M
                )
                if fallback is None:
                    raise NoMatchError(
                        f"No boundary found for ({lat}, {lon}) within "
                        f"{settings.FALLBACK_RADIUS_M} m fallback radius."
                    )
                result = self._assemble(fallback, MatchType.NEAREST)

            results[idx] = result
            await self._cache.set(lat, lon, result)

        return results  # type: ignore[return-value]
