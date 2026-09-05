"""
QA gate run against the staging table before every publish.

Implements the checks from the design document (Section 9): geometry
validity (ST_IsValid / ST_MakeValid), row-count sanity against the
source's known feature count, and a spot-check against the
ground-truth landmark list. A failing run must block publish.py.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from locus.infrastructure.db import get_connection, init_db_pool, close_db_pool
from locus.pipeline.ground_truth import LANDMARKS

logger = logging.getLogger(__name__)


async def check_validity(boundary_version: str) -> list[dict]:
    """Run ``ST_IsValid`` over every staging row for the given version.

    Attempts ``ST_MakeValid`` on failures and re-checks.  Returns a
    list of dicts describing any *un-repairable* geometries.
    """
    invalid_rows: list[dict] = []
    async with get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, county_name, ward_name,
                   ST_IsValid(geom) AS is_valid
              FROM kenya_admin_boundaries_staging
             WHERE boundary_version = $1
            """,
            boundary_version,
        )
        for row in rows:
            if not row["is_valid"]:
                fixed = await conn.fetchval(
                    """
                    UPDATE kenya_admin_boundaries_staging
                       SET geom = ST_Multi(ST_MakeValid(geom))
                     WHERE id = $1
                    RETURNING ST_IsValid(geom)
                    """,
                    row["id"],
                )
                if not fixed:
                    invalid_rows.append(
                        {
                            "id": row["id"],
                            "county_name": row["county_name"],
                            "ward_name": row["ward_name"],
                            "repairable": False,
                        }
                    )
                else:
                    logger.info(
                        "Repaired geometry id=%s (%s / %s)",
                        row["id"],
                        row["county_name"],
                        row["ward_name"],
                    )
    return invalid_rows


async def check_row_count(boundary_version: str, expected: int) -> bool:
    """Compare staging row count against *expected*; fail on >10 %
    discrepancy."""
    async with get_connection() as conn:
        actual = await conn.fetchval(
            """
            SELECT count(*)
              FROM kenya_admin_boundaries_staging
             WHERE boundary_version = $1
            """,
            boundary_version,
        )
    if actual == 0:
        logger.error("No staging rows for version %s", boundary_version)
        return False
    ratio = abs(actual - expected) / expected
    if ratio > 0.10:
        logger.error(
            "Row count mismatch: expected %d, got %d (%.1f%% off)",
            expected,
            actual,
            ratio * 100,
        )
        return False
    logger.info("Row count OK: %d (expected %d)", actual, expected)
    return True


async def check_ground_truth(boundary_version: str) -> list[dict]:
    """Spot-check staging against the curated landmark list.

    Returns a list of dicts describing any mismatches.
    """
    mismatches: list[dict] = []
    async with get_connection() as conn:
        for lm in LANDMARKS:
            row = await conn.fetchrow(
                """
                SELECT county_name, constituency
                  FROM kenya_admin_boundaries_staging
                 WHERE boundary_version = $1
                   AND ST_Contains(
                           geom,
                           ST_SetSRID(ST_Point($3, $2), 4326)
                       )
                 LIMIT 1
                """,
                boundary_version,
                lm.lat,
                lm.lon,
            )
            if row is None:
                mismatches.append(
                    {
                        "landmark": lm.name,
                        "issue": "no boundary contains this point",
                    }
                )
            elif (
                row["county_name"] != lm.expected_county
                or row["constituency"] != lm.expected_constituency
            ):
                mismatches.append(
                    {
                        "landmark": lm.name,
                        "expected_county": lm.expected_county,
                        "actual_county": row["county_name"],
                        "expected_constituency": lm.expected_constituency,
                        "actual_constituency": row["constituency"],
                    }
                )
    return mismatches


def main() -> None:
    """CLI entrypoint — exits non-zero on any failing check so
    ``publish.py`` will refuse to proceed."""
    parser = argparse.ArgumentParser(
        description="Validate staging geometry before publish"
    )
    parser.add_argument("--boundary-version", required=True)
    parser.add_argument(
        "--expected-count",
        type=int,
        default=0,
        help="Expected row count (0 to skip the row-count check)",
    )
    args = parser.parse_args()

    async def _run() -> bool:
        await init_db_pool()
        try:
            ok = True

            invalid = await check_validity(args.boundary_version)
            if invalid:
                logger.error("Unrepairable geometries: %s", invalid)
                ok = False

            if args.expected_count > 0:
                if not await check_row_count(
                    args.boundary_version, args.expected_count
                ):
                    ok = False

            mismatches = await check_ground_truth(args.boundary_version)
            if mismatches:
                logger.error("Ground-truth mismatches: %s", mismatches)
                ok = False

            return ok
        finally:
            await close_db_pool()

    passed = asyncio.run(_run())
    if not passed:
        logger.error("Validation FAILED — publish will be blocked.")
        sys.exit(1)
    logger.info("All validation checks passed.")


if __name__ == "__main__":
    main()
