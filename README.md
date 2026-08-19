# Locus

**A self-hosted reverse-geocoding API for Kenyan administrative boundaries.**

Locus resolves a latitude/longitude pair to Kenya's devolved administrative hierarchy — County, Constituency, Ward, Location, and Sub-Location — using a custom Point-in-Polygon (PIP) engine built on PostgreSQL/PostGIS. Commercial providers like Google Maps don't natively return these micro-administrative and electoral levels, so Locus bridges that gap with self-hosted GIS data layers.

---

## Why Locus

- **No paid third-party dependency** for Kenya's micro-administrative layers.
- **Sub-millisecond spatial lookups** via GiST-indexed PostGIS queries.
- **Handles real-world edge cases**: islands (Lake Victoria, Lamu archipelago), water bodies, GPS jitter near borders, and disputed boundaries.
- **Time-aware boundary data**: IEBC delimitation reviews and KNBS census updates are versioned, never silently overwritten.

---

## Architecture

Three-tier design:

1. **Spatial Database** — PostgreSQL + PostGIS storing boundary polygons with spatial indices.
2. **Backend API Engine** — FastAPI (Python) resolving requests via parameterized spatial SQL.
3. **Ingestion Pipeline** — offline scripts that cleanse, repair, version, and load shapefiles/GeoJSON into the database.

An optional fourth component, a **Redis read-through cache**, sits in front of PostGIS once query volume justifies it — it is not on the v1 critical path.

### Request flow

```
API Client → Input Validation → Cache Lookup → Exact Query (ST_Contains)
→ Fallback Query (ST_DWithin) → Response Assembly → Cache Write → API Response
```

---

## Data Sources

| Source | Coverage |
|---|---|
| UNOCHA / HDX Kenya Administrative Boundaries | Admin levels 1, 2, 4, 5, 6 |
| Kenya National Bureau of Statistics (KNBS) | Sub-location level, census-aligned |
| Independent Electoral and Boundaries Commission (IEBC) | 47 counties, 290 constituencies, 1,450 wards |
| OpenStreetMap (OSM) Kenya | Cross-validation of physical boundaries |

> Licensing note: OSM is ODbL-licensed and HDX datasets carry their own attribution terms. Attribution is tracked per-source in the schema (`source_dataset`) so the API's terms-of-use stays accurate.

### Ingestion steps

1. **CRS standardization** — reproject to WGS 84 (EPSG:4326).
2. **Boundary cleaning** — topology repair for self-intersections/gaps.
3. **PostGIS ingestion** — load via `shp2pgsql` or GeoPandas/Shapely.
4. **Multipart normalization** — force every geometry to `MultiPolygon` (`ST_Multi`) so disjoint parts (islands, exclaves) load correctly.
5. **Validity check** — `ST_IsValid` over every feature; reject or auto-repair (`ST_MakeValid`) before it reaches production.

---

## Database Schema

Core table: `kenya_admin_boundaries`

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PRIMARY KEY` | Unique record identifier |
| `county_name` | `VARCHAR(100)` | Not null |
| `constituency` | `VARCHAR(100)` | Not null |
| `ward_name` | `VARCHAR(100)` | Not null |
| `location_name` | `VARCHAR(100)` | Nullable |
| `sub_location` | `VARCHAR(100)` | Nullable |
| `geom` | `GEOMETRY(MultiPolygon, 4326)` | Not null — `MultiPolygon`, not `Polygon`, to support disjoint island counties |
| `source_dataset` | `VARCHAR(50)` | Provenance — `'IEBC'`, `'KNBS'`, `'HDX'` |
| `boundary_version` | `VARCHAR(20)` | e.g. `'iebc-2012-delimitation'` |
| `effective_from` | `DATE` | When this boundary became authoritative |
| `effective_to` | `DATE` | Nullable — `NULL` means currently active |
| `is_current` | `BOOLEAN` | Default `true` — fast filter without a date comparison |
| `updated_at` | `TIMESTAMPTZ` | For cache invalidation and auditing |

**Normalization trade-off:** flat text columns (county/constituency/ward name) on every row let a single `ST_Contains` return the full admin path in one lookup — the right shape for a reverse-geocoding hot path. The recommended pattern is a hybrid: a normalized `admin_units` reference table (self-referencing `parent_id`) as the source of truth, refreshed into this flat table on every publish.

### Indexes

```sql
CREATE INDEX idx_admin_boundaries_geom
  ON kenya_admin_boundaries USING GIST(geom);

