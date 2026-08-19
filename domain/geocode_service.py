"""
Orchestrates a single reverse-geocode request end to end.
Sequence (mirrors the request-flow diagram at the top of this
document):
1. validate_coordinates() - domain/validation.py
2. cache.get() - infrastructure/cache.py
3. repository.find_exact() - ST_Contains lookup
4. repository.find_nearest() if (3) misses - ST_DWithin fallback,
tolerance from settings.FALLBACK_RADIUS_M
5. assemble a GeocodeResult, tagging match_type
6. cache.set()
7. return to the caller (api/routes.py)
This is the only place that sequence is allowed to live - routes.py
calls this and nothing else for the actual geocoding logic.
"""
