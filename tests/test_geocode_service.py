"""
Unit tests for GeocodeService.

Mocks BoundaryRepository and GeocodeCache - no real database or Redis
connection. Tests orchestration logic only: validation -> cache ->
exact query -> fallback -> response assembly.
"""

import pytest
from unittest.mock import AsyncMock

from locus.domain.geocode_service import GeocodeService, NoMatchError
from locus.domain.validation import InvalidCoordinatesError
from locus.domain.schemas import GeocodeResult, MatchType


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_repo():
    """AsyncMock repository — all methods return None/empty by default."""
    repo = AsyncMock()
    repo.find_exact = AsyncMock(return_value=None)
    repo.find_nearest = AsyncMock(return_value=None)
    repo.find_exact_batch = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_cache():
    """AsyncMock cache — get returns None (miss) by default."""
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    return cache


@pytest.fixture
def service(mock_repo, mock_cache):
    """A GeocodeService wired with mock repository and cache."""
    return GeocodeService(repository=mock_repo, cache=mock_cache)


@pytest.fixture
def nairobi_row():
    """Row dict as returned by the repository for central Nairobi."""
    return {
        "county_name": "Nairobi",
        "constituency": "Starehe",
        "ward_name": "Nairobi Central",
        "location_name": "Nairobi Central",
        "sub_location": "KICC",
        "boundary_version": "iebc-2024",
        "distance_m": 0.0,
    }


@pytest.fixture
def nairobi_result():
    """Expected GeocodeResult for an exact Nairobi hit."""
    return GeocodeResult(
        county_name="Nairobi",
        constituency="Starehe",
        ward_name="Nairobi Central",
        location_name="Nairobi Central",
        sub_location="KICC",
        match_type=MatchType.EXACT,
        distance_m=0.0,
        boundary_version="iebc-2024",
    )


# ── Test classes ─────────────────────────────────────────────────────


class TestReverseGeocodeCacheHit:
    """Cache hit short-circuits before the repository is called."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_result(
        self, service, mock_repo, mock_cache, nairobi_result
    ):
        mock_cache.get.return_value = nairobi_result

        result = await service.reverse_geocode(-1.2864, 36.8172)

        assert result == nairobi_result
        mock_cache.get.assert_awaited_once_with(-1.2864, 36.8172)
        mock_repo.find_exact.assert_not_awaited()
        mock_repo.find_nearest.assert_not_awaited()


class TestReverseGeocodeExactMatch:
    """Exact repository match → match_type=EXACT, distance_m=0."""

    @pytest.mark.asyncio
    async def test_exact_match(
        self, service, mock_repo, mock_cache, nairobi_row
    ):
        mock_repo.find_exact.return_value = nairobi_row

        result = await service.reverse_geocode(-1.2864, 36.8172)

        assert result.match_type == MatchType.EXACT
        assert result.distance_m == 0.0
        assert result.county_name == "Nairobi"
        mock_cache.set.assert_awaited_once()


class TestReverseGeocodeFallback:
    """Miss on find_exact → triggers find_nearest, match_type=NEAREST."""

    @pytest.mark.asyncio
    async def test_fallback_to_nearest(
        self, service, mock_repo, mock_cache, nairobi_row
    ):
        mock_repo.find_exact.return_value = None
        nearest_row = {**nairobi_row, "distance_m": 150.5}
        mock_repo.find_nearest.return_value = nearest_row

        result = await service.reverse_geocode(-1.2864, 36.8172)

        assert result.match_type == MatchType.NEAREST
        assert result.distance_m == 150.5
        mock_repo.find_nearest.assert_awaited_once()


class TestReverseGeocodeNoMatch:
    """Miss on both exact and fallback → raises NoMatchError."""

    @pytest.mark.asyncio
    async def test_no_match_raises(self, service, mock_repo):
        mock_repo.find_exact.return_value = None
        mock_repo.find_nearest.return_value = None

        with pytest.raises(NoMatchError):
            await service.reverse_geocode(-1.2864, 36.8172)


class TestInvalidCoordinates:
    """Invalid coordinates raise InvalidCoordinatesError before repo call."""

    @pytest.mark.asyncio
    async def test_out_of_range(self, service, mock_repo):
        with pytest.raises(InvalidCoordinatesError):
            await service.reverse_geocode(90.0, 0.0)

        mock_repo.find_exact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swapped_coordinates(self, service, mock_repo):
        """lon value in lat slot and vice versa."""
        with pytest.raises(InvalidCoordinatesError, match="swapped"):
            await service.reverse_geocode(36.8172, -1.2864)

        mock_repo.find_exact.assert_not_awaited()


class TestBatchGeocoding:
    """reverse_geocode_batch calls the batched repo method once, not
    once per point."""

    @pytest.mark.asyncio
    async def test_batch_calls_batched_method(
        self, service, mock_repo, mock_cache, nairobi_row
    ):
        points = [(-1.2864, 36.8172), (-1.2900, 36.8200)]
        mock_repo.find_exact_batch.return_value = [nairobi_row, nairobi_row]

        results = await service.reverse_geocode_batch(points)

        assert len(results) == 2
        mock_repo.find_exact_batch.assert_awaited_once()
        # Crucially: find_exact must NOT have been called per-point
        mock_repo.find_exact.assert_not_awaited()
