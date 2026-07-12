# syntax=docker/dockerfile:1

# -----------------------------------------------------------------------------
# Builder stage — resolve dependencies into an isolated virtual environment.
# -----------------------------------------------------------------------------
FROM python:3.14-slim-bookworm@sha256:4ff4b92a68355dbdb52584ab3391dff8d371a61d4e063468bfd0130e3189c6d9 AS builder

# Pin uv to a known-good release for reproducible builds.
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv

WORKDIR /app

# Place the project environment outside /app so a bind mount cannot overwrite it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache


# -----------------------------------------------------------------------------
# Runtime stage — minimal image with only runtime system deps and the venv.
# -----------------------------------------------------------------------------
FROM python:3.14-slim-bookworm@sha256:4ff4b92a68355dbdb52584ab3391dff8d371a61d4e063468bfd0130e3189c6d9

# Pin uv to the same release used at build time so entrypoints can use `uv run`.
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_NO_CACHE=1
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install runtime system deps for pymupdf and unstructured, then clean apt caches.
# Also run upgrade to pick up security patches (libcurl4, libgif7, etc.) that
# may not yet be baked into the pinned base image SHA.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root runtime user (fixed UID/GID 1000 for predictable volume ownership).
RUN groupadd -g 1000 app \
    && useradd -u 1000 -g app -d /app -s /usr/sbin/nologin app \
    && mkdir -p /var/lib/rag-wiki/uploads \
    && chown app:app /var/lib/rag-wiki/uploads

# Copy the pre-built virtual environment from the builder stage with correct ownership.
COPY --from=builder --chown=app:app /opt/venv /opt/venv

COPY --chown=app:app docker-entrypoint.sh /app/docker-entrypoint.sh
COPY --chown=app:app . .

# Ensure the runtime user can write to the working directory (e.g. for uv caches).
RUN chown app:app /app

USER app

# Default command (overridden per-service in compose).
CMD ["uv", "run", "uvicorn", "rag_wiki.main:app", "--host", "0.0.0.0", "--port", "8000"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
