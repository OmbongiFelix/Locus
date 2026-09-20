"""
CLI script: loads a raw shapefile/GeoJSON/GeoPackage release into the
staging table.

Implements the ingestion steps from the design document (Section 2):
CRS standardization to EPSG:4326, topology cleaning, multipart
normalization (ST_Multi), and load into
kenya_admin_boundaries_staging. Run manually or via a scheduled job
when IEBC/KNBS/HDX publish a new boundary release.

Handles both single-layer formats (Shapefile, GeoJSON) and
multi-layer formats (GeoPackage, FileGDB) uniformly. For multi-layer
sources the boundary layer is auto-selected only when the choice is
unambiguous — either the source has exactly one layer, or exactly one
layer is both a polygon layer and carries the required electoral
attribute columns. Anything more ambiguous than that is refused
rather than guessed: the script lists every candidate layer (name,
geometry type, columns) and asks for --layer explicitly. This matters
because a GeoPackage can silently bundle unrelated layers (e.g. a
points-of-interest layer alongside the boundary polygons), and
picking the wrong one would ingest the wrong data with no error.

Usage:
    python -m locus.pipeline.ingest --source data/iebc_2024.shp \\
        --source-dataset IEBC --boundary-version iebc-2024-delimitation

    python -m locus.pipeline.ingest --source data/iebc_2024.gpkg \\
        --source-dataset IEBC --boundary-version iebc-2024-delimitation

    # Explicit layer, when auto-selection can't disambiguate:
    python -m locus.pipeline.ingest --source data/iebc_2024.gpkg \\
        --layer wards --source-dataset IEBC \\
        --boundary-version iebc-2024-delimitation

    # Inspect layers without ingesting anything:
    python -m locus.pipeline.ingest --source data/iebc_2024.gpkg --list-layers
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any, cast

import geopandas as gpd
from shapely.geometry import MultiPolygon
from shapely.validation import make_valid

from locus.infrastructure.db import get_connection, init_db_pool, close_db_pool

logger = logging.getLogger(__name__)

# Aliases account for the 10-character field-name truncation imposed
# by shapefile's DBF attribute table (e.g. "constituency" -> "CONSTITUEN").
REQUIRED_COLUMN_ALIASES: dict[str, list[str]] = {
    "county_name": ["county_name", "COUNTY"],
    "constituency": ["constituency", "CONSTITUEN"],
    "ward_name": ["ward_name", "WARD"],
}


def list_layers(source_path: str) -> list[dict[str, Any]]:
    """Return every layer in *source_path* with its geometry type and
    attribute columns.

    Works uniformly across single-layer sources (Shapefile, GeoJSON —
    reported as one synthetic layer) and multi-layer sources
    (GeoPackage, FileGDB). Safe to call purely for inspection, e.g.
    via --list-layers.
    """
    try:
        layer_info = gpd.list_layers(source_path)  # geopandas >= 0.14
        entries = [
            {"name": row["name"], "geometry_type": row["geometry_type"]}
            for _, row in layer_info.iterrows()
        ]
    except AttributeError:
        # Older geopandas without gpd.list_layers(): fall back to fiona.
        # Geometry type is unknown here, so multi-layer auto-selection
        # below will always defer to --layer rather than guess.
        import fiona

        entries = [
            {"name": name, "geometry_type": None}
            for name in fiona.listlayers(source_path)
        ]

    layers: list[dict[str, Any]] = []
    for entry in entries:
        sample = gpd.read_file(source_path, layer=entry["name"], rows=0)
        layers.append(
            {
                "name": entry["name"],
                "geometry_type": entry["geometry_type"] or "unknown",
                "columns": [c for c in sample.columns if c != "geometry"],
            }
        )
    return layers


def _has_required_columns(columns: set[str]) -> bool:
    """True if *columns* carries every mandatory electoral field,
    under either its canonical name or its shapefile-truncated alias.
    """
    return all(
        any(alias in columns for alias in aliases)
        for aliases in REQUIRED_COLUMN_ALIASES.values()
    )


def resolve_layer(source_path: str, requested_layer: str | None) -> str | None:
    """Decide which layer to read from *source_path*.

    - An explicit --layer is always honoured as-is, once confirmed to exist.
    - A single-layer source needs no decision.
    - A multi-layer source is auto-resolved only when exactly one
      layer is both a polygon layer and has the required electoral
      columns. Otherwise this raises, listing every layer found, so
      you can pass --layer yourself rather than risk silently
      ingesting the wrong one.
    """
    layers = list_layers(source_path)

    if requested_layer is not None:
        names = {l["name"] for l in layers}
        if requested_layer not in names:
            raise ValueError(
                f"Layer {requested_layer!r} not found in {source_path}. "
                f"Available layers: {sorted(names)}"
            )
        return requested_layer

    if len(layers) == 1:
        return layers[0]["name"]

    candidates = [
        l
        for l in layers
        if "Polygon" in (l["geometry_type"] or "")
        and _has_required_columns(set(l["columns"]))
    ]

    if len(candidates) == 1:
        logger.info(
            "Auto-selected layer %r out of %d layers in %s "
            "(only unambiguous polygon layer with the required columns)",
            candidates[0]["name"],
            len(layers),
            source_path,
        )
        return candidates[0]["name"]

    listing = "\n".join(
        f"  - {l['name']}  ({l['geometry_type']}): {l['columns']}"
        for l in layers
    )
    reason = "no layer matched" if not candidates else "more than one layer matched"
    raise ValueError(
        f"Cannot auto-select a boundary layer in {source_path} ({reason}).\n"
        f"Layers found:\n{listing}\n"
        f"Re-run with --layer <name> to choose explicitly, or use "
        f"--list-layers to inspect them first."
    )


def load_and_clean(source_path: str, layer: str | None = None) -> gpd.GeoDataFrame:
    """Load a shapefile/GeoJSON/GeoPackage layer, reproject to
    EPSG:4326, repair topology, and normalise all geometries to
    MultiPolygon.

    This script is intentionally decoupled from the live API's
    connection pool — it opens its own short-lived connection since it
    runs as an occasional offline job.
    """
    gdf = gpd.read_file(source_path, layer=layer)

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
    ``is_current`` left ``false``. Never writes to the live table.

    county_name/constituency/ward_name are electoral boundaries and
    are expected on every row, so a missing value defaults to ``""``
    rather than ``None`` — it signals "should be present but isn't"
    instead of "not applicable." sub_county_name/location_name/
    sub_location are administrative subdivisions that legitimately
    vary by source dataset, so a missing value defaults to ``None``.

    Column fallbacks also cover GADM's naming (NAME_1/NAME_2/NAME_3
    for county/sub-county/ward). NAME_2 is deliberately routed to
    sub_county_name, not constituency — GADM's level-2 units are
    administrative sub-counties, not IEBC electoral constituencies,
    and mapping one onto the other would mislabel the data as
    something it isn't. A GADM-sourced row will therefore always
    insert an empty ``constituency`` — that's expected, not a bug.
    Populating real constituency boundaries needs an actual
    electoral boundary source (e.g. IEBC's admbnda dataset).

    All rows are inserted as a single batch inside one transaction —
    if any row fails, nothing is written, so publish.py can never pick
    up a partial staging set for this ``boundary_version``.

    Returns the number of rows inserted.
    """
    records = [
        (
            row["geometry"].wkt,
            row.get("county_name", row.get("COUNTY", row.get("NAME_1", ""))),
            row.get("constituency", row.get("CONSTITUEN", "")),
            row.get("ward_name", row.get("WARD", row.get("NAME_3", ""))),
            row.get("sub_county_name", row.get("SUBCOUNTY", row.get("NAME_2", None))),
            row.get("location_name", row.get("LOCATION", None)),
            row.get("sub_location", row.get("SUBLOC", None)),
            source_dataset,
            boundary_version,
        )
        for _, row in gdf.iterrows()
    ]

    async with get_connection() as conn:
        async with conn.transaction():
            await conn.executemany(
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
                records,
            )
    return len(records)


