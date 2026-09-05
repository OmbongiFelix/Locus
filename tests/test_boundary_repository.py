"""
Integration tests for BoundaryRepository against a real PostGIS
instance.

Uses a small, fixed set of seeded test polygons (not the full
national dataset) so tests are fast and deterministic. Covers the
edge cases from the design document (Section 6): islands, water
bodies, and boundary-line points.
"""

import os

import pytest
import pytest_asyncio
import asyncpg

from locus.infrastructure.boundary_repository import BoundaryRepository

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://test_user:test_password@localhost:5433/test_db",
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="module")
async def db_pool():
    """Create a test DB pool, seed test data, and tear down afterwards."""
    pool = await asyncpg.create_pool(dsn=TEST_DB_URL)

    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")

        await conn.execute("""
            DROP TABLE IF EXISTS kenya_admin_boundaries;
            CREATE TABLE kenya_admin_boundaries (
                id               BIGSERIAL PRIMARY KEY,
                geom             geometry(MultiPolygon, 4326) NOT NULL,
                county_name      TEXT NOT NULL,
                constituency     TEXT NOT NULL,
                ward_name        TEXT NOT NULL,
                location_name    TEXT,
                sub_location     TEXT,
                source_dataset   TEXT NOT NULL,
                boundary_version TEXT NOT NULL,
                is_current       BOOLEAN NOT NULL DEFAULT false,
                effective_from   TIMESTAMPTZ DEFAULT now(),
                effective_to     TIMESTAMPTZ
            )
        """)
        await conn.execute(
            "CREATE INDEX idx_test_geom "
            "ON kenya_admin_boundaries USING GIST (geom)"
        )

        # Seed: polygon roughly covering central Nairobi
        await conn.execute("""
            INSERT INTO kenya_admin_boundaries (
                geom, county_name, constituency, ward_name,
                location_name, sub_location,
                source_dataset, boundary_version, is_current
            ) VALUES (
                ST_Multi(ST_GeomFromText(
                    'POLYGON((36.80 -1.30, 36.85 -1.30, '
                    '36.85 -1.25, 36.80 -1.25, 36.80 -1.30))',
                    4326
                )),
                'Nairobi', 'Starehe', 'Nairobi Central',
                'Nairobi Central', 'KICC',
                'test', 'test-v1', true
            )
        """)

        # Seed: MultiPolygon "island" case (Lamu-like archipelago)
        await conn.execute("""
            INSERT INTO kenya_admin_boundaries (
                geom, county_name, constituency, ward_name,
                location_name, sub_location,
                source_dataset, boundary_version, is_current
            ) VALUES (
                ST_Multi(ST_Collect(ARRAY[
                    ST_GeomFromText(
                        'POLYGON((40.85 -2.28, 40.95 -2.28, '
                        '40.95 -2.22, 40.85 -2.22, 40.85 -2.28))',
                        4326
                    ),
                    ST_GeomFromText(
                        'POLYGON((40.80 -2.10, 40.86 -2.10, '
                        '40.86 -2.06, 40.80 -2.06, 40.80 -2.10))',
                        4326
                    )
                ])),
                'Lamu', 'Lamu West', 'Shella',
                'Shella', NULL,
                'test', 'test-v1', true
            )
        """)

    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def repo(db_pool, monkeypatch):
    """Patch the db module's pool to use our test pool."""
    import locus.infrastructure.db as db_mod

    monkeypatch.setattr(db_mod, "_pool", db_pool)
    return BoundaryRepository()


# ── Tests ────────────────────────────────────────────────────────────


class TestFindExact:
    @pytest.mark.asyncio
    async def test_point_inside_nairobi(self, repo):
        result = await repo.find_exact(-1.2750, 36.8200)
        assert result is not None
        assert result["county_name"] == "Nairobi"
        assert result["constituency"] == "Starehe"

    @pytest.mark.asyncio
    async def test_point_in_open_water(self, repo):
        """A point in the Indian Ocean — no boundary should contain it."""
        result = await repo.find_exact(-3.0, 42.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_multipolygon_island(self, repo):
        """A point inside one of the island polygons."""
        result = await repo.find_exact(-2.25, 40.90)
        assert result is not None
        assert result["county_name"] == "Lamu"


class TestFindNearestFallback:
    @pytest.mark.asyncio
    async def test_point_just_outside_boundary(self, repo):
        """A point just outside Nairobi polygon, within fallback tolerance."""
        result = await repo.find_nearest(-1.3050, 36.8200, radius_m=5000)
        assert result is not None
        assert result["county_name"] == "Nairobi"
        assert result["distance_m"] > 0

    @pytest.mark.asyncio
    async def test_point_far_outside(self, repo):
        """A point far away — should return None."""
        result = await repo.find_nearest(0.0, 30.0, radius_m=5000)
        assert result is None


class TestFindExactBatch:
    @pytest.mark.asyncio
    async def test_returns_in_input_order(self, repo):
        points = [
            (-1.2750, 36.8200),  # inside Nairobi
            (-3.0, 42.0),  # open water
            (-2.25, 40.90),  # inside Lamu island
        ]
        results = await repo.find_exact_batch(points)

        assert len(results) == 3
        assert results[0] is not None
        assert results[0]["county_name"] == "Nairobi"
        assert results[1] is None
        assert results[2] is not None
        assert results[2]["county_name"] == "Lamu"


class TestGetCurrentVersion:
    @pytest.mark.asyncio
    async def test_returns_version(self, repo):
        result = await repo.get_current_version()
        assert result["boundary_version"] == "test-v1"
