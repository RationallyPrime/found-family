# Optimized multi-stage build with best practices for Python + uv
FROM python:3.13-slim-bookworm AS base

# Builder stage
FROM base AS builder

# Install uv from official image (using latest stable)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Set uv environment for optimal builds
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy only dependency files first (for better layer caching)
COPY pyproject.toml uv.lock ./

# Install dependencies only (without the project) using cache mount
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy the rest of the application
COPY src/ ./src/

# Install the project itself (will be fast since deps are cached)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Runtime stage
FROM base

# Create non-root user for security
RUN useradd -m -u 1000 claude && \
    mkdir -p /app/logs && \
    chown -R claude:claude /app

WORKDIR /app

# Copy only the virtual environment and source from builder
COPY --from=builder --chown=claude:claude /app/.venv /app/.venv
COPY --from=builder --chown=claude:claude /app/src /app/src

# Copy configuration files
COPY --chown=claude:claude pyproject.toml uv.lock ./

# Set environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install curl for health checks (minimal overhead)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Switch to non-root user
USER claude

# Correct health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Run the application with hot reload
CMD ["uvicorn", "memory_palace.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]