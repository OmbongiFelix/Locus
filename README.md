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
| `locus/api/` | HTTP endpoints (`routes.py`, `auth_routes.py`, `api_key_routes.py`), Pydantic schemas, auth & rate limiting |
| `locus/domain/` | Business logic (`AuthService`, `APIKeyService`, `GeocodeService`), domain dataclasses (`models.py`, `schemas.py`) |
| `locus/infrastructure/` | asyncpg pool, Redis cache, repositories (`user_repository.py`, `api_key_repository.py`, `boundary_repository.py`), email service |
| `locus/pipeline/` | Offline CLI scripts: ingest → validate → publish |

---

## Quick Start

### 1. Start infrastructure

```bash
# Copy and edit environment variables
cp .env.example .env

# Start PostGIS and Redis
docker compose up -d
```

### 2. Run the database migrations

Run the sequential migrations in order:

```bash
# Connect to the running Postgres container and run the migrations
docker exec -i postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
    < migrations/001_initial_schema.sql

docker exec -i postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
    < migrations/002_users.sql

docker exec -i postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
    < migrations/003_api_keys.sql
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
# Health check (public)
curl http://localhost:8000/v1/health

# Boundary version (public)
curl http://localhost:8000/v1/boundaries/version
```

---

## Self-Service Accounts & API Key Management

Locus includes a production-ready, self-service account system for user registration, email verification, session authentication, and API key management.

### Account Lifecycle Flow

```
1. POST /auth/register           → Create unverified account, send verification email
2. POST /auth/verify-email       → Verify email with single-use token, activate account
3. POST /auth/login              → Authenticate with email/password, receive JWT access token
4. POST /api-keys                → Generate API key (raw key returned ONCE only)
5. GET  /api-keys                → View key metadata (safe prefixes, no secrets)
6. DELETE /api-keys/{key_id}     → Immediately revoke key
7. GET  /v1/protected/geocode/.. → Authenticate with Bearer API key
```

---

### Step-by-Step API Usage

#### 1. Register an account

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "developer@example.com",
    "password": "CorrectHorseBatteryStaple123!"
  }'
```

*Response (201 Created):*
```json
{
  "id": "c1f7b824-3450-4822-b5e1-9556ce6d3d9b",
  "email": "developer@example.com",
  "is_active": true,
  "is_verified": false,
  "created_at": "2026-10-14T12:00:00Z"
}
```

#### 2. Verify email address

In development (`EMAIL_PROVIDER=console`), the verification link and token are logged to the console/stdout:
`[DEV EMAIL] Verification email to developer@example.com: verification link is http://localhost:8000/auth/verify-email?token=<TOKEN>`

Verify the account by submitting the token:

```bash
curl -X POST http://localhost:8000/auth/verify-email \
  -H "Content-Type: application/json" \
  -d '{"token": "<TOKEN_FROM_EMAIL>"}'
```

*Response (200 OK):*
```json
{
  "id": "c1f7b824-3450-4822-b5e1-9556ce6d3d9b",
  "email": "developer@example.com",
  "is_active": true,
  "is_verified": true,
  "created_at": "2026-10-14T12:00:00Z"
}
```

*(Optional) Resend verification email:*
```bash
curl -X POST http://localhost:8000/auth/resend-verification \
  -H "Content-Type: application/json" \
  -d '{"email": "developer@example.com"}'
```

#### 3. Sign in (Obtain Access Token)

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "developer@example.com",
    "password": "CorrectHorseBatteryStaple123!"
  }'
```

*Response (200 OK):*
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

#### 4. Generate an API Key

> **Important Security Notice**: The raw key secret (`key`) is returned **only once**, immediately after creation. It is never stored in plaintext and cannot be recovered later. Store it securely.

```bash
curl -X POST http://localhost:8000/api-keys \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -d '{
    "name": "Production Backend",
    "expires_in_days": 90
  }'
```

*Response (201 Created):*
```json
{
  "id": "e81d43a6-d069-424a-95ee-d0ebf8d42d65",
  "name": "Production Backend",
  "key": "locus_live_W7zJ5r4F2qE9kL1p...",
  "key_prefix": "locus_live_W7zJ",
  "created_at": "2026-10-14T12:05:00Z",
  "expires_at": "2027-01-12T12:05:00Z"
}
```

#### 5. List Your API Keys

List all keys belonging to your account. Only safe non-secret metadata is returned:

```bash
curl http://localhost:8000/api-keys \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

