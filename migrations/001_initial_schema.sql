-- migrations/001_initial_schema.sql
-- Initial schema for the Locus Kenyan reverse-geocoding service.
-- Run once against the target database before starting the API.
--
-- Both tables share the same column set so staging rows can be
-- promoted to production via a single INSERT … SELECT inside
-- pipeline/publish.py.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ── Live production table ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kenya_admin_boundaries (
    id               BIGSERIAL       PRIMARY KEY,
    geom             geometry(MultiPolygon, 4326) NOT NULL,
    county_name      TEXT            NOT NULL,
    constituency     TEXT            NOT NULL,
    ward_name        TEXT            NOT NULL,
    sub_county_name   TEXT,            
    location_name    TEXT,
    sub_location     TEXT,
    source_dataset   TEXT            NOT NULL,
    boundary_version TEXT            NOT NULL,
    is_current       BOOLEAN         NOT NULL DEFAULT false,
    effective_from   TIMESTAMPTZ,
    effective_to     TIMESTAMPTZ
);

-- Spatial index for ST_Contains / ST_DWithin queries
CREATE INDEX IF NOT EXISTS idx_boundaries_geom
    ON kenya_admin_boundaries USING GIST (geom);

-- Partial index accelerating the is_current = true filter
-- used on every live request path
CREATE INDEX IF NOT EXISTS idx_boundaries_current
    ON kenya_admin_boundaries (is_current)
    WHERE is_current = true;

-- Composite index for version + active lookups
CREATE INDEX IF NOT EXISTS idx_boundaries_version_current
    ON kenya_admin_boundaries (boundary_version, is_current)
    WHERE is_current = true;


-- ── Staging table (same schema) ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS kenya_admin_boundaries_staging (
    id               BIGSERIAL       PRIMARY KEY,
    geom             geometry(MultiPolygon, 4326) NOT NULL,
    county_name      TEXT            NOT NULL,
    constituency     TEXT            NOT NULL,
    ward_name        TEXT            NOT NULL,
    sub_county_name  TEXT,
    location_name    TEXT,
    sub_location     TEXT,
    source_dataset   TEXT            NOT NULL,
    boundary_version TEXT            NOT NULL,
    is_current       BOOLEAN         NOT NULL DEFAULT false,
    effective_from   TIMESTAMPTZ,
    effective_to     TIMESTAMPTZ
);

-- Spatial index on staging for validate_geometry.py ground-truth checks
CREATE INDEX IF NOT EXISTS idx_staging_geom
    ON kenya_admin_boundaries_staging USING GIST (geom);

-- Version lookup for pipeline scripts
CREATE INDEX IF NOT EXISTS idx_staging_version
    ON kenya_admin_boundaries_staging (boundary_version);
