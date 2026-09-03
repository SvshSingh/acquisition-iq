# Long-running API container. Not a serverless function, deliberately — see
# ARCHITECTURE.md: scrape jobs outlast a serverless timeout, and the connection
# pool and parsed dataset are only worth having if the process survives.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies in their own layer, so a code change does not reinstall the world.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/pyproject.toml ./pyproject.toml
# Migration tooling travels with the image, so `alembic upgrade head` can be run
# in the container against a real database. Without these the schema-management
# capability documented in ARCHITECTURE.md would not actually exist in the
# deployed artifact.
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic

# The committed snapshot the demo serves. Copied rather than mounted so the
# image runs standalone — a container that needs a volume to answer a request is
# not deployable to Render as-is.
COPY data ./data

# Non-root: the process only ever reads its own files and talks to the network.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

ENV SEED_DATASET_PATH=/app/data/seed_glendale.json \
    ENVIRONMENT=production \
    DEBUG=false \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

# Shell form so $PORT expands — Render assigns the port at runtime.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