*Response (200 OK):*
```json
[
  {
    "id": "e81d43a6-d069-424a-95ee-d0ebf8d42d65",
    "name": "Production Backend",
    "key_prefix": "locus_live_W7zJ",
    "created_at": "2026-10-14T12:05:00Z",
    "expires_at": "2027-01-12T12:05:00Z",
    "revoked_at": null,
    "is_active": true
  }
]
```

#### 6. Revoke an API Key

Revocation takes effect immediately in the database:

```bash
curl -X DELETE http://localhost:8000/api-keys/e81d43a6-d069-424a-95ee-d0ebf8d42d65 \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

*Response (200 OK):*
```json
{
  "id": "e81d43a6-d069-424a-95ee-d0ebf8d42d65",
  "name": "Production Backend",
  "key_prefix": "locus_live_W7zJ",
  "created_at": "2026-10-14T12:05:00Z",
  "expires_at": "2027-01-12T12:05:00Z",
  "revoked_at": "2026-10-14T12:10:00Z",
  "is_active": false
}
```

---

### Authenticating Requests to Protected Endpoints

Protected endpoints require a valid API key passed in the standard `Authorization` header:

```bash
# Reverse geocode a point (KICC, Nairobi)
curl "http://localhost:8000/v1/protected/geocode/reverse?lat=-1.2864&lon=36.8172" \
  -H "Authorization: Bearer locus_live_W7zJ5r4F2qE9kL1p..."

# Batch reverse geocode
curl -X POST http://localhost:8000/v1/protected/geocode/reverse/batch \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer locus_live_W7zJ5r4F2qE9kL1p..." \
  -d '{"points": [{"lat": -1.2864, "lon": 36.8172}]}'
```

#### Endpoint Access Policy

| Endpoint | Access | Required Authentication | Description |
|---|---|---|---|
| `GET /v1/health` | Public | None | Liveness/readiness probe |
| `GET /v1/boundaries/version` | Public | None | Active boundary version |
| `POST /auth/register` | Public (Rate-limited) | None | Create account |
| `POST /auth/verify-email` | Public | None | Confirm email verification token |
| `POST /auth/resend-verification` | Public (Rate-limited) | None | Resend verification token |
| `POST /auth/login` | Public (Rate-limited) | None | Login with email & password |
| `GET /auth/me` | Authenticated User | `Authorization: Bearer <JWT>` | View user profile |
| `POST /api-keys` | Authenticated User | `Authorization: Bearer <JWT>` | Create an API key |
| `GET /api-keys` | Authenticated User | `Authorization: Bearer <JWT>` | List user's API keys |
| `GET /api-keys/{id}` | Authenticated User | `Authorization: Bearer <JWT>` | Get key metadata |
| `DELETE /api-keys/{id}` | Authenticated User | `Authorization: Bearer <JWT>` | Revoke an API key |
| `GET /v1/protected/geocode/reverse` | Protected | `Authorization: Bearer <API_KEY>` | Reverse geocode point |
| `POST /v1/protected/geocode/reverse/batch` | Protected | `Authorization: Bearer <API_KEY>` | Batch reverse geocode |
| `GET /v1/geocode/reverse` | Legacy Protected | `X-API-Key: <KEY>` | Backward-compatible reverse geocode |
| `POST /v1/geocode/reverse/batch` | Legacy Protected | `X-API-Key: <KEY>` | Backward-compatible batch reverse geocode |

---

## Security Model

1. **Password Hashing**: Passwords are hashed using **Argon2id** (`argon2-cffi`), configured with `time_cost=3`, `memory_cost=64 MiB`, and `parallelism=4`. Plaintext passwords are never stored or logged.
2. **API Key Generation & Storage**:
   - Generated using Python's `secrets.token_urlsafe(32)` providing 256 bits of cryptographic entropy.
   - Prepended with a recognizable identifier prefix: `locus_live_`.
   - Stored exclusively as a **SHA-256 cryptographic hash** (`key_hash`).
   - The raw key is returned **only once** at creation and is never logged or displayed again.
   - Non-secret prefixes (e.g. `locus_live_abcd`) are stored for display and human recognition.
3. **Email Verification**:
   - Verification tokens are high-entropy single-use tokens generated with `secrets`.
   - Stored as SHA-256 hashes in `email_verification_tokens`.
   - Consumed atomically with user account verification in a database transaction (`UPDATE ... WHERE used_at IS NULL`).
   - Accounts must be verified before login and API key issuance are permitted.
4. **Session / Access Tokens**:
   - JWTs signed using HMAC-SHA256 (HS256) with environment-configured `SECRET_KEY`.
   - Short expiration window (default 60 minutes).
   - API keys cannot be used to manage user login credentials or alter account state.
5. **Anti-Enumeration Protections**:
   - Resend verification requests always return a uniform confirmation message.
   - Failed login attempts run constant-time dummy password hashing to prevent timing-based email enumeration.
   - API key retrieval and revocation endpoints return `404 Not Found` if a key ID belongs to another user, preventing ID enumeration.
6. **Rate Limiting**:
   - Auth endpoints are protected by `AUTH_RATE_LIMIT_PER_MINUTE` per client IP.
   - API requests are protected by `RATE_LIMIT_PER_MINUTE` per API key.

---

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

---

## Running Tests

### Unit & API contract tests (fast, no external services required)

```bash
uv run pytest -m "not integration" -v
```

### Integration tests (requires real PostGIS instance)

```bash
# Start test database container
docker compose -f docker-compose.test.yml up -d

