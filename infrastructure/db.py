"""
Async database connection management for PostGIS.
Owns the single asyncpg (or SQLAlchemy async engine) connection pool
for the whole process. Opened once at app startup (main.py) and
reused by every request - never opened per-request.
"""
