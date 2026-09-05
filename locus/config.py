"""
Centralized runtime configuration for the Locus service.

Reads environment variables once into a cached Settings object so the
rest of the codebase never calls os.environ directly. Includes the

Kenya bounding box constants used by domain/validation.py for the
cheap early-reject check described in the design document (Section 5).
"""

from functools import lru_cache
from typing import NamedTuple

from pydantic_settings import BaseSettings


class KenyaBBox(NamedTuple):
    """Approximate bounding box for Kenya used for early-reject validation."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


KENYA_BBOX = KenyaBBox(lat_min=-4.72, lat_max=5.06, lon_min=33.5, lon_max=41.9)


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables / .env file."""

    DATABASE_URL: str = "postgresql://app_user:password@localhost:5432/app_db"
    REDIS_URL: str = "redis://:password@localhost:6379/0"
    RATE_LIMIT_PER_MINUTE: int = 60
    FALLBACK_RADIUS_M: float = 5000.0
    MAX_BATCH_SIZE: int = 50
    API_KEYS: str = ""  # comma-separated list of valid API keys
    DB_POOL_MIN_SIZE: int = 2
    DB_POOL_MAX_SIZE: int = 10
    DB_POOL_TIMEOUT: float = 30.0
    CACHE_TTL_SECONDS: int = 3600  # 1 hour default
    CACHE_MISS_TTL_SECONDS: int = 60  # short TTL for misses

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings singleton.  Import this, not Settings."""
    return Settings()
