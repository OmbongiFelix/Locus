"""
FastAPI application entrypoint for the Locus reverse-geocoding service.

Builds the FastAPI app, mounts the versioned router from api/routes.py,
and wires startup/shutdown events that open and close the database
connection pool and the Redis client.

This is the only file that should be run directly
(uvicorn locus.main:app). All request-handling logic lives in api/
and domain/ — this file is composition only.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from locus.api.routes import router
from locus.infrastructure.db import init_db_pool, close_db_pool
from locus.infrastructure.cache import init_cache, close_cache
from locus.config import get_settings
from locus.logging_config import configure_logging
from locus.domain.validation import InvalidCoordinatesError
from locus.domain.geocode_service import NoMatchError
from fastapi.middleware.cors import CORSMiddleware

configure_logging()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: open DB pool and Redis.  Shutdown: close both."""
    await init_db_pool()
    await init_cache()
    yield
    await close_cache()
    await close_db_pool()


app = FastAPI(
    title="Locus",
    version="0.1.0",
    description="Kenyan reverse-geocoding service backed by PostGIS",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://locus-frontend-8t7a.onrender.com/", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/v1")


# ── Global exception handlers ───────────────────────────────────────


@app.exception_handler(InvalidCoordinatesError)
async def _invalid_coordinates_handler(
    request: Request, exc: InvalidCoordinatesError
):
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_coordinates", "message": str(exc)},
    )


@app.exception_handler(NoMatchError)
async def _no_match_handler(request: Request, exc: NoMatchError):
    return JSONResponse(
        status_code=404,
        content={"error": "no_match", "message": str(exc)},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred.",
        },
    )