-- Speeds up the common case: only the currently-active boundary set
CREATE INDEX idx_admin_boundaries_geom_current
  ON kenya_admin_boundaries USING GIST(geom)
  WHERE is_current = true;
```

---

## Spatial Query Logic

**Baseline (exact) query:**

```sql
SELECT county_name, constituency, ward_name, location_name, sub_location
FROM kenya_admin_boundaries
WHERE is_current = true
  AND ST_Contains(geom, ST_SetSRID(ST_Point(:lon, :lat), 4326))
LIMIT 1;
```

**Fallback query** — runs only when the exact match is empty, within a small tolerance (default 25m):

```sql
SELECT county_name, constituency, ward_name, location_name, sub_location,
  ST_Distance(geom::geography, pt::geography) AS distance_m
FROM kenya_admin_boundaries,
  (SELECT ST_SetSRID(ST_Point(:lon, :lat), 4326) AS pt) AS q
WHERE is_current = true
  AND ST_DWithin(geom::geography, pt::geography, 25)
ORDER BY distance_m ASC
LIMIT 1;
```

If both queries miss, the point is treated as outside Kenya's administrative coverage and the API returns an explicit `no_match` rather than an empty `200`.

### Why the fallback exists

- **Boundary gap / topology seam** — adjacent wards don't share an exact edge after cleaning.
- **GPS jitter** — consumer GPS accuracy (~5–10m) can place a point just outside its true ward.
- **Genuinely outside Kenya** — open water or across an international border.

---

## API Reference

| Method & Path | Purpose | Notes |
|---|---|---|
| `GET /v1/geocode/reverse` | Resolve a single lat/lon | Query params: `lat`, `lon` |
| `POST /v1/geocode/reverse/batch` | Resolve many points in one call | Body: array of `{lat, lon}`; capped batch size (e.g. 500) |
| `GET /v1/boundaries/version` | Report the active `boundary_version` | Lets consumers detect reference-data changes |
| `GET /v1/health` | Liveness/readiness probe | For load balancer / container orchestration |

### Example

```
GET /v1/geocode/reverse?lat=-1.2921&lon=36.8219

