"""
CLI script: loads a raw shapefile/GeoJSON release into the staging
table.

Implements the ingestion steps from the design document (Section 2):
CRS standardization to EPSG:4326, topology cleaning, multipart
normalization (ST_Multi), and load into
kenya_admin_boundaries_staging. Run manually or via a scheduled job
when IEBC/KNBS/HDX publish a new boundary release.

Usage:
    python -m locus.pipeline.ingest --source data/iebc_2024.shp \\
        --source-dataset IEBC --boundary-version iebc-2024-delimitation
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, cast

import geopandas as gpd
from shapely.geometry import MultiPolygon
from shapely.validation import make_valid

from locus.infrastructure.db import get_connection, init_db_pool, close_db_pool

logger = logging.getLogger(__name__)


def load_and_clean(source_path: str) -> gpd.GeoDataFrame:
    """Load a shapefile/GeoJSON, reproject to EPSG:4326, repair
    topology, and normalise all geometries to MultiPolygon.

    This script is intentionally decoupled from the live API's
    connection pool — it opens its own short-lived connection since it
    runs as an occasional offline job.
    """
    gdf = gpd.read_file(source_path)

    # CRS standardisation
    if gdf.crs is None:
        logger.warning("No CRS found — assuming EPSG:4326")
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        logger.info("Reprojecting from %s to EPSG:4326", gdf.crs)
        gdf = gdf.to_crs(epsg=4326)

    # Topology repair (fix self-intersections / gaps)
    gdf["geometry"] = cast(Any, [
    make_valid(g) if not g.is_valid else g for g in gdf["geometry"]
    ])

    # Normalise to MultiPolygon (islands / exclaves)
    gdf["geometry"] = cast(Any, [
    g if isinstance(g, MultiPolygon) else MultiPolygon([g])
    for g in gdf["geometry"]
    ])

    return gdf


async def write_to_staging(
    gdf: gpd.GeoDataFrame,
    source_dataset: str,
    boundary_version: str,
) -> int:
    """Write cleaned rows into ``kenya_admin_boundaries_staging``.

    Tags every row with *source_dataset* and *boundary_version*,
    ``is_current`` left ``false``.  Never writes to the live table.

    Returns the number of rows inserted.
    """
    count = 0
    async with get_connection() as conn:
        for _, row in gdf.iterrows():
            await conn.execute(
                """
                INSERT INTO kenya_admin_boundaries_staging (
                    geom, county_name, constituency, ward_name,
                    sub_county_name, location_name, sub_location,
                    source_dataset, boundary_version, is_current
                ) VALUES (
                    ST_Multi(ST_GeomFromText($1, 4326)),
                    $2, $3, $4, $5, $6, $7, $8, $9, false
                )
                """,
                row["geometry"].wkt,
                row.get("county_name", row.get("COUNTY", "")),
                row.get("constituency", row.get("CONSTITUEN", "")),
                row.get("ward_name", row.get("WARD", "")),
                row.get("sub_county_name", row.get("SUBCOUNTY", None)),
                row.get("location_name", row.get("LOCATION", None)),
                row.get("sub_location", row.get("SUBLOC", None)),
                source_dataset,
                boundary_version,
            )
            count += 1
    return count


def main() -> None:
    """CLI entrypoint for the ingestion pipeline."""
    parser = argparse.ArgumentParser(
        description="Ingest boundary data into the staging table"
    )
    parser.add_argument(
        "--source", required=True, help="Path to shapefile or GeoJSON"
    )
    parser.add_argument(
        "--source-dataset",
        required=True,
        help="Dataset label (e.g. IEBC, KNBS, HDX)",
    )
    parser.add_argument(
        "--boundary-version",
        required=True,
        help="Version tag (e.g. iebc-2024-delimitation)",
    )
    args = parser.parse_args()

    logger.info("Loading %s …", args.source)
    gdf = load_and_clean(args.source)
    logger.info("Cleaned %d features", len(gdf))

    async def _run() -> None:
        await init_db_pool()
        try:
            n = await write_to_staging(
                gdf, args.source_dataset, args.boundary_version
            )
            logger.info("Inserted %d rows into staging", n)
        finally:
            await close_db_pool()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
