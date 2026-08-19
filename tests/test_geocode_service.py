"""
Unit tests for GeocodeService.
Mocks BoundaryRepository and GeocodeCache - no real database or Redis
connection. Tests orchestration logic only: validation -> cache ->
exact query -> fallback -> response assembly.
"""
