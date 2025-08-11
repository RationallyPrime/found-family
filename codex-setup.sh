#!/usr/bin/env bash
set -euo pipefail

# Ensure uv is installed
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Ensure required Python version
uv python install 3.13 >/dev/null

# Install project dependencies including dev extras
uv sync --extra dev

# Create .env from example if missing
if [ ! -f .env ]; then
  cp .env.example .env
fi

# Verify Docker availability
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required but not installed." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required but not available." >&2
  exit 1
fi

# Start Neo4j container
docker compose up -d neo4j

echo "Waiting for Neo4j to be ready..."
until docker compose exec -T neo4j cypher-shell -u neo4j -p password "RETURN 1" &>/dev/null; do
  sleep 1
  echo -n "."
done
echo ""

# Start FastAPI app (with reload for dev)
uv run uvicorn memory_palace.main:app --reload --host 0.0.0.0 --port 8000