def main() -> None:
    """CLI entrypoint for the ingestion pipeline."""
    parser = argparse.ArgumentParser(
        description="Ingest boundary data into the staging table"
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Path to a shapefile, GeoJSON, or GeoPackage",
    )
    parser.add_argument(
        "--source-dataset",
        help="Dataset label (e.g. IEBC, KNBS, HDX). Required unless --list-layers.",
    )
    parser.add_argument(
        "--boundary-version",
        help="Version tag (e.g. iebc-2024-delimitation). Required unless --list-layers.",
    )
    parser.add_argument(
        "--layer",
        default=None,
        help=(
            "Explicit layer name for multi-layer sources (GeoPackage, "
            "FileGDB). If omitted, auto-selected when unambiguous — see "
            "--list-layers to check first."
        ),
    )
    parser.add_argument(
        "--list-layers",
        action="store_true",
        help="Print the layers found in --source and exit without ingesting.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.list_layers:
        for l in list_layers(args.source):
            print(f"{l['name']}\t{l['geometry_type']}\t{l['columns']}")
        return

    if not args.source_dataset or not args.boundary_version:
        parser.error(
            "--source-dataset and --boundary-version are required "
            "unless --list-layers is set"
        )

    layer = resolve_layer(args.source, args.layer)

    logger.info("Loading %s (layer=%s) …", args.source, layer)
    gdf = load_and_clean(args.source, layer=layer)
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