# Build stage
FROM python:3.13-slim as builder

# Install uv
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies (production only)
RUN uv sync --frozen

# Runtime stage
FROM python:3.13-slim

# Create non-root user for security
RUN useradd -m -u 1000 claude && \
    mkdir -p /app/logs && \
    chown -R claude:claude /app

# Set working directory
WORKDIR /app

# Install uv in runtime
RUN pip install uv

# Copy everything with correct ownership
COPY --chown=claude:claude pyproject.toml uv.lock ./
COPY --chown=claude:claude src/ ./src/
COPY --from=builder --chown=claude:claude /app/.venv /app/.venv

# Set Python path
ENV PYTHONPATH=/app/src

# Switch to non-root user
USER claude

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/memory/health')"

# Run the application (no --reload in production)
CMD ["uv", "run", "uvicorn", "memory_palace.main:app", "--host", "0.0.0.0", "--port", "8000"]