200 OK
{
  "county_name": "Nairobi",
  "constituency": "Starehe",
  "ward_name": "Nairobi Central",
  "location_name": "Central",
  "sub_location": "City Square",
  "match_type": "exact",
  "distance_m": 0,
  "boundary_version": "iebc-2012-delimitation"
}
```

`match_type` is `"exact"` for a direct hit, `"nearest"` when the fallback tolerance was used, or the call returns `404` with a `no_match` reason.

### Input validation

Coordinates outside Kenya's approximate bounding box are rejected before reaching PostGIS:

```
lat: -4.72 to 5.06
lon: 33.5 to 41.9
```

This is a cheap early exit that also catches the common client bug of swapped `lat`/`lon`.

### Error codes

| Code | Case | Response |
|---|---|---|
| `400` | Missing/invalid `lat`/`lon`, or out of plausible range | `error: "invalid_coordinates"` |
| `404` | No boundary match after fallback | `error: "no_match"` |
| `413` | Batch exceeds configured size cap | `error: "batch_too_large"` |
| `429` | Rate limit exceeded | `error: "rate_limited"`, includes `Retry-After` header |
| `500` | Unhandled server/database error | `error: "internal_error"`, logged with a correlation ID |

---

## Edge Cases & Data Integrity

- **Islands & multi-part counties** — Lake Victoria islands (Mfangano, Rusinga, Ndere) and the Lamu archipelago are physically disjoint from their county's mainland polygon. This is why `geom` is `MultiPolygon`.
- **Water bodies** — points in Lake Victoria, Lake Turkana, or the Indian Ocean return a clear `no_match` rather than an empty or misleading result.
- **Disputed boundaries** — e.g. sections of the Nairobi–Kajiado and Nairobi–Kiambu lines. `source_dataset` and `boundary_version` keep every classification traceable to an authoritative source; a small admin-override table can record known resolutions without touching bulk-loaded data.
- **GPS jitter** — absorbed by the tolerance-based fallback; `match_type: "nearest"` tells consumers when to treat a result as borderline.

---

## Caching & Performance

Caching is an optimization for scale, not a correctness requirement — the GiST-indexed query is already sub-millisecond.

- **Cache by rounded coordinate** (5–6 decimal places) with a read-through Redis layer. Don't round coarser than that — it risks caching the wrong ward near boundaries.
- **Cache `/v1/boundaries/version` aggressively** — it only changes on a new dataset publish.
- **Invalidate the entire cache** on every `boundary_version` publish rather than per-key.
- **Don't** simplify production query geometry (`ST_SimplifyPreserveTopology`) — reserve that for map-rendering use cases only.
- **Don't** add a cache before there's a measured latency/cost problem.
- Scale reads with **PostGIS read replicas** + **PgBouncer** if concurrency outgrows a single primary.

---

## Boundary Data Update & Versioning (Blue-Green Publish)

1. **Stage** — load the new dataset into `kenya_admin_boundaries_staging`, running the full Section 2 ingestion pipeline.
2. **Validate** — row counts, `ST_IsValid`, and a spot-check against the ground-truth test set.
3. **Publish atomically** — in a single transaction: set `effective_to` on current rows, insert staged rows as `is_current = true` under the new `boundary_version`, and swap. Never `TRUNCATE` the live table in place.
4. **Retain history** — prior versions stay queryable via `effective_from`/`effective_to` for audit and reproducibility.

A scheduled job (cron/Airflow) checks for new IEBC/KNBS/HDX releases, though given how infrequently boundaries change, a manually-triggered run with the same validation gates is equally reasonable.

---

## Testing, Validation & QA

- **Ground-truth regression set** — well-known landmarks per county (KICC, State House, Mombasa Old Town) with verified expected admin levels, re-run after every publish.
- **Topology validation on ingest** — `ST_IsValid`/`ST_MakeValid` gates every feature before staging.
- **API-layer tests (pytest)** — cover an ocean point, an island point, a boundary-line point, an out-of-range coordinate, and a point just outside Kenya.
- **Load testing (k6 / Locust)** — confirm sub-millisecond performance under realistic concurrent load.

---

## Security & Operations

- **Auth & rate limiting** — API keys plus per-key rate limits (`429` responses).
- **Monitoring** — Prometheus + Grafana dashboards; alerts on connection-pool exhaustion or a spike in `no_match` responses (often signals a bad boundary publish).
- **Logging** — query volume and coarse geographic distribution only; no unnecessary client-identifying detail.
- **Backups** — automated PostGIS dumps before every boundary swap, plus a regular independent schedule.
- **Deployment** — containerized via Docker; API and database scale independently.

---

## Tech Stack

| Tool | Role |
|---|---|
| FastAPI / Python | Backend server |
| PostgreSQL / PostGIS | Spatial engine |
| GeoPandas & Shapely | Data cleansing/reprojection |
| QGIS | Visual shapefile/layer verification |
| Docker | Containerized deployment |
| Redis | Optional read-through cache |
| GeoAlchemy2 | Typed PostGIS access from FastAPI |
| PgBouncer | Connection pooling |
| pytest / k6 | API tests / load testing |
| Prometheus / Grafana | Operational monitoring |

---

## Do We Need GeoDjango?

**Short answer: no**, not for the core reverse-geocoding API. The hot path is a single parameterized spatial query — raw SQL or SQLAlchemy + GeoAlchemy2 already expresses that cleanly inside FastAPI. Running Django alongside FastAPI would mean two settings systems, two dependency trees, and two deployment units for an ORM abstraction the query doesn't need.

**Where it earns its place:** boundary *curation* — letting non-GIS staff inspect and fix flagged or disputed geometries through a browser. GeoDjango's admin gives a map-based geometry editor essentially for free, as an isolated, optional microservice against the same PostGIS database. Consider whether QGIS (already in the stack) plus the ingestion scripts covers this need first; reach for GeoDjango once a browser tool's scope outgrows a thin custom page.

| Criterion | Core Reverse-Geocoding API | Boundary Curation / Admin Tool |
|---|---|---|
| Primary need | One fast, parameterized spatial query per request | Occasional, human-in-the-loop polygon editing |
| Best-fit tool | FastAPI + raw SQL / SQLAlchemy+GeoAlchemy2 | Django + GeoDjango admin |
| Recommendation | Keep FastAPI as designed | Add only if staff need to edit boundaries outside QGIS |

---

## Project Structure

```
locus/
├── main.py                              # App bootstrap / composition root
├── config.py                            # Centralized settings, KENYA_BBOX
├── logging_config.py                    # Structured JSON logging
│
├── api/
│   ├── routes.py                        # HTTP endpoint definitions (thin layer)
│   ├── schemas.py                       # Public wire-format Pydantic models
│   └── dependencies.py                  # Service construction, auth, rate limiting
│
├── domain/
│   ├── schemas.py                       # Internal dataclasses (framework-free)
│   ├── validation.py                    # Kenya bbox early-reject check
│   └── geocode_service.py               # Orchestrates the full request sequence
│
├── infrastructure/
│   ├── db.py                            # Process-wide PostGIS connection pool
│   ├── boundary_repository.py           # The only file with raw spatial SQL
│   └── cache.py                         # Redis read-through cache
│
├── pipeline/                            # Offline / data publishing
│   ├── ingest.py                        # Loads raw shapefile/GeoJSON → staging
│   ├── validate_geometry.py             # QA gate before every publish
│   └── publish.py                       # Atomic blue-green promotion to production
│
└── tests/
    ├── test_geocode_service.py          # Unit tests (mocked repo/cache)
    ├── test_boundary_repository.py      # Integration tests (real PostGIS)
    └── test_api.py                      # End-to-end HTTP contract tests
