set shell := ["bash", "-euo", "pipefail", "-c"]

image := env_var_or_default("IMAGE", "memory-palace:local")
neo4j_image := env_var_or_default("NEO4J_IMAGE", "neo4j:5.26-community-trixie@sha256:4bae36aff76271e27fd6a6ed0835413f86a284cd179cfb1cb7d188f5f7533aca")
prek_version := "0.3.8"
trivy_image := env_var_or_default("TRIVY_IMAGE", "aquasec/trivy:0.72.0@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f")
trivy_cpus := env_var_or_default("TRIVY_CPUS", "1.0")
trivy_memory := env_var_or_default("TRIVY_MEMORY", "2g")

default:
    @just --list

# Install the locked development environment.
sync:
    uv sync --frozen

# Verify that pyproject.toml and uv.lock agree.
lock-check:
    uv lock --check

# Format Python sources and tests.
format:
    uv run --frozen ruff format src tests scripts

# Verify formatting without changing files.
format-check:
    uv run --frozen ruff format --check src tests scripts

# Run Ruff without automatic fixes.
lint:
    uv run --frozen ruff check src tests scripts

# Run the Astral type checker.
typecheck:
    uv run --frozen ty check

# Validate the prek hook configuration.
hooks-check:
    prek validate-config .pre-commit-config.yaml

# Install the blocking pre-commit and pre-push hooks.
hooks:
    uv tool install --force "prek=={{prek_version}}"
    "$(uv tool dir --bin)/prek" install --hook-type pre-commit --hook-type pre-push

# Run fast tests that do not require Neo4j.
test:
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 uv run --frozen pytest -m "not integration"

# Run tests that require a reachable Neo4j instance.
test-integration:
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 uv run --frozen pytest -m integration

# Parse every maintained shell entrypoint.
shell-syntax:
    bash -n run.sh run-prod.sh setup.sh scripts/infrastructure/setup-cloudflare-tunnel.sh scripts/infrastructure/smoke-production-compose.sh scripts/infrastructure/scan-source.sh

# Normalize both Compose configurations without requiring a real secret file.
compose-check:
    NEO4J_PASSWORD=compose-validation-password JWT_SECRET_KEY=compose-validation-jwt-secret-000000000000 VOYAGE_API_KEY=compose-validation-voyage-key OAUTH_OWNER_USERNAME=owner OAUTH_OWNER_PASSWORD=compose-validation-owner-password PUBLIC_BASE_URL=https://memory.example.com docker compose --env-file .env.example config --quiet
    NEO4J_PASSWORD=compose-validation-password JWT_SECRET_KEY=compose-validation-jwt-secret-000000000000 VOYAGE_API_KEY=compose-validation-voyage-key OAUTH_OWNER_USERNAME=owner OAUTH_OWNER_PASSWORD=compose-validation-owner-password PUBLIC_BASE_URL=https://memory.example.com docker compose --env-file .env.example -f docker-compose.prod.yml config --quiet
    NEO4J_PASSWORD=compose-validation-password JWT_SECRET_KEY=compose-validation-jwt-secret-000000000000 VOYAGE_API_KEY=compose-validation-voyage-key OAUTH_OWNER_USERNAME=owner OAUTH_OWNER_PASSWORD=compose-validation-owner-password PUBLIC_BASE_URL=https://memory.example.com docker compose --env-file .env.example -f docker-compose.prod.yml -f docker-compose.smoke.yml config --quiet

# Run the local deterministic quality gate.
ci: lock-check format-check lint typecheck hooks-check test shell-syntax compose-check

# Build the production image.
build:
    docker build --tag {{image}} .

# Confirm the built image declares and runs as an unprivileged user.
container-test: build
    docker run --rm --entrypoint /app/.venv/bin/python {{image}} -c 'import os; assert os.getuid() == 65532'

# Boot the hardened production topology with isolated throwaway state.
compose-smoke:
    ./scripts/infrastructure/smoke-production-compose.sh

# Scan source, lockfiles, configuration, and secrets.
security:
    ./scripts/infrastructure/scan-source.sh {{trivy_image}}

# Scan the locally built production image.
security-image: build
    docker run --rm --cpus {{trivy_cpus}} --memory {{trivy_memory}} --pids-limit 256 -v /var/run/docker.sock:/var/run/docker.sock -v memory-palace-trivy-cache:/root/.cache/trivy {{trivy_image}} image --ignore-unfixed --severity HIGH,CRITICAL --exit-code 1 {{image}}
    docker pull {{neo4j_image}}
    docker run --rm --cpus {{trivy_cpus}} --memory {{trivy_memory}} --pids-limit 256 -v /var/run/docker.sock:/var/run/docker.sock -v memory-palace-trivy-cache:/root/.cache/trivy -v "$PWD/.trivyignore.yaml:/etc/trivyignore.yaml:ro" {{trivy_image}} image --ignore-unfixed --ignorefile /etc/trivyignore.yaml --severity HIGH,CRITICAL --exit-code 1 {{neo4j_image}}

# Run all quality, build, runtime-user, and security gates.
verify: ci container-test compose-smoke security security-image
