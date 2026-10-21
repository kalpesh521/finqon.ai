# syntax=docker/dockerfile:1
#
# Build context is the repository root (see docker-compose.yml), so the image can carry
# the migrations, role script, seed data and eval set alongside the API.
#
#   api    the production API image: non-root, no psql, no dev dependencies.
#   tools  api + psql + pytest/ruff/pyyaml, for one-off setup, tests and the eval.

FROM python:3.12-slim AS api

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# An editable install keeps the package under /app/backend, so finqon.core.config can
# still find the repository root (db/, dataset/, eval/) two levels up.
COPY backend/pyproject.toml backend/alembic.ini backend/
COPY backend/finqon backend/finqon
COPY backend/alembic backend/alembic
RUN pip install -e backend

RUN useradd --system --uid 10001 --home-dir /nonexistent --no-create-home finqon
USER finqon

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "finqon.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]


FROM api AS tools

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY backend/tests backend/tests
COPY db db
COPY dataset dataset
COPY eval eval
RUN pip install -e "backend[dev]"

WORKDIR /app/backend
CMD ["pytest", "-q"]
