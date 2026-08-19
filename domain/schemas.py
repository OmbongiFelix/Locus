"""
Internal data contracts for the domain layer.
Separate from api/schemas.py (the public wire format) so the two can
evolve independently. No Pydantic, no FastAPI - plain dataclasses so
this module is importable from pipeline/ scripts without pulling in
the web framework.
"""
