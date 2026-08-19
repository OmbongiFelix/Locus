"""
HTTP route definitions for the Locus reverse-geocoding API.
Endpoints:
GET /v1/geocode/reverse - single-point reverse geocode
POST /v1/geocode/reverse/batch - batch reverse geocode (capped size)
GET /v1/boundaries/version - currently active boundary_version
GET /v1/health - liveness/readiness probe
Routes are thin: they parse/validate the HTTP layer via schemas.py,
call into domain/geocode_service.py, and translate the result (or a
raised domain exception) into an HTTP response. No spatial logic here.
"""

