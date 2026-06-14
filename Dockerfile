FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install system deps for pymupdf and unstructured
RUN apt-get update && apt-get install -y \
    libmupdf-dev \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Isolate the venv so the .:/app bind mount never overwrites it
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN mkdir -p src/ragwiki && touch src/ragwiki/__init__.py
RUN uv sync --frozen --no-cache

COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

COPY . .

# Default command (overridden per-service in compose)
CMD ["uv", "run", "uvicorn", "ragwiki.main:app", "--host", "0.0.0.0", "--port", "8000"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]