# syntax=docker/dockerfile:1.7

ARG UV_VERSION=0.11.19
ARG UV_DIGEST=sha256:b46b03ddfcfbf8f547af7e9eaefdf8a39c8cebcba7c98858d3162bd28cf536f6
ARG DISTROLESS_PYTHON_ROOT_DIGEST=sha256:393cdf69ec7a5e217f837f2ff9b2123e06545d89c6e718c14ad020451fcb1900
ARG DISTROLESS_PYTHON_NONROOT_DIGEST=sha256:828da6b298ecebf90580c84476c29b847b6432b46dbfaa642726b87ac527ee22

FROM ghcr.io/astral-sh/uv:${UV_VERSION}@${UV_DIGEST} AS uv

FROM gcr.io/distroless/python3-debian13:latest@${DISTROLESS_PYTHON_ROOT_DIGEST} AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/bin/python3.13 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY --from=uv /uv /uv
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    ["/uv", "sync", "--frozen", "--no-install-project", "--no-dev"]

COPY README.md LICENSE ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/uv \
    ["/uv", "sync", "--frozen", "--no-dev"]

FROM gcr.io/distroless/python3-debian13:nonroot@${DISTROLESS_PYTHON_NONROOT_DIGEST} AS runtime

ENV PATH=/app/.venv/bin \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

WORKDIR /app

COPY --from=builder --chown=65532:65532 /app/.venv /app/.venv
COPY --from=builder --chown=65532:65532 /app/src /app/src

USER 65532:65532

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).close()"]

ENTRYPOINT ["/app/.venv/bin/python", "-m", "uvicorn"]
CMD ["memory_palace.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
