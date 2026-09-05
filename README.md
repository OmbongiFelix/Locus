# Locus

> Reverse-geocode any lat/lon to Kenya's County, Constituency, Ward, Location, and Sub-Location — self-hosted, PostGIS-powered, no third-party API required.

## Architecture

```
API Client → Input Validation → Cache Lookup → Exact Query (ST_Contains)
  → Fallback Query (ST_DWithin) → Response Assembly → Cache Write → API Response
```

**Dependency direction is one-way**: `api/` → `domain/` → `infrastructure/`.

| Layer | Responsibility |
|-------|---------------|
| `locus/api/` | HTTP endpoints, Pydantic schemas, auth & rate limiting |
| `locus/domain/` | Validation, orchestration (`GeocodeService`), domain dataclasses |
| `locus/infrastructure/` | asyncpg pool, Redis cache, raw spatial SQL |
| `locus/pipeline/` | Offline CLI scripts: ingest → validate → publish |

## Quick Start

### 1. Start infrastructure

```bash
# Copy and edit environment variables
cp .env.example .env

# Start PostGIS and Redis
docker compose up -d
```

### 2. Run the database migration

```bash
# Connect to the running Postgres container and run the migration
docker exec -i postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
    < migrations/001_initial_schema.sql
```

### 3. Install dependencies

```bash
# Using uv (recommended)
uv sync --all-groups

# Or using pip
pip install -e ".[dev]"
```

### 4. Start the API

```bash
uv run uvicorn locus.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Test it

```bash
# Health check
curl http://localhost:8000/v1/health

# Reverse geocode KICC
curl "http://localhost:8000/v1/geocode/reverse?lat=-1.2864&lon=36.8172" \
    -H "X-API-Key: your-key"

# Batch geocode
curl -X POST http://localhost:8000/v1/geocode/reverse/batch \
    -H "Content-Type: application/json" \
    -H "X-API-Key: your-key" \
    -d '{"points": [{"lat": -1.2864, "lon": 36.8172}]}'

# Boundary version
curl http://localhost:8000/v1/boundaries/version
```

## Offline Pipeline

The data pipeline has three stages that must be run in order:

### 1. Ingest

Load a shapefile or GeoJSON into the staging table:

```bash
uv run python -m locus.pipeline.ingest \
    --source data/iebc_2024.shp \
    --source-dataset IEBC \
    --boundary-version iebc-2024-delimitation
```

### 2. Validate

QA gate — checks geometry validity, row counts, and ground-truth landmarks:

```bash
uv run python -m locus.pipeline.validate_geometry \
    --boundary-version iebc-2024-delimitation \
    --expected-count 1450
```

### 3. Publish

Atomically promote validated staging data to production:

```bash
uv run python -m locus.pipeline.publish \
    --boundary-version iebc-2024-delimitation
```

## Running Tests

### Unit tests (no external services needed)

```bash
uv run pytest tests/test_geocode_service.py tests/test_api.py -v
```

### Integration tests (requires PostGIS)

```bash
# Start the test database
docker compose -f docker-compose.test.yml up -d

# Wait for it to be healthy, then run
TEST_DATABASE_URL=postgresql://test_user:test_password@localhost:5433/test_db \
    uv run pytest tests/test_boundary_repository.py -v -m integration

# Tear down
docker compose -f docker-compose.test.yml down
```

### All tests

```bash
uv run pytest -v
```

## Project Layout

```
locus/                          # Python package
├── main.py                     # FastAPI composition root
├── config.py                   # Settings (env vars), KENYA_BBOX
├── logging_config.py           # JSON structured logging
├── api/
│   ├── routes.py               # HTTP endpoints (thin layer)
│   ├── schemas.py              # Pydantic request/response models
│   └── dependencies.py         # Auth, rate limiting, service wiring
├── domain/
│   ├── schemas.py              # Internal dataclasses (MatchType, GeocodeResult)
│   ├── validation.py           # Bounding-box + swapped-param checks
│   └── geocode_service.py      # Orchestration (the 7-step sequence)
├── infrastructure/
│   ├── db.py                   # asyncpg pool lifecycle
│   ├── boundary_repository.py  # Raw spatial SQL (ST_Contains, ST_DWithin)
│   └── cache.py                # Redis read-through cache
└── pipeline/
    ├── ground_truth.py          # Curated landmark list for QA
    ├── ingest.py                # Shapefile/GeoJSON → staging table
    ├── validate_geometry.py     # ST_IsValid, row count, ground-truth
    └── publish.py               # Blue-green staging → production

tests/                          # Test suite
├── test_geocode_service.py     # Unit tests (mocked, fast)
├── test_boundary_repository.py # Integration tests (real PostGIS)
└── test_api.py                 # HTTP contract tests (TestClient)

migrations/
└── 001_initial_schema.sql      # Initial PostGIS schema
```

## Design Decisions & Assumptions

- **Cache fails open**: every code path works correctly if Redis is down.
- **Historical rows are never deleted**: `is_current = false` + `effective_to` set, so queries against a past `boundary_version` stay reproducible.
- **Rate limiting is in-memory**: suitable for single-process deployments; swap to Redis-backed counters for multi-process.
- **API keys are comma-separated in `API_KEYS`**: leave empty for open access (development). In production, set real keys.
- **`FALLBACK_RADIUS_M` defaults to 5000 m**: coordinates near a boundary edge (or slightly offshore) still get a result.