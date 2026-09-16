"""
Spatial query layer over the kenya_admin_boundaries table.

Implements the two queries defined in the design document (Section 4):
find_exact(lat, lon) - ST_Contains against is_current boundaries
find_nearest(lat, lon, radius_m) - ST_DWithin fallback, ordered by
                                   ST_Distance, used only when
                                   find_exact() returns nothing

This is the only file in the codebase that contains raw spatial SQL.
"""

from __future__ import annotations

from locus.infrastructure.db import get_connection


def _row_to_dict(row, distance_m: float = 0.0) -> dict:
    """Convert an asyncpg Record to a plain dict with the expected keys."""
    return {
        "county_name": row["county_name"],
        "constituency": row["constituency"],
        "ward_name": row["ward_name"],
        "sub_county_name": row["sub_county_name"], #i added this line to include sub_county_name in the output
        "location_name": row["location_name"],
        "sub_location": row["sub_location"],
        "boundary_version": row["boundary_version"],
        "distance_m": distance_m,
    }


class BoundaryRepository:
    """Spatial query layer — the only file with raw SQL against the
    ``kenya_admin_boundaries`` table.

    Every query uses parameterized placeholders ($1, $2, …) — never
    string-format coordinates into SQL.
    """

    async def find_exact(self, lat: float, lon: float) -> dict | None:
        """ST_Contains lookup scoped to ``is_current = true``.

        Parameters
        ----------
        lat, lon : float
            WGS-84 coordinates.  Note: ST_Point takes (lon, lat) order.
        """
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT county_name, constituency, ward_name, sub_county_name,
                       location_name, sub_location, boundary_version
                  FROM kenya_admin_boundaries
                 WHERE is_current = true
                   AND ST_Contains(
                           geom,
                           ST_SetSRID(ST_Point($2, $1), 4326)
                       )
                 LIMIT 1
                """,
                lat,
                lon,
            )
        if row is None:
            return None
        return _row_to_dict(row, distance_m=0.0)

    async def find_nearest(
        self, lat: float, lon: float, radius_m: float
    ) -> dict | None:
        """ST_DWithin / ST_Distance fallback (geography cast).

        Used only when ``find_exact()`` returns nothing.
        """
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT county_name, constituency, ward_name, sub_county_name,
                       location_name, sub_location, boundary_version,
                       ST_Distance(
                           geom::geography,
                           ST_SetSRID(ST_Point($2, $1), 4326)::geography
                       ) AS distance_m
                  FROM kenya_admin_boundaries
                 WHERE is_current = true
                   AND ST_DWithin(
                           geom::geography,
                           ST_SetSRID(ST_Point($2, $1), 4326)::geography,
                           $3
                       )
                 ORDER BY distance_m
                 LIMIT 1
                """,
                lat,
                lon,
                radius_m,
            )
        if row is None:
            return None
        return _row_to_dict(row, distance_m=float(row["distance_m"]))

    async def find_exact_batch(
        self, points: list[tuple[float, float]]
    ) -> list[dict | None]:
        """Single multi-point ST_Contains query; results in input order.

        Uses ``unnest`` with ``ORDINALITY`` so the DB does one pass
        instead of N sequential round-trips.
        """
        if not points:
            return []

        lats = [p[0] for p in points]
        lons = [p[1] for p in points]

        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                WITH input_points AS (
                    SELECT ordinality AS idx, lat, lon
                      FROM unnest($1::float8[], $2::float8[])
                               WITH ORDINALITY AS t(lat, lon, ordinality)
                )
                SELECT ip.idx,
                       b.county_name,
                       b.constituency,
                       b.ward_name,
                       b.sub_county_name,  
                       b.location_name,
                       b.sub_location,
                       b.boundary_version
                  FROM input_points ip
                  LEFT JOIN kenya_admin_boundaries b
                    ON b.is_current = true
                   AND ST_Contains(
                           b.geom,
                           ST_SetSRID(ST_Point(ip.lon, ip.lat), 4326)
                       )
                 ORDER BY ip.idx
                """,
                lats,
                lons,
            )

        result_map: dict[int, dict] = {}
        for row in rows:
            if row["county_name"] is not None:
                result_map[row["idx"]] = _row_to_dict(row, distance_m=0.0)

        return [result_map.get(i + 1) for i in range(len(points))]

    async def get_current_version(self) -> dict:
        """Return the active ``boundary_version`` and ``effective_from``.

        Used by the ``GET /boundaries/version`` endpoint.
        """
        async with get_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT DISTINCT boundary_version, effective_from
                  FROM kenya_admin_boundaries
                 WHERE is_current = true
                 LIMIT 1
                """
            )
        if row is None:
            return {"boundary_version": "none", "effective_from": None}
        return {
            "boundary_version": row["boundary_version"],
            "effective_from": row["effective_from"],
        }
