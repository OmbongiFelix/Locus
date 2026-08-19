"""
Request-scoped FastAPI dependencies: service construction, auth, and
rate limiting.
Centralizing these here means routes.py stays declarative - each
route just lists which dependencies it needs.
"""
