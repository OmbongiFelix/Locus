"""
Pydantic request/response contracts for the public API.
These are the wire-format types - separate from domain/schemas.py,
which defines internal dataclasses. Keeping them separate means the
JSON shape can evolve (versioned under /v1, /v2) without touching
internal domain logic.
"""

