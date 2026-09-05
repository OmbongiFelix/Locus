"""
Blue-green publish: promotes a validated staging table to production.

Implements the publish sequence from the design document (Section 8):
within a single transaction, set effective_to on the currently active
rows, insert the staged rows as is_current, and flip the version -
never TRUNCATE the live table in place.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys

from locus.infrastructure.db import get_connection, init_db_pool, close_db_pool
from locus.infrastructure.cache import GeocodeCache, init_cache, close_cache

logger = logging.getLogger(__name__)


async def publish(boundary_version: str) -> None:
    """Atomically promote staging rows to the live table.

    Within a single transaction:
      1. ``effective_to = now()`` and ``is_current = false`` on all
         currently-active rows.
      2. Insert staged rows with ``is_current = true``,
         ``effective_from = now()``.

    Superseded rows are never deleted — historical queries against a
    past ``boundary_version`` remain reproducible.

    ``cache.flush_all()`` is called immediately after a successful
    commit.
    """
    async with get_connection() as conn:
        async with conn.transaction():
            # Deactivate currently-active rows
            await conn.execute(
                """
                UPDATE kenya_admin_boundaries
                   SET effective_to = now(),
                       is_current   = false
                 WHERE is_current = true
                """
            )

            # Promote staging rows
            inserted = await conn.fetchval(
                """
                WITH moved AS (
                    INSERT INTO kenya_admin_boundaries (
                        geom, county_name, constituency, ward_name,
                        location_name, sub_location,
                        source_dataset, boundary_version,
                        is_current, effective_from
                    )
                    SELECT geom, county_name, constituency, ward_name,
                           location_name, sub_location,
                           source_dataset, boundary_version,
                           true, now()
                      FROM kenya_admin_boundaries_staging
                     WHERE boundary_version = $1
                    RETURNING 1
                )
                SELECT count(*) FROM moved
                """,
                boundary_version,
            )
            logger.info(
                "Promoted %s staging rows for version %s",
                inserted,
                boundary_version,
            )

    # Cache flush after successful commit
    cache = GeocodeCache()
    await cache.flush_all()
    logger.info("Cache flushed after publish")


def main() -> None:
    """CLI entrypoint — refuses to run unless ``validate_geometry``
    passed for this ``boundary_version``."""
    parser = argparse.ArgumentParser(
        description="Publish validated staging data to the live table"
    )
    parser.add_argument("--boundary-version", required=True)
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip the validation gate (DANGEROUS — development only)",
    )
    args = parser.parse_args()

    if not args.skip_validation:
        logger.info("Running validation gate …")
        ret = subprocess.run(
            [
                sys.executable,
                "-m",
                "locus.pipeline.validate_geometry",
                "--boundary-version",
                args.boundary_version,
            ],
            check=False,
        )
        if ret.returncode != 0:
            logger.error("Validation failed — refusing to publish.")
            sys.exit(1)

    async def _run() -> None:
        await init_db_pool()
        await init_cache()
        try:
            await publish(args.boundary_version)
        finally:
            await close_cache()
            await close_db_pool()

    asyncio.run(_run())
    logger.info("Publish complete for version %s", args.boundary_version)


if __name__ == "__main__":
    main()
