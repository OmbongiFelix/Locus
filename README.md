# Locus

> Reverse-geocode any lat/lon to Kenya's County, Sub-County, Constituency, Ward, Location, and Sub-Location — self-hosted, PostGIS-powered, no third-party API required.

**API version:** `0.1.0` · **Spec:** OpenAPI 3.1 · **Stack:** FastAPI, PostGIS, Redis, asyncpg

## Table of Contents

- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [API Overview](#api-overview)
- [Geocoding API](#geocoding-api)
- [Boundaries & Health API](#boundaries--health-api)
- [Self-Service Accounts & API Key Management](#self-service-accounts--api-key-management)
- [Schema Reference](#schema-reference)
- [Errors](#errors)
- [Security Model](#security-model)
- [Offline Pipeline](#offline-pipeline)
- [Running Tests](#running-tests)
- [Project Layout](#project-layout)

---

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

docker exec -i postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
    < migrations/004_unverified_trial.sql
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

> **Tip:** The OpenAPI document (`openapi.json`) describes every endpoint below. If you are running the stock FastAPI app, interactive docs are also served at `/docs` (Swagger UI) and `/redoc`.

---

## API Overview

**Base URL (local):** `http://localhost:8000`

### Versioned and unversioned routes

Account and API-key endpoints are exposed under **two equivalent paths**. Prefer the versioned `/v1/...` form in new integrations.

| Resource | Versioned (preferred) | Unversioned alias |
|---|---|---|
| Auth | `/v1/auth/*` | `/auth/*` |
| API keys | `/v1/api-keys/*` | `/api-keys/*` |

Geocoding, boundary, and health endpoints exist only under `/v1/...`.

### Authentication schemes

| Scheme | Header | Used by |
|---|---|---|
| **None** | — | Health, boundary version, register, verify-email, resend-verification, login |
| **User JWT** | `Authorization: Bearer <ACCESS_TOKEN>` | `/auth/me`, all `/api-keys` endpoints |
| **API key (Bearer)** | `Authorization: Bearer <API_KEY>` | `/v1/protected/geocode/*` |
| **API key (legacy)** | `X-API-Key: <API_KEY>` | `/v1/geocode/*` |

> **Note:** The OpenAPI spec marks the `Authorization` header as *optional* on protected and user-scoped endpoints because the check is enforced at runtime, not by schema validation. Requests without a valid credential are rejected. The legacy `X-API-Key` header is declared *required*.

### Endpoint reference

| Method | Path | Tag | Access | Auth | Success | Description |
|---|---|---|---|---|---|---|
| `GET` | `/v1/health` | — | Public | None | `200` | Liveness/readiness probe — 200 once DB and cache are reachable |
| `GET` | `/v1/boundaries/version` | — | Public | None | `200` | Active boundary version and effective date |
| `POST` | `/v1/auth/register` | auth | Public (rate-limited) | None | `201` | Register a new user account |
| `POST` | `/v1/auth/verify-email` | auth | Public | None | `200` | Verify email with a single-use token |
| `POST` | `/v1/auth/resend-verification` | auth | Public (rate-limited) | None | `200` | Request a fresh verification token |
| `POST` | `/v1/auth/login` | auth | Public (rate-limited) | None | `200` | Sign in and obtain an access token |
| `GET` | `/v1/auth/me` | auth | Authenticated user | Bearer JWT | `200` | Get authenticated user profile |
| `POST` | `/v1/api-keys` | api-keys | Authenticated user | Bearer JWT | `201` | Generate a new API key |
| `GET` | `/v1/api-keys` | api-keys | Authenticated user | Bearer JWT | `200` | List the user's API keys |
| `GET` | `/v1/api-keys/{key_id}` | api-keys | Authenticated user | Bearer JWT | `200` | Get API key metadata |
| `DELETE` | `/v1/api-keys/{key_id}` | api-keys | Authenticated user | Bearer JWT | `200` | Revoke an API key |
| `GET` | `/v1/protected/geocode/reverse` | — | Protected | Bearer API key | `200` | Single-point reverse geocode |
| `POST` | `/v1/protected/geocode/reverse/batch` | — | Protected | Bearer API key | `200` | Batch reverse geocode |
| `GET` | `/v1/geocode/reverse` | — | Legacy protected | `X-API-Key` | `200` | Backward-compatible single-point reverse geocode |
| `POST` | `/v1/geocode/reverse/batch` | — | Legacy protected | `X-API-Key` | `200` | Backward-compatible batch reverse geocode |

Every endpoint that accepts input can also return `422 Validation Error` (see [Errors](#errors)). Unversioned aliases (`/auth/*`, `/api-keys/*`) behave identically to their `/v1` counterparts.

---

## Geocoding API

### Single-point reverse geocode

`GET /v1/protected/geocode/reverse`

| Parameter | In | Type | Required | Constraints | Description |
|---|---|---|---|---|---|
| `lat` | query | number | Yes | `-90` to `90` | Latitude (WGS 84) |
| `lon` | query | number | Yes | `-180` to `180` | Longitude (WGS 84) |
| `Authorization` | header | string | Yes (enforced at runtime) | `Bearer <API_KEY>` | API key credential |

```bash
# Reverse geocode a point (KICC, Nairobi)
curl "http://localhost:8000/v1/protected/geocode/reverse?lat=-1.2864&lon=36.8172" \
  -H "Authorization: Bearer locus_live_W7zJ5r4F2qE9kL1p..."
```

*Response (200 OK) — values are illustrative:*
```json
{
  "county_name": "Nairobi",
  "constituency": "Starehe",
  "ward_name": "Nairobi Central",
  "sub_county_name": "Starehe",
  "location_name": null,
  "sub_location": null,
  "match_type": "exact",
  "distance_m": 0.0,
  "boundary_version": "iebc-2024-delimitation"
}
```

### Batch reverse geocode

`POST /v1/protected/geocode/reverse/batch`

The request body is a [`BatchGeocodeRequest`](#batchgeocoderequest). The number of points per request is capped at the server-side `MAX_BATCH_SIZE` setting. The response is a JSON **array** of [`GeocodeResponse`](#geocoderesponse) objects.

```bash
curl -X POST http://localhost:8000/v1/protected/geocode/reverse/batch \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer locus_live_W7zJ5r4F2qE9kL1p..." \
  -d '{"points": [{"lat": -1.2864, "lon": 36.8172}, {"lat": -4.0435, "lon": 39.6682}]}'
```

### Legacy endpoints (`X-API-Key`)

`GET /v1/geocode/reverse` and `POST /v1/geocode/reverse/batch` accept the same parameters and bodies and return the same responses, but authenticate with an `X-API-Key` header instead of `Authorization: Bearer`. They are retained for backward compatibility; use the `/v1/protected/...` endpoints for new integrations.

```bash
curl "http://localhost:8000/v1/geocode/reverse?lat=-1.2864&lon=36.8172" \
  -H "X-API-Key: locus_live_W7zJ5r4F2qE9kL1p..."
```

### Match types

| `match_type` | Meaning |
|---|---|
| `exact` | The point falls inside a boundary polygon (`ST_Contains`). |
| `nearest` | No polygon contains the point; the closest boundary within the fallback radius (`ST_DWithin`) is returned. `distance_m` gives the distance in metres. |

---

## Boundaries & Health API

### Boundary version — `GET /v1/boundaries/version`

Returns the active `boundary_version` and its `effective_from` date (nullable).

```bash
curl http://localhost:8000/v1/boundaries/version
```

```json
{
  "boundary_version": "iebc-2024-delimitation",
  "effective_from": "2024-01-01"
}
```

### Health — `GET /v1/health`

Liveness/readiness probe. Returns `200` with a free-form JSON object once the database and cache are reachable.

```bash
curl http://localhost:8000/v1/health
```

---

## Self-Service Accounts & API Key Management

Locus includes a production-ready, self-service account system for user registration, email verification, session authentication, and API key management.

### Account Lifecycle Flow

```
1. POST   /v1/auth/register          → Create unverified account, send verification email
2. POST   /v1/auth/verify-email      → Verify email with single-use token, activate account
3. POST   /v1/auth/login             → Authenticate with email/password, receive JWT access token
4. POST   /v1/api-keys               → Generate API key (raw key returned ONCE only)
5. GET    /v1/api-keys               → View key metadata (safe prefixes, no secrets)
6. DELETE /v1/api-keys/{key_id}      → Immediately revoke key
7. GET    /v1/protected/geocode/...  → Authenticate with Bearer API key
```

> The unversioned paths (`/auth/...`, `/api-keys/...`) are equivalent aliases. Examples below use the versioned form.

---

### Step-by-Step API Usage

#### 1. Register an account

`POST /v1/auth/register` — body: [`RegisterRequest`](#registerrequest)

```bash
curl -X POST http://localhost:8000/v1/auth/register \
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

`POST /v1/auth/verify-email` — body: [`VerifyEmailRequest`](#verifyemailrequest)

In development (`EMAIL_PROVIDER=console`), the verification link and token are logged to the console/stdout:
`[DEV EMAIL] Verification email to developer@example.com: verification link is http://localhost:8000/auth/verify-email?token=<TOKEN>`

Verify the account by submitting the token:

```bash
curl -X POST http://localhost:8000/v1/auth/verify-email \
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

*(Optional) Resend verification email* — `POST /v1/auth/resend-verification`, body: [`ResendVerificationRequest`](#resendverificationrequest):
```bash
curl -X POST http://localhost:8000/v1/auth/resend-verification \
  -H "Content-Type: application/json" \
  -d '{"email": "developer@example.com"}'
```

This endpoint always returns a generic [`MessageResponse`](#messageresponse) to prevent account enumeration:
```json
{ "message": "<generic confirmation message>" }
```

#### 3. Sign in (obtain access token)

`POST /v1/auth/login` — body: [`LoginRequest`](#loginrequest)

```bash
curl -X POST http://localhost:8000/v1/auth/login \
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

#### 3a. View your profile

`GET /v1/auth/me` — returns a [`UserResponse`](#userresponse).

```bash
curl http://localhost:8000/v1/auth/me \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

#### 4. Generate an API key

`POST /v1/api-keys` — body: [`CreateAPIKeyRequest`](#createapikeyrequest)

> **Important Security Notice**: The raw key secret (`key`) is returned **only once**, immediately after creation. It is never stored in plaintext and cannot be recovered later. Store it securely.

```bash
curl -X POST http://localhost:8000/v1/api-keys \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -d '{
    "name": "Production Backend",
    "expires_in_days": 90
  }'
```

*Response (201 Created):* [`CreatedAPIKeyResponse`](#createdapikeyresponse)
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

#### 5. List your API keys

`GET /v1/api-keys` — returns an array of [`APIKeyResponse`](#apikeyresponse). Only safe, non-secret metadata is returned:

```bash
curl http://localhost:8000/v1/api-keys \
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

#### 5a. Get a single key's metadata

`GET /v1/api-keys/{key_id}` — `key_id` must be a UUID. Returns an [`APIKeyResponse`](#apikeyresponse).

```bash
curl http://localhost:8000/v1/api-keys/e81d43a6-d069-424a-95ee-d0ebf8d42d65 \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

#### 6. Revoke an API key

`DELETE /v1/api-keys/{key_id}` — revocation is permanent and takes effect immediately:

```bash
curl -X DELETE http://localhost:8000/v1/api-keys/e81d43a6-d069-424a-95ee-d0ebf8d42d65 \
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

## Schema Reference

All request and response bodies are JSON. Fields marked **required** must be present; nullable fields may be `null` or omitted where the schema allows.

### Request schemas

#### RegisterRequest

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `email` | string | Yes | — | User's email address |
| `password` | string | Yes | 8–128 characters | User password |

#### LoginRequest

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | User's email address |
| `password` | string | Yes | User's password |

#### VerifyEmailRequest

| Field | Type | Required | Description |
|---|---|---|---|
| `token` | string | Yes | Cryptographically secure verification token |

#### ResendVerificationRequest

| Field | Type | Required | Description |
|---|---|---|---|
| `email` | string | Yes | Registered email address |

#### CreateAPIKeyRequest

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `name` | string | Yes | 1–100 characters | Human-readable name for the API key |
| `expires_in_days` | integer \| null | No | 1–365 | Optional lifetime in days; omit or `null` for no expiry |

#### GeocodeRequest

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `lat` | number | Yes | -90 to 90 | Latitude (WGS 84) |
| `lon` | number | Yes | -180 to 180 | Longitude (WGS 84) |

#### BatchGeocodeRequest

| Field | Type | Required | Description |
|---|---|---|---|
| `points` | array of [`GeocodeRequest`](#geocoderequest) | Yes | Points to geocode (capped at `MAX_BATCH_SIZE`) |

### Response schemas

#### GeocodeResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `county_name` | string | Yes | County |
| `constituency` | string | Yes | Constituency |
| `ward_name` | string | Yes | Ward |
| `sub_county_name` | string \| null | No | Sub-County |
| `location_name` | string \| null | No | Location |
| `sub_location` | string \| null | No | Sub-Location |
| `match_type` | `"exact"` \| `"nearest"` | Yes | How the boundary was matched (see [Match types](#match-types)) |
| `distance_m` | number | Yes | Distance in metres from the point to the matched boundary |
| `boundary_version` | string | Yes | Boundary dataset version used for the match |

#### VersionResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `boundary_version` | string | Yes | Active boundary version |
| `effective_from` | date (`YYYY-MM-DD`) \| null | No | Date the version took effect |

#### UserResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (UUID) | Yes | User ID |
| `email` | string | Yes | Email address |
| `is_active` | boolean | Yes | Whether the account is active |
| `is_verified` | boolean | Yes | Whether the email has been verified |
| `created_at` | string (date-time) | Yes | Account creation time |

#### TokenResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `access_token` | string | Yes | JWT access token |
| `token_type` | string | No | Always `bearer` (default) |
| `expires_in` | integer | Yes | Token lifetime in seconds |

#### APIKeyResponse

Safe metadata representation of an API key (never exposes the key or its hash).

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (UUID) | Yes | Key ID |
| `name` | string | Yes | Human-readable name |
| `key_prefix` | string | Yes | Non-secret prefix for recognition (e.g. `locus_live_W7zJ`) |
| `created_at` | string (date-time) | Yes | Creation time |
| `expires_at` | string (date-time) \| null | No | Expiry time, if any |
| `revoked_at` | string (date-time) \| null | No | Revocation time, if revoked |
| `is_active` | boolean | Yes | Whether the key is currently usable |

#### CreatedAPIKeyResponse

Returned immediately after creation. Contains the raw key **once only**.

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string (UUID) | Yes | Key ID |
| `name` | string | Yes | Human-readable name |
| `key` | string | Yes | **Raw API key secret — shown only once** |
| `key_prefix` | string | Yes | Non-secret prefix |
| `created_at` | string (date-time) | Yes | Creation time |
| `expires_at` | string (date-time) \| null | No | Expiry time, if any |

#### MessageResponse

| Field | Type | Required | Description |
|---|---|---|---|
| `message` | string | Yes | Human-readable message |

---

## Errors

Request validation failures return **`422 Unprocessable Entity`** with an `HTTPValidationError` body:

```json
{
  "detail": [
    {
      "loc": ["query", "lat"],
      "msg": "Input should be less than or equal to 90",
      "type": "less_than_equal",
      "input": "123",
      "ctx": { "le": 90 }
    }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `detail[].loc` | array of string \| integer | Location of the error (e.g. `["query", "lat"]`) |
| `detail[].msg` | string | Human-readable message |
| `detail[].type` | string | Machine-readable error type |
| `detail[].input` | any | The offending input (optional) |
| `detail[].ctx` | object | Additional context (optional) |

Other status codes returned at runtime (not enumerated in the OpenAPI document):

| Status | When |
|---|---|
| `401` / `403` | Missing, invalid, expired, or revoked credentials; unverified accounts exceeding the trial limit (see [Security Model](#security-model)) |
| `404` | API key ID not found or owned by another user |
| `429` | Rate limit exceeded |

---

## Security Model

1. **Password Hashing**: Passwords are hashed using **Argon2id** (`argon2-cffi`), configured with `time_cost=3`, `memory_cost=64 MiB`, and `parallelism=4`. Plaintext passwords are never stored or logged. Accepted password length is 8–128 characters.
2. **API Key Generation & Storage**:
   - Generated using Python's `secrets.token_urlsafe(32)` providing 256 bits of cryptographic entropy.
   - Prepended with a recognizable identifier prefix: `locus_live_`.
   - Stored exclusively as a **SHA-256 cryptographic hash** (`key_hash`).
   - The raw key is returned **only once** at creation and is never logged or displayed again.
   - Non-secret prefixes (e.g. `locus_live_abcd`) are stored for display and human recognition.
   - Optional expiry of 1–365 days; revocation is permanent and immediate.
3. **Email Verification & Unverified Trial Policy**:
   - Verification tokens are high-entropy single-use tokens generated with `secrets`.
   - Stored as SHA-256 hashes in `email_verification_tokens`.
   - Consumed atomically with user account verification in a database transaction (`UPDATE ... WHERE used_at IS NULL`).
   - **Reviewer / Trial Allowance**: Unverified accounts are permitted to sign in, create API keys, and make up to **5 API requests** (`MAX_UNVERIFIED_REQUESTS=5`) so reviewers can evaluate the API immediately without verifying upfront.
   - **Limit Enforcement**: Upon making the 6th request, unverified accounts receive an HTTP 403 Forbidden error with a message instructing them to activate their account (`You have reached the maximum of 5 requests for unverified accounts. Please activate your account by verifying your email to continue using the API.`).
   - **Automatic Activation Email**: When the 5-request trial limit is reached, an activation email with a fresh verification link is automatically dispatched to the user's registered email address.
   - Once verified, the request count limit is removed and full access is granted.
4. **Session / Access Tokens**:
   - JWTs signed using HMAC-SHA256 (HS256) with environment-configured `SECRET_KEY`.
   - Short expiration window (default 60 minutes, reported via `expires_in`).
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

The published `--boundary-version` is what the API reports in `GET /v1/boundaries/version` and in the `boundary_version` field of every geocode response.

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
│   ├── auth_routes.py          # /auth + /v1/auth registration, verification, login, me
│   ├── api_key_routes.py       # /api-keys + /v1/api-keys creation, listing, revocation
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
├── 003_api_keys.sql            # API keys schema
└── 004_unverified_trial.sql    # Unverified trial request count schema
```