# Run integration tests against test PostGIS instance
TEST_DATABASE_URL=postgresql://test_user:test_password@localhost:5433/test_db \
    uv run pytest -m integration -v

# Stop test database container
docker compose -f docker-compose.test.yml down
```

### Run complete test suite

```bash
uv run pytest -v
```

---

## Project Layout

```
locus/                          # Python package
├── main.py                     # FastAPI composition root and router mounting
├── config.py                   # Settings (env vars), KENYA_BBOX
├── logging_config.py           # JSON structured logging
├── security.py                 # Argon2id hashing, SHA-256 tokens, HS256 JWT
├── api/
│   ├── routes.py               # Geocoding endpoints (public & protected)
│   ├── auth_routes.py          # /auth registration, verification, login, me
│   ├── api_key_routes.py       # /api-keys creation, listing, revocation
│   ├── schemas.py              # Pydantic request/response models
│   └── dependencies.py         # Auth, rate limiting, service wiring
├── domain/
│   ├── models.py               # Pure internal dataclasses (User, APIKey, Token)
│   ├── auth_service.py         # User account & verification business logic
│   ├── api_key_service.py      # API key lifecycle & validation business logic
│   ├── schemas.py              # Internal dataclasses (MatchType, GeocodeResult)
│   ├── validation.py           # Bounding-box + swapped-param checks
│   └── geocode_service.py      # Orchestration (the 7-step sequence)
├── infrastructure/
│   ├── db.py                   # asyncpg pool lifecycle
│   ├── user_repository.py      # User & verification token asyncpg persistence
│   ├── api_key_repository.py   # API key asyncpg persistence & joins
│   ├── boundary_repository.py  # Raw spatial SQL (ST_Contains, ST_DWithin)
│   ├── cache.py                # Redis read-through cache
│   └── email_service.py        # Email delivery abstraction (Console, Mock, SMTP)
└── pipeline/
    ├── ground_truth.py         # Curated landmark list for QA
    ├── ingest.py               # Shapefile/GeoJSON → staging table
    ├── validate_geometry.py    # ST_IsValid, row count, ground-truth
    └── publish.py              # Blue-green staging → production

tests/                          # Test suite
├── test_security.py            # Password hashing, token, and JWT tests
├── test_auth_service.py        # Registration, verification, login domain tests
├── test_api_key_service.py     # API key service domain tests
├── test_auth_api.py            # /auth HTTP contract tests
├── test_api_keys_api.py        # /api-keys HTTP contract tests
├── test_protected_endpoints.py # API key authentication & policy tests
├── test_user_and_key_repositories.py # Repository mapping & integration tests
├── test_geocode_service.py     # Geocode domain unit tests (mocked, fast)
├── test_boundary_repository.py # Boundary integration tests (real PostGIS)
└── test_api.py                 # Geocoding HTTP contract tests

migrations/
├── 001_initial_schema.sql      # Initial PostGIS schema
├── 002_users.sql               # User accounts and verification tokens schema
└── 003_api_keys.sql            # API keys schema
```