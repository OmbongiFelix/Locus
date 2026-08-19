"""
End-to-end API tests using FastAPI's TestClient.
Exercises the HTTP layer (api/routes.py) with the geocode service
dependency overridden to a mock, covering the request/response
contract and error codes defined in the design document (Section 5).
"""
