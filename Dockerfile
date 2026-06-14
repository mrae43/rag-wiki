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

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-cache

COPY . .

# Default command (overridden per-service in compose)
CMD ["uv", "run", "uvicorn", "ragwiki.main:app", "--host", "0.0.0.0", "--port", "8000"]