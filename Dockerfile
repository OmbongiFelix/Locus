FROM python:3.12-slim

WORKDIR /app

# System dependencies required by geospatial/database packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv

# Copy package metadata and README
COPY pyproject.toml uv.lock README.md ./

# Copy application package
COPY locus/ ./locus/

# Install the application and its dependencies
RUN uv pip install --system .

# Copy environment configuration
COPY .env ./

# FastAPI
EXPOSE 8000

CMD ["uvicorn", "locus.main:app", "--host", "0.0.0.0", "--port", "8000"]