```

### Layering rules

- **`domain/` never imports from `api/` or `infrastructure/`.** Dependency direction flows one way: `domain → infrastructure`.
- **`api/routes.py` stays thin.** Anything beyond calling the service and mapping exceptions belongs in `domain/geocode_service.py`.
- **`infrastructure/boundary_repository.py` is the only file with raw spatial SQL.** All queries here are parameterized — never string-formatted.
- **`pipeline/publish.py` is the only script permitted to write to the live table** outside the initial schema migration. Manual fixes go through staging + publish, never a direct `UPDATE`.
- **The cache fails open, not closed** — every code path works correctly with Redis disabled or unreachable.

---

## Getting Started

```bash
# Run the API
uvicorn locus.main:app --reload

# Run unit tests (fast, mocked — no DB/Redis needed)
pytest locus/tests/test_geocode_service.py

# Run integration tests (requires a running PostGIS test DB)
pytest locus/tests/test_boundary_repository.py -m integration

# Ingest a new boundary release
python -m locus.pipeline.ingest --source data/iebc_2024.shp \
  --source-dataset IEBC --boundary-version iebc-2024-delimitation

# Validate staged data
python -m locus.pipeline.validate_geometry

# Publish (blue-green swap + cache flush)
python -m locus.pipeline.publish --boundary-version iebc-2024-delimitation
```

---

## License & Attribution

Boundary data is sourced from UNOCHA/HDX, KNBS, IEBC, and OpenStreetMap. OSM data is ODbL-licensed; HDX datasets carry their own attribution terms. Per-source attribution is tracked via the `source_dataset` column and should be reflected on the API's terms-of-use page